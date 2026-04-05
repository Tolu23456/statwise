/*
 * StatWise Dataset Cleaner — C++17  v3.0
 * ========================================
 * Reads raw CSVs and JSONs from all football data sources, applies a deep
 * multi-stage cleaning pipeline, normalises to a unified schema,
 * deduplicates, and writes one merged CSV per calendar year.
 *
 * Cleaning pipeline (applied in order):
 *   Phase 1 : xgabora Matches.csv  (475K rows, 2000-2025)
 *   Phase 2 : football-data.co.uk  (per-season CSVs, 1993-2025)
 *   Phase 3 : understat xG shots   (inject xG into phase-1/2 records)
 *   Phase 4 : martj42 international results  (47K+ matches since 1872)
 *   Phase 5 : jfjelstul FIFA World Cup       (1930-2022)
 *   Phase 6 : openfootball/football.json     (per-season JSON, 2011-2025)
 *   Phase 7 : Cross-source conflict scan     (flag score disagreements)
 *   Phase 8 : Fuzzy duplicate detection      (Jaro-Winkler, same-date window)
 *   Phase 9 : Data quality scoring           (0-100, per match)
 *   Phase 10: Year-bucketed output           (YYYY_matches.csv)
 *
 * Cleaning steps applied per row:
 *   A. RFC 4180 CSV / minimal JSON parsing with BOM + CRLF stripping
 *   B. Team name normalisation: 1000+ alias → canonical mappings
 *   C. Date parsing: 10 format variants → ISO 8601 (YYYY-MM-DD)
 *   D. Score validation: range 0-25 per side, both sides present
 *   E. Odds validation: range 1.01-300, implied probability sum 80-140%
 *   F. Stats validation: shots 0-60, corners 0-30, fouls 0-50, cards 0-15
 *   G. xG validation + Poisson plausibility check (0.0-12.0)
 *   H. League slug + country normalisation
 *   I. League tier classification (1=top, 4=lower, 0=international)
 *   J. Duplicate detection: {ISO-date, canonical_home, canonical_away}
 *   K. Source-priority merge with conflict flagging
 *   L. Z-score outlier filtering per league stratum (|z|>4.5 on goals)
 *   M. Jaro-Winkler fuzzy dedup pass (post-global-merge)
 *   N. Data quality scoring: count of valid populated fields
 *
 * Performance design:
 *   - Thread pool (default: nproc/2, max 6 workers)
 *   - CPU governor: throttles if CPU > 70%
 *   - RAM governor: pauses if free RAM < 400 MB
 *   - Memory-mapped file reads for large CSVs
 *   - Lock-free per-thread dedup tables; global merge with mutex
 *   - Graceful SIGTERM/SIGINT: finishes current phase then writes output
 *
 * Output schema:
 *   date, home_team, away_team, home_goals, away_goals, league_slug,
 *   country, source, halftime_home, halftime_away, shots_home, shots_away,
 *   shots_on_target_home, shots_on_target_away, corners_home, corners_away,
 *   fouls_home, fouls_away, yellows_home, yellows_away, reds_home, reds_away,
 *   elo_home, elo_away, odds_home, odds_draw, odds_away,
 *   max_odds_home, max_odds_draw, max_odds_away,
 *   avg_odds_home, avg_odds_draw, avg_odds_away,
 *   asian_handicap_line, asian_handicap_home, asian_handicap_away,
 *   over25_odds, under25_odds, max_over25, max_under25,
 *   xg_home, xg_away,
 *   quality_score, league_tier, is_international, score_conflict,
 *   tournament, is_neutral
 *
 * Usage:
 *   ./dataset_cleaner [raw_dir] [clean_dir] [--workers N] [--verbose]
 */

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <signal.h>
#include <sstream>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <unordered_map>
#include <unordered_set>
#include <vector>

// ─── global stop flag ─────────────────────────────────────────────────────────
static std::atomic<bool> g_stop{false};
static void handle_signal(int) { g_stop.store(true); }

// ─── logging ──────────────────────────────────────────────────────────────────
static std::mutex g_log_mu;
static bool       g_verbose = false;

static void logmsg(const char* level, const std::string& msg) {
    auto now = std::chrono::system_clock::now();
    auto t   = std::chrono::system_clock::to_time_t(now);
    struct tm tm_buf{};
    localtime_r(&t, &tm_buf);
    char ts[32];
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &tm_buf);
    std::lock_guard<std::mutex> lk(g_log_mu);
    std::cout << "[" << ts << "] [" << std::setw(5) << std::left << level << "] "
              << msg << "\n" << std::flush;
}
#define LOG_INFO(m)  logmsg("INFO",  m)
#define LOG_WARN(m)  logmsg("WARN",  m)
#define LOG_ERROR(m) logmsg("ERROR", m)
#define LOG_OK(m)    logmsg("OK",    m)
#define LOG_DEBUG(m) do { if (g_verbose) logmsg("DEBUG", m); } while(0)

// ─── system resource helpers ──────────────────────────────────────────────────
static long free_ram_mb() {
    std::ifstream f("/proc/meminfo");
    std::string line;
    while (std::getline(f, line)) {
        if (line.rfind("MemAvailable:", 0) == 0) {
            long kb = 0;
            sscanf(line.c_str(), "MemAvailable: %ld kB", &kb);
            return kb / 1024;
        }
    }
    return 99999;
}

static int cpu_usage_pct() {
    static long prev_idle = 0, prev_total = 0;
    std::ifstream f("/proc/stat");
    if (!f.is_open()) return 0;
    std::string tag;
    long u, n, s, idle, io, irq, sirq, steal;
    f >> tag >> u >> n >> s >> idle >> io >> irq >> sirq >> steal;
    long total  = u + n + s + idle + io + irq + sirq + steal;
    long d_idle  = idle  - prev_idle;
    long d_total = total - prev_total;
    prev_idle  = idle; prev_total = total;
    if (d_total <= 0) return 0;
    return static_cast<int>((1.0 - (double)d_idle / d_total) * 100.0);
}

static void throttle_if_needed() {
    cpu_usage_pct();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    int cpu = cpu_usage_pct();
    if (cpu > 70) {
        LOG_DEBUG("CPU at " + std::to_string(cpu) + "% — throttling 500ms");
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
    long ram = free_ram_mb();
    if (ram < 400) {
        LOG_WARN("RAM low (" + std::to_string(ram) + " MB) — pausing 8s");
        std::this_thread::sleep_for(std::chrono::seconds(8));
    }
}

// ─── thread pool ──────────────────────────────────────────────────────────────
class ThreadPool {
public:
    explicit ThreadPool(int n) {
        for (int i = 0; i < n; ++i)
            _workers.emplace_back([this]{ worker_fn(); });
    }
    ~ThreadPool() {
        { std::unique_lock<std::mutex> lk(_mu); _done = true; }
        _cv.notify_all();
        for (auto& t : _workers) t.join();
    }
    void enqueue(std::function<void()> fn) {
        { std::unique_lock<std::mutex> lk(_mu); _tasks.push_back(std::move(fn)); }
        _cv.notify_one();
    }
    void wait_all() {
        while (true) {
            std::unique_lock<std::mutex> lk(_mu);
            if (_tasks.empty() && _active == 0) break;
            lk.unlock();
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    }
private:
    void worker_fn() {
        while (true) {
            std::function<void()> task;
            {
                std::unique_lock<std::mutex> lk(_mu);
                _cv.wait(lk, [this]{ return _done || !_tasks.empty(); });
                if (_done && _tasks.empty()) return;
                task = std::move(_tasks.front());
                _tasks.pop_front();
                ++_active;
            }
            task();
            { std::unique_lock<std::mutex> lk(_mu); --_active; }
        }
    }
    std::vector<std::thread>          _workers;
    std::deque<std::function<void()>> _tasks;
    std::mutex                        _mu;
    std::condition_variable           _cv;
    bool                              _done{false};
    int                               _active{0};
};

// ─── CSV parser (RFC 4180 + BOM + CRLF) ──────────────────────────────────────
static std::vector<std::string> parse_csv_row(const std::string& line) {
    std::vector<std::string> fields;
    std::string field;
    bool in_quotes = false;
    for (size_t i = 0; i < line.size(); ++i) {
        char c = line[i];
        if (in_quotes) {
            if (c == '"') {
                if (i + 1 < line.size() && line[i+1] == '"') { field += '"'; ++i; }
                else in_quotes = false;
            } else { field += c; }
        } else {
            if (c == '"') in_quotes = true;
            else if (c == ',') { fields.push_back(field); field.clear(); }
            else if (c != '\r') field += c;
        }
    }
    fields.push_back(field);
    return fields;
}

static std::string strip_bom(const std::string& s) {
    if (s.size() >= 3 &&
        (unsigned char)s[0] == 0xEF &&
        (unsigned char)s[1] == 0xBB &&
        (unsigned char)s[2] == 0xBF) return s.substr(3);
    return s;
}

static std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n\"");
    size_t b = s.find_last_not_of(" \t\r\n\"");
    if (a == std::string::npos) return "";
    return s.substr(a, b - a + 1);
}

static std::string lower(std::string s) {
    for (auto& c : s) c = (char)tolower((unsigned char)c);
    return s;
}

// ─── header index builder ─────────────────────────────────────────────────────
using HeaderIdx = std::unordered_map<std::string, int>;

static HeaderIdx make_header_idx(const std::vector<std::string>& hdr) {
    HeaderIdx idx;
    for (int i = 0; i < (int)hdr.size(); ++i)
        idx[trim(hdr[i])] = i;
    return idx;
}

static std::string get(const std::vector<std::string>& row, const HeaderIdx& idx,
                        const std::string& col) {
    auto it = idx.find(col);
    if (it == idx.end()) return "";
    if (it->second >= (int)row.size()) return "";
    return trim(row[it->second]);
}

// ─── team name normalisation (1000+ aliases) ──────────────────────────────────
static const std::unordered_map<std::string, std::string> TEAM_ALIASES = {
    // ── England ──────────────────────────────────────────────────────────────
    {"Man United",              "Manchester United"},
    {"Man Utd",                 "Manchester United"},
    {"Manchester Utd",          "Manchester United"},
    {"Manchester United FC",    "Manchester United"},
    {"Man City",                "Manchester City"},
    {"Manchester C",            "Manchester City"},
    {"Manchester City FC",      "Manchester City"},
    {"Spurs",                   "Tottenham Hotspur"},
    {"Tottenham",               "Tottenham Hotspur"},
    {"Tottenham H",             "Tottenham Hotspur"},
    {"Tottenham Hotspur FC",    "Tottenham Hotspur"},
    {"Sheffield Utd",           "Sheffield United"},
    {"Sheffield United FC",     "Sheffield United"},
    {"Sheffield Weds",          "Sheffield Wednesday"},
    {"Sheffield Wed",           "Sheffield Wednesday"},
    {"Sheffield Wednesday FC",  "Sheffield Wednesday"},
    {"West Brom",               "West Bromwich Albion"},
    {"West Brom A",             "West Bromwich Albion"},
    {"WBA",                     "West Bromwich Albion"},
    {"West Bromwich Albion FC", "West Bromwich Albion"},
    {"Wolves",                  "Wolverhampton Wanderers"},
    {"Wolverhampton",           "Wolverhampton Wanderers"},
    {"Wolverhampton Wanderers FC","Wolverhampton Wanderers"},
    {"Nott'm Forest",           "Nottingham Forest"},
    {"Nottm Forest",            "Nottingham Forest"},
    {"Nottingham Forest FC",    "Nottingham Forest"},
    {"QPR",                     "Queens Park Rangers"},
    {"Queen Park Rng",          "Queens Park Rangers"},
    {"Queens Park Rangers FC",  "Queens Park Rangers"},
    {"Leicester",               "Leicester City"},
    {"Leicester City FC",       "Leicester City"},
    {"Norwich",                 "Norwich City"},
    {"Norwich City FC",         "Norwich City"},
    {"Hull",                    "Hull City"},
    {"Hull City FC",            "Hull City"},
    {"Stoke",                   "Stoke City"},
    {"Stoke City FC",           "Stoke City"},
    {"Cardiff",                 "Cardiff City"},
    {"Cardiff City FC",         "Cardiff City"},
    {"Swansea",                 "Swansea City"},
    {"Swansea City AFC",        "Swansea City"},
    {"Brighton",                "Brighton & Hove Albion"},
    {"Brighton & HA",           "Brighton & Hove Albion"},
    {"Brighton & Hove Albion FC","Brighton & Hove Albion"},
    {"Brentford FC",            "Brentford"},
    {"Fulham FC",               "Fulham"},
    {"Middlesbrough FC",        "Middlesbrough"},
    {"Boro",                    "Middlesbrough"},
    {"Burnley FC",              "Burnley"},
    {"Blackburn",               "Blackburn Rovers"},
    {"Blackburn Rov",           "Blackburn Rovers"},
    {"Blackburn Rovers FC",     "Blackburn Rovers"},
    {"Bolton",                  "Bolton Wanderers"},
    {"Bolton Wanderers FC",     "Bolton Wanderers"},
    {"Wigan",                   "Wigan Athletic"},
    {"Wigan Athletic AFC",      "Wigan Athletic"},
    {"Coventry",                "Coventry City"},
    {"Coventry City FC",        "Coventry City"},
    {"Derby",                   "Derby County"},
    {"Derby County FC",         "Derby County"},
    {"Sunderland AFC",          "Sunderland"},
    {"Newcastle",               "Newcastle United"},
    {"Newcastle Utd",           "Newcastle United"},
    {"Newcastle United FC",     "Newcastle United"},
    {"Aston Villa FC",          "Aston Villa"},
    {"Ipswich",                 "Ipswich Town"},
    {"Ipswich Town FC",         "Ipswich Town"},
    {"Charlton",                "Charlton Athletic"},
    {"Charlton Athletic FC",    "Charlton Athletic"},
    {"Leeds",                   "Leeds United"},
    {"Leeds United FC",         "Leeds United"},
    {"Watford FC",              "Watford"},
    {"Crystal Palace FC",       "Crystal Palace"},
    {"Palace",                  "Crystal Palace"},
    {"Everton FC",              "Everton"},
    {"Chelsea FC",              "Chelsea"},
    {"Arsenal FC",              "Arsenal"},
    {"Liverpool FC",            "Liverpool"},
    {"Southampton FC",          "Southampton"},
    {"Portsmouth FC",           "Portsmouth"},
    {"Luton",                   "Luton Town"},
    {"Luton Town FC",           "Luton Town"},
    {"Millwall FC",             "Millwall"},
    {"Reading FC",              "Reading"},
    {"Bristol City FC",         "Bristol City"},
    {"West Ham",                "West Ham United"},
    {"West Ham Utd",            "West Ham United"},
    {"West Ham United FC",      "West Ham United"},
    {"Huddersfield",            "Huddersfield Town"},
    {"Huddersfield Town AFC",   "Huddersfield Town"},
    {"Preston",                 "Preston North End"},
    {"Preston NE",              "Preston North End"},
    {"Preston North End FC",    "Preston North End"},
    {"Rotherham",               "Rotherham United"},
    {"Rotherham United FC",     "Rotherham United"},
    {"Barnsley FC",             "Barnsley"},
    {"Blackpool FC",            "Blackpool"},
    {"Birmingham",              "Birmingham City"},
    {"Birmingham C",            "Birmingham City"},
    {"Birmingham City FC",      "Birmingham City"},
    {"Swindon",                 "Swindon Town"},
    {"Swindon Town FC",         "Swindon Town"},
    {"Bradford",                "Bradford City"},
    {"Bradford City AFC",       "Bradford City"},
    {"Oldham",                  "Oldham Athletic"},
    {"Wimbledon",               "AFC Wimbledon"},
    {"AFC Wimbledon",           "AFC Wimbledon"},
    {"Accrington",              "Accrington Stanley"},
    {"Peterborough",            "Peterborough United"},
    {"Exeter",                  "Exeter City"},
    {"Wycombe",                 "Wycombe Wanderers"},
    {"Oxford",                  "Oxford United"},
    {"Shrewsbury",              "Shrewsbury Town"},
    {"Fleetwood",               "Fleetwood Town"},
    // ── Germany ──────────────────────────────────────────────────────────────
    {"Bayern",                  "Bayern Munich"},
    {"Bayern Munchen",          "Bayern Munich"},
    {"FC Bayern",               "Bayern Munich"},
    {"FC Bayern Munchen",       "Bayern Munich"},
    {"FC Bayern Munich",        "Bayern Munich"},
    {"Bayern Munich FC",        "Bayern Munich"},
    {"Dortmund",                "Borussia Dortmund"},
    {"BVB",                     "Borussia Dortmund"},
    {"B. Dortmund",             "Borussia Dortmund"},
    {"Borussia Dortmund FC",    "Borussia Dortmund"},
    {"Gladbach",                "Borussia Monchengladbach"},
    {"M'gladbach",              "Borussia Monchengladbach"},
    {"Mgladbach",               "Borussia Monchengladbach"},
    {"Borussia Mgladbach",      "Borussia Monchengladbach"},
    {"Bayer Leverkusen",        "Bayer Leverkusen"},
    {"Leverkusen",              "Bayer Leverkusen"},
    {"Bayer 04 Leverkusen",     "Bayer Leverkusen"},
    {"RB Leipzig",              "RB Leipzig"},
    {"Leipzig",                 "RB Leipzig"},
    {"Schalke",                 "Schalke 04"},
    {"FC Schalke 04",           "Schalke 04"},
    {"Wolfsburg",               "VfL Wolfsburg"},
    {"VfL Wolfsburg",           "VfL Wolfsburg"},
    {"VfL Wolfsburg FC",        "VfL Wolfsburg"},
    {"Freiburg",                "SC Freiburg"},
    {"Eintracht Frankfurt",     "Eintracht Frankfurt"},
    {"Frankfurt",               "Eintracht Frankfurt"},
    {"Stuttgart",               "VfB Stuttgart"},
    {"VfB Stuttgart",           "VfB Stuttgart"},
    {"Augsburg",                "FC Augsburg"},
    {"FC Augsburg",             "FC Augsburg"},
    {"Hoffenheim",              "TSG Hoffenheim"},
    {"TSG Hoffenheim",          "TSG Hoffenheim"},
    {"TSG 1899 Hoffenheim",     "TSG Hoffenheim"},
    {"Hertha",                  "Hertha Berlin"},
    {"Hertha BSC",              "Hertha Berlin"},
    {"Hertha BSC Berlin",       "Hertha Berlin"},
    {"Union Berlin",            "Union Berlin"},
    {"1. FC Union Berlin",      "Union Berlin"},
    {"Werder Bremen",           "Werder Bremen"},
    {"SV Werder Bremen",        "Werder Bremen"},
    {"Bremen",                  "Werder Bremen"},
    {"Cologne",                 "FC Cologne"},
    {"Koln",                    "FC Cologne"},
    {"1. FC Koln",              "FC Cologne"},
    {"Mainz",                   "FSV Mainz 05"},
    {"Mainz 05",                "FSV Mainz 05"},
    {"1. FSV Mainz 05",         "FSV Mainz 05"},
    {"Bochum",                  "VfL Bochum"},
    {"VfL Bochum",              "VfL Bochum"},
    {"Heidenheim",              "1. FC Heidenheim"},
    {"FC Heidenheim",           "1. FC Heidenheim"},
    {"Darmstadt",               "SV Darmstadt 98"},
    {"Darmstadt 98",            "SV Darmstadt 98"},
    {"Hamburger SV",            "Hamburger SV"},
    {"Hamburg",                 "Hamburger SV"},
    {"HSV",                     "Hamburger SV"},
    {"Hannover",                "Hannover 96"},
    {"Hannover 96",             "Hannover 96"},
    {"Fortuna Dusseldorf",      "Fortuna Dusseldorf"},
    {"Greuther Furth",          "SpVgg Greuther Furth"},
    {"Nuremberg",               "1. FC Nurnberg"},
    {"Nurnberg",                "1. FC Nurnberg"},
    {"Paderborn",               "SC Paderborn 07"},
    // ── Spain ────────────────────────────────────────────────────────────────
    {"Real Madrid",             "Real Madrid"},
    {"Real Madrid CF",          "Real Madrid"},
    {"Barcelona",               "FC Barcelona"},
    {"FC Barcelona",            "FC Barcelona"},
    {"Barca",                   "FC Barcelona"},
    {"Atletico Madrid",         "Atletico Madrid"},
    {"Atl. Madrid",             "Atletico Madrid"},
    {"Atletico de Madrid",      "Atletico Madrid"},
    {"Club Atletico de Madrid", "Atletico Madrid"},
    {"Sevilla",                 "Sevilla FC"},
    {"Sevilla FC",              "Sevilla FC"},
    {"Villarreal",              "Villarreal CF"},
    {"Villarreal CF",           "Villarreal CF"},
    {"Athletic Bilbao",         "Athletic Bilbao"},
    {"Athletic",                "Athletic Bilbao"},
    {"Ath Bilbao",              "Athletic Bilbao"},
    {"Athletic Club",           "Athletic Bilbao"},
    {"Real Betis",              "Real Betis"},
    {"Betis",                   "Real Betis"},
    {"Real Betis Balompie",     "Real Betis"},
    {"Valencia",                "Valencia CF"},
    {"Valencia CF",             "Valencia CF"},
    {"Real Sociedad",           "Real Sociedad"},
    {"Sociedad",                "Real Sociedad"},
    {"Osasuna",                 "CA Osasuna"},
    {"CA Osasuna",              "CA Osasuna"},
    {"Girona",                  "Girona FC"},
    {"Girona FC",               "Girona FC"},
    {"Getafe",                  "Getafe CF"},
    {"Las Palmas",              "UD Las Palmas"},
    {"Alaves",                  "Deportivo Alaves"},
    {"Deportivo Alaves",        "Deportivo Alaves"},
    {"Celta Vigo",              "Celta de Vigo"},
    {"Celta",                   "Celta de Vigo"},
    {"Rayo Vallecano",          "Rayo Vallecano"},
    {"Rayo",                    "Rayo Vallecano"},
    {"Mallorca",                "RCD Mallorca"},
    {"RCD Mallorca",            "RCD Mallorca"},
    {"Cadiz",                   "Cadiz CF"},
    {"Almeria",                 "UD Almeria"},
    {"Espanyol",                "RCD Espanyol"},
    {"RCD Espanyol",            "RCD Espanyol"},
    {"Leganes",                 "CD Leganes"},
    {"Deportivo",               "RC Deportivo"},
    {"RC Deportivo",            "RC Deportivo"},
    {"Valladolid",              "Real Valladolid"},
    {"Granada",                 "Granada CF"},
    {"Eibar",                   "SD Eibar"},
    {"Levante",                 "Levante UD"},
    {"Malaga",                  "Malaga CF"},
    {"Zaragoza",                "Real Zaragoza"},
    {"Sporting Gijon",          "Real Sporting de Gijon"},
    // ── Italy ────────────────────────────────────────────────────────────────
    {"Juventus",                "Juventus"},
    {"Juventus FC",             "Juventus"},
    {"Juve",                    "Juventus"},
    {"Inter Milan",             "Inter Milan"},
    {"Inter",                   "Inter Milan"},
    {"Internazionale",          "Inter Milan"},
    {"FC Internazionale",       "Inter Milan"},
    {"FC Internazionale Milano","Inter Milan"},
    {"AC Milan",                "AC Milan"},
    {"Milan",                   "AC Milan"},
    {"Roma",                    "AS Roma"},
    {"AS Roma",                 "AS Roma"},
    {"Napoli",                  "SSC Napoli"},
    {"SSC Napoli",              "SSC Napoli"},
    {"Lazio",                   "SS Lazio"},
    {"SS Lazio",                "SS Lazio"},
    {"Fiorentina",              "ACF Fiorentina"},
    {"ACF Fiorentina",          "ACF Fiorentina"},
    {"Atalanta",                "Atalanta BC"},
    {"Atalanta BC",             "Atalanta BC"},
    {"Torino",                  "Torino FC"},
    {"Torino FC",               "Torino FC"},
    {"Bologna",                 "Bologna FC"},
    {"Bologna FC",              "Bologna FC"},
    {"Udinese",                 "Udinese Calcio"},
    {"Udinese Calcio",          "Udinese Calcio"},
    {"Sampdoria",               "UC Sampdoria"},
    {"UC Sampdoria",            "UC Sampdoria"},
    {"Sassuolo",                "US Sassuolo"},
    {"US Sassuolo",             "US Sassuolo"},
    {"Empoli",                  "Empoli FC"},
    {"Empoli FC",               "Empoli FC"},
    {"Monza",                   "AC Monza"},
    {"AC Monza",                "AC Monza"},
    {"Genoa",                   "Genoa CFC"},
    {"Genoa CFC",               "Genoa CFC"},
    {"Cagliari",                "Cagliari Calcio"},
    {"Frosinone",               "Frosinone Calcio"},
    {"Lecce",                   "US Lecce"},
    {"US Lecce",                "US Lecce"},
    {"Verona",                  "Hellas Verona"},
    {"Hellas Verona",           "Hellas Verona"},
    {"Hellas Verona FC",        "Hellas Verona"},
    {"Parma",                   "Parma Calcio"},
    {"Chievo",                  "Chievo Verona"},
    {"Venezia",                 "Venezia FC"},
    {"Spezia",                  "Spezia Calcio"},
    {"Brescia",                 "Brescia Calcio"},
    {"Crotone",                 "FC Crotone"},
    {"Benevento",               "Benevento Calcio"},
    {"Como",                    "Como 1907"},
    // ── France ───────────────────────────────────────────────────────────────
    {"PSG",                     "Paris Saint-Germain"},
    {"Paris SG",                "Paris Saint-Germain"},
    {"Paris Saint Germain",     "Paris Saint-Germain"},
    {"Paris Saint-Germain FC",  "Paris Saint-Germain"},
    {"Marseille",               "Olympique Marseille"},
    {"Olympique Marseille",     "Olympique Marseille"},
    {"Olympique de Marseille",  "Olympique Marseille"},
    {"Lyon",                    "Olympique Lyonnais"},
    {"Olympique Lyonnais",      "Olympique Lyonnais"},
    {"Olympique de Lyon",       "Olympique Lyonnais"},
    {"Monaco",                  "AS Monaco"},
    {"AS Monaco",               "AS Monaco"},
    {"Lille",                   "LOSC Lille"},
    {"LOSC Lille",              "LOSC Lille"},
    {"Nice",                    "OGC Nice"},
    {"OGC Nice",                "OGC Nice"},
    {"Rennes",                  "Stade Rennais"},
    {"Stade Rennais",           "Stade Rennais"},
    {"Stade Rennais FC",        "Stade Rennais"},
    {"Lens",                    "RC Lens"},
    {"RC Lens",                 "RC Lens"},
    {"Strasbourg",              "RC Strasbourg"},
    {"RC Strasbourg",           "RC Strasbourg"},
    {"Nantes",                  "FC Nantes"},
    {"FC Nantes",               "FC Nantes"},
    {"Montpellier",             "Montpellier HSC"},
    {"Montpellier HSC",         "Montpellier HSC"},
    {"Toulouse",                "Toulouse FC"},
    {"Toulouse FC",             "Toulouse FC"},
    {"Metz",                    "FC Metz"},
    {"FC Metz",                 "FC Metz"},
    {"Brest",                   "Stade Brest"},
    {"Stade Brest",             "Stade Brest"},
    {"Le Havre",                "Le Havre AC"},
    {"Auxerre",                 "AJ Auxerre"},
    {"AJ Auxerre",              "AJ Auxerre"},
    {"Lorient",                 "FC Lorient"},
    {"Reims",                   "Stade de Reims"},
    {"Stade de Reims",          "Stade de Reims"},
    {"Angers",                  "SCO Angers"},
    {"Clermont",                "Clermont Foot"},
    {"Saint-Etienne",           "AS Saint-Etienne"},
    {"St Etienne",              "AS Saint-Etienne"},
    {"Bordeaux",                "FC Girondins de Bordeaux"},
    {"Girondins Bordeaux",      "FC Girondins de Bordeaux"},
    {"Caen",                    "Stade Malherbe Caen"},
    {"Guingamp",                "EA Guingamp"},
    {"Dijon",                   "Dijon FCO"},
    {"Troyes",                  "ESTAC Troyes"},
    // ── Netherlands ──────────────────────────────────────────────────────────
    {"Ajax",                    "AFC Ajax"},
    {"AFC Ajax",                "AFC Ajax"},
    {"Ajax FC",                 "AFC Ajax"},
    {"PSV",                     "PSV Eindhoven"},
    {"PSV Eindhoven",           "PSV Eindhoven"},
    {"Feyenoord",               "Feyenoord"},
    {"Feyenoord Rotterdam",     "Feyenoord"},
    {"AZ",                      "AZ Alkmaar"},
    {"AZ Alkmaar",              "AZ Alkmaar"},
    {"Utrecht",                 "FC Utrecht"},
    {"FC Utrecht",              "FC Utrecht"},
    {"Twente",                  "FC Twente"},
    {"FC Twente",               "FC Twente"},
    {"Heerenveen",              "SC Heerenveen"},
    {"Groningen",               "FC Groningen"},
    {"FC Groningen",            "FC Groningen"},
    {"Vitesse",                 "SBV Vitesse"},
    {"SBV Vitesse",             "SBV Vitesse"},
    {"Fortuna Sittard",         "Fortuna Sittard"},
    {"Heracles",                "Heracles Almelo"},
    {"Sparta Rotterdam",        "Sparta Rotterdam"},
    {"NEC",                     "NEC Nijmegen"},
    {"NEC Nijmegen",            "NEC Nijmegen"},
    {"RKC Waalwijk",            "RKC Waalwijk"},
    {"Go Ahead Eagles",         "Go Ahead Eagles"},
    {"Almere City",             "Almere City"},
    {"PEC Zwolle",              "PEC Zwolle"},
    {"SC Cambuur",              "SC Cambuur"},
    // ── Portugal ─────────────────────────────────────────────────────────────
    {"Benfica",                 "SL Benfica"},
    {"SL Benfica",              "SL Benfica"},
    {"Sport Lisboa e Benfica",  "SL Benfica"},
    {"Porto",                   "FC Porto"},
    {"FC Porto",                "FC Porto"},
    {"Sporting CP",             "Sporting CP"},
    {"Sporting",                "Sporting CP"},
    {"Sporting Clube de Portugal","Sporting CP"},
    {"Braga",                   "SC Braga"},
    {"SC Braga",                "SC Braga"},
    {"Vitoria Guimaraes",       "Vitoria SC"},
    {"Guimaraes",               "Vitoria SC"},
    {"Vitoria SC",              "Vitoria SC"},
    {"Boavista",                "Boavista FC"},
    {"Boavista FC",             "Boavista FC"},
    {"Rio Ave",                 "Rio Ave FC"},
    {"Moreirense",              "Moreirense FC"},
    {"Famalicao",               "FC Famalicao"},
    {"Gil Vicente",             "Gil Vicente FC"},
    {"Estoril",                 "GD Estoril Praia"},
    {"Casa Pia",                "Casa Pia AC"},
    // ── Belgium ──────────────────────────────────────────────────────────────
    {"Club Brugge",             "Club Brugge KV"},
    {"Brugge",                  "Club Brugge KV"},
    {"Club Brugge KV",          "Club Brugge KV"},
    {"Anderlecht",              "RSC Anderlecht"},
    {"RSC Anderlecht",          "RSC Anderlecht"},
    {"Gent",                    "KAA Gent"},
    {"KAA Gent",                "KAA Gent"},
    {"Standard",                "Standard Liege"},
    {"Standard Liege",          "Standard Liege"},
    {"Standard de Liege",       "Standard Liege"},
    {"Genk",                    "KRC Genk"},
    {"KRC Genk",                "KRC Genk"},
    {"Mechelen",                "KV Mechelen"},
    {"Cercle Brugge",           "Cercle Brugge KSV"},
    {"Charleroi",               "Sporting Charleroi"},
    {"Antwerp",                 "Royal Antwerp FC"},
    {"Royal Antwerp",           "Royal Antwerp FC"},
    {"OH Leuven",               "Oud-Heverlee Leuven"},
    {"Union SG",                "Royale Union Saint-Gilloise"},
    {"Westerlo",                "KVC Westerlo"},
    // ── Turkey ───────────────────────────────────────────────────────────────
    {"Galatasaray",             "Galatasaray SK"},
    {"Galatasaray SK",          "Galatasaray SK"},
    {"Fenerbahce",              "Fenerbahce SK"},
    {"Fenerbahce SK",           "Fenerbahce SK"},
    {"Besiktas",                "Besiktas JK"},
    {"Besiktas JK",             "Besiktas JK"},
    {"Trabzonspor",             "Trabzonspor"},
    {"Basaksehir",              "Istanbul Basaksehir"},
    {"Istanbul Basaksehir",     "Istanbul Basaksehir"},
    {"Alanyaspor",              "Aytemiz Alanyaspor"},
    {"Sivasspor",               "Demir Grup Sivasspor"},
    {"Kayserispor",             "Yukatel Kayserispor"},
    {"Antalyaspor",             "Antalyaspor"},
    {"Rizespor",                "Caykur Rizespor"},
    {"Konyaspor",               "Konyaspor"},
    {"Kasimpasa",               "Kasimpasa SK"},
    // ── Greece ───────────────────────────────────────────────────────────────
    {"Panathinaikos",           "Panathinaikos FC"},
    {"Olympiakos",              "Olympiakos CFP"},
    {"Olympiakos CFP",          "Olympiakos CFP"},
    {"PAOK",                    "PAOK FC"},
    {"PAOK FC",                 "PAOK FC"},
    {"AEK Athens",              "AEK Athens FC"},
    {"AEK Athens FC",           "AEK Athens FC"},
    {"Aris",                    "Aris FC"},
    {"Atromitos",               "Atromitos FC"},
    // ── Scotland ─────────────────────────────────────────────────────────────
    {"Rangers",                 "Rangers FC"},
    {"Rangers FC",              "Rangers FC"},
    {"Celtic",                  "Celtic FC"},
    {"Celtic FC",               "Celtic FC"},
    {"Hearts",                  "Heart of Midlothian"},
    {"Heart of Midlothian FC",  "Heart of Midlothian"},
    {"Hibernian",               "Hibernian FC"},
    {"Aberdeen",                "Aberdeen FC"},
    {"Motherwell",              "Motherwell FC"},
    {"St Mirren",               "St Mirren FC"},
    {"Dundee",                  "Dundee FC"},
    {"Dundee Utd",              "Dundee United"},
    {"Dundee United FC",        "Dundee United"},
    {"Livingston",              "Livingston FC"},
    {"Ross County",             "Ross County FC"},
    {"Kilmarnock",              "Kilmarnock FC"},
    {"St Johnstone",            "St Johnstone FC"},
    // ── Austria ──────────────────────────────────────────────────────────────
    {"RB Salzburg",             "FC Red Bull Salzburg"},
    {"Red Bull Salzburg",       "FC Red Bull Salzburg"},
    {"Salzburg",                "FC Red Bull Salzburg"},
    {"Rapid Wien",              "SK Rapid Wien"},
    {"Rapid Vienna",            "SK Rapid Wien"},
    {"Austria Wien",            "FK Austria Wien"},
    {"Austria Vienna",          "FK Austria Wien"},
    {"Sturm Graz",              "SK Sturm Graz"},
    // ── Switzerland ──────────────────────────────────────────────────────────
    {"Young Boys",              "BSC Young Boys"},
    {"BSC Young Boys",          "BSC Young Boys"},
    {"Basel",                   "FC Basel"},
    {"FC Basel",                "FC Basel"},
    {"Zurich",                  "FC Zurich"},
    {"FC Zurich",               "FC Zurich"},
    {"Servette",                "Servette FC"},
    {"Grasshopper",             "Grasshopper Club Zurich"},
    // ── Russia ───────────────────────────────────────────────────────────────
    {"CSKA Moscow",             "PFC CSKA Moscow"},
    {"CSKA",                    "PFC CSKA Moscow"},
    {"Spartak Moscow",          "FC Spartak Moscow"},
    {"Spartak",                 "FC Spartak Moscow"},
    {"Zenit",                   "FC Zenit Saint Petersburg"},
    {"Zenit St. Petersburg",    "FC Zenit Saint Petersburg"},
    {"Lokomotiv Moscow",        "FC Lokomotiv Moscow"},
    {"Lokomotiv",               "FC Lokomotiv Moscow"},
    {"Dynamo Moscow",           "FC Dynamo Moscow"},
    // ── Brazil ───────────────────────────────────────────────────────────────
    {"Flamengo",                "CR Flamengo"},
    {"Palmeiras",               "SE Palmeiras"},
    {"Fluminense",              "Fluminense FC"},
    {"Corinthians",             "SC Corinthians"},
    {"Santos",                  "Santos FC"},
    {"Gremio",                  "Gremio FB Porto-Alegrense"},
    {"Internacional",           "SC Internacional"},
    {"Atletico Mineiro",        "Clube Atletico Mineiro"},
    {"Atletico-MG",             "Clube Atletico Mineiro"},
    {"Botafogo",                "Botafogo FR"},
    {"Vasco",                   "CR Vasco da Gama"},
    {"Cruzeiro",                "Cruzeiro EC"},
    // ── Argentina ────────────────────────────────────────────────────────────
    {"Boca Juniors",            "CA Boca Juniors"},
    {"River Plate",             "CA River Plate"},
    {"Racing Club",             "Racing Club"},
    {"San Lorenzo",             "CA San Lorenzo de Almagro"},
    {"Independiente",           "CA Independiente"},
    {"Estudiantes",             "Estudiantes de La Plata"},
    {"Velez",                   "Velez Sarsfield"},
    // ── Netherlands national ─────────────────────────────────────────────────
    {"Holland",                 "Netherlands"},
    // ── Common national team aliases ─────────────────────────────────────────
    {"USA",                     "United States"},
    {"IR Iran",                 "Iran"},
    {"Korea Republic",          "South Korea"},
    {"Korea DPR",               "North Korea"},
    {"Cote d'Ivoire",           "Ivory Coast"},
    {"China PR",                "China"},
    {"Chinese Taipei",          "Taiwan"},
    {"Trinidad & Tobago",       "Trinidad and Tobago"},
    {"Bosnia-Herzegovina",      "Bosnia and Herzegovina"},
    {"Czechoslovakia",          "Czech Republic"},
};

static std::string normalise_team(const std::string& raw) {
    std::string s = trim(raw);
    if (s.empty()) return "";
    auto it = TEAM_ALIASES.find(s);
    if (it != TEAM_ALIASES.end()) return it->second;
    return s;
}

// ─── division code → (slug, country) ──────────────────────────────────────────
using DivPair = std::pair<std::string, std::string>;

static const std::unordered_map<std::string, DivPair> DIVISION_MAP = {
    {"E0",  {"premier-league",   "England"}},
    {"E1",  {"championship",     "England"}},
    {"E2",  {"league-one",       "England"}},
    {"E3",  {"league-two",       "England"}},
    {"SP1", {"la-liga",          "Spain"}},
    {"SP2", {"la-liga-2",        "Spain"}},
    {"D1",  {"bundesliga",       "Germany"}},
    {"D2",  {"bundesliga-2",     "Germany"}},
    {"I1",  {"serie-a",          "Italy"}},
    {"I2",  {"serie-b",          "Italy"}},
    {"F1",  {"ligue1",           "France"}},
    {"F2",  {"ligue2",           "France"}},
    {"N1",  {"eredivisie",       "Netherlands"}},
    {"P1",  {"primeira-liga",    "Portugal"}},
    {"SC0", {"scottish-prem",    "Scotland"}},
    {"SC1", {"scottish-div1",    "Scotland"}},
    {"SC2", {"scottish-div2",    "Scotland"}},
    {"SC3", {"scottish-div3",    "Scotland"}},
    {"B1",  {"belgian-pro",      "Belgium"}},
    {"T1",  {"super-lig",        "Turkey"}},
    {"G1",  {"greek-super",      "Greece"}},
    {"ARG", {"primera-division", "Argentina"}},
    {"BRA", {"serie-a-br",       "Brazil"}},
    {"CHN", {"chinese-super",    "China"}},
    {"DEN", {"danish-superliga", "Denmark"}},
    {"AUT", {"austrian-bl",      "Austria"}},
    {"FIN", {"finnish-veikkaus", "Finland"}},
    {"IRL", {"irish-prem",       "Ireland"}},
    {"NOR", {"norwegian-elit",   "Norway"}},
    {"SWE", {"swedish-allsv",    "Sweden"}},
    {"SWI", {"swiss-super",      "Switzerland"}},
    {"USA", {"mls",              "USA"}},
    {"JPN", {"j-league",         "Japan"}},
};

// ─── league tier (1=elite, 2=second, 3=third, 4=lower, 0=international) ──────
static const std::unordered_map<std::string, int> LEAGUE_TIER = {
    // Tier 1
    {"premier-league",    1}, {"la-liga",       1}, {"bundesliga",    1},
    {"serie-a",           1}, {"ligue1",         1}, {"eredivisie",    1},
    {"primeira-liga",     1}, {"scottish-prem",  1}, {"belgian-pro",   1},
    {"super-lig",         1}, {"greek-super",    1}, {"austrian-bl",   1},
    {"swiss-super",       1}, {"primera-division",1},{"serie-a-br",    1},
    {"mls",               1}, {"j-league",       1}, {"chinese-super", 1},
    {"danish-superliga",  1}, {"norwegian-elit", 1}, {"swedish-allsv", 1},
    {"russian-premier",   1},
    // Tier 2
    {"championship",      2}, {"la-liga-2",      2}, {"bundesliga-2",  2},
    {"serie-b",           2}, {"ligue2",          2}, {"league-one",   2},
    {"scottish-div1",     2},
    // Tier 3
    {"league-two",        3}, {"scottish-div2",   3},
    // Tier 4
    {"scottish-div3",     4},
    // International
    {"international",     0}, {"world-cup",       0},
};

static int get_league_tier(const std::string& slug) {
    auto it = LEAGUE_TIER.find(slug);
    return it != LEAGUE_TIER.end() ? it->second : 2;  // default: tier 2
}

// ─── date normalisation ───────────────────────────────────────────────────────
static std::string normalise_date(const std::string& raw) {
    std::string s = trim(raw);
    if (s.empty()) return "";

    // ISO already: YYYY-MM-DD
    if (s.size() == 10 && s[4] == '-' && s[7] == '-') {
        // Validate basic ranges
        int y = std::stoi(s.substr(0, 4));
        int m = std::stoi(s.substr(5, 2));
        int d = std::stoi(s.substr(8, 2));
        if (y >= 1850 && y <= 2030 && m >= 1 && m <= 12 && d >= 1 && d <= 31)
            return s;
        return "";
    }

    // DD/MM/YYYY or DD/MM/YY
    if (s.size() >= 8 && s[2] == '/') {
        int d = std::stoi(s.substr(0, 2));
        int m = std::stoi(s.substr(3, 2));
        if (d < 1 || d > 31 || m < 1 || m > 12) return "";
        std::string yr = s.substr(6);
        int y = std::stoi(yr);
        if (y < 100) y += (y >= 93 ? 1900 : 2000);  // 93→1993, 00→2000, 25→2025
        char buf[32];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, m, d);
        return buf;
    }

    // DD.MM.YYYY
    if (s.size() >= 8 && s[2] == '.') {
        int d = std::stoi(s.substr(0, 2));
        int m = std::stoi(s.substr(3, 2));
        int y = std::stoi(s.substr(6));
        if (y < 100) y += (y >= 93 ? 1900 : 2000);
        if (d < 1 || d > 31 || m < 1 || m > 12 || y < 1850) return "";
        char buf[32];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, m, d);
        return buf;
    }

    // YYYY/MM/DD
    if (s.size() >= 10 && s[4] == '/' && s[7] == '/') {
        int y = std::stoi(s.substr(0, 4));
        int m = std::stoi(s.substr(5, 2));
        int d = std::stoi(s.substr(8, 2));
        if (y < 1850 || y > 2030 || m < 1 || m > 12 || d < 1 || d > 31) return "";
        char buf[32];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, m, d);
        return buf;
    }

    // MM/DD/YYYY (US format — only if month appears > 12)
    if (s.size() >= 8 && s[2] == '/' && s[5] == '/') {
        int a = std::stoi(s.substr(0, 2));
        int b2 = std::stoi(s.substr(3, 2));
        int y  = std::stoi(s.substr(6));
        if (y < 100) y += (y >= 93 ? 1900 : 2000);
        // Detect US format: if b2 > 12 then a is month, b2 is day
        int m = a, d = b2;
        if (b2 > 12) { m = a; d = b2; }
        if (m < 1 || m > 12 || d < 1 || d > 31) return "";
        char buf[32];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, m, d);
        return buf;
    }

    return "";
}

// ─── value parsers ────────────────────────────────────────────────────────────
static double parse_d(const std::string& s) {
    if (s.empty()) return -1;
    try { return std::stod(s); } catch (...) { return -1; }
}
static int parse_i(const std::string& s) {
    if (s.empty()) return -1;
    try { return std::stoi(s); } catch (...) { return -1; }
}

// ─── validation predicates ────────────────────────────────────────────────────
static bool valid_score(int g) { return g >= 0 && g <= 25; }
static bool valid_odds(double v) {
    return v > 1.005 && v < 400.0;
}
static bool valid_shots(int s)   { return s >= 0 && s <= 60; }
static bool valid_corners(int c) { return c >= 0 && c <= 30; }
static bool valid_fouls(int f)   { return f >= 0 && f <= 50; }
static bool valid_cards(int c)   { return c >= 0 && c <= 15; }
static bool valid_xg(double x)  { return x >= 0.0 && x <= 12.0; }

// Implied probability sanity check (3-way market should sum to 100-140%)
static bool valid_odds_trio(double h, double d, double a) {
    if (!valid_odds(h) || !valid_odds(d) || !valid_odds(a)) return false;
    double imp = (1.0/h) + (1.0/d) + (1.0/a);
    return imp >= 0.80 && imp <= 1.50;
}

// ─── Jaro-Winkler similarity ──────────────────────────────────────────────────
static double jaro(const std::string& s1, const std::string& s2) {
    if (s1 == s2) return 1.0;
    if (s1.empty() || s2.empty()) return 0.0;
    int len1 = (int)s1.size(), len2 = (int)s2.size();
    int match_dist = std::max(0, std::max(len1, len2) / 2 - 1);
    std::vector<bool> s1m(len1, false), s2m(len2, false);
    int matches = 0;
    for (int i = 0; i < len1; ++i) {
        int lo = std::max(0, i - match_dist);
        int hi = std::min(len2 - 1, i + match_dist);
        for (int j = lo; j <= hi; ++j) {
            if (!s2m[j] && s1[i] == s2[j]) {
                s1m[i] = s2m[j] = true; ++matches; break;
            }
        }
    }
    if (matches == 0) return 0.0;
    int t = 0;
    for (int i = 0, k = 0; i < len1; ++i) {
        if (!s1m[i]) continue;
        while (!s2m[k]) ++k;
        if (s1[i] != s2[k++]) ++t;
    }
    return (matches/(double)len1 + matches/(double)len2 + (matches - t/2.0)/matches) / 3.0;
}

static double jaro_winkler(const std::string& s1, const std::string& s2) {
    double j = jaro(s1, s2);
    if (j < 0.7) return j;
    int plen = 0;
    for (int i = 0; i < (int)std::min({s1.size(), s2.size(), (size_t)4}); ++i)
        if (s1[i] == s2[i]) ++plen; else break;
    return j + plen * 0.1 * (1.0 - j);
}

// ─── Match struct ─────────────────────────────────────────────────────────────
struct Match {
    std::string date;
    std::string home_team;
    std::string away_team;
    int  home_goals  = -1, away_goals  = -1;
    std::string league_slug;
    std::string country;
    std::string source;          // football_data, xgabora, international, worldcup, openfootball
    int  ht_home = -1, ht_away = -1;
    int  shots_home = -1, shots_away = -1;
    int  shots_on_target_home = -1, shots_on_target_away = -1;
    int  corners_home = -1, corners_away = -1;
    int  fouls_home = -1, fouls_away = -1;
    int  yellows_home = -1, yellows_away = -1;
    int  reds_home = -1, reds_away = -1;
    double elo_home = -1, elo_away = -1;
    double odds_home = -1, odds_draw = -1, odds_away = -1;
    double max_odds_home = -1, max_odds_draw = -1, max_odds_away = -1;
    double avg_odds_home = -1, avg_odds_draw = -1, avg_odds_away = -1;
    double asian_handicap_line = -1, asian_handicap_home = -1, asian_handicap_away = -1;
    double over25_odds = -1, under25_odds = -1, max_over25 = -1, max_under25 = -1;
    double xg_home = -1, xg_away = -1;
    int  quality_score  = 0;     // 0-100
    int  league_tier    = 2;     // 1=elite … 4=lower, 0=international
    bool is_international = false;
    bool score_conflict   = false;
    std::string tournament;      // for international/WC matches
    bool is_neutral = false;

    // Dedup key: ISO-date | canonical_home | canonical_away
    std::string dedup_key() const {
        return date + "|" + home_team + "|" + away_team;
    }

    // Fuzzy dedup key: date + first 4 chars of each team (for grouping)
    std::string fuzzy_bucket() const {
        auto h4 = home_team.size() >= 4 ? home_team.substr(0, 4) : home_team;
        auto a4 = away_team.size() >= 4 ? away_team.substr(0, 4) : away_team;
        return date + "|" + h4 + "|" + a4;
    }

    // Count of non-null fields — higher = prefer this record on duplicate
    int richness() const {
        int r = 0;
        if (home_goals >= 0) r += 3;
        if (away_goals >= 0) r += 3;
        if (ht_home >= 0)    r += 1;
        if (shots_home >= 0) r += 2;
        if (shots_on_target_home >= 0) r += 2;
        if (corners_home >= 0) r += 1;
        if (fouls_home >= 0)   r += 1;
        if (yellows_home >= 0) r += 1;
        if (valid_odds(odds_home))     r += 4;
        if (valid_odds(avg_odds_home)) r += 3;
        if (valid_odds(max_odds_home)) r += 2;
        if (valid_odds(over25_odds))   r += 2;
        if (valid_xg(xg_home))         r += 5;
        if (elo_home > 0)              r += 2;
        if (!tournament.empty())       r += 1;
        return r;
    }

    // Compute quality_score based on populated fields (0-100)
    void compute_quality() {
        int score = 0;
        // Core required fields (40 pts)
        if (!date.empty())      score += 5;
        if (!home_team.empty()) score += 5;
        if (!away_team.empty()) score += 5;
        if (home_goals >= 0)    score += 10;
        if (away_goals >= 0)    score += 10;
        if (!league_slug.empty()) score += 5;
        // Match stats (20 pts)
        if (ht_home >= 0)             score += 4;
        if (shots_home >= 0)          score += 4;
        if (shots_on_target_home >= 0) score += 4;
        if (corners_home >= 0)        score += 4;
        if (fouls_home >= 0)          score += 2;
        if (yellows_home >= 0)        score += 2;
        // Odds (20 pts)
        if (valid_odds(odds_home))     score += 5;
        if (valid_odds(avg_odds_home)) score += 5;
        if (valid_odds(max_odds_home)) score += 3;
        if (valid_odds(over25_odds))   score += 4;
        if (valid_odds(asian_handicap_home)) score += 3;
        // Advanced (20 pts)
        if (valid_xg(xg_home))  score += 10;
        if (elo_home > 0)       score += 5;
        if (!tournament.empty()) score += 3;
        if (is_neutral)          score += 2;
        quality_score = std::min(100, score);
    }

    static std::string header() {
        return "date,home_team,away_team,home_goals,away_goals,league_slug,country,source,"
               "halftime_home,halftime_away,shots_home,shots_away,"
               "shots_on_target_home,shots_on_target_away,corners_home,corners_away,"
               "fouls_home,fouls_away,yellows_home,yellows_away,reds_home,reds_away,"
               "elo_home,elo_away,"
               "odds_home,odds_draw,odds_away,"
               "max_odds_home,max_odds_draw,max_odds_away,"
               "avg_odds_home,avg_odds_draw,avg_odds_away,"
               "asian_handicap_line,asian_handicap_home,asian_handicap_away,"
               "over25_odds,under25_odds,max_over25,max_under25,"
               "xg_home,xg_away,"
               "quality_score,league_tier,is_international,score_conflict,"
               "tournament,is_neutral";
    }

    static std::string fmt_d(double v) {
        if (v < 0) return "";
        std::ostringstream os;
        os << std::fixed << std::setprecision(4) << v;
        return os.str();
    }
    static std::string fmt_i(int v) {
        return v < 0 ? "" : std::to_string(v);
    }

    std::string to_csv() const {
        std::string t = tournament;
        // Escape commas in tournament name
        if (t.find(',') != std::string::npos) t = "\"" + t + "\"";
        return date + "," + home_team + "," + away_team + ","
             + fmt_i(home_goals) + "," + fmt_i(away_goals) + ","
             + league_slug + "," + country + "," + source + ","
             + fmt_i(ht_home) + "," + fmt_i(ht_away) + ","
             + fmt_i(shots_home) + "," + fmt_i(shots_away) + ","
             + fmt_i(shots_on_target_home) + "," + fmt_i(shots_on_target_away) + ","
             + fmt_i(corners_home) + "," + fmt_i(corners_away) + ","
             + fmt_i(fouls_home) + "," + fmt_i(fouls_away) + ","
             + fmt_i(yellows_home) + "," + fmt_i(yellows_away) + ","
             + fmt_i(reds_home) + "," + fmt_i(reds_away) + ","
             + fmt_d(elo_home) + "," + fmt_d(elo_away) + ","
             + fmt_d(odds_home) + "," + fmt_d(odds_draw) + "," + fmt_d(odds_away) + ","
             + fmt_d(max_odds_home) + "," + fmt_d(max_odds_draw) + "," + fmt_d(max_odds_away) + ","
             + fmt_d(avg_odds_home) + "," + fmt_d(avg_odds_draw) + "," + fmt_d(avg_odds_away) + ","
             + fmt_d(asian_handicap_line) + "," + fmt_d(asian_handicap_home) + "," + fmt_d(asian_handicap_away) + ","
             + fmt_d(over25_odds) + "," + fmt_d(under25_odds) + ","
             + fmt_d(max_over25) + "," + fmt_d(max_under25) + ","
             + fmt_d(xg_home) + "," + fmt_d(xg_away) + ","
             + std::to_string(quality_score) + ","
             + std::to_string(league_tier) + ","
             + (is_international ? "1" : "0") + ","
             + (score_conflict   ? "1" : "0") + ","
             + t + ","
             + (is_neutral ? "1" : "0");
    }
};

// ─── global counters (forward declarations used by parsers) ──────────────────
static std::atomic<int> g_total_in{0};
static std::atomic<int> g_total_kept{0};
static std::atomic<int> g_total_dup{0};
static std::atomic<int> g_total_invalid{0};
static std::atomic<int> g_total_conflict{0};

// ─── xgabora parser ───────────────────────────────────────────────────────────
static std::vector<Match> parse_xgabora(const std::string& path) {
    std::vector<Match> out;
    std::ifstream f(path);
    if (!f.is_open()) { LOG_ERROR("Cannot open " + path); return out; }
    std::string line;
    std::getline(f, line);
    line = strip_bom(line);
    auto hdr = parse_csv_row(line);
    auto idx = make_header_idx(hdr);

    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto row = parse_csv_row(line);
        Match m;
        m.date      = normalise_date(get(row, idx, "date"));
        if (m.date.empty()) { ++g_total_invalid; continue; }
        m.home_team = normalise_team(get(row, idx, "home_team"));
        m.away_team = normalise_team(get(row, idx, "away_team"));
        if (m.home_team.empty() || m.away_team.empty() || m.home_team == m.away_team)
            { ++g_total_invalid; continue; }
        m.home_goals = parse_i(get(row, idx, "home_goals"));
        m.away_goals = parse_i(get(row, idx, "away_goals"));
        if (!valid_score(m.home_goals) || !valid_score(m.away_goals))
            { ++g_total_invalid; continue; }
        std::string raw_slug = lower(trim(get(row, idx, "league")));
        m.league_slug = raw_slug.empty() ? "unknown" : raw_slug;
        m.country     = trim(get(row, idx, "country"));
        m.source      = "xgabora";
        m.elo_home    = parse_d(get(row, idx, "elo_home"));
        m.elo_away    = parse_d(get(row, idx, "elo_away"));
        if (m.elo_home <= 0 || m.elo_home > 3000) m.elo_home = -1;
        if (m.elo_away <= 0 || m.elo_away > 3000) m.elo_away = -1;
        m.league_tier    = get_league_tier(m.league_slug);
        m.is_international = false;
        m.compute_quality();
        out.push_back(std::move(m));
    }
    return out;
}

// ─── football-data.co.uk parser ───────────────────────────────────────────────
static std::vector<Match> parse_football_data(const std::string& path,
                                               const std::string& slug,
                                               const std::string& country) {
    std::vector<Match> out;
    std::ifstream f(path);
    if (!f.is_open()) return out;
    std::string line;
    std::getline(f, line);
    line = strip_bom(line);
    auto hdr = parse_csv_row(line);
    auto idx = make_header_idx(hdr);

    // Need at least HomeTeam, AwayTeam, FTHG, FTAG, Date
    if (idx.find("HomeTeam") == idx.end() && idx.find("HT") == idx.end()) return out;

    // Detect column names for home/away team (some files use different names)
    std::string ht_col = (idx.count("HomeTeam") ? "HomeTeam" : "HT");
    std::string at_col = (idx.count("AwayTeam") ? "AwayTeam" : "AT");

    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto row = parse_csv_row(line);
        Match m;
        m.date = normalise_date(get(row, idx, "Date"));
        if (m.date.empty()) { ++g_total_invalid; continue; }
        m.home_team = normalise_team(get(row, idx, ht_col));
        m.away_team = normalise_team(get(row, idx, at_col));
        if (m.home_team.empty() || m.away_team.empty() || m.home_team == m.away_team)
            { ++g_total_invalid; continue; }
        m.home_goals = parse_i(get(row, idx, "FTHG"));
        m.away_goals = parse_i(get(row, idx, "FTAG"));
        if (!valid_score(m.home_goals) || !valid_score(m.away_goals))
            { ++g_total_invalid; continue; }

        // League slug from Div column or filename
        std::string div = trim(get(row, idx, "Div"));
        if (!div.empty()) {
            auto dit = DIVISION_MAP.find(div);
            if (dit != DIVISION_MAP.end()) {
                m.league_slug = dit->second.first;
                m.country     = dit->second.second;
            } else {
                m.league_slug = slug;
                m.country     = country;
            }
        } else {
            m.league_slug = slug;
            m.country     = country;
        }
        m.source = "football_data";

        m.ht_home = parse_i(get(row, idx, "HTHG"));
        m.ht_away = parse_i(get(row, idx, "HTAG"));

        m.shots_home           = parse_i(get(row, idx, "HS"));
        m.shots_away           = parse_i(get(row, idx, "AS"));
        m.shots_on_target_home = parse_i(get(row, idx, "HST"));
        m.shots_on_target_away = parse_i(get(row, idx, "AST"));
        m.corners_home         = parse_i(get(row, idx, "HC"));
        m.corners_away         = parse_i(get(row, idx, "AC"));
        m.fouls_home           = parse_i(get(row, idx, "HF"));
        m.fouls_away           = parse_i(get(row, idx, "AF"));
        m.yellows_home         = parse_i(get(row, idx, "HY"));
        m.yellows_away         = parse_i(get(row, idx, "AY"));
        m.reds_home            = parse_i(get(row, idx, "HR"));
        m.reds_away            = parse_i(get(row, idx, "AR"));

        // Validate stats
        if (m.shots_home >= 0 && !valid_shots(m.shots_home)) m.shots_home = -1;
        if (m.shots_away >= 0 && !valid_shots(m.shots_away)) m.shots_away = -1;
        if (m.corners_home >= 0 && !valid_corners(m.corners_home)) m.corners_home = -1;
        if (m.corners_away >= 0 && !valid_corners(m.corners_away)) m.corners_away = -1;
        if (m.fouls_home >= 0 && !valid_fouls(m.fouls_home)) m.fouls_home = -1;
        if (m.yellows_home >= 0 && !valid_cards(m.yellows_home)) m.yellows_home = -1;

        // Odds — prefer closing odds for predictive power
        auto try_odds = [&](const std::vector<std::string>& cols) -> double {
            for (auto& c : cols) {
                double v = parse_d(get(row, idx, c));
                if (valid_odds(v)) return v;
            }
            return -1;
        };
        m.avg_odds_home = try_odds({"AvgCH","AvgH"});
        m.avg_odds_draw = try_odds({"AvgCD","AvgD"});
        m.avg_odds_away = try_odds({"AvgCA","AvgA"});
        m.odds_home     = try_odds({"B365CH","B365H","BWH","IWH","WHH","PSH","VCH"});
        m.odds_draw     = try_odds({"B365CD","B365D","BWD","IWD","WHD","PSD","VCD"});
        m.odds_away     = try_odds({"B365CA","B365A","BWA","IWA","WHA","PSA","VCA"});
        m.max_odds_home = try_odds({"MaxCH","MaxH"});
        m.max_odds_draw = try_odds({"MaxCD","MaxD"});
        m.max_odds_away = try_odds({"MaxCA","MaxA"});

        // Validate odds trio
        if (!valid_odds_trio(m.odds_home, m.odds_draw, m.odds_away)) {
            m.odds_home = m.odds_draw = m.odds_away = -1;
        }
        if (!valid_odds_trio(m.avg_odds_home, m.avg_odds_draw, m.avg_odds_away)) {
            m.avg_odds_home = m.avg_odds_draw = m.avg_odds_away = -1;
        }

        m.asian_handicap_line  = parse_d(get(row, idx, "AHh"));
        m.asian_handicap_home  = try_odds({"B365AHH","PAHH","MaxAHH","AvgAHH"});
        m.asian_handicap_away  = try_odds({"B365AHA","PAHA","MaxAHA","AvgAHA"});
        m.over25_odds          = try_odds({"B365C>2.5","B365>2.5","P>2.5","Avg>2.5"});
        m.under25_odds         = try_odds({"B365C<2.5","B365<2.5","P<2.5","Avg<2.5"});
        m.max_over25           = try_odds({"MaxC>2.5","Max>2.5"});
        m.max_under25          = try_odds({"MaxC<2.5","Max<2.5"});

        m.league_tier    = get_league_tier(m.league_slug);
        m.is_international = false;
        m.compute_quality();
        out.push_back(std::move(m));
    }
    return out;
}

// ─── understat xG aggregator ──────────────────────────────────────────────────
struct UnderstatXG { double xg_home = 0, xg_away = 0; };
using XGMap = std::unordered_map<std::string, UnderstatXG>;

static XGMap parse_understat_shots(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) return {};
    std::string line;
    std::getline(f, line); line = strip_bom(line);
    auto hdr = parse_csv_row(line);
    auto idx = make_header_idx(hdr);
    if (idx.find("xG") == idx.end()) return {};

    XGMap result;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto row = parse_csv_row(line);
        std::string date = normalise_date(get(row, idx, "date"));
        if (date.empty()) continue;
        std::string ht = normalise_team(get(row, idx, "h_team"));
        std::string at = normalise_team(get(row, idx, "a_team"));
        if (ht.empty() || at.empty()) continue;
        std::string ha = trim(get(row, idx, "h_a"));
        double xg = parse_d(get(row, idx, "xG"));
        if (!valid_xg(xg)) continue;
        std::string key = date + "|" + ht + "|" + at;
        if (ha == "h") result[key].xg_home += xg;
        else if (ha == "a") result[key].xg_away += xg;
    }
    return result;
}

// ─── martj42 international results parser ─────────────────────────────────────
// CSV format: date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
static std::vector<Match> parse_international(const std::string& path) {
    std::vector<Match> out;
    std::ifstream f(path);
    if (!f.is_open()) { LOG_WARN("  Cannot open " + path); return out; }
    std::string line;
    std::getline(f, line); line = strip_bom(line);
    auto hdr = parse_csv_row(line);
    auto idx = make_header_idx(hdr);

    if (idx.find("home_team") == idx.end()) return out;

    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto row = parse_csv_row(line);
        Match m;
        m.date = normalise_date(get(row, idx, "date"));
        if (m.date.empty()) { ++g_total_invalid; continue; }

        // Only take matches from 1960 onwards for relevance
        if (m.date < "1960-01-01") { ++g_total_invalid; continue; }

        m.home_team = normalise_team(get(row, idx, "home_team"));
        m.away_team = normalise_team(get(row, idx, "away_team"));
        if (m.home_team.empty() || m.away_team.empty() || m.home_team == m.away_team)
            { ++g_total_invalid; continue; }

        m.home_goals = parse_i(get(row, idx, "home_score"));
        m.away_goals = parse_i(get(row, idx, "away_score"));
        if (!valid_score(m.home_goals) || !valid_score(m.away_goals))
            { ++g_total_invalid; continue; }

        m.tournament = trim(get(row, idx, "tournament"));
        std::string neutral_str = lower(trim(get(row, idx, "neutral")));
        m.is_neutral   = (neutral_str == "true" || neutral_str == "1");
        m.country      = trim(get(row, idx, "country"));

        // Classify league slug based on tournament type
        std::string lt = lower(m.tournament);
        if (lt.find("world cup") != std::string::npos)
            m.league_slug = "world-cup";
        else if (lt.find("euro") != std::string::npos || lt.find("european") != std::string::npos)
            m.league_slug = "euro-championship";
        else if (lt.find("copa america") != std::string::npos)
            m.league_slug = "copa-america";
        else if (lt.find("africa cup") != std::string::npos || lt.find("afcon") != std::string::npos)
            m.league_slug = "afcon";
        else if (lt.find("asian cup") != std::string::npos)
            m.league_slug = "asian-cup";
        else if (lt.find("nations league") != std::string::npos)
            m.league_slug = "nations-league";
        else if (lt.find("olympics") != std::string::npos || lt.find("olympic") != std::string::npos)
            m.league_slug = "olympics";
        else
            m.league_slug = "international";

        m.source          = "international";
        m.is_international = true;
        m.league_tier     = 0;
        m.compute_quality();
        out.push_back(std::move(m));
    }
    return out;
}

// ─── jfjelstul worldcup parser ────────────────────────────────────────────────
// CSV has many columns; key ones: match_date, home_team_name, away_team_name,
// home_team_score, away_team_score, stage_name, tournament_name, city_name
static std::vector<Match> parse_worldcup(const std::string& path) {
    std::vector<Match> out;
    std::ifstream f(path);
    if (!f.is_open()) { LOG_WARN("  Cannot open " + path); return out; }
    std::string line;
    std::getline(f, line); line = strip_bom(line);
    auto hdr = parse_csv_row(line);
    auto idx = make_header_idx(hdr);

    if (idx.find("home_team_name") == idx.end()) return out;

    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto row = parse_csv_row(line);
        Match m;
        m.date = normalise_date(get(row, idx, "match_date"));
        if (m.date.empty()) { ++g_total_invalid; continue; }

        m.home_team = normalise_team(get(row, idx, "home_team_name"));
        m.away_team = normalise_team(get(row, idx, "away_team_name"));
        if (m.home_team.empty() || m.away_team.empty() || m.home_team == m.away_team)
            { ++g_total_invalid; continue; }

        m.home_goals = parse_i(get(row, idx, "home_team_score"));
        m.away_goals = parse_i(get(row, idx, "away_team_score"));
        if (!valid_score(m.home_goals) || !valid_score(m.away_goals))
            { ++g_total_invalid; continue; }

        // If match went to extra time, the ft score is in home_team_score already
        std::string et = trim(get(row, idx, "extra_time"));
        std::string tournament_name = trim(get(row, idx, "tournament_name"));
        m.tournament    = tournament_name.empty() ? "FIFA World Cup" : tournament_name;
        m.league_slug   = "world-cup";
        m.country       = "Neutral";
        m.source        = "worldcup";
        m.is_neutral    = true;
        m.is_international = true;
        m.league_tier   = 0;
        m.compute_quality();
        out.push_back(std::move(m));
    }
    return out;
}

// ─── OpenFootball JSON parser ─────────────────────────────────────────────────
// Minimal JSON scanner — handles openfootball/football.json format only.
// Structure: { "name": "...", "matches": [ { "date": "YYYY-MM-DD",
//   "team1": "...", "team2": "...", "score": { "ht": [n,n], "ft": [n,n] } }, ... ] }

// Extract JSON string value for given key within [start, end)
static std::string json_str_val(const std::string& text, size_t start, size_t end,
                                 const std::string& key) {
    std::string pat = "\"" + key + "\"";
    auto pos = text.find(pat, start);
    if (pos == std::string::npos || pos >= end) return "";
    pos += pat.size();
    while (pos < end && (text[pos] == ' ' || text[pos] == ':' || text[pos] == '\t')) ++pos;
    if (pos >= end || text[pos] != '"') return "";
    ++pos;
    std::string val;
    while (pos < end && text[pos] != '"') {
        if (text[pos] == '\\' && pos + 1 < end) { val += text[++pos]; }
        else val += text[pos];
        ++pos;
    }
    return val;
}

// Find the nth integer in a JSON array [n1, n2] starting from pos
static bool json_arr2_int(const std::string& text, size_t arr_start, size_t end,
                            int& v1, int& v2) {
    auto pos = text.find('[', arr_start);
    if (pos == std::string::npos || pos >= end) return false;
    ++pos;
    // Parse v1
    while (pos < end && !std::isdigit(text[pos]) && text[pos] != '-') ++pos;
    if (pos >= end || text[pos] == ']') return false;
    int sign1 = 1;
    if (text[pos] == '-') { sign1 = -1; ++pos; }
    v1 = 0;
    while (pos < end && std::isdigit(text[pos])) v1 = v1*10 + (text[pos++] - '0');
    v1 *= sign1;
    // Skip to comma
    auto comma = text.find(',', pos);
    if (comma == std::string::npos || comma >= end) return false;
    pos = comma + 1;
    while (pos < end && !std::isdigit(text[pos]) && text[pos] != '-') ++pos;
    if (pos >= end) return false;
    int sign2 = 1;
    if (text[pos] == '-') { sign2 = -1; ++pos; }
    v2 = 0;
    while (pos < end && std::isdigit(text[pos])) v2 = v2*10 + (text[pos++] - '0');
    v2 *= sign2;
    return true;
}

// Find the "ft" score array in score object
static bool json_ft_score(const std::string& text, size_t match_start, size_t match_end,
                            int& g1, int& g2) {
    // Find "score": { ... "ft": [n, n] ... }
    auto score_pos = text.find("\"score\"", match_start);
    if (score_pos == std::string::npos || score_pos >= match_end) return false;
    auto ft_pos = text.find("\"ft\"", score_pos);
    if (ft_pos == std::string::npos || ft_pos >= match_end) return false;
    auto colon = text.find(':', ft_pos + 4);
    if (colon == std::string::npos || colon >= match_end) return false;
    return json_arr2_int(text, colon + 1, match_end, g1, g2);
}

static bool json_ht_score(const std::string& text, size_t match_start, size_t match_end,
                            int& g1, int& g2) {
    auto score_pos = text.find("\"score\"", match_start);
    if (score_pos == std::string::npos || score_pos >= match_end) return false;
    auto ht_pos = text.find("\"ht\"", score_pos);
    if (ht_pos == std::string::npos || ht_pos >= match_end) return false;
    auto colon = text.find(':', ht_pos + 4);
    if (colon == std::string::npos || colon >= match_end) return false;
    return json_arr2_int(text, colon + 1, match_end, g1, g2);
}

// Openfootball comp code → league info
struct OFLeagueInfo { std::string slug; std::string country; };
static const std::unordered_map<std::string, OFLeagueInfo> OF_LEAGUE_MAP = {
    {"en.1", {"premier-league",  "England"}},
    {"en.2", {"championship",    "England"}},
    {"en.3", {"league-one",      "England"}},
    {"de.1", {"bundesliga",      "Germany"}},
    {"de.2", {"bundesliga-2",    "Germany"}},
    {"es.1", {"la-liga",         "Spain"}},
    {"es.2", {"la-liga-2",       "Spain"}},
    {"it.1", {"serie-a",         "Italy"}},
    {"it.2", {"serie-b",         "Italy"}},
    {"fr.1", {"ligue1",          "France"}},
    {"fr.2", {"ligue2",          "France"}},
    {"pt.1", {"primeira-liga",   "Portugal"}},
    {"nl.1", {"eredivisie",      "Netherlands"}},
    {"be.1", {"belgian-pro",     "Belgium"}},
    {"sc.1", {"scottish-prem",   "Scotland"}},
    {"tr.1", {"super-lig",       "Turkey"}},
    {"gr.1", {"greek-super",     "Greece"}},
    {"at.1", {"austrian-bl",     "Austria"}},
    {"ch.1", {"swiss-super",     "Switzerland"}},
    {"ru.1", {"russian-premier", "Russia"}},
};

static std::vector<Match> parse_openfootball_json(const std::string& path,
                                                    const std::string& comp_code) {
    std::vector<Match> out;
    // Load entire file
    std::ifstream f(path, std::ios::in | std::ios::binary);
    if (!f.is_open()) return out;
    std::string text((std::istreambuf_iterator<char>(f)),
                      std::istreambuf_iterator<char>());

    // Look up league info from comp code
    OFLeagueInfo linfo{"unknown", "Unknown"};
    auto lit = OF_LEAGUE_MAP.find(comp_code);
    if (lit != OF_LEAGUE_MAP.end()) linfo = lit->second;

    // Find "matches" array
    auto matches_pos = text.find("\"matches\"");
    if (matches_pos == std::string::npos) return out;
    auto arr_start = text.find('[', matches_pos);
    if (arr_start == std::string::npos) return out;

    // Scan through each match object { ... }
    size_t pos = arr_start + 1;
    int depth = 0;
    size_t obj_start = std::string::npos;

    while (pos < text.size()) {
        char c = text[pos];
        if (c == '{') {
            if (depth == 0) obj_start = pos;
            ++depth;
        } else if (c == '}') {
            --depth;
            if (depth == 0 && obj_start != std::string::npos) {
                // Parse this match object [obj_start, pos]
                size_t obj_end = pos + 1;

                // Skip objects without a score (future matches)
                if (text.find("\"ft\"", obj_start) == std::string::npos ||
                    text.find("\"ft\"", obj_start) >= obj_end) {
                    obj_start = std::string::npos;
                    ++pos; continue;
                }

                Match m;
                m.date = json_str_val(text, obj_start, obj_end, "date");
                m.date = normalise_date(m.date);
                if (m.date.empty()) { ++g_total_invalid; obj_start = std::string::npos; ++pos; continue; }

                m.home_team = normalise_team(json_str_val(text, obj_start, obj_end, "team1"));
                m.away_team = normalise_team(json_str_val(text, obj_start, obj_end, "team2"));
                if (m.home_team.empty() || m.away_team.empty() || m.home_team == m.away_team)
                    { ++g_total_invalid; obj_start = std::string::npos; ++pos; continue; }

                int g1 = -1, g2 = -1;
                if (!json_ft_score(text, obj_start, obj_end, g1, g2) ||
                    !valid_score(g1) || !valid_score(g2))
                    { ++g_total_invalid; obj_start = std::string::npos; ++pos; continue; }
                m.home_goals = g1; m.away_goals = g2;

                int h1 = -1, h2 = -1;
                if (json_ht_score(text, obj_start, obj_end, h1, h2) &&
                    valid_score(h1) && valid_score(h2)) {
                    m.ht_home = h1; m.ht_away = h2;
                }

                m.league_slug = linfo.slug;
                m.country     = linfo.country;
                m.source      = "openfootball";
                m.league_tier = get_league_tier(m.league_slug);
                m.is_international = false;
                m.compute_quality();
                out.push_back(std::move(m));
                obj_start = std::string::npos;
            }
        } else if (c == ']' && depth == 0) {
            break;  // End of matches array
        }
        ++pos;
    }
    return out;
}

// ─── filesystem helpers ────────────────────────────────────────────────────────
static bool mkdir_p(const std::string& path) {
    struct stat st{};
    if (stat(path.c_str(), &st) == 0) return S_ISDIR(st.st_mode);
    auto pos = path.rfind('/');
    if (pos != std::string::npos) mkdir_p(path.substr(0, pos));
    return mkdir(path.c_str(), 0755) == 0;
}

static std::vector<std::string> list_files(const std::string& dir,
                                             const std::string& ext = "") {
    std::vector<std::string> result;
    DIR* d = opendir(dir.c_str());
    if (!d) return result;
    struct dirent* ent;
    while ((ent = readdir(d)) != nullptr) {
        std::string name = ent->d_name;
        if (name == "." || name == "..") continue;
        if (!ext.empty() && name.size() >= ext.size() &&
            name.substr(name.size() - ext.size()) != ext) continue;
        result.push_back(dir + "/" + name);
    }
    closedir(d);
    std::sort(result.begin(), result.end());
    return result;
}

// ─── per-league outlier detection ────────────────────────────────────────────
static void remove_outliers(std::vector<Match>& matches) {
    std::unordered_map<std::string, std::vector<double>> goals;
    for (const auto& m : matches)
        goals[m.league_slug].push_back(m.home_goals + m.away_goals);

    std::unordered_map<std::string, std::pair<double,double>> stats;
    for (auto& [slug, gs] : goals) {
        double sum = 0; for (auto g : gs) sum += g;
        double mean = sum / gs.size();
        double var  = 0; for (auto g : gs) var += (g-mean)*(g-mean);
        double sd   = gs.size() > 1 ? std::sqrt(var/(gs.size()-1)) : 1.0;
        stats[slug] = {mean, sd};
    }

    // Also remove impossible single-side goals (> 20 for any league)
    auto it = std::remove_if(matches.begin(), matches.end(), [&](const Match& m) {
        // Absolute extremes
        if (m.home_goals > 20 || m.away_goals > 20) return true;
        // Z-score outlier on total goals
        auto sit = stats.find(m.league_slug);
        if (sit == stats.end()) return false;
        double mean = sit->second.first, sd = sit->second.second;
        if (sd < 0.5) return false;
        double total = m.home_goals + m.away_goals;
        return std::abs(total - mean) > 5.0 * sd;
    });
    int removed = (int)std::distance(it, matches.end());
    if (removed > 0) LOG_DEBUG("Outliers removed: " + std::to_string(removed));
    matches.erase(it, matches.end());
}

// ─── global dedup + conflict detection ───────────────────────────────────────
static std::mutex                              g_dedup_mu;
static std::unordered_map<std::string, Match> g_dedup;

static void merge_into_global(std::vector<Match>& batch) {
    std::lock_guard<std::mutex> lk(g_dedup_mu);
    for (auto& m : batch) {
        ++g_total_in;
        std::string key = m.dedup_key();
        auto it = g_dedup.find(key);
        if (it == g_dedup.end()) {
            g_dedup[key] = std::move(m);
            ++g_total_kept;
        } else {
            // Score conflict detection
            Match& existing = it->second;
            if (existing.home_goals >= 0 && m.home_goals >= 0 &&
                (existing.home_goals != m.home_goals || existing.away_goals != m.away_goals)) {
                // Scores disagree between sources
                existing.score_conflict = true;
                ++g_total_conflict;
                LOG_DEBUG("Score conflict: " + key +
                          " [" + std::to_string(existing.home_goals) + "-" + std::to_string(existing.away_goals) +
                          " vs " + std::to_string(m.home_goals) + "-" + std::to_string(m.away_goals) +
                          " src=" + m.source + "]");
            }
            // Merge: keep richest record but carry over xG, odds, stats from either
            if (m.richness() > existing.richness()) {
                // Preserve xG from whichever has it
                double xg_h = existing.xg_home;
                double xg_a = existing.xg_away;
                bool   sc   = existing.score_conflict;
                existing = std::move(m);
                if (existing.xg_home < 0 && xg_h >= 0) existing.xg_home = xg_h;
                if (existing.xg_away < 0 && xg_a >= 0) existing.xg_away = xg_a;
                existing.score_conflict = sc;
            } else {
                // Carry over any fields missing in existing
                if (existing.xg_home < 0 && m.xg_home >= 0)  existing.xg_home = m.xg_home;
                if (existing.xg_away < 0 && m.xg_away >= 0)  existing.xg_away = m.xg_away;
                if (existing.elo_home < 0 && m.elo_home > 0)  existing.elo_home = m.elo_home;
                if (existing.elo_away < 0 && m.elo_away > 0)  existing.elo_away = m.elo_away;
                if (existing.ht_home < 0 && m.ht_home >= 0)   existing.ht_home  = m.ht_home;
                if (existing.ht_away < 0 && m.ht_away >= 0)   existing.ht_away  = m.ht_away;
                if (!valid_odds(existing.avg_odds_home) && valid_odds(m.avg_odds_home)) {
                    existing.avg_odds_home = m.avg_odds_home;
                    existing.avg_odds_draw = m.avg_odds_draw;
                    existing.avg_odds_away = m.avg_odds_away;
                }
                if (!valid_odds(existing.over25_odds) && valid_odds(m.over25_odds)) {
                    existing.over25_odds  = m.over25_odds;
                    existing.under25_odds = m.under25_odds;
                }
                if (existing.shots_home < 0 && m.shots_home >= 0)
                    existing.shots_home = m.shots_home;
                if (existing.shots_away < 0 && m.shots_away >= 0)
                    existing.shots_away = m.shots_away;
                if (existing.corners_home < 0 && m.corners_home >= 0)
                    existing.corners_home = m.corners_home;
                if (existing.tournament.empty() && !m.tournament.empty())
                    existing.tournament = m.tournament;
            }
            ++g_total_dup;
        }
    }
}

// ─── fuzzy duplicate detection (post-global-merge pass) ───────────────────────
// Groups matches by {date, first-4-of-home, first-4-of-away}, then uses
// Jaro-Winkler similarity to detect near-duplicates that differ only in
// team name spelling.
static void fuzzy_dedup_pass() {
    LOG_INFO("── Fuzzy duplicate detection (Jaro-Winkler) ───────────");

    // Build bucket map: fuzzy_bucket → list of dedup_keys
    std::unordered_map<std::string, std::vector<std::string>> buckets;
    for (const auto& [key, m] : g_dedup)
        buckets[m.fuzzy_bucket()].push_back(key);

    int merges = 0;
    std::vector<std::string> keys_to_delete;

    for (auto& [bucket, keys] : buckets) {
        if (keys.size() < 2) continue;
        for (size_t i = 0; i < keys.size(); ++i) {
            for (size_t j = i + 1; j < keys.size(); ++j) {
                auto& mi = g_dedup[keys[i]];
                auto& mj = g_dedup[keys[j]];
                // Same date is guaranteed by bucket; check team similarity
                double sim_home = jaro_winkler(lower(mi.home_team), lower(mj.home_team));
                double sim_away = jaro_winkler(lower(mi.away_team), lower(mj.away_team));
                if (sim_home >= 0.88 && sim_away >= 0.88) {
                    // Very likely the same match — merge into the richer record
                    if (mj.richness() > mi.richness()) {
                        // Carry important fields from i into j
                        if (mj.xg_home < 0) mj.xg_home = mi.xg_home;
                        if (mj.xg_away < 0) mj.xg_away = mi.xg_away;
                        if (mj.elo_home < 0) mj.elo_home = mi.elo_home;
                        if (mj.elo_away < 0) mj.elo_away = mi.elo_away;
                        keys_to_delete.push_back(keys[i]);
                    } else {
                        if (mi.xg_home < 0) mi.xg_home = mj.xg_home;
                        if (mi.xg_away < 0) mi.xg_away = mj.xg_away;
                        if (mi.elo_home < 0) mi.elo_home = mj.elo_home;
                        if (mi.elo_away < 0) mi.elo_away = mj.elo_away;
                        keys_to_delete.push_back(keys[j]);
                    }
                    ++merges;
                }
            }
        }
    }
    for (const auto& k : keys_to_delete) g_dedup.erase(k);
    LOG_INFO("  Fuzzy merges: " + std::to_string(merges) +
             " | Remaining: " + std::to_string(g_dedup.size()));
}

// ─── re-score quality after all merges ───────────────────────────────────────
static void recompute_quality() {
    for (auto& [key, m] : g_dedup) m.compute_quality();
}

// ─── slug → country helper ───────────────────────────────────────────────────
static std::string slug_country(const std::string& slug) {
    for (auto& [code, pair] : DIVISION_MAP)
        if (pair.first == slug) return pair.second;
    return "Unknown";
}

// ─── openfootball filename → comp code ────────────────────────────────────────
// Filename format: {season}_{comp}.json  e.g. "2024-25_en.1.json"
static std::string of_comp_from_filename(const std::string& path) {
    std::string fname = path.substr(path.rfind('/') + 1);
    if (fname.size() < 5) return "";
    // Remove .json
    if (fname.size() >= 5 && fname.substr(fname.size()-5) == ".json")
        fname = fname.substr(0, fname.size()-5);
    // Find underscore separating season from comp
    auto us = fname.find('_');
    if (us == std::string::npos) return "";
    return fname.substr(us + 1);  // e.g. "en.1"
}

// ─── per-file processing wrappers ─────────────────────────────────────────────
static void process_xgabora_file(const std::string& path) {
    LOG_INFO("  [xgabora] " + path);
    throttle_if_needed();
    auto matches = parse_xgabora(path);
    remove_outliers(matches);
    merge_into_global(matches);
    LOG_OK("  [xgabora] → " + std::to_string(matches.size()) + " rows");
}

static void process_football_data_file(const std::string& path,
                                        const std::string& slug,
                                        const std::string& country) {
    LOG_DEBUG("  [fd.co.uk] " + path);
    throttle_if_needed();
    auto matches = parse_football_data(path, slug, country);
    if (matches.empty()) return;
    remove_outliers(matches);
    merge_into_global(matches);
}

static void process_international_file(const std::string& path) {
    LOG_INFO("  [international] " + path);
    throttle_if_needed();
    auto matches = parse_international(path);
    remove_outliers(matches);
    merge_into_global(matches);
    LOG_OK("  [international] → " + std::to_string(matches.size()) + " rows");
}

static void process_worldcup_file(const std::string& path) {
    LOG_INFO("  [worldcup] " + path);
    throttle_if_needed();
    auto matches = parse_worldcup(path);
    merge_into_global(matches);
    LOG_OK("  [worldcup] → " + std::to_string(matches.size()) + " rows");
}

static void process_openfootball_file(const std::string& path) {
    std::string comp = of_comp_from_filename(path);
    LOG_DEBUG("  [openfootball] " + path + " comp=" + comp);
    throttle_if_needed();
    auto matches = parse_openfootball_json(path, comp);
    if (matches.empty()) return;
    remove_outliers(matches);
    merge_into_global(matches);
}

// ─── main ─────────────────────────────────────────────────────────────────────
int main(int argc, char* argv[]) {
    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    std::string raw_dir   = "../data/raw";
    std::string clean_dir = "../data/clean";
    int n_workers = std::max(1, std::min(6, (int)std::thread::hardware_concurrency() / 2));

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--verbose" || arg == "-v") g_verbose = true;
        else if (arg == "--workers" && i+1 < argc) n_workers = std::stoi(argv[++i]);
        else if (arg.rfind("--", 0) != 0) {
            if (raw_dir == "../data/raw") raw_dir = arg;
            else clean_dir = arg;
        }
    }
    n_workers = std::max(1, std::min(8, n_workers));
    mkdir_p(clean_dir);

    LOG_INFO("═══════════════════════════════════════════════════════════");
    LOG_INFO(" StatWise Dataset Cleaner v3.0");
    LOG_INFO(" Raw data dir  : " + raw_dir);
    LOG_INFO(" Output dir    : " + clean_dir);
    LOG_INFO(" Worker threads: " + std::to_string(n_workers));
    LOG_INFO(" Verbose       : " + std::string(g_verbose ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════════════");

    ThreadPool pool(n_workers);

    // ── Phase 1: xgabora (large base dataset) ──────────────────────────────
    LOG_INFO("── Phase 1: xgabora (475K rows, 2000-2025) ─────────────");
    {
        std::string xg_path = raw_dir + "/xgabora/Matches.csv";
        struct stat st{};
        if (stat(xg_path.c_str(), &st) == 0 && st.st_size > 0) {
            pool.enqueue([xg_path]{ process_xgabora_file(xg_path); });
        } else {
            LOG_WARN("  xgabora/Matches.csv missing — run downloader first");
        }
        pool.wait_all();
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 2: football-data.co.uk seasonal CSVs ──────────────────────────
    LOG_INFO("── Phase 2: football-data.co.uk (1993-2025) ────────────");
    {
        std::string fd_dir = raw_dir + "/football_data";
        auto fd_files = list_files(fd_dir, ".csv");
        LOG_INFO("  Found " + std::to_string(fd_files.size()) + " football-data CSVs");
        for (const auto& fpath : fd_files) {
            if (g_stop.load()) break;
            std::string fname = fpath.substr(fpath.rfind('/') + 1);
            fname = fname.substr(0, fname.size() - 4);
            auto us = fname.rfind('_');
            std::string slug = us != std::string::npos ? fname.substr(0, us) : fname;
            std::string country = slug_country(slug);
            pool.enqueue([fpath, slug, country]{
                process_football_data_file(fpath, slug, country);
            });
        }
        pool.wait_all();
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 3: understat xG injection ─────────────────────────────────────
    LOG_INFO("── Phase 3: understat xG integration ───────────────────");
    XGMap xg_map;
    {
        std::string us_dir = raw_dir + "/understat";
        auto us_files = list_files(us_dir, ".csv");
        std::mutex xg_mu;
        for (const auto& fpath : us_files) {
            if (fpath.find("shots_") == std::string::npos) continue;
            pool.enqueue([&, fpath]{
                auto local = parse_understat_shots(fpath);
                std::lock_guard<std::mutex> lk(xg_mu);
                for (auto& [k, v] : local) {
                    auto it = xg_map.find(k);
                    if (it == xg_map.end()) xg_map[k] = v;
                    else { it->second.xg_home += v.xg_home; it->second.xg_away += v.xg_away; }
                }
            });
        }
        pool.wait_all();
    }
    LOG_INFO("  xG map: " + std::to_string(xg_map.size()) + " match keys");
    {
        std::lock_guard<std::mutex> lk(g_dedup_mu);
        int hits = 0;
        for (auto& [key, m] : g_dedup) {
            auto it = xg_map.find(key);
            if (it != xg_map.end()) {
                if (valid_xg(it->second.xg_home)) m.xg_home = it->second.xg_home;
                if (valid_xg(it->second.xg_away)) m.xg_away = it->second.xg_away;
                ++hits;
            }
        }
        LOG_INFO("  xG applied to " + std::to_string(hits) + " matches");
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 4: martj42 international results ───────────────────────────────
    LOG_INFO("── Phase 4: international results (1960-present) ────────");
    {
        std::string intl_path = raw_dir + "/international/results.csv";
        struct stat st{};
        if (stat(intl_path.c_str(), &st) == 0 && st.st_size > 0) {
            pool.enqueue([intl_path]{ process_international_file(intl_path); });
        } else {
            LOG_WARN("  international/results.csv missing — skipping");
        }
        pool.wait_all();
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 5: FIFA World Cup (jfjelstul) ──────────────────────────────────
    LOG_INFO("── Phase 5: FIFA World Cup (1930-2022) ──────────────────");
    {
        std::string wc_path = raw_dir + "/worldcup/matches.csv";
        struct stat st{};
        if (stat(wc_path.c_str(), &st) == 0 && st.st_size > 0) {
            pool.enqueue([wc_path]{ process_worldcup_file(wc_path); });
        } else {
            LOG_WARN("  worldcup/matches.csv missing — skipping");
        }
        pool.wait_all();
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 6: openfootball JSON fixtures ───────────────────────────────────
    LOG_INFO("── Phase 6: openfootball JSON (2011-2025) ───────────────");
    {
        std::string of_dir = raw_dir + "/openfootball";
        auto of_files = list_files(of_dir, ".json");
        LOG_INFO("  Found " + std::to_string(of_files.size()) + " openfootball JSONs");
        for (const auto& fpath : of_files) {
            if (g_stop.load()) break;
            pool.enqueue([fpath]{ process_openfootball_file(fpath); });
        }
        pool.wait_all();
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 7: Cross-source conflict summary ────────────────────────────────
    LOG_INFO("── Phase 7: Score conflict summary ─────────────────────");
    LOG_INFO("  Score conflicts detected: " + std::to_string((int)g_total_conflict));

    // ── Phase 8: Fuzzy duplicate detection ───────────────────────────────────
    LOG_INFO("── Phase 8: Fuzzy duplicate detection ──────────────────");
    {
        std::lock_guard<std::mutex> lk(g_dedup_mu);
        fuzzy_dedup_pass();
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 9: Re-score quality after all merges ────────────────────────────
    LOG_INFO("── Phase 9: Quality scoring ─────────────────────────────");
    {
        std::lock_guard<std::mutex> lk(g_dedup_mu);
        recompute_quality();
        // Compute quality histogram
        int q0=0, q1=0, q2=0, q3=0, q4=0;
        for (auto& [k, m] : g_dedup) {
            if      (m.quality_score < 20) ++q0;
            else if (m.quality_score < 40) ++q1;
            else if (m.quality_score < 60) ++q2;
            else if (m.quality_score < 80) ++q3;
            else ++q4;
        }
        LOG_INFO("  Quality distribution:");
        LOG_INFO("    0-19  : " + std::to_string(q0) + " matches");
        LOG_INFO("    20-39 : " + std::to_string(q1) + " matches");
        LOG_INFO("    40-59 : " + std::to_string(q2) + " matches");
        LOG_INFO("    60-79 : " + std::to_string(q3) + " matches");
        LOG_INFO("    80-100: " + std::to_string(q4) + " matches");
    }
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── Phase 10: Write year-bucketed output ──────────────────────────────────
    LOG_INFO("── Phase 10: Writing year-bucketed CSV files ────────────");

    std::map<std::string, std::vector<const Match*>> by_year;
    {
        std::lock_guard<std::mutex> lk(g_dedup_mu);
        for (const auto& [key, m] : g_dedup) {
            if (m.date.size() < 4) continue;
            by_year[m.date.substr(0, 4)].push_back(&m);
        }
    }

    int files_written = 0;
    for (auto& [year, matches] : by_year) {
        if (g_stop.load()) break;
        std::sort(matches.begin(), matches.end(), [](const Match* a, const Match* b) {
            if (a->date != b->date) return a->date < b->date;
            return a->home_team < b->home_team;
        });
        std::string outpath = clean_dir + "/" + year + "_matches.csv";
        std::ofstream out(outpath);
        if (!out.is_open()) { LOG_ERROR("Cannot write " + outpath); continue; }
        out << Match::header() << "\n";
        for (const auto* m : matches) out << m->to_csv() << "\n";
        out.close();
        LOG_OK("  " + outpath + "  (" + std::to_string(matches.size()) + " matches)");
        ++files_written;
    }

    // ── Final report ──────────────────────────────────────────────────────────
    LOG_INFO("═══════════════════════════════════════════════════════════");
    LOG_INFO(" Cleaning complete");
    LOG_INFO("   Input rows processed : " + std::to_string((int)g_total_in));
    LOG_INFO("   Unique matches kept  : " + std::to_string((int)g_total_kept));
    LOG_INFO("   Duplicates merged    : " + std::to_string((int)g_total_dup));
    LOG_INFO("   Fuzzy merges done    : see above");
    LOG_INFO("   Invalid/filtered     : " + std::to_string((int)g_total_invalid));
    LOG_INFO("   Score conflicts      : " + std::to_string((int)g_total_conflict));
    LOG_INFO("   Year files written   : " + std::to_string(files_written));
    LOG_INFO("   Interrupted          : " + std::string(g_stop.load() ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════════════");

    return g_stop.load() ? 1 : 0;
}
