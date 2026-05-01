void compute_all_features_v6(const double* pe, const double* pad, const int* mg, const double* od, double cts, const double* ls, int nh, const int* ghh, const int* gah, const int* whh, const double* tsh, const double* ehh, int na, const int* gha, const int* gaa, const int* wha, const double* tsa, const double* eha, int n2, const int* gh2, const int* ga2, const int* wh2, double ha, const double* squad_v, double* out) {
    // 1. Compute Base v4 features (125 total)
    compute_all_features_v4(pe, pad, mg, od, cts, ls, nh, ghh, gah, whh, tsh, ehh, na, gha, gaa, wha, tsa, eha, n2, gh2, ga2, wh2, ha, out);

    // 2. Add Titan v6 Player/Squad features (starting from index 125)
    // squad_v: [home_val, away_v, home_age, away_age, home_rating, away_rating]
    double hv = squad_v[0], av = squad_v[1];
    out[125] = hv; out[126] = av;
    out[127] = (hv + 1.0) / (av + 1.0); // Value Ratio
    out[128] = squad_v[2]; out[129] = squad_v[3]; // Avg Age
    out[130] = squad_v[4]; out[131] = squad_v[5]; // Lineup Rating
    out[132] = squad_v[4] - squad_v[5]; // Rating Diff

    // Padding/Zero for future Titan features (total 160 features)
    for(int j=133; j<160; ++j) out[j] = 0.0;
}
