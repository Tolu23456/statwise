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

} // extern "C"
} // namespace statwise
