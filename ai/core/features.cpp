/*
 * StatWise — C++ feature-engineering kernel
 *
 * Compiled with -O3 -march=native -ffast-math for maximum throughput.
 * All functions are exported as C symbols so ctypes can load them directly.
 *
 * Functions:
 *   Original  : compute_elo_ratings, compute_form_vector, compute_h2h_stats,
 *               compute_goal_probability, compute_elo_probabilities,
 *               batch_compute_features
 *   New (v2)  : compute_attack_defense_elo, compute_poisson_score_matrix,
 *               compute_consecutive_runs, compute_venue_split_form,
 *               compute_goals_variance
 */
#include "features.h"
#include <cmath>
#include <cstring>
#include <algorithm>
#include <numeric>
#include <unordered_map>
#include <vector>
#include <string>
#include <stdexcept>
#include <ctime>

namespace statwise {

/* ── constants ─────────────────────────────────────────────────────────── */

static constexpr double DEFAULT_ELO        = 1500.0;
static constexpr double ELO_SCALE          = 400.0;
static constexpr double FORM_DECAY         = 0.85;
static constexpr int    FORM_WINDOW        = 15;
static constexpr double POISSON_LAMBDA_CAP = 6.0;
static constexpr int    SCORE_MAX          = 7;   // score matrix up to 6×6
static constexpr double DIXON_COLES_RHO    = -0.13;
static constexpr int    RUN_CAP            = 15;

/* ── private helpers ───────────────────────────────────────────────────── */

static double expected_score(double ra, double rb) {
    return 1.0 / (1.0 + std::pow(10.0, (rb - ra) / ELO_SCALE));
}

static double gd_multiplier(int gd) {
    if (gd <= 1) return 1.0;
    if (gd == 2) return 1.5;
    if (gd == 3) return 1.75;
    return std::min(1.75 + 0.15 * (gd - 3), 3.0);
}

/* Poisson PMF — log-space for numerical stability */
static double poisson_pmf(double lambda, int k) {
    if (lambda <= 0.0) return (k == 0) ? 1.0 : 0.0;
    if (k < 0) return 0.0;
    double lp = k * std::log(lambda) - lambda;
    for (int i = 1; i <= k; ++i) lp -= std::log(static_cast<double>(i));
    return std::exp(lp);
}

/*
 * Dixon-Coles τ correction applied to the bivariate Poisson cell (h,a).
 * rho = -0.13 (standard parameter; tightens draws/low-score cells).
 */
static double dc_correction(int h, int a, double lh, double la, double rho) {
    if (h == 0 && a == 0) return 1.0 - lh * la * rho;
    if (h == 0 && a == 1) return 1.0 + lh * rho;
    if (h == 1 && a == 0) return 1.0 + la * rho;
    if (h == 1 && a == 1) return 1.0 - rho;
    return 1.0;
}

/* Weighted mean / variance helpers */
static void weighted_moments(const double* vals, const double* w, int n,
                              double& mean, double& var) {
    double sw = 0, sx = 0, sx2 = 0;
    for (int i = 0; i < n; ++i) {
        sw  += w[i];
        sx  += w[i] * vals[i];
        sx2 += w[i] * vals[i] * vals[i];
    }
    if (sw < 1e-12) { mean = 0; var = 0; return; }
    mean = sx / sw;
    var  = std::max(0.0, sx2 / sw - mean * mean);
}

/* ── extern "C" implementations ────────────────────────────────────────── */

extern "C" {

/* ── 1. compute_elo_ratings (original) ──────────────────────────────── */

void compute_elo_ratings(
    const char** home_teams, const char** away_teams,
    const int* home_goals,   const int* away_goals,
    int n_matches, double k_factor, double home_advantage,
    double* out_home_elos,   double* out_away_elos)
{
    std::unordered_map<std::string, double> ratings;

    for (int i = 0; i < n_matches; ++i) {
        std::string home(home_teams[i]), away(away_teams[i]);
        if (!ratings.count(home)) ratings[home] = DEFAULT_ELO;
        if (!ratings.count(away)) ratings[away] = DEFAULT_ELO;

        double rh = ratings[home] + home_advantage;
        double ra = ratings[away];
        double eh = expected_score(rh, ra);
        double sh = (home_goals[i] > away_goals[i]) ? 1.0 :
                    (home_goals[i] < away_goals[i]) ? 0.0 : 0.5;

        int gd = std::abs(home_goals[i] - away_goals[i]);
        double ek = k_factor * gd_multiplier(gd);

        ratings[home] += ek * (sh       - eh);
        ratings[away] += ek * ((1-sh)   - (1-eh));

        out_home_elos[i] = ratings[home];
        out_away_elos[i] = ratings[away];
    }
}

/* ── 2. compute_form_vector (original, 10 outputs) ───────────────────── */

void compute_form_vector(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches,
    const double* recency_weights,
    double* out_form)
{
    double ws = 0, wins = 0, draws = 0, losses = 0;
    double gs = 0, gc = 0, mom = 0, cs = 0, sg = 0;
    int n = std::min(n_matches, FORM_WINDOW);

    for (int i = 0; i < n; ++i) {
        double w = recency_weights ? recency_weights[i]
                                   : std::pow(FORM_DECAY, n - 1 - i);
        ws += w;
        int s = was_home[i] ? home_goals[i] : away_goals[i];
        int c = was_home[i] ? away_goals[i] : home_goals[i];
        gs += w * s; gc += w * c;
        if (s > c)  { wins += w;  mom += w * 3.0; }
        else if (s == c){ draws+= w; mom += w * 1.0; }
        else           { losses+= w; }
        if (c == 0) cs += w;
        if (s > 0)  sg += w;
    }
    if (ws < 1e-9) { for (int j=0;j<10;++j) out_form[j]=0; return; }

    out_form[0] = wins   / ws;
    out_form[1] = draws  / ws;
    out_form[2] = losses / ws;
    out_form[3] = gs     / ws;
    out_form[4] = gc     / ws;
    out_form[5] = (gs - gc) / ws;
    out_form[6] = mom    / ws;
    out_form[7] = (wins * 3.0 + draws) / (ws * 3.0);
    out_form[8] = cs     / ws;
    out_form[9] = sg     / ws;
}

/* ── 3. compute_h2h_stats (original, 6 outputs) ─────────────────────── */

void compute_h2h_stats(
    const int* home_goals, const int* away_goals,
    const int* was_first, int n_matches, double* out_h2h)
{
    if (n_matches == 0) {
        for (int i=0;i<6;++i) out_h2h[i]=0; return;
    }
    double wins=0,draws=0,losses=0,gf=0,gs2=0;
    for (int i=0;i<n_matches;++i) {
        int g1 = was_first[i] ? home_goals[i] : away_goals[i];
        int g2 = was_first[i] ? away_goals[i] : home_goals[i];
        if (g1>g2) wins++; else if(g1==g2) draws++; else losses++;
        gf+=g1; gs2+=g2;
    }
    double n=(double)n_matches;
    out_h2h[0]=wins/n; out_h2h[1]=draws/n; out_h2h[2]=losses/n;
    out_h2h[3]=gf/n;   out_h2h[4]=gs2/n;  out_h2h[5]=n;
}

/* ── 4. compute_goal_probability (original) ─────────────────────────── */

void compute_goal_probability(
    double attack_home, double defense_away,
    double attack_away, double defense_home,
    double league_avg_goals, double home_adv,
    double* out_over25, double* out_btts)
{
    double lh = std::min(attack_home * defense_away * home_adv, POISSON_LAMBDA_CAP);
    double la = std::min(attack_away * defense_home,             POISSON_LAMBDA_CAP);
    if (lh <= 0) lh = league_avg_goals * home_adv / 2.0;
    if (la <= 0) la = league_avg_goals / 2.0;

    double pu25 = 0;
    for (int h=0; h<=2; ++h)
        for (int a=0; a<=2-h; ++a)
            pu25 += poisson_pmf(lh,h) * poisson_pmf(la,a);
    *out_over25 = 1.0 - pu25;
    *out_btts   = (1.0 - poisson_pmf(lh,0)) * (1.0 - poisson_pmf(la,0));
}

/* ── 5. compute_elo_probabilities (original) ─────────────────────────── */

void compute_elo_probabilities(
    double elo_home, double elo_away, double home_advantage,
    double* prob_home, double* prob_draw, double* prob_away)
{
    double adj  = elo_home + home_advantage;
    double eh   = expected_score(adj, elo_away);
    double diff = std::abs(adj - elo_away);
    double dpb  = std::max(0.05, std::min(0.35, 0.28 * std::exp(-0.0015 * diff)));
    double rem  = 1.0 - dpb;
    *prob_home  = eh * rem;
    *prob_away  = (1.0 - eh) * rem;
    *prob_draw  = dpb;
    double tot  = *prob_home + *prob_draw + *prob_away;
    *prob_home /= tot; *prob_draw /= tot; *prob_away /= tot;
}

/* ── 6. batch_compute_features (original) ───────────────────────────── */

void batch_compute_features(
    const double* home_elos, const double* away_elos,
    const double* home_forms, const double* away_forms,
    const double* h2h_stats_in, const double* league_stats,
    double home_advantage, int n_matches,
    double* out_features, int n_features)
{
    const int FD=10, H2HD=6, LD=4;
    for (int i=0; i<n_matches; ++i) {
        double* feat = out_features + i * n_features;
        int f = 0;
        double eh=home_elos[i], ea=away_elos[i];
        double ph,pd,pa;
        compute_elo_probabilities(eh,ea,home_advantage,&ph,&pd,&pa);
        feat[f++]=eh; feat[f++]=ea; feat[f++]=eh-ea;
        feat[f++]=ph; feat[f++]=pd; feat[f++]=pa;
        const double* hf = home_forms + i*FD;
        for (int j=0;j<FD;++j) feat[f++]=hf[j];
        const double* af = away_forms + i*FD;
        for (int j=0;j<FD;++j) feat[f++]=af[j];
        const double* h2 = h2h_stats_in + i*H2HD;
        for (int j=0;j<H2HD;++j) feat[f++]=h2[j];
        const double* ls = league_stats + i*LD;
        double p25,pbt;
        compute_goal_probability(hf[3],af[4],af[3],hf[4],ls[0],ls[3],&p25,&pbt);
        feat[f++]=p25; feat[f++]=pbt;
        feat[f++]=hf[0]-af[0]; feat[f++]=hf[3]-af[3];
        feat[f++]=hf[6]-af[6]; feat[f++]=hf[7]-af[7];
    }
}

/* ══════════════════════════════════════════════════════════════════════════
 *  NEW functions (v2)
 * ══════════════════════════════════════════════════════════════════════════ */

/* ── 7. compute_attack_defense_elo (NEW) ─────────────────────────────── */

/*
 * Separate attack and defence Elo ratings.
 *
 * Model:
 *   expected_attack_score  = 1 / (1 + 10^((opp_def - team_att + home_bonus) / K_GOALS))
 *   actual_attack_score    = clamp(goals_scored / GOAL_NORM, 0, 1)
 *   update: att_elo += k_factor * (actual - expected)
 *
 * Defence Elo: same but from the conceding perspective (fewer goals = better).
 */
void compute_attack_defense_elo(
    const char** home_teams, const char** away_teams,
    const int* home_goals,   const int* away_goals,
    int n_matches, double k_factor, double home_advantage,
    double* out_home_att, double* out_home_def,
    double* out_away_att, double* out_away_def)
{
    constexpr double GOAL_NORM   = 2.5;   // expected goals ≈ GOAL_NORM → score 1
    constexpr double ATT_SCALE   = 300.0; // narrower scale for goals-based Elo
    constexpr double HOME_ATT_BONUS = 30.0;

    std::unordered_map<std::string, double> att, def;

    for (int i = 0; i < n_matches; ++i) {
        std::string home(home_teams[i]), away(away_teams[i]);
        if (!att.count(home)) { att[home]=DEFAULT_ELO; def[home]=DEFAULT_ELO; }
        if (!att.count(away)) { att[away]=DEFAULT_ELO; def[away]=DEFAULT_ELO; }

        /* Expected goals score: probability that home team "beats" away defense */
        double e_home_att = 1.0 / (1.0 + std::pow(10.0,
            (def[away] - att[home] - HOME_ATT_BONUS) / ATT_SCALE));
        double e_away_att = 1.0 / (1.0 + std::pow(10.0,
            (def[home] - att[away] + HOME_ATT_BONUS) / ATT_SCALE));

        /* Actual normalised goals */
        double a_home_att = std::min(home_goals[i] / GOAL_NORM, 1.0);
        double a_away_att = std::min(away_goals[i] / GOAL_NORM, 1.0);

        /* Defence actual: clean = 1, lots conceded = 0 */
        double a_home_def = std::max(0.0, 1.0 - away_goals[i] / GOAL_NORM);
        double a_away_def = std::max(0.0, 1.0 - home_goals[i] / GOAL_NORM);

        /* Goal-difference multiplier for scaling the update */
        int gd = std::abs(home_goals[i] - away_goals[i]);
        double gm = gd_multiplier(gd);
        double ek = k_factor * gm;

        att[home] += ek * (a_home_att - e_home_att);
        att[away] += ek * (a_away_att - e_away_att);
        def[home] += ek * (a_home_def - (1.0 - e_away_att));
        def[away] += ek * (a_away_def - (1.0 - e_home_att));

        out_home_att[i] = att[home];
        out_home_def[i] = def[home];
        out_away_att[i] = att[away];
        out_away_def[i] = def[away];
    }
}

/* ── 8. compute_poisson_score_matrix (NEW) ───────────────────────────── */

void compute_poisson_score_matrix(
    double lambda_home, double lambda_away,
    double rho, double* out)
{
    lambda_home = std::min(std::max(lambda_home, 0.01), POISSON_LAMBDA_CAP);
    lambda_away = std::min(std::max(lambda_away, 0.01), POISSON_LAMBDA_CAP);

    /* Build NxN probability matrix with DC correction */
    double mat[SCORE_MAX][SCORE_MAX];
    double total_w = 0.0;
    for (int h = 0; h < SCORE_MAX; ++h) {
        for (int a = 0; a < SCORE_MAX; ++a) {
            double p = poisson_pmf(lambda_home, h) * poisson_pmf(lambda_away, a);
            p *= dc_correction(h, a, lambda_home, lambda_away, rho);
            p  = std::max(p, 0.0);
            mat[h][a] = p;
            total_w  += p;
        }
    }
    /* Normalise so probabilities sum to 1 */
    if (total_w > 1e-12)
        for (int h=0;h<SCORE_MAX;++h)
            for (int a=0;a<SCORE_MAX;++a)
                mat[h][a] /= total_w;

    /* Aggregate into outputs */
    double p_over15=0, p_over25=0, p_over35=0;
    double p_btts=0, p_hcs=0, p_acs=0;

    for (int h=0;h<SCORE_MAX;++h) {
        for (int a=0;a<SCORE_MAX;++a) {
            double p = mat[h][a];
            int tot = h + a;
            if (tot >= 2) p_over15 += p;
            if (tot >= 3) p_over25 += p;
            if (tot >= 4) p_over35 += p;
            if (h >= 1 && a >= 1) p_btts += p;
            if (a == 0)           p_hcs  += p;
            if (h == 0)           p_acs  += p;
        }
    }

    out[0] = p_over15;
    out[1] = p_over25;
    out[2] = p_over35;
    out[3] = p_btts;
    out[4] = p_hcs;
    out[5] = p_acs;
    out[6] = mat[0][0];                         /* p_0_0 */
    out[7] = (1<SCORE_MAX) ? mat[1][0] : 0.0;  /* p_1_0 */
    out[8] = (1<SCORE_MAX) ? mat[0][1] : 0.0;  /* p_0_1 */
    out[9] = (1<SCORE_MAX) ? mat[1][1] : 0.0;  /* p_1_1 */
}

/* ── 9. compute_consecutive_runs (NEW) ───────────────────────────────── */

void compute_consecutive_runs(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches, double* out)
{
    int unbeaten = 0, winless = 0;

    /* Walk backwards from most recent match */
    for (int i = n_matches - 1; i >= 0; --i) {
        int s = was_home[i] ? home_goals[i] : away_goals[i];
        int c = was_home[i] ? away_goals[i] : home_goals[i];
        bool won  = (s > c);
        bool drew = (s == c);
        bool lost = (s < c);

        if (i == n_matches - 1) {
            /* Seed both runs from the latest match */
            unbeaten = (won || drew) ? 1 : 0;
            winless  = (lost || drew) ? 1 : 0;
        } else {
            if (won || drew) unbeaten++; else break;
        }
        (void)drew; (void)lost; /* suppress warnings */
    }
    for (int i = n_matches - 1; i >= 0; --i) {
        int s = was_home[i] ? home_goals[i] : away_goals[i];
        int c = was_home[i] ? away_goals[i] : home_goals[i];
        bool lost = (s < c), drew = (s == c);
        if (i == n_matches - 1) { winless = (lost || drew) ? 1 : 0; continue; }
        if (lost || drew) winless++; else break;
    }

    out[0] = std::min(unbeaten, RUN_CAP) / static_cast<double>(RUN_CAP);
    out[1] = std::min(winless,  RUN_CAP) / static_cast<double>(RUN_CAP);
}

/* ── 10. compute_venue_split_form (NEW) ─────────────────────────────── */

void compute_venue_split_form(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches,
    int is_home_venue, double* out)
{
    double wins=0, pts=0, gs=0, gc=0, n=0;
    for (int i=0; i<n_matches; ++i) {
        if (was_home[i] != is_home_venue) continue;
        int s = is_home_venue ? home_goals[i] : away_goals[i];
        int c = is_home_venue ? away_goals[i] : home_goals[i];
        if (s>c) { wins+=1; pts+=3; }
        else if (s==c) { pts+=1; }
        gs+=s; gc+=c; n+=1;
    }
    if (n < 1e-9) { for(int i=0;i<4;++i) out[i]=0; return; }
    out[0] = wins / n;
    out[1] = pts  / n;
    out[2] = gs   / n;
    out[3] = gc   / n;
}

/* ── 11. compute_goals_variance (NEW) ───────────────────────────────── */

void compute_goals_variance(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches, double* out)
{
    int n = std::min(n_matches, FORM_WINDOW * 2);
    if (n == 0) { out[0]=0; out[1]=0; return; }

    std::vector<double> scored(n), conceded(n), weights(n);
    for (int i=0; i<n; ++i) {
        int idx = n_matches - n + i;      /* oldest to newest */
        int s = was_home[idx] ? home_goals[idx] : away_goals[idx];
        int c = was_home[idx] ? away_goals[idx] : home_goals[idx];
        scored[i]   = static_cast<double>(s);
        conceded[i] = static_cast<double>(c);
        weights[i]  = std::pow(FORM_DECAY, n - 1 - i);
    }

    double ms, vs, mc, vc;
    weighted_moments(scored.data(),   weights.data(), n, ms, vs);
    weighted_moments(conceded.data(), weights.data(), n, mc, vc);

    out[0] = vs;
    out[1] = vc;
}

/* ── 12. compute_form_trend (NEW v3) ─────────────────────────────────── */

void compute_form_trend(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches, double* out)
{
    if (n_matches < 6) { *out = 0.0; return; }

    auto get_ppg = [&](int start, int end) {
        if (start >= end) return 0.0;
        double pts = 0;
        for (int i = start; i < end; ++i) {
            int s = was_home[i] ? home_goals[i] : away_goals[i];
            int c = was_home[i] ? away_goals[i] : home_goals[i];
            if (s > c) pts += 3.0; else if (s == c) pts += 1.0;
        }
        return pts / (end - start);
    };

    int n5 = std::min(5, n_matches);
    double ppg_recent = get_ppg(n_matches - n5, n_matches);
    double ppg_prev = 0;
    if (n_matches >= 10) {
        ppg_prev = get_ppg(n_matches - 10, n_matches - 5);
    } else {
        ppg_prev = get_ppg(0, n_matches - n5);
    }
    *out = ppg_recent - ppg_prev;
}

/* ── 13. compute_scoring_consistency (NEW v3) ───────────────────────── */

void compute_scoring_consistency(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches, double* out)
{
    int n = std::min(n_matches, 15);
    if (n < 3) { *out = 0.5; return; }

    double mean = 0, var = 0;
    std::vector<double> goals(n);
    std::vector<double> weights(n, 1.0);
    for (int i = 0; i < n; ++i) {
        int idx = n_matches - n + i;
        goals[i] = was_home[idx] ? home_goals[idx] : away_goals[idx];
    }
    weighted_moments(goals.data(), weights.data(), n, mean, var);
    *out = 1.0 / (1.0 + std::sqrt(var));
}

/* ── 14. compute_h2h_extended (NEW v3) ──────────────────────────────── */

void compute_h2h_extended(
    const int* home_goals, const int* away_goals,
    const int* was_home_team_first, int n_matches, double* out)
{
    if (n_matches == 0) { out[0] = 2.6; out[1] = 1.25; return; }

    double total_goals = 0;
    double home_wins = 0, away_wins = 0, draws = 0;

    for (int i = 0; i < n_matches; ++i) {
        int g1 = was_home_team_first[i] ? home_goals[i] : away_goals[i];
        int g2 = was_home_team_first[i] ? away_goals[i] : home_goals[i];
        total_goals += (g1 + g2);
        if (g1 > g2) home_wins++;
        else if (g1 < g2) away_wins++;
        else draws++;
    }
    out[0] = total_goals / n_matches;
    out[1] = (home_wins + 0.5) / (away_wins + 0.5);
}

/* ── 15. compute_last_n_goals (NEW v3) ──────────────────────────────── */

void compute_last_n_goals(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches, int n, int is_scored, double* out)
{
    int count = std::min(n, n_matches);
    if (count == 0) { *out = 1.2; return; }

    double sum = 0;
    for (int i = 0; i < count; ++i) {
        int idx = n_matches - 1 - i;
        int s = was_home[idx] ? home_goals[idx] : away_goals[idx];
        int c = was_home[idx] ? away_goals[idx] : home_goals[idx];
        sum += (is_scored ? s : c);
    }
    *out = sum / count;
}

/* ── 16. compute_draw_rate (NEW v3) ─────────────────────────────────── */

void compute_draw_rate(
    const int* home_goals, const int* away_goals, int n_matches, double* out)
{
    if (n_matches < 3) { *out = 0.24; return; }
    int draws = 0;
    for (int i = 0; i < n_matches; ++i) {
        if (home_goals[i] == away_goals[i]) draws++;
    }
    *out = static_cast<double>(draws) / n_matches;
}

/* ── 17. compute_temporal_features (NEW v3) ─────────────────────────── */

void compute_temporal_features(
    double current_ts, const double* history_ts, int n_matches, double* out)
{
    /* days since last match */
    double days = 7.0; // default
    if (n_matches > 0) {
        double last_ts = history_ts[n_matches - 1];
        if (current_ts > last_ts) {
            days = (current_ts - last_ts) / 86400.0;
        }
    }
    out[0] = std::min(days, 60.0) / 60.0;

    /* season stage */
    /* Simple approximation of season stage from timestamp.
       Assumes standard European season Aug-May. */
    time_t rawtime = static_cast<time_t>(current_ts);
    struct tm timeinfo;
    gmtime_r(&rawtime, &timeinfo);
    int month = timeinfo.tm_mon + 1; // 1-12

    int pos = 0;
    if (month == 8) pos = 0;
    else if (month == 9) pos = 1;
    else if (month == 10) pos = 2;
    else if (month == 11) pos = 3;
    else if (month == 12) pos = 4;
    else if (month == 1) pos = 5;
    else if (month == 2) pos = 6;
    else if (month == 3) pos = 7;
    else if (month == 4) pos = 8;
    else if (month == 5) pos = 9;
    else pos = -1;

    out[1] = (pos == -1) ? 0.5 : (pos / 9.0);
}

/* ── 18. compute_streak (NEW v3) ────────────────────────────────────── */

void compute_streak(
    const int* home_goals, const int* away_goals,
    const int* was_home, int n_matches, double* out)
{
    int streak = 0;
    int sign = 0;
    int count = std::min(n_matches, 12);

    for (int i = 0; i < count; ++i) {
        int idx = n_matches - 1 - i;
        int s = was_home[idx] ? home_goals[idx] : away_goals[idx];
        int c = was_home[idx] ? away_goals[idx] : home_goals[idx];

        int outcome = (s > c) ? 1 : ((s < c) ? -1 : 0);
        if (streak == 0) {
            streak = outcome;
            sign = outcome;
        } else if (outcome == sign) {
            streak += sign;
        } else {
            break;
        }
    }
    *out = std::max(-1.0, std::min(1.0, streak / 5.0));
}

/* ── 19. compute_all_features_v3 (NEW v3) ───────────────────────────── */

void compute_all_features_v3(
    const double* pre_elos,
    const double* pre_att_def,
    const int*    match_goals,
    const double* odds,
    double        current_ts,
    const double* league_stats,
    int           n_h, const int* gh_h, const int* ga_h, const int* wh_h, const double* ts_h,
    int           n_a, const int* gh_a, const int* ga_a, const int* wh_a, const double* ts_a,
    int           n_h2h, const int* gh_h2h, const int* ga_h2h, const int* wh_h2h,
    double        home_advantage,
    double*       out)
{
    /* 1. Elo (6) 0-5 */
    std::memcpy(out, pre_elos, 6 * sizeof(double));

    /* 2. Attack / Defence Elo (4) 6-9 */
    std::memcpy(out + 6, pre_att_def, 4 * sizeof(double));

    /* 3. Home overall form (10) 10-19 */
    compute_form_vector(gh_h, ga_h, wh_h, n_h, nullptr, out + 10);

    /* 4. Away overall form (10) 20-29 */
    compute_form_vector(gh_a, ga_a, wh_a, n_a, nullptr, out + 20);

    /* 5. Home venue-split form (4) 30-33 */
    compute_venue_split_form(gh_h, ga_h, wh_h, n_h, 1, out + 30);

    /* 6. Away venue-split form (4) 34-37 */
    compute_venue_split_form(gh_a, ga_a, wh_a, n_a, 0, out + 34);

    /* 7. H2H (6) 38-43 */
    compute_h2h_stats(gh_h2h, ga_h2h, wh_h2h, n_h2h, out + 38);

    /* 8. Dixon-Coles goal probs (10) 44-53 */
    double lh = out[13] / std::max(league_stats[0]/2, 0.1) * out[24] / std::max(league_stats[0]/2, 0.1) * league_stats[3] * (league_stats[0]/2);
    // Wait, the logic in features.py is more complex.
    // Let's use the parameters directly as in features.py
    double lavg = league_stats[0];
    double half = std::max(lavg / 2.0, 0.1);
    double ha_str = out[13] / half;
    double hd_str = std::max(half - out[14], 0.1) / half;
    double aa_str = out[23] / half;
    double ad_str = std::max(half - out[24], 0.1) / half;

    double l_h = std::max(ha_str * ad_str * league_stats[3] * half, 0.1);
    double l_a = std::max(aa_str * hd_str * half, 0.1);
    l_h = std::min(l_h, 6.0); l_a = std::min(l_a, 6.0);

    compute_poisson_score_matrix(l_h, l_a, -0.13, out + 44);
    out[50] = l_h;
    out[51] = l_a;
    out[52] = l_h / std::max(l_a, 0.01);
    out[53] = l_h + l_a;

    /* 9. Dixon-Coles exact score probs (4) 54-57 are already in out + 50...53 from above?
       Wait, compute_poisson_score_matrix fills 10 doubles.
       [0..5] are aggregates, [6..9] are 0-0, 1-0, 0-1, 1-1.
       In features.py, 44-53 areaggregates + lambda stats. 54-57 are exact scores.
    */
    // Re-filling to match features.py exactly:
    double dc[10];
    compute_poisson_score_matrix(l_h, l_a, -0.13, dc);
    out[44] = dc[0]; out[45] = dc[1]; out[46] = dc[2]; out[47] = dc[3]; out[48] = dc[4]; out[49] = dc[5];
    out[50] = l_h; out[51] = l_a; out[52] = l_h / std::max(l_a, 0.01); out[53] = l_h + l_a;
    out[54] = dc[6]; out[55] = dc[7]; out[56] = dc[8]; out[57] = dc[9];

    /* 10. Differentials (4) 58-61 */
    out[58] = out[10] - out[20];
    out[59] = out[13] - out[23];
    out[60] = out[16] - out[26];
    out[61] = out[17] - out[27];

    /* 11. Market (4) 62-65 */
    double oh = odds[0], od = odds[1], oa = odds[2];
    bool real_odds = (oh > 1 && od > 1 && oa > 1);
    double ih = real_odds ? (1.0/oh) : out[3];
    double id = real_odds ? (1.0/od) : out[4];
    double ia = real_odds ? (1.0/oa) : out[5];
    double ovr = ih + id + ia;
    double s = (ovr > 1e-9) ? ovr : 1.0;
    out[62] = ih / s; out[63] = id / s; out[64] = ia / s; out[65] = ovr;

    /* 12. Strengths (4) 66-69 */
    out[66] = ha_str; out[67] = aa_str; out[68] = hd_str; out[69] = ad_str;

    /* 13. Consecutive runs (4) 70-73 */
    compute_consecutive_runs(gh_h, ga_h, wh_h, n_h, out + 70);
    compute_consecutive_runs(gh_a, ga_a, wh_a, n_a, out + 72);

    /* 14. Streaks (2) 74-75 */
    compute_streak(gh_h, ga_h, wh_h, n_h, out + 74);
    compute_streak(gh_a, ga_a, wh_a, n_a, out + 75);

    /* 15. Trends (2) 76-77 */
    compute_form_trend(gh_h, ga_h, wh_h, n_h, out + 76);
    compute_form_trend(gh_a, ga_a, wh_a, n_a, out + 77);

    /* 16. Consistency (2) 78-79 */
    compute_scoring_consistency(gh_h, ga_h, wh_h, n_h, out + 78);
    compute_scoring_consistency(gh_a, ga_a, wh_a, n_a, out + 79);

    /* 17. H2H extended (2) 80-81 */
    compute_h2h_extended(gh_h2h, ga_h2h, wh_h2h, n_h2h, out + 80);

    /* 18. League context (3) 82-84 */
    out[82] = lavg; out[83] = league_stats[4]; out[84] = league_stats[5];

    /* 19. Venue PPG diff (1) 85 */
    out[85] = out[31] - out[35];

    /* 20. Attack/defence vs league (4) 86-89 */
    double latt_half = std::max(league_stats[1] * half, 0.1);
    out[86] = out[13] / latt_half - 1.0;
    out[87] = out[23] / latt_half - 1.0;
    out[88] = 1.0 - out[14] / latt_half;
    out[89] = 1.0 - out[24] / latt_half;

    /* 21. Goals variance (4) 90-93 */
    compute_goals_variance(gh_h, ga_h, wh_h, n_h, out + 90);
    compute_goals_variance(gh_a, ga_a, wh_a, n_a, out + 92);

    /* 22. Recent 3-match goals (4) 94-97 */
    compute_last_n_goals(gh_h, ga_h, wh_h, n_h, 3, 1, out + 94);
    compute_last_n_goals(gh_a, ga_a, wh_a, n_a, 3, 1, out + 95);
    compute_last_n_goals(gh_h, ga_h, wh_h, n_h, 3, 0, out + 96);
    compute_last_n_goals(gh_a, ga_a, wh_a, n_a, 3, 0, out + 97);

    /* 23. Temporal / draw context (6) 98-103 */
    compute_temporal_features(current_ts, ts_h, n_h, out + 98);
    // Days since last for away is out[99]
    double tmp_away[2];
    compute_temporal_features(current_ts, ts_a, n_a, tmp_away);
    out[99] = tmp_away[0];
    // out[100] is season_stage (already in out[99] from home call)
    out[100] = out[99]; // Wait, temporal call for home sets out[99] as season_stage.
    // Let's re-correct:
    // temporal for home: out[98] = days_h, out[99] = season_stage
    // temporal for away: tmp[0] = days_a, tmp[1] = season_stage
    out[99] = tmp_away[0];
    out[100] = tmp_away[1]; // season_stage

    compute_draw_rate(gh_h, ga_h, n_h, out + 101);
    compute_draw_rate(gh_a, ga_a, n_a, out + 102);
    out[103] = real_odds ? 1.0 : 0.0;
}

} // extern "C"
} // namespace statwise
