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
#include <omp.h>

namespace statwise {

static constexpr double DEFAULT_ELO        = 1500.0;
static constexpr double ELO_SCALE          = 400.0;
static constexpr double FORM_DECAY         = 0.85;
static constexpr int    FORM_WINDOW        = 15;
static constexpr double POISSON_LAMBDA_CAP = 6.0;
static constexpr int    SCORE_MAX          = 7;
static constexpr double DIXON_COLES_RHO    = -0.13;
static constexpr int    RUN_CAP            = 15;

static double expected_score(double ra, double rb) {
    return 1.0 / (1.0 + std::pow(10.0, (rb - ra) / ELO_SCALE));
}

static double gd_multiplier(int gd) {
    if (gd <= 1) return 1.0;
    if (gd == 2) return 1.5;
    if (gd == 3) return 1.75;
    return std::min(1.75 + 0.15 * (gd - 3), 3.0);
}

static double poisson_pmf(double lambda, int k) {
    if (lambda <= 0.0) return (k == 0) ? 1.0 : 0.0;
    if (k < 0) return 0.0;
    double lp = k * std::log(lambda) - lambda;
    for (int i = 1; i <= k; ++i) lp -= std::log(static_cast<double>(i));
    return std::exp(lp);
}

static double dc_correction(int h, int a, double lh, double la, double rho) {
    if (h == 0 && a == 0) return 1.0 - lh * la * rho;
    if (h == 0 && a == 1) return 1.0 + lh * rho;
    if (h == 1 && a == 0) return 1.0 + la * rho;
    if (h == 1 && a == 1) return 1.0 - rho;
    return 1.0;
}

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

extern "C" {

void compute_elo_ratings(const char** home_teams, const char** away_teams, const int* home_goals, const int* away_goals, int n_matches, double k_factor, double home_advantage, double* out_home_elos, double* out_away_elos) {
    std::unordered_map<std::string, double> ratings;
    for (int i = 0; i < n_matches; ++i) {
        std::string home(home_teams[i]), away(away_teams[i]);
        if (!ratings.count(home)) ratings[home] = DEFAULT_ELO;
        if (!ratings.count(away)) ratings[away] = DEFAULT_ELO;
        double rh = ratings[home] + home_advantage, ra = ratings[away];
        double eh = expected_score(rh, ra), sh = (home_goals[i] > away_goals[i]) ? 1.0 : (home_goals[i] < away_goals[i] ? 0.0 : 0.5);
        double ek = k_factor * gd_multiplier(std::abs(home_goals[i] - away_goals[i]));
        ratings[home] += ek * (sh - eh); ratings[away] += ek * ((1-sh) - (1-eh));
        out_home_elos[i] = ratings[home]; out_away_elos[i] = ratings[away];
    }
}

void compute_form_vector(const int* home_goals, const int* away_goals, const int* was_home, int n_matches, const double* recency_weights, double* out_form) {
    double ws = 0, wins = 0, draws = 0, losses = 0, gs = 0, gc = 0, mom = 0, cs = 0, sg = 0;
    int n = std::min(n_matches, FORM_WINDOW);
    for (int i = 0; i < n; ++i) {
        int idx = n_matches - n + i;
        double w = recency_weights ? recency_weights[i] : std::pow(FORM_DECAY, n - 1 - i);
        ws += w; int s = was_home[idx] ? home_goals[idx] : away_goals[idx], c = was_home[idx] ? away_goals[idx] : home_goals[idx];
        gs += w * s; gc += w * c; if (s > c) { wins += w; mom += w * 3.0; } else if (s == c) { draws += w; mom += w * 1.0; } else losses += w;
        if (c == 0) cs += w; if (s > 0) sg += w;
    }
    if (ws < 1e-9) { for (int j=0;j<10;++j) out_form[j]=0; return; }
    out_form[0]=wins/ws; out_form[1]=draws/ws; out_form[2]=losses/ws; out_form[3]=gs/ws; out_form[4]=gc/ws; out_form[5]=(gs-gc)/ws; out_form[6]=mom/ws; out_form[7]=(wins*3+draws)/(ws*3); out_form[8]=cs/ws; out_form[9]=sg/ws;
}

void compute_h2h_stats(const int* home_goals, const int* away_goals, const int* was_first, int n_matches, double* out_h2h) {
    if (n_matches == 0) { for (int i=0;i<6;++i) out_h2h[i]=0; return; }
    double wins=0,draws=0,losses=0,gf=0,gs2=0;
    for (int i=0; i<n_matches;++i) {
        int g1 = was_first[i] ? home_goals[i] : away_goals[i], g2 = was_first[i] ? away_goals[i] : home_goals[i];
        if (g1>g2) wins++; else if(g1==g2) draws++; else losses++;
        gf+=g1; gs2+=g2;
    }
    double n=(double)n_matches;
    out_h2h[0]=wins/n; out_h2h[1]=draws/n; out_h2h[2]=losses/n; out_h2h[3]=gf/n; out_h2h[4]=gs2/n; out_h2h[5]=n;
}

void compute_goal_probability(double attack_home, double defense_away, double attack_away, double defense_home, double league_avg_goals, double home_adv, double* out_over25, double* out_btts) {
    double lh = std::min(attack_home * defense_away * home_adv, POISSON_LAMBDA_CAP), la = std::min(attack_away * defense_home, POISSON_LAMBDA_CAP);
    if (lh <= 0) lh = league_avg_goals * home_adv / 2.0; if (la <= 0) la = league_avg_goals / 2.0;
    double pu25 = 0; for (int h=0; h<=2; ++h) for (int a=0; a<=2-h; ++a) pu25 += poisson_pmf(lh,h) * poisson_pmf(la,a);
    *out_over25 = 1.0 - pu25; *out_btts = (1.0 - poisson_pmf(lh,0)) * (1.0 - poisson_pmf(la,0));
}

void compute_elo_probabilities(double elo_home, double elo_away, double home_advantage, double* prob_home, double* prob_draw, double* prob_away) {
    double adj = elo_home + home_advantage, eh = expected_score(adj, elo_away), diff = std::abs(adj - elo_away);
    double dpb = std::max(0.05, std::min(0.35, 0.28 * std::exp(-0.0015 * diff))), rem = 1.0 - dpb;
    *prob_home = eh * rem; *prob_away = (1.0 - eh) * rem; *prob_draw = dpb;
    double tot = *prob_home + *prob_draw + *prob_away; *prob_home /= tot; *prob_draw /= tot; *prob_away /= tot;
}

void batch_compute_features(const double* home_elos, const double* away_elos, const double* home_forms, const double* away_forms, const double* h2h_stats_in, const double* league_stats, double home_advantage, int n_matches, double* out_features, int n_features) {
    const int FD=10, H2HD=6, LD=4;
    for (int i=0; i<n_matches; ++i) {
        double* feat = out_features + i * n_features;
        double eh=home_elos[i], ea=away_elos[i], ph,pd,pa;
        compute_elo_probabilities(eh,ea,home_advantage,&ph,&pd,&pa);
        feat[0]=eh; feat[1]=ea; feat[2]=eh-ea; feat[3]=ph; feat[4]=pd; feat[5]=pa;
        const double* hf = home_forms + i*FD; for (int j=0;j<FD;++j) feat[6+j]=hf[j];
        const double* af = away_forms + i*FD; for (int j=0;j<FD;++j) feat[16+j]=af[j];
        const double* h2 = h2h_stats_in + i*H2HD; for (int j=0;j<H2HD;++j) feat[26+j]=h2[j];
        const double* ls = league_stats + i*LD; double p25,pbt;
        compute_goal_probability(hf[3],af[4],af[3],hf[4],ls[0],ls[3],&p25,&pbt);
        feat[32]=p25; feat[33]=pbt; feat[34]=hf[0]-af[0]; feat[35]=hf[3]-af[3]; feat[36]=hf[6]-af[6]; feat[37]=hf[7]-af[7];
    }
}

void compute_attack_defense_elo(const char** home_teams, const char** away_teams, const int* home_goals, const int* away_goals, int n_matches, double k_factor, double home_advantage, double* out_home_att, double* out_home_def, double* out_away_att, double* out_away_def) {
    std::unordered_map<std::string, double> att, def;
    for (int i = 0; i < n_matches; ++i) {
        std::string home(home_teams[i]), away(away_teams[i]);
        if (!att.count(home)) { att[home]=DEFAULT_ELO; def[home]=DEFAULT_ELO; }
        if (!att.count(away)) { att[away]=DEFAULT_ELO; def[away]=DEFAULT_ELO; }
        double e_ha = 1.0 / (1.0 + std::pow(10.0, (def[away] - att[home] - 30.0) / 300.0)), e_aa = 1.0 / (1.0 + std::pow(10.0, (def[home] - att[away] + 30.0) / 300.0));
        double ek = k_factor * gd_multiplier(std::abs(home_goals[i] - away_goals[i]));
        att[home] += ek * (std::min(home_goals[i]/2.5, 1.0) - e_ha);
        att[away] += ek * (std::min(away_goals[i]/2.5, 1.0) - e_aa);
        def[home] += ek * (std::max(0.0, 1.0 - away_goals[i]/2.5) - (1.0 - e_aa));
        def[away] += ek * (std::max(0.0, 1.0 - home_goals[i]/2.5) - (1.0 - e_ha));
        out_home_att[i] = att[home]; out_home_def[i] = def[home]; out_away_att[i] = att[away]; out_away_def[i] = def[away];
    }
}

void compute_poisson_score_matrix(double lh, double la, double rho, double* out) {
    lh = std::min(std::max(lh, 0.01), 6.0); la = std::min(std::max(la, 0.01), 6.0);
    double mat[7][7], tw = 0;
    for (int h = 0; h < 7; ++h) for (int a = 0; a < 7; ++a) {
        double p = poisson_pmf(lh, h) * poisson_pmf(la, a) * dc_correction(h, a, lh, la, rho);
        mat[h][a] = std::max(p, 0.0); tw += mat[h][a];
    }
    if (tw > 1e-12) for (int h=0;h<7;++h) for (int a=0;a<7;++a) mat[h][a] /= tw;
    double p15=0, p25=0, p35=0, btts=0, hcs=0, acs=0;
    for (int h=0; h<7; ++h) for (int a=0; a<7; ++a) {
        double p = mat[h][a]; int t = h+a;
        if (t>=2) p15+=p; if (t>=3) p25+=p; if (t>=4) p35+=p; if (h>=1 && a>=1) btts+=p; if (a==0) hcs+=p; if (h==0) acs+=p;
    }
    out[0]=p15; out[1]=p25; out[2]=p35; out[3]=btts; out[4]=hcs; out[5]=acs; out[6]=mat[0][0]; out[7]=mat[1][0]; out[8]=mat[0][1]; out[9]=mat[1][1];
}

void compute_consecutive_runs(const int* hg, const int* ag, const int* wh, int n, double* out) {
    int ub = 0, wl = 0;
    for (int i=n-1; i>=0; --i) {
        int s = wh[i]?hg[i]:ag[i], c = wh[i]?ag[i]:hg[i];
        if (s>=c) ub++; else break;
    }
    for (int i=n-1; i>=0; --i) {
        int s = wh[i]?hg[i]:ag[i], c = wh[i]?ag[i]:hg[i];
        if (s<=c) wl++; else break;
    }
    out[0]=std::min(ub, 15)/15.0; out[1]=std::min(wl, 15)/15.0;
}

void compute_venue_split_form(const int* hg, const int* ag, const int* wh, int n_m, int is_h, double* out) {
    double w=0, pts=0, gs=0, gc=0, n=0;
    for (int i=0; i<n_m; ++i) {
        if (wh[i] != is_h) continue;
        int s = is_h?hg[i]:ag[i], c = is_h?ag[i]:hg[i];
        if (s>c) { w++; pts+=3; } else if (s==c) pts+=1;
        gs+=s; gc+=c; n++;
    }
    if (n<1e-9) { for(int i=0;i<4;++i) out[i]=0; return; }
    out[0]=w/n; out[1]=pts/n; out[2]=gs/n; out[3]=gc/n;
}

void compute_goals_variance(const int* hg, const int* ag, const int* wh, int n_m, double* out) {
    int n = std::min(n_m, 30); if (n==0) { out[0]=0; out[1]=0; return; }
    std::vector<double> s(n), c(n), w(n);
    for (int i=0; i<n; ++i) {
        int idx = n_m-n+i; s[i]=wh[idx]?hg[idx]:ag[idx]; c[i]=wh[idx]?ag[idx]:hg[idx]; w[i]=std::pow(0.85, n-1-i);
    }
    double ms, vs, mc, vc; weighted_moments(s.data(), w.data(), n, ms, vs); weighted_moments(c.data(), w.data(), n, mc, vc);
    out[0]=vs; out[1]=vc;
}

void compute_form_trend(const int* hg, const int* ag, const int* wh, int n, double* out) {
    if (n<6) { *out=0; return; }
    auto get_p = [&](int s, int e) {
        if (s>=e) return 0.0; double p=0;
        for (int i=s; i<e; ++i) { int sc=wh[i]?hg[i]:ag[i], co=wh[i]?ag[i]:hg[i]; if(sc>co) p+=3; else if(sc==co) p+=1; }
        return p/(e-s);
    };
    int n5 = std::min(5, n); *out = get_p(n-n5, n) - (n>=10 ? get_p(n-10, n-5) : get_p(0, n-n5));
}

void compute_scoring_consistency(const int* hg, const int* ag, const int* wh, int n_m, double* out) {
    int n = std::min(n_m, 15); if (n<3) { *out=0.5; return; }
    double m=0, v=0; std::vector<double> g(n), w(n, 1.0);
    for (int i = 0; i < n; ++i) g[i] = wh[n_m-n+i]?hg[n_m-n+i]:ag[n_m-n+i];
    weighted_moments(g.data(), w.data(), n, m, v); *out = 1.0/(1.0+std::sqrt(v));
}

void compute_h2h_extended(const int* hg, const int* ag, const int* wf, int n, double* out) {
    if (n==0) { out[0]=2.6; out[1]=1.25; return; }
    double tg=0, hw=0, aw=0;
    for (int i=0; i<n; ++i) {
        int g1=wf[i]?hg[i]:ag[i], g2=wf[i]?ag[i]:hg[i]; tg+=(g1+g2);
        if (g1>g2) hw++; else if (g1<g2) aw++;
    }
    out[0]=tg/n; out[1]=(hw+0.5)/(aw+0.5);
}

void compute_last_n_goals(const int* hg, const int* ag, const int* wh, int n_m, int n, int is_s, double* out) {
    int c = std::min(n, n_m); if (c==0) { *out=1.2; return; }
    double sum=0; for (int i=0; i<c; ++i) { int idx=n_m-1-i; sum+=(is_s ? (wh[idx]?hg[idx]:ag[idx]) : (wh[idx]?ag[idx]:hg[idx])); }
    *out=sum/c;
}

void compute_draw_rate(const int* hg, const int* ag, int n, double* out) {
    if (n<3) { *out=0.24; return; }
    int d=0; for (int i=0; i<n; ++i) if(hg[i]==ag[i]) d++;
    *out=(double)d/n;
}

void compute_temporal_features(double cts, const double* hts, int n, double* out) {
    double days = 7.0; if (n>0 && cts>hts[n-1]) days=(cts-hts[n-1])/86400.0;
    out[0]=std::min(days, 60.0)/60.0;
    time_t rt = (time_t)cts; struct tm ti; gmtime_r(&rt, &ti);
    int m=ti.tm_mon+1, p=-1;
    if(m==8)p=0;else if(m==9)p=1;else if(m==10)p=2;else if(m==11)p=3;else if(m==12)p=4;else if(m==1)p=5;else if(m==2)p=6;else if(m==3)p=7;else if(m==4)p=8;else if(m==5)p=9;
    out[1]=(p==-1?0.5:p/9.0);
}

void compute_streak(const int* hg, const int* ag, const int* wh, int n_m, double* out) {
    int s=0, sn=0, c=std::min(n_m, 12);
    for (int i=0; i<c; ++i) {
        int idx=n_m-1-i, s1=wh[idx]?hg[idx]:ag[idx], c1=wh[idx]?ag[idx]:hg[idx], o=(s1>c1?1:(s1<c1?-1:0));
        if (s==0) { s=o; sn=o; } else if (o==sn) s+=sn; else break;
    }
    *out=std::max(-1.0, std::min(1.0, s/5.0));
}

void compute_volatility(const double* history_values, int n, double* out) {
    if (n < 3) { *out = 0; return; }
    double mean = 0, var = 0;
    std::vector<double> w(n, 1.0);
    weighted_moments(history_values, w.data(), n, mean, var);
    *out = std::sqrt(var) / 100.0;
}

void compute_acceleration(const int* hg, const int* ag, const int* wh, int n, double* out) {
    if (n < 10) { *out = 0; return; }
    auto get_p = [&](int s, int e) {
        double pts = 0;
        for (int i = s; i < e; ++i) {
            int sc = wh[i] ? hg[i] : ag[i], co = wh[i] ? ag[i] : hg[i];
            if (sc > co) pts += 3; else if (sc == co) pts += 1;
        }
        return pts / (e - s);
    };
    double p1 = get_p(n - 5, n), p2 = get_p(n - 10, n - 5), p3 = (n >= 15) ? get_p(n - 15, n - 10) : p2;
    *out = (p1 - p2) - (p2 - p3);
}

void compute_all_features_v3(const double* pe, const double* pad, const int* mg, const double* od, double cts, const double* ls, int nh, const int* ghh, const int* gah, const int* whh, const double* tsh, int na, const int* gha, const int* gaa, const int* wha, const double* tsa, int n2, const int* gh2, const int* ga2, const int* wh2, double ha, double* out) {
    std::memcpy(out, pe, 6*8); std::memcpy(out+6, pad, 4*8);
    compute_form_vector(ghh, gah, whh, nh, 0, out+10); compute_form_vector(gha, gaa, wha, na, 0, out+20);
    compute_venue_split_form(ghh, gah, whh, nh, 1, out+30); compute_venue_split_form(gha, gaa, wha, na, 0, out+34);
    compute_h2h_stats(gh2, ga2, wh2, n2, out+38);
    double half=std::max(ls[0]/2, 0.1), has=out[13]/half, hds=std::max(half-out[14], 0.1)/half, aas=out[23]/half, ads=std::max(half-out[24], 0.1)/half;
    double lh=std::min(std::max(has*ads*ls[3]*half, 0.1), 6.0), la=std::min(std::max(aas*hds*half, 0.1), 6.0);
    double dc[10]; compute_poisson_score_matrix(lh, la, -0.13, dc);
    std::memcpy(out+44, dc, 6*8); out[50]=lh; out[51]=la; out[52]=lh/std::max(la, 0.01); out[53]=lh+la; std::memcpy(out+54, dc+6, 4*8);
    out[58]=out[10]-out[20]; out[59]=out[13]-out[23]; out[60]=out[16]-out[26]; out[61]=out[17]-out[27];
    bool ro=(od[0]>1&&od[1]>1&&od[2]>1); double ih=ro?1/od[0]:out[3], id=ro?1/od[1]:out[4], ia=ro?1/od[2]:out[5], ov=ih+id+ia, s_ov=ov>1e-9?ov:1;
    out[62]=ih/s_ov; out[63]=id/s_ov; out[64]=ia/s_ov; out[65]=ov;
    out[66]=has; out[67]=aas; out[68]=hds; out[69]=ads;
    compute_consecutive_runs(ghh, gah, whh, nh, out+70); compute_consecutive_runs(gha, gaa, wha, na, out+72);
    compute_streak(ghh, gah, whh, nh, out+74); compute_streak(gha, gaa, wha, na, out+75);
    compute_form_trend(ghh, gah, whh, nh, out+76); compute_form_trend(gha, gaa, wha, na, out+77);
    compute_scoring_consistency(ghh, gah, whh, nh, out+78); compute_scoring_consistency(gha, gaa, wha, na, out+79);
    compute_h2h_extended(gh2, ga2, wh2, n2, out+80);
    out[82]=ls[0]; out[83]=ls[4]; out[84]=ls[5]; out[85]=out[31]-out[35];
    out[86]=out[13]/(std::max(ls[1]*half, 0.1))-1; out[87]=out[23]/(std::max(ls[1]*half, 0.1))-1; out[88]=1-out[14]/(std::max(ls[1]*half, 0.1)); out[89]=1-out[24]/(std::max(ls[1]*half, 0.1));
    compute_goals_variance(ghh, gah, whh, nh, out+90); compute_goals_variance(gha, gaa, wha, na, out+92);
    compute_last_n_goals(ghh, gah, whh, nh, 3, 1, out+94); compute_last_n_goals(gha, gaa, wha, na, 3, 1, out+95);
    compute_last_n_goals(ghh, gah, whh, nh, 3, 0, out+96); compute_last_n_goals(gha, gaa, wha, na, 3, 0, out+97);
    double t_h[2], t_a[2]; compute_temporal_features(cts, tsh, nh, t_h); compute_temporal_features(cts, tsa, na, t_a);
    out[98]=t_h[0]; out[99]=t_a[0]; out[100]=t_h[1];
    compute_draw_rate(ghh, gah, nh, out+101); compute_draw_rate(gha, gaa, na, out+102); out[103]=ro?1:0;
}

void compute_all_features_v4(const double* pe, const double* pad, const int* mg, const double* od, double cts, const double* ls, int nh, const int* ghh, const int* gah, const int* whh, const double* tsh, const double* ehh, int na, const int* gha, const int* gaa, const int* wha, const double* tsa, const double* eha, int n2, const int* gh2, const int* ga2, const int* wh2, double ha, double* out) {
    compute_all_features_v3(pe, pad, mg, od, cts, ls, nh, ghh, gah, whh, tsh, na, gha, gaa, wha, tsa, n2, gh2, ga2, wh2, ha, out);
    out[104]=(out[10]-out[62]); out[105]=(out[20]-out[64]); out[106]=(out[13]-out[24]); out[107]=(out[23]-out[14]); out[108]=out[16]*out[17]; out[109]=out[26]*out[27];
    compute_volatility(ehh, nh, out+110); compute_volatility(eha, na, out+111);
    compute_acceleration(ghh, gah, whh, nh, out+112); compute_acceleration(gha, gaa, wha, na, out+113);
    out[114]=out[2]*out[61]; out[115]=out[65]*(out[110]+out[111]);
    for(int j=116; j<125; ++j) out[j]=0.0;
}

void compute_all_features_bulk_v4(const int* ti, int nt, const int* agh, const int* aga, const double* ats, const int* ahi, const int* aai, const double* ape, const double* apad, const double* ao, const double* als, const int* tmi, const int* tmp, const int* tmc, const double* ahe, const double* aae, int lb, double ha, double* out) {
    #pragma omp parallel for schedule(dynamic)
    for (int i=0; i<nt; ++i) {
        int idx=ti[i], ht=ahi[idx], at=aai[idx];
        auto f_h = [&](int tid, std::vector<int>& v, std::vector<double>& ev, const double* all_e) {
            int p=tmp[tid], c=tmc[tid];
            for (int j=0; j<c; ++j) { int mid=tmi[p+j]; if(mid<idx) { v.push_back(mid); ev.push_back(all_e[mid]); } else break; }
            if ((int)v.size()>lb) { v.erase(v.begin(), v.end()-lb); ev.erase(ev.begin(), ev.end()-lb); }
        };
        std::vector<int> hh, ah; std::vector<double> ehh, eha; f_h(ht, hh, ehh, ahe); f_h(at, ah, eha, aae);
        std::vector<int> h2; const std::vector<int>& sm=(hh.size()<ah.size())?hh:ah;
        for (int m:sm) if((ahi[m]==ht&&aai[m]==at)||(ahi[m]==at&&aai[m]==ht)) h2.push_back(m);
        int nh=hh.size(), na=ah.size(), n2=h2.size();
        std::vector<int> ghh(nh), gah(nh), whh(nh); std::vector<double> tsh(nh);
        for(int j=0;j<nh;++j){ int m=hh[j]; ghh[j]=agh[m]; gah[j]=aga[m]; whh[j]=(ahi[m]==ht?1:0); tsh[j]=ats[m]; }
        std::vector<int> gha(na), gaa(na), wha(na); std::vector<double> tsa(na);
        for(int j=0;j<na;++j){ int m=ah[j]; gha[j]=agh[m]; gaa[j]=aga[m]; wha[j]=(ahi[m]==at?1:0); tsa[j]=ats[m]; }
        std::vector<int> gh2(n2), ga2(n2), wh2(n2);
        for(int j=0;j<n2;++j){ int m=h2[j]; gh2[j]=agh[m]; ga2[j]=aga[m]; wh2[j]=(ahi[m]==ht?1:0); }
        int mg[2]={agh[idx], aga[idx]};
        compute_all_features_v4(ape+idx*6, apad+idx*4, mg, ao+idx*3, ats[idx], als+idx*6, nh, ghh.data(), gah.data(), whh.data(), tsh.data(), ehh.data(), na, gha.data(), gaa.data(), wha.data(), tsa.data(), eha.data(), n2, gh2.data(), ga2.data(), wh2.data(), ha, out+(long long)i*125);
    }
}

} // extern "C"
} // namespace statwise
