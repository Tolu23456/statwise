#pragma once
#include <vector>
#include <string>
#include <unordered_map>
#include <array>

namespace statwise {

struct MatchRecord {
    int  home_goals;
    int  away_goals;
    bool neutral_venue;
};

struct TeamElo {
    std::string name;
    double rating;
    int    games_played;
};

struct FormVector {
    double win_rate;
    double draw_rate;
    double loss_rate;
    double goals_scored_avg;
    double goals_conceded_avg;
    double goal_diff_avg;
    double points_per_game;
    double weighted_momentum;
    double clean_sheets_rate;
    double scoring_games_rate;
};

struct FeatureVector {
    double elo_home;
    double elo_away;
    double elo_diff;
    double elo_win_prob_home;
    double elo_win_prob_away;
    double elo_draw_prob;

    double home_form_win_rate;
    double home_form_goals_scored;
    double home_form_goals_conceded;
    double home_form_momentum;
    double home_form_ppg;

    double away_form_win_rate;
    double away_form_goals_scored;
    double away_form_goals_conceded;
    double away_form_momentum;
    double away_form_ppg;

    double h2h_home_win_rate;
    double h2h_draw_rate;
    double h2h_away_win_rate;
    double h2h_home_goals_avg;
    double h2h_away_goals_avg;
    double h2h_total_matches;

    double home_advantage_weight;
    double league_attack_home;
    double league_attack_away;
    double league_defense_home;
    double league_defense_away;

    double over25_probability;
    double btts_probability;
};

extern "C" {

    /* ── Original functions ─────────────────────────────────────────────── */

    void compute_elo_ratings(
        const char** home_teams,
        const char** away_teams,
        const int*   home_goals,
        const int*   away_goals,
        int          n_matches,
        double       k_factor,
        double       home_advantage,
        double*      out_home_elos,
        double*      out_away_elos
    );

    void compute_form_vector(
        const int*    home_goals,
        const int*    away_goals,
        const int*    was_home,
        int           n_matches,
        const double* recency_weights,
        double*       out_form        /* 10 doubles */
    );

    void compute_h2h_stats(
        const int* home_goals,
        const int* away_goals,
        const int* was_home_team_first,
        int        n_matches,
        double*    out_h2h            /* 6 doubles */
    );

    void compute_goal_probability(
        double attack_home,
        double defense_away,
        double attack_away,
        double defense_home,
        double league_avg_goals,
        double home_adv,
        double* out_prob_over25,
        double* out_prob_btts
    );

    void compute_elo_probabilities(
        double elo_home,
        double elo_away,
        double home_advantage,
        double* prob_home,
        double* prob_draw,
        double* prob_away
    );

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
        int           n_features
    );

    /* ── NEW: Attack / Defence Elo ──────────────────────────────────────── */

    /**
     * Separate attack and defence Elo ratings for every team.
     * Attack Elo rises when a team scores more than "expected" given the
     * opponent's defence Elo, and vice-versa.  Both start at 1500.
     * out_*[i] = ratings *after* match i for home / away team.
     */
    void compute_attack_defense_elo(
        const char** home_teams,
        const char** away_teams,
        const int*   home_goals,
        const int*   away_goals,
        int          n_matches,
        double       k_factor,
        double       home_advantage,
        double*      out_home_att,
        double*      out_home_def,
        double*      out_away_att,
        double*      out_away_def
    );

    /* ── NEW: Dixon-Coles Poisson score matrix ──────────────────────────── */

    /**
     * Full Poisson score matrix up to 6×6 with Dixon-Coles correction
     * for low-scoring cells (rho = -0.13 is standard).
     *
     * Returns 10 scalars in out[]:
     *   [0] p_over15    [1] p_over25    [2] p_over35
     *   [3] p_btts      [4] p_home_cs   [5] p_away_cs
     *   [6] p_0_0       [7] p_1_0       [8] p_0_1     [9] p_1_1
     */
    void compute_poisson_score_matrix(
        double  lambda_home,
        double  lambda_away,
        double  rho,
        double* out            /* 10 doubles */
    );

    /* ── NEW: Consecutive run lengths ───────────────────────────────────── */

    /**
     * Current unbeaten / winless run for a team (normalised to [-1,1]).
     * out[0] = unbeaten_run_norm  (positive = currently unbeaten)
     * out[1] = winless_run_norm   (positive = currently without a win)
     */
    void compute_consecutive_runs(
        const int* home_goals,
        const int* away_goals,
        const int* was_home,
        int        n_matches,
        double*    out            /* 2 doubles */
    );

    /* ── NEW: Venue-split form vector ───────────────────────────────────── */

    /**
     * 4-element form vector for home-only (is_home_venue=1) or
     * away-only (is_home_venue=0) matches.
     * out: [win_rate, ppg, avg_goals_scored, avg_goals_conceded]
     */
    void compute_venue_split_form(
        const int* home_goals,
        const int* away_goals,
        const int* was_home,
        int        n_matches,
        int        is_home_venue,
        double*    out            /* 4 doubles */
    );

    /* ── NEW: Goals variance ────────────────────────────────────────────── */

    /**
     * Recency-weighted variance in goals scored and conceded.
     * out[0] = scored_variance
     * out[1] = conceded_variance
     */
    void compute_goals_variance(
        const int* home_goals,
        const int* away_goals,
        const int* was_home,
        int        n_matches,
        double*    out            /* 2 doubles */
    );
}

} // namespace statwise
