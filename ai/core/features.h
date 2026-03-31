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
        const int* home_goals,
        const int* away_goals,
        const int* was_home,
        int        n_matches,
        const double* recency_weights,
        double*    out_form
    );

    void compute_h2h_stats(
        const int* home_goals,
        const int* away_goals,
        const int* was_home_team_first,
        int        n_matches,
        double*    out_h2h
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

    void compute_elo_probabilities(
        double elo_home,
        double elo_away,
        double home_advantage,
        double* prob_home,
        double* prob_draw,
        double* prob_away
    );
}

} // namespace statwise
