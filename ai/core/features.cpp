#include "features.h"
#include <cmath>
#include <cstring>
#include <algorithm>
#include <numeric>
#include <unordered_map>
#include <vector>
#include <string>
#include <stdexcept>

namespace statwise {

static constexpr double DEFAULT_ELO      = 1500.0;
static constexpr double ELO_SCALE        = 400.0;
static const     double DRAW_CORRECTION  = 0.1;
static constexpr double FORM_DECAY       = 0.85;
static constexpr int    FORM_WINDOW      = 15;
static constexpr double POISSON_LAMBDA_CAP = 6.0;

static double expected_score(double rating_a, double rating_b) {
    return 1.0 / (1.0 + std::pow(10.0, (rating_b - rating_a) / ELO_SCALE));
}

static double match_outcome_score(int goals_a, int goals_b) {
    if (goals_a > goals_b) return 1.0;
    if (goals_a < goals_b) return 0.0;
    return 0.5;
}

static double poisson_cdf(double lambda, int k) {
    double cumulative = 0.0;
    double term = std::exp(-lambda);
    for (int i = 0; i <= k; ++i) {
        cumulative += term;
        if (i < k) term *= lambda / (i + 1);
    }
    return cumulative;
}

static double poisson_pmf(double lambda, int k) {
    if (lambda <= 0.0) return (k == 0) ? 1.0 : 0.0;
    double log_pmf = k * std::log(lambda) - lambda;
    for (int i = 1; i <= k; ++i) log_pmf -= std::log(static_cast<double>(i));
    return std::exp(log_pmf);
}

extern "C" {

void compute_elo_ratings(
    const char** home_teams,
    const char** away_teams,
    const int*   home_goals,
    const int*   away_goals,
    int          n_matches,
    double       k_factor,
    double       home_advantage,
    double*      out_home_elos,
    double*      out_away_elos)
{
    std::unordered_map<std::string, double> ratings;

    for (int i = 0; i < n_matches; ++i) {
        std::string home(home_teams[i]);
        std::string away(away_teams[i]);

        if (ratings.find(home) == ratings.end()) ratings[home] = DEFAULT_ELO;
        if (ratings.find(away) == ratings.end()) ratings[away] = DEFAULT_ELO;

        double r_home = ratings[home] + home_advantage;
        double r_away = ratings[away];

        double e_home = expected_score(r_home, r_away);
        double e_away = 1.0 - e_home;

        double s_home = match_outcome_score(home_goals[i], away_goals[i]);
        double s_away = 1.0 - s_home;

        // Goal difference k-factor multiplier
        int gd = std::abs(home_goals[i] - away_goals[i]);
        double gd_mult = 1.0;
        if (gd == 2) gd_mult = 1.5;
        else if (gd == 3) gd_mult = 1.75;
        else if (gd >= 4) gd_mult = 1.75 + 0.15 * (gd - 3);
        gd_mult = std::min(gd_mult, 3.0);

        double effective_k = k_factor * gd_mult;

        double prev_home = ratings[home];
        double prev_away = ratings[away];

        ratings[home] = prev_home + effective_k * (s_home - e_home);
        ratings[away] = prev_away + effective_k * (s_away - e_away);

        out_home_elos[i] = ratings[home];
        out_away_elos[i] = ratings[away];
    }
}

void compute_form_vector(
    const int*    home_goals,
    const int*    away_goals,
    const int*    was_home,
    int           n_matches,
    const double* recency_weights,
    double*       out_form)
{
    // out_form layout: [win_rate, draw_rate, loss_rate, goals_scored, goals_conceded,
    //                   goal_diff, ppg, momentum, clean_sheets, scoring_games]
    double weight_sum = 0.0;
    double wins = 0.0, draws = 0.0, losses = 0.0;
    double goals_scored = 0.0, goals_conceded = 0.0;
    double momentum = 0.0;
    double clean_sheets = 0.0, scoring_games = 0.0;

    int n = std::min(n_matches, FORM_WINDOW);

    for (int i = 0; i < n; ++i) {
        double w = recency_weights ? recency_weights[i] : std::pow(FORM_DECAY, n - 1 - i);
        weight_sum += w;

        int scored, conceded;
        if (was_home[i]) {
            scored    = home_goals[i];
            conceded  = away_goals[i];
        } else {
            scored    = away_goals[i];
            conceded  = home_goals[i];
        }

        goals_scored    += w * scored;
        goals_conceded  += w * conceded;
        if (scored > conceded)  { wins   += w; momentum += w * 3.0; }
        else if (scored == conceded) { draws  += w; momentum += w * 1.0; }
        else                     { losses += w; momentum += w * 0.0; }

        if (conceded == 0) clean_sheets += w;
        if (scored > 0)    scoring_games += w;
    }

    if (weight_sum < 1e-9) {
        for (int j = 0; j < 10; ++j) out_form[j] = 0.0;
        return;
    }

    out_form[0] = wins   / weight_sum;
    out_form[1] = draws  / weight_sum;
    out_form[2] = losses / weight_sum;
    out_form[3] = goals_scored   / weight_sum;
    out_form[4] = goals_conceded / weight_sum;
    out_form[5] = (goals_scored - goals_conceded) / weight_sum;
    out_form[6] = momentum / weight_sum;
    out_form[7] = (wins * 3.0 + draws * 1.0) / (weight_sum * 3.0);
    out_form[8] = clean_sheets / weight_sum;
    out_form[9] = scoring_games / weight_sum;
}

void compute_h2h_stats(
    const int* home_goals,
    const int* away_goals,
    const int* was_home_team_first,
    int        n_matches,
    double*    out_h2h)
{
    // out_h2h: [win_rate_first, draw_rate, loss_rate_first, goals_first, goals_second, n_matches]
    if (n_matches == 0) {
        for (int i = 0; i < 6; ++i) out_h2h[i] = 0.0;
        out_h2h[5] = 0.0;
        return;
    }

    double wins = 0, draws = 0, losses = 0;
    double goals_first = 0, goals_second = 0;

    for (int i = 0; i < n_matches; ++i) {
        int g1, g2;
        if (was_home_team_first[i]) {
            g1 = home_goals[i]; g2 = away_goals[i];
        } else {
            g1 = away_goals[i]; g2 = home_goals[i];
        }
        if (g1 > g2)       wins++;
        else if (g1 == g2) draws++;
        else               losses++;
        goals_first  += g1;
        goals_second += g2;
    }

    double n = static_cast<double>(n_matches);
    out_h2h[0] = wins   / n;
    out_h2h[1] = draws  / n;
    out_h2h[2] = losses / n;
    out_h2h[3] = goals_first  / n;
    out_h2h[4] = goals_second / n;
    out_h2h[5] = n;
}

void compute_goal_probability(
    double attack_home,
    double defense_away,
    double attack_away,
    double defense_home,
    double league_avg_goals,
    double home_adv,
    double* out_prob_over25,
    double* out_prob_btts)
{
    double lambda_home = std::min(attack_home * defense_away * home_adv, POISSON_LAMBDA_CAP);
    double lambda_away = std::min(attack_away * defense_home,             POISSON_LAMBDA_CAP);

    if (lambda_home <= 0.0) lambda_home = league_avg_goals * home_adv / 2.0;
    if (lambda_away <= 0.0) lambda_away = league_avg_goals / 2.0;

    // P(total goals > 2.5) = 1 - P(total goals <= 2)
    double prob_under25 = 0.0;
    for (int gh = 0; gh <= 2; ++gh) {
        for (int ga = 0; ga <= 2 - gh; ++ga) {
            prob_under25 += poisson_pmf(lambda_home, gh) * poisson_pmf(lambda_away, ga);
        }
    }
    *out_prob_over25 = 1.0 - prob_under25;

    // P(BTTS) = P(home scores >= 1) * P(away scores >= 1)
    double prob_home_no_score = poisson_pmf(lambda_home, 0);
    double prob_away_no_score = poisson_pmf(lambda_away, 0);
    *out_prob_btts = (1.0 - prob_home_no_score) * (1.0 - prob_away_no_score);
}

void compute_elo_probabilities(
    double elo_home,
    double elo_away,
    double home_advantage,
    double* prob_home,
    double* prob_draw,
    double* prob_away)
{
    double adj_home = elo_home + home_advantage;
    double e_home = expected_score(adj_home, elo_away);

    // Estimate draw probability using a logistic approximation
    double elo_diff = std::abs(adj_home - elo_away);
    double draw_center = 0.28;
    double draw_decay  = 0.0015;
    double draw_prob   = draw_center * std::exp(-draw_decay * elo_diff);
    draw_prob = std::max(0.05, std::min(draw_prob, 0.35));

    double remainder = 1.0 - draw_prob;
    *prob_home = e_home * remainder;
    *prob_away = (1.0 - e_home) * remainder;
    *prob_draw = draw_prob;

    // Normalise
    double total = *prob_home + *prob_draw + *prob_away;
    *prob_home /= total;
    *prob_draw /= total;
    *prob_away /= total;
}

void batch_compute_features(
    const double* home_elos,
    const double* away_elos,
    const double* home_forms,
    const double* away_forms,
    const double* h2h_stats,
    const double* league_stats,
    double        home_advantage,
    int           n_matches,
    double*       out_features,
    int           n_features)
{
    const int FORM_DIM  = 10;
    const int H2H_DIM   = 6;
    const int LEAGUE_DIM = 4; // [avg_goals, home_attack_strength, away_attack_strength, home_adv]

    for (int i = 0; i < n_matches; ++i) {
        double* feat = out_features + i * n_features;
        int     f    = 0;

        double elo_h = home_elos[i];
        double elo_a = away_elos[i];

        // Elo features
        double ph, pd, pa;
        compute_elo_probabilities(elo_h, elo_a, home_advantage, &ph, &pd, &pa);
        feat[f++] = elo_h;
        feat[f++] = elo_a;
        feat[f++] = elo_h - elo_a;
        feat[f++] = ph;
        feat[f++] = pd;
        feat[f++] = pa;

        // Home form
        const double* hf = home_forms + i * FORM_DIM;
        for (int j = 0; j < FORM_DIM; ++j) feat[f++] = hf[j];

        // Away form
        const double* af = away_forms + i * FORM_DIM;
        for (int j = 0; j < FORM_DIM; ++j) feat[f++] = af[j];

        // H2H
        const double* h2h = h2h_stats + i * H2H_DIM;
        for (int j = 0; j < H2H_DIM; ++j) feat[f++] = h2h[j];

        // Goal probabilities
        const double* ls = league_stats + i * LEAGUE_DIM;
        double p_over25, p_btts;
        compute_goal_probability(
            hf[3], af[4],
            af[3], hf[4],
            ls[0], ls[3],
            &p_over25, &p_btts
        );
        feat[f++] = p_over25;
        feat[f++] = p_btts;

        // Form differentials
        feat[f++] = hf[0] - af[0];
        feat[f++] = hf[3] - af[3];
        feat[f++] = hf[6] - af[6];
        feat[f++] = hf[7] - af[7];
    }
}

} // extern "C"
} // namespace statwise
