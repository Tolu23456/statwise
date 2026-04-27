#ifndef STATWISE_FEATURES_H
#define STATWISE_FEATURES_H

namespace statwise {

extern "C" {

void compute_elo_ratings(const char** home_teams, const char** away_teams, const int* home_goals, const int* away_goals, int n_matches, double k_factor, double home_advantage, double* out_home_elos, double* out_away_elos);
void compute_form_vector(const int* home_goals, const int* away_goals, const int* was_home, int n_matches, const double* recency_weights, double* out_form);
void compute_h2h_stats(const int* home_goals, const int* away_goals, const int* was_first, int n_matches, double* out_h2h);
void compute_goal_probability(double attack_home, double defense_away, double attack_away, double defense_home, double league_avg_goals, double home_adv, double* out_over25, double* out_btts);
void compute_elo_probabilities(double elo_home, double elo_away, double home_advantage, double* prob_home, double* prob_draw, double* prob_away);
void batch_compute_features(const double* home_elos, const double* away_elos, const double* home_forms, const double* away_forms, const double* h2h_stats_in, const double* league_stats, double home_advantage, int n_matches, double* out_features, int n_features);
void compute_attack_defense_elo(const char** home_teams, const char** away_teams, const int* home_goals, const int* away_goals, int n_matches, double k_factor, double home_advantage, double* out_home_att, double* out_home_def, double* out_away_att, double* out_away_def);
void compute_poisson_score_matrix(double lh, double la, double rho, double* out);
void compute_consecutive_runs(const int* hg, const int* ag, const int* wh, int n, double* out);
void compute_venue_split_form(const int* hg, const int* ag, const int* wh, int n_m, int is_h, double* out);
void compute_goals_variance(const int* hg, const int* ag, const int* wh, int n_m, double* out);
void compute_form_trend(const int* hg, const int* ag, const int* wh, int n, double* out);
void compute_scoring_consistency(const int* hg, const int* ag, const int* wh, int n_m, double* out);
void compute_h2h_extended(const int* hg, const int* ag, const int* wf, int n, double* out);
void compute_last_n_goals(const int* hg, const int* ag, const int* wh, int n_m, int n, int is_s, double* out);
void compute_draw_rate(const int* hg, const int* ag, int n, double* out);
void compute_temporal_features(double cts, const double* hts, int n, double* out);
void compute_streak(const int* hg, const int* ag, const int* wh, int n_m, double* out);

void compute_all_features_v3(const double* pe, const double* pad, const int* mg, const double* od, double cts, const double* ls, int nh, const int* ghh, const int* gah, const int* whh, const double* tsh, int na, const int* gha, const int* gaa, const int* wha, const double* tsa, int n2, const int* gh2, const int* ga2, const int* wh2, double ha, double* out);
void compute_all_features_bulk_v4(const int* ti, int nt, const int* agh, const int* aga, const double* ats, const int* ahi, const int* aai, const double* ape, const double* apad, const double* ao, const double* als, const int* tmi, const int* tmp, const int* tmc, const double* ahe, const double* aae, int lb, double ha, double* out);

} // extern "C"
} // namespace statwise

#endif
