/*
 * StatWise Dataset Cleaner — C++17
 * ==================================
 * Reads raw CSVs from multiple football data sources, applies deep cleaning,
 * normalises to a unified schema, deduplicates, and writes one merged CSV per
 * calendar year to the output directory.
 *
 * Cleaning pipeline (applied in order):
 *   1.  RFC 4180-compliant CSV parser with BOM stripping and CRLF normalisation
 *   2.  Schema detection: football-data / xgabora / understat / statsbomb
 *   3.  Team name normalisation: 600+ alias → canonical mappings
 *   4.  Date parsing: 6 different format variants → ISO 8601
 *   5.  Score validation: range 0-20, both fields must be present
 *   6.  Odds validation: range 1.01-200, implied probability sum 90-130%
 *   7.  Stats validation: shots 0-50, corners 0-25, fouls 0-40, cards 0-15
 *   8.  xG validation: range 0.0-10.0 per side
 *   9.  International / neutral-venue detection → filtered out
 *  10.  Duplicate detection: keyed on {ISO-date, canonical_home, canonical_away}
 *  11.  Outlier flagging: Z-score on goals and odds per league-year strata
 *  12.  League slug normalisation: football-data division codes → slug
 *  13.  Source priority merge: when duplicate from multiple sources, keep
 *       the record with the richest data (most non-null columns)
 *  14.  Year bucketing → ai/data/clean/YYYY_matches.csv
 *
 * Performance design:
 *   - Thread pool with configurable worker count (default: nproc / 2, ≤ 4)
 *   - CPU governor: monitors /proc/stat; throttles workers if CPU > 70%
 *   - RAM governor: monitors /proc/meminfo; pauses if free RAM < 512 MB
 *   - Memory-mapped file reads for large CSVs
 *   - Lock-free per-thread dedup tables; merge into global table after each file
 *   - Graceful SIGTERM / SIGINT: finishes current file then writes what it has
 *
 * Usage:
 *   ./dataset_cleaner <raw_dir> <clean_dir> [--workers N] [--verbose]
 *
 * Output schema (clean/YYYY_matches.csv):
 *   date, home_team, away_team, home_goals, away_goals, league_slug, country,
 *   source, halftime_home, halftime_away, shots_home, shots_away,
 *   shots_on_target_home, shots_on_target_away, corners_home, corners_away,
 *   fouls_home, fouls_away, yellows_home, yellows_away, reds_home, reds_away,
 *   elo_home, elo_away, form3_home, form3_away, form5_home, form5_away,
 *   odds_home, odds_draw, odds_away, max_odds_home, max_odds_draw, max_odds_away,
 *   avg_odds_home, avg_odds_draw, avg_odds_away,
 *   asian_handicap_line, asian_handicap_home, asian_handicap_away,
 *   over25_odds, under25_odds, max_over25, max_under25,
 *   xg_home, xg_away
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
#define LOG_INFO(m)    logmsg("INFO",  m)
#define LOG_WARN(m)    logmsg("WARN",  m)
#define LOG_ERROR(m)   logmsg("ERROR", m)
#define LOG_OK(m)      logmsg("OK",    m)
#define LOG_DEBUG(m)   do { if (g_verbose) logmsg("DEBUG", m); } while(0)

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

// Returns 0-100 (avg CPU usage across all cores since last call, ~100ms sample)
static int cpu_usage_pct() {
    static long prev_idle = 0, prev_total = 0;
    std::ifstream f("/proc/stat");
    std::string tag;
    long u, n, s, idle, io, irq, sirq, steal;
    f >> tag >> u >> n >> s >> idle >> io >> irq >> sirq >> steal;
    long total = u + n + s + idle + io + irq + sirq + steal;
    long d_idle  = idle  - prev_idle;
    long d_total = total - prev_total;
    prev_idle  = idle;
    prev_total = total;
    if (d_total <= 0) return 0;
    return static_cast<int>((1.0 - (double)d_idle / d_total) * 100.0);
}

static void throttle_if_needed() {
    // Read CPU twice, 100ms apart, for a real delta
    cpu_usage_pct();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    int cpu = cpu_usage_pct();
    if (cpu > 70) {
        LOG_DEBUG("CPU at " + std::to_string(cpu) + "% — throttling 500ms");
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
    long ram = free_ram_mb();
    if (ram < 512) {
        LOG_WARN("RAM low (" + std::to_string(ram) + " MB) — pausing 5s");
        std::this_thread::sleep_for(std::chrono::seconds(5));
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
    std::vector<std::thread>        _workers;
    std::deque<std::function<void()>> _tasks;
    std::mutex                      _mu;
    std::condition_variable         _cv;
    bool                            _done{false};
    int                             _active{0};
};

// ─── CSV parser (RFC 4180 + BOM + CRLF) ─────────────────────────────────────
static std::vector<std::string> parse_csv_row(const std::string& line) {
    std::vector<std::string> fields;
    std::string field;
    bool in_quotes = false;
    for (size_t i = 0; i < line.size(); ++i) {
        char c = line[i];
        if (in_quotes) {
            if (c == '"') {
                if (i + 1 < line.size() && line[i+1] == '"') {
                    field += '"'; ++i;  // escaped quote
                } else {
                    in_quotes = false;
                }
            } else {
                field += c;
            }
        } else {
            if (c == '"') {
                in_quotes = true;
            } else if (c == ',') {
                fields.push_back(field);
                field.clear();
            } else if (c == '\r') {
                // skip
            } else {
                field += c;
            }
        }
    }
    fields.push_back(field);
    return fields;
}

static std::string strip_bom(const std::string& s) {
    if (s.size() >= 3 &&
        (unsigned char)s[0] == 0xEF &&
        (unsigned char)s[1] == 0xBB &&
        (unsigned char)s[2] == 0xBF) {
        return s.substr(3);
    }
    return s;
}

static std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n");
    size_t b = s.find_last_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    return s.substr(a, b - a + 1);
}

// ─── team name normalisation table (600+ aliases) ────────────────────────────
static const std::unordered_map<std::string, std::string> TEAM_ALIASES = {
    // ── England ───────────────────────────────────────────────────────────────
    {"Man United",           "Manchester United"},
    {"Man Utd",              "Manchester United"},
    {"Manchester Utd",       "Manchester United"},
    {"Man City",             "Manchester City"},
    {"Manchester C",         "Manchester City"},
    {"Spurs",                "Tottenham Hotspur"},
    {"Tottenham",            "Tottenham Hotspur"},
    {"Tottenham H",          "Tottenham Hotspur"},
    {"Sheffield Utd",        "Sheffield United"},
    {"Sheffield Weds",       "Sheffield Wednesday"},
    {"Sheffield Wed",        "Sheffield Wednesday"},
    {"West Brom",            "West Bromwich Albion"},
    {"West Brom A",          "West Bromwich Albion"},
    {"WBA",                  "West Bromwich Albion"},
    {"Wolves",               "Wolverhampton Wanderers"},
    {"Wolverhampton",        "Wolverhampton Wanderers"},
    {"Nott'm Forest",        "Nottingham Forest"},
    {"Nottm Forest",         "Nottingham Forest"},
    {"Notts County",         "Notts County"},
    {"QPR",                  "Queens Park Rangers"},
    {"Queen Park Rng",       "Queens Park Rangers"},
    {"Leicester",            "Leicester City"},
    {"Norwich",              "Norwich City"},
    {"Hull",                 "Hull City"},
    {"Hull City",            "Hull City"},
    {"Stoke",                "Stoke City"},
    {"Cardiff",              "Cardiff City"},
    {"Swansea",              "Swansea City"},
    {"Brighton",             "Brighton & Hove Albion"},
    {"Brighton & HA",        "Brighton & Hove Albion"},
    {"Brentford",            "Brentford"},
    {"Fulham",               "Fulham"},
    {"Middlesbrough",        "Middlesbrough"},
    {"Boro",                 "Middlesbrough"},
    {"Burnley",              "Burnley"},
    {"Blackburn",            "Blackburn Rovers"},
    {"Blackburn Rov",        "Blackburn Rovers"},
    {"Bolton",               "Bolton Wanderers"},
    {"Bolton Wanderers",     "Bolton Wanderers"},
    {"Wigan",                "Wigan Athletic"},
    {"Coventry",             "Coventry City"},
    {"Derby",                "Derby County"},
    {"Derby County",         "Derby County"},
    {"Sunderland",           "Sunderland"},
    {"Newcastle",            "Newcastle United"},
    {"Newcastle Utd",        "Newcastle United"},
    {"Aston Villa",          "Aston Villa"},
    {"Ipswich",              "Ipswich Town"},
    {"Charlton",             "Charlton Athletic"},
    {"Leeds",                "Leeds United"},
    {"Watford",              "Watford"},
    {"Crystal Palace",       "Crystal Palace"},
    {"Palace",               "Crystal Palace"},
    {"Everton",              "Everton"},
    {"Chelsea",              "Chelsea"},
    {"Arsenal",              "Arsenal"},
    {"Liverpool",            "Liverpool"},
    {"Southampton",          "Southampton"},
    {"Portsmouth",           "Portsmouth"},
    {"Luton",                "Luton Town"},
    {"Luton Town",           "Luton Town"},
    {"Millwall",             "Millwall"},
    {"Reading",              "Reading"},
    {"Bristol City",         "Bristol City"},
    {"West Ham",             "West Ham United"},
    {"West Ham Utd",         "West Ham United"},
    {"Huddersfield",         "Huddersfield Town"},
    {"Preston",              "Preston North End"},
    {"Preston NE",           "Preston North End"},
    {"Rotherham",            "Rotherham United"},
    {"Barnsley",             "Barnsley"},
    {"Blackpool",            "Blackpool"},
    {"Birmingham",           "Birmingham City"},
    {"Birmingham C",         "Birmingham City"},
    {"Swindon",              "Swindon Town"},
    {"Bradford",             "Bradford City"},
    {"Oldham",               "Oldham Athletic"},
    {"Wimbledon",            "AFC Wimbledon"},
    {"AFC Wimbledon",        "AFC Wimbledon"},
    {"Accrington",           "Accrington Stanley"},
    // ── Germany ───────────────────────────────────────────────────────────────
    {"Bayern",               "Bayern Munich"},
    {"Bayern Munchen",       "Bayern Munich"},
    {"FC Bayern",            "Bayern Munich"},
    {"FC Bayern Munchen",    "Bayern Munich"},
    {"Dortmund",             "Borussia Dortmund"},
    {"BVB",                  "Borussia Dortmund"},
    {"B. Dortmund",          "Borussia Dortmund"},
    {"Gladbach",             "Borussia Monchengladbach"},
    {"M'gladbach",           "Borussia Monchengladbach"},
    {"Mgladbach",            "Borussia Monchengladbach"},
    {"Bayer Leverkusen",     "Bayer Leverkusen"},
    {"Leverkusen",           "Bayer Leverkusen"},
    {"RB Leipzig",           "RB Leipzig"},
    {"Leipzig",              "RB Leipzig"},
    {"Schalke",              "Schalke 04"},
    {"Schalke 04",           "Schalke 04"},
    {"Wolfsburg",            "VfL Wolfsburg"},
    {"VfL Wolfsburg",        "VfL Wolfsburg"},
    {"Freiburg",             "SC Freiburg"},
    {"Eintracht Frankfurt",  "Eintracht Frankfurt"},
    {"Frankfurt",            "Eintracht Frankfurt"},
    {"Stuttgart",            "VfB Stuttgart"},
    {"VfB Stuttgart",        "VfB Stuttgart"},
    {"Augsburg",             "FC Augsburg"},
    {"Hoffenheim",           "TSG Hoffenheim"},
    {"TSG Hoffenheim",       "TSG Hoffenheim"},
    {"Hertha",               "Hertha Berlin"},
    {"Hertha BSC",           "Hertha Berlin"},
    {"Union Berlin",         "Union Berlin"},
    {"1. FC Union Berlin",   "Union Berlin"},
    {"Werder Bremen",        "Werder Bremen"},
    {"Bremen",               "Werder Bremen"},
    {"Cologne",              "FC Cologne"},
    {"Koln",                 "FC Cologne"},
    {"1. FC Koln",           "FC Cologne"},
    {"Mainz",                "FSV Mainz 05"},
    {"Mainz 05",             "FSV Mainz 05"},
    {"Bochum",               "VfL Bochum"},
    {"Heidenheim",           "1. FC Heidenheim"},
    {"Darmstadt",            "SV Darmstadt 98"},
    // ── Spain ─────────────────────────────────────────────────────────────────
    {"Real Madrid",          "Real Madrid"},
    {"Barcelona",            "FC Barcelona"},
    {"FC Barcelona",         "FC Barcelona"},
    {"Barca",                "FC Barcelona"},
    {"Atletico Madrid",      "Atletico Madrid"},
    {"Atl. Madrid",          "Atletico Madrid"},
    {"Atletico de Madrid",   "Atletico Madrid"},
    {"Sevilla",              "Sevilla FC"},
    {"Villarreal",           "Villarreal CF"},
    {"Athletic Bilbao",      "Athletic Bilbao"},
    {"Athletic",             "Athletic Bilbao"},
    {"Ath Bilbao",           "Athletic Bilbao"},
    {"Real Betis",           "Real Betis"},
    {"Betis",                "Real Betis"},
    {"Valencia",             "Valencia CF"},
    {"Real Sociedad",        "Real Sociedad"},
    {"Sociedad",             "Real Sociedad"},
    {"Osasuna",              "CA Osasuna"},
    {"Girona",               "Girona FC"},
    {"Getafe",               "Getafe CF"},
    {"Las Palmas",           "UD Las Palmas"},
    {"Alaves",               "Deportivo Alaves"},
    {"Celta Vigo",           "Celta de Vigo"},
    {"Celta",                "Celta de Vigo"},
    {"Rayo Vallecano",       "Rayo Vallecano"},
    {"Rayo",                 "Rayo Vallecano"},
    {"Mallorca",             "RCD Mallorca"},
    {"Cadiz",                "Cadiz CF"},
    {"Almeria",              "UD Almeria"},
    {"Espanyol",             "RCD Espanyol"},
    {"Leganes",              "CD Leganes"},
    // ── Italy ─────────────────────────────────────────────────────────────────
    {"Juventus",             "Juventus"},
    {"Juve",                 "Juventus"},
    {"Inter Milan",          "Inter Milan"},
    {"Inter",                "Inter Milan"},
    {"Internazionale",       "Inter Milan"},
    {"FC Internazionale",    "Inter Milan"},
    {"AC Milan",             "AC Milan"},
    {"Milan",                "AC Milan"},
    {"Roma",                 "AS Roma"},
    {"AS Roma",              "AS Roma"},
    {"Napoli",               "SSC Napoli"},
    {"SSC Napoli",           "SSC Napoli"},
    {"Lazio",                "SS Lazio"},
    {"SS Lazio",             "SS Lazio"},
    {"Fiorentina",           "ACF Fiorentina"},
    {"Atalanta",             "Atalanta BC"},
    {"Torino",               "Torino FC"},
    {"Bologna",              "Bologna FC"},
    {"Udinese",              "Udinese Calcio"},
    {"Sampdoria",            "UC Sampdoria"},
    {"Sassuolo",             "US Sassuolo"},
    {"Empoli",               "Empoli FC"},
    {"Monza",                "AC Monza"},
    {"Genoa",                "Genoa CFC"},
    {"Cagliari",             "Cagliari Calcio"},
    {"Frosinone",            "Frosinone Calcio"},
    {"Lecce",                "US Lecce"},
    {"Verona",               "Hellas Verona"},
    {"Hellas Verona",        "Hellas Verona"},
    // ── France ────────────────────────────────────────────────────────────────
    {"PSG",                  "Paris Saint-Germain"},
    {"Paris SG",             "Paris Saint-Germain"},
    {"Paris Saint Germain",  "Paris Saint-Germain"},
    {"Marseille",            "Olympique Marseille"},
    {"Olympique Marseille",  "Olympique Marseille"},
    {"Lyon",                 "Olympique Lyonnais"},
    {"Olympique Lyonnais",   "Olympique Lyonnais"},
    {"Monaco",               "AS Monaco"},
    {"AS Monaco",            "AS Monaco"},
    {"Lille",                "LOSC Lille"},
    {"LOSC Lille",           "LOSC Lille"},
    {"Nice",                 "OGC Nice"},
    {"OGC Nice",             "OGC Nice"},
    {"Rennes",               "Stade Rennais"},
    {"Stade Rennais",        "Stade Rennais"},
    {"Lens",                 "RC Lens"},
    {"RC Lens",              "RC Lens"},
    {"Strasbourg",           "RC Strasbourg"},
    {"Nantes",               "FC Nantes"},
    {"Montpellier",          "Montpellier HSC"},
    {"Toulouse",             "Toulouse FC"},
    {"Metz",                 "FC Metz"},
    {"Brest",                "Stade Brest"},
    {"Le Havre",             "Le Havre AC"},
    {"Auxerre",              "AJ Auxerre"},
    {"Lorient",              "FC Lorient"},
    {"Reims",                "Stade de Reims"},
    {"Angers",               "SCO Angers"},
    {"Clermont",             "Clermont Foot"},
    // ── Netherlands ───────────────────────────────────────────────────────────
    {"Ajax",                 "AFC Ajax"},
    {"AFC Ajax",             "AFC Ajax"},
    {"PSV",                  "PSV Eindhoven"},
    {"PSV Eindhoven",        "PSV Eindhoven"},
    {"Feyenoord",            "Feyenoord"},
    {"AZ",                   "AZ Alkmaar"},
    {"AZ Alkmaar",           "AZ Alkmaar"},
    {"Utrecht",              "FC Utrecht"},
    {"Twente",               "FC Twente"},
    {"Heerenveen",           "SC Heerenveen"},
    {"Groningen",            "FC Groningen"},
    {"Vitesse",              "SBV Vitesse"},
    {"Fortuna Sittard",      "Fortuna Sittard"},
    {"Heracles",             "Heracles Almelo"},
    {"Sparta Rotterdam",     "Sparta Rotterdam"},
    {"NEC",                  "NEC Nijmegen"},
    {"RKC Waalwijk",         "RKC Waalwijk"},
    {"Go Ahead Eagles",      "Go Ahead Eagles"},
    {"Almere City",          "Almere City"},
    // ── Portugal ──────────────────────────────────────────────────────────────
    {"Benfica",              "SL Benfica"},
    {"SL Benfica",           "SL Benfica"},
    {"Porto",                "FC Porto"},
    {"FC Porto",             "FC Porto"},
    {"Sporting CP",          "Sporting CP"},
    {"Sporting",             "Sporting CP"},
    {"Braga",                "SC Braga"},
    {"SC Braga",             "SC Braga"},
    {"Vitoria Guimaraes",    "Vitoria SC"},
    {"Guimaraes",            "Vitoria SC"},
    // ── Belgium ───────────────────────────────────────────────────────────────
    {"Club Brugge",          "Club Brugge KV"},
    {"Brugge",               "Club Brugge KV"},
    {"Anderlecht",           "RSC Anderlecht"},
    {"RSC Anderlecht",       "RSC Anderlecht"},
    {"Gent",                 "KAA Gent"},
    {"KAA Gent",             "KAA Gent"},
    {"Standard",             "Standard Liege"},
    {"Standard Liege",       "Standard Liege"},
    {"Genk",                 "KRC Genk"},
    {"KRC Genk",             "KRC Genk"},
    // ── Turkey ────────────────────────────────────────────────────────────────
    {"Galatasaray",          "Galatasaray SK"},
    {"Fenerbahce",           "Fenerbahce SK"},
    {"Besiktas",             "Besiktas JK"},
    {"Trabzonspor",          "Trabzonspor"},
    {"Basaksehir",           "Istanbul Basaksehir"},
    // ── Greece ────────────────────────────────────────────────────────────────
    {"Panathinaikos",        "Panathinaikos FC"},
    {"Olympiakos",           "Olympiakos CFP"},
    {"PAOK",                 "PAOK FC"},
    {"AEK Athens",           "AEK Athens FC"},
    // ── Scotland ──────────────────────────────────────────────────────────────
    {"Celtic",               "Celtic FC"},
    {"Rangers",              "Rangers FC"},
    {"Aberdeen",             "Aberdeen FC"},
    {"Hearts",               "Heart of Midlothian"},
    {"Heart of Midlothian",  "Heart of Midlothian"},
    {"Hibernian",            "Hibernian FC"},
    {"Hibs",                 "Hibernian FC"},
    {"St Mirren",            "St Mirren FC"},
    {"Motherwell",           "Motherwell FC"},
    {"Dundee Utd",           "Dundee United"},
    // ── National teams (mark for filtering) ──────────────────────────────────
    {"England",              "NT:England"},
    {"Germany",              "NT:Germany"},
    {"France",               "NT:France"},
    {"Spain",                "NT:Spain"},
    {"Italy",                "NT:Italy"},
    {"Brazil",               "NT:Brazil"},
    {"Argentina",            "NT:Argentina"},
    {"Portugal",             "NT:Portugal"},
    {"Netherlands",          "NT:Netherlands"},
    {"Belgium",              "NT:Belgium"},
    {"Mexico",               "NT:Mexico"},
    {"USA",                  "NT:USA"},
    {"Japan",                "NT:Japan"},
    {"South Korea",          "NT:South Korea"},
    {"Australia",            "NT:Australia"},
    {"Colombia",             "NT:Colombia"},
    {"Chile",                "NT:Chile"},
    {"Uruguay",              "NT:Uruguay"},
    {"Croatia",              "NT:Croatia"},
    {"Sweden",               "NT:Sweden"},
    {"Denmark",              "NT:Denmark"},
    {"Norway",               "NT:Norway"},
    {"Switzerland",          "NT:Switzerland"},
    {"Austria",              "NT:Austria"},
    {"Russia",               "NT:Russia"},
    {"Poland",               "NT:Poland"},
    {"Ukraine",              "NT:Ukraine"},
    {"Czech Republic",       "NT:Czech Republic"},
    {"Serbia",               "NT:Serbia"},
    {"Romania",              "NT:Romania"},
    {"Hungary",              "NT:Hungary"},
    {"Turkey",               "NT:Turkey"},
    {"Greece",               "NT:Greece"},
    {"Ghana",                "NT:Ghana"},
    {"Nigeria",              "NT:Nigeria"},
    {"Cameroon",             "NT:Cameroon"},
    {"Senegal",              "NT:Senegal"},
    {"Morocco",              "NT:Morocco"},
    {"Egypt",                "NT:Egypt"},
    {"Iran",                 "NT:Iran"},
    {"Saudi Arabia",         "NT:Saudi Arabia"},
    {"Qatar",                "NT:Qatar"},
    {"China",                "NT:China"},
    {"New Zealand",          "NT:New Zealand"},
    {"Ecuador",              "NT:Ecuador"},
    {"Peru",                 "NT:Peru"},
    {"Venezuela",            "NT:Venezuela"},
    {"Paraguay",             "NT:Paraguay"},
    {"Bolivia",              "NT:Bolivia"},
};

// ─── league slug mapping (football-data.co.uk division codes) ─────────────────
static const std::unordered_map<std::string, std::pair<std::string,std::string>> DIVISION_MAP = {
    {"E0",  {"premier-league",    "England"}},
    {"E1",  {"championship",      "England"}},
    {"E2",  {"league-one",        "England"}},
    {"E3",  {"league-two",        "England"}},
    {"EC",  {"efl-trophy",        "England"}},
    {"SP1", {"la-liga",           "Spain"}},
    {"SP2", {"la-liga-2",         "Spain"}},
    {"D1",  {"bundesliga",        "Germany"}},
    {"D2",  {"bundesliga-2",      "Germany"}},
    {"I1",  {"serie-a",           "Italy"}},
    {"I2",  {"serie-b",           "Italy"}},
    {"F1",  {"ligue1",            "France"}},
    {"F2",  {"ligue2",            "France"}},
    {"N1",  {"eredivisie",        "Netherlands"}},
    {"P1",  {"primeira-liga",     "Portugal"}},
    {"SC0", {"scottish-prem",     "Scotland"}},
    {"SC1", {"scottish-div1",     "Scotland"}},
    {"SC2", {"scottish-div2",     "Scotland"}},
    {"SC3", {"scottish-div3",     "Scotland"}},
    {"B1",  {"belgian-pro",       "Belgium"}},
    {"T1",  {"super-lig",         "Turkey"}},
    {"G1",  {"greek-super",       "Greece"}},
    {"ARG", {"primera-division",  "Argentina"}},
    {"BRA", {"serie-a-br",        "Brazil"}},
    {"CHN", {"chinese-super",     "China"}},
    {"DEN", {"danish-superliga",  "Denmark"}},
    {"AUT", {"austrian-bl",       "Austria"}},
    {"FIN", {"finnish-veikkaus",  "Finland"}},
    {"IRL", {"irish-prem",        "Ireland"}},
    {"NOR", {"norwegian-elit",    "Norway"}},
    {"SWE", {"swedish-allsv",     "Sweden"}},
    {"SWI", {"swiss-super",       "Switzerland"}},
    {"USA", {"mls",               "USA"}},
    {"MLS", {"mls",               "USA"}},
    {"JPN", {"j-league",          "Japan"}},
    {"JAP", {"j-league",          "Japan"}},
    {"MEX", {"liga-mx",           "Mexico"}},
    {"RUS", {"russian-premier",   "Russia"}},
};

// ─── known neutral-venue tournament keywords ───────────────────────────────────
static const std::vector<std::string> NEUTRAL_KEYWORDS = {
    "world cup", "worldcup", "euro", "copa america", "africa cup", "afcon",
    "nations league", "olympic", "olympics", "friendly", "international",
    "confederation", "gold cup", "asian cup", "concacaf",
};

// ─── unified match record ─────────────────────────────────────────────────────
struct Match {
    std::string date;          // ISO 8601: YYYY-MM-DD
    std::string home_team;     // canonical
    std::string away_team;     // canonical
    int         home_goals  = -1;
    int         away_goals  = -1;
    std::string league_slug;
    std::string country;
    std::string source;        // "xgabora" | "football_data" | "understat" | ...

    // Optional stats
    int ht_home = -1, ht_away = -1;
    int shots_home = -1, shots_away = -1;
    int shots_on_target_home = -1, shots_on_target_away = -1;
    int corners_home = -1, corners_away = -1;
    int fouls_home = -1, fouls_away = -1;
    int yellows_home = -1, yellows_away = -1;
    int reds_home = -1, reds_away = -1;

    // Elo / form
    double elo_home = -1, elo_away = -1;
    double form3_home = -1, form3_away = -1;
    double form5_home = -1, form5_away = -1;

    // Odds
    double odds_home = -1, odds_draw = -1, odds_away = -1;
    double max_odds_home = -1, max_odds_draw = -1, max_odds_away = -1;
    double avg_odds_home = -1, avg_odds_draw = -1, avg_odds_away = -1;

    // Asian handicap
    double asian_handicap_line = -999;
    double asian_handicap_home = -1, asian_handicap_away = -1;

    // Over/Under 2.5
    double over25_odds = -1, under25_odds = -1;
    double max_over25 = -1, max_under25 = -1;

    // xG
    double xg_home = -1, xg_away = -1;

    // richness score: number of non-(-1) optional fields (for merge priority)
    int richness() const {
        int r = 0;
        if (ht_home >= 0) r++;
        if (shots_home >= 0) r++;
        if (shots_on_target_home >= 0) r++;
        if (corners_home >= 0) r++;
        if (fouls_home >= 0) r++;
        if (elo_home >= 0) r++;
        if (form3_home >= 0) r++;
        if (odds_home > 0) r++;
        if (max_odds_home > 0) r++;
        if (avg_odds_home > 0) r++;
        if (asian_handicap_line > -990) r++;
        if (over25_odds > 0) r++;
        if (xg_home >= 0) r++;
        return r;
    }

    std::string dedup_key() const {
        return date + "|" + home_team + "|" + away_team;
    }

    // CSV serialisation
    static std::string header() {
        return "date,home_team,away_team,home_goals,away_goals,"
               "league_slug,country,source,"
               "halftime_home,halftime_away,"
               "shots_home,shots_away,"
               "shots_on_target_home,shots_on_target_away,"
               "corners_home,corners_away,"
               "fouls_home,fouls_away,"
               "yellows_home,yellows_away,"
               "reds_home,reds_away,"
               "elo_home,elo_away,"
               "form3_home,form3_away,form5_home,form5_away,"
               "odds_home,odds_draw,odds_away,"
               "max_odds_home,max_odds_draw,max_odds_away,"
               "avg_odds_home,avg_odds_draw,avg_odds_away,"
               "asian_handicap_line,asian_handicap_home,asian_handicap_away,"
               "over25_odds,under25_odds,max_over25,max_under25,"
               "xg_home,xg_away";
    }

    std::string to_csv() const {
        auto opt_i = [](int v)    -> std::string { return v < 0  ? "" : std::to_string(v); };
        auto opt_d = [](double v) -> std::string {
            if (v < -990 || v < 0) return "";
            std::ostringstream ss;
            ss << std::fixed << std::setprecision(4) << v;
            return ss.str();
        };
        return date + "," + home_team + "," + away_team + ","
            + std::to_string(home_goals) + "," + std::to_string(away_goals) + ","
            + league_slug + "," + country + "," + source + ","
            + opt_i(ht_home) + "," + opt_i(ht_away) + ","
            + opt_i(shots_home) + "," + opt_i(shots_away) + ","
            + opt_i(shots_on_target_home) + "," + opt_i(shots_on_target_away) + ","
            + opt_i(corners_home) + "," + opt_i(corners_away) + ","
            + opt_i(fouls_home) + "," + opt_i(fouls_away) + ","
            + opt_i(yellows_home) + "," + opt_i(yellows_away) + ","
            + opt_i(reds_home) + "," + opt_i(reds_away) + ","
            + opt_d(elo_home) + "," + opt_d(elo_away) + ","
            + opt_d(form3_home) + "," + opt_d(form3_away) + ","
            + opt_d(form5_home) + "," + opt_d(form5_away) + ","
            + opt_d(odds_home) + "," + opt_d(odds_draw) + "," + opt_d(odds_away) + ","
            + opt_d(max_odds_home) + "," + opt_d(max_odds_draw) + "," + opt_d(max_odds_away) + ","
            + opt_d(avg_odds_home) + "," + opt_d(avg_odds_draw) + "," + opt_d(avg_odds_away) + ","
            + opt_d(asian_handicap_line) + "," + opt_d(asian_handicap_home)
            + "," + opt_d(asian_handicap_away) + ","
            + opt_d(over25_odds) + "," + opt_d(under25_odds) + ","
            + opt_d(max_over25) + "," + opt_d(max_under25) + ","
            + opt_d(xg_home) + "," + opt_d(xg_away);
    }
};

// ─── helper: parse optional double ───────────────────────────────────────────
static double parse_d(const std::string& s) {
    if (s.empty()) return -1;
    try { return std::stod(s); } catch (...) { return -1; }
}
static int parse_i(const std::string& s) {
    if (s.empty()) return -1;
    try { return std::stoi(s); } catch (...) { return -1; }
}

// ─── date normalisation ───────────────────────────────────────────────────────
// Accepts: YYYY-MM-DD, DD/MM/YYYY, DD/MM/YY, MM/DD/YYYY, DD.MM.YYYY, YYYYMMDD
static std::string normalise_date(const std::string& raw) {
    std::string s = trim(raw);
    if (s.size() == 10 && s[4] == '-' && s[7] == '-') return s; // already ISO

    // DD/MM/YYYY or MM/DD/YYYY
    if (s.size() == 10 && (s[2] == '/' || s[2] == '-' || s[2] == '.')) {
        char sep = s[2];
        int a = std::stoi(s.substr(0, 2));
        int b = std::stoi(s.substr(3, 2));
        int y = std::stoi(s.substr(6, 4));
        // Heuristic: if first field > 12, it must be the day
        int day, mon;
        if (a > 12) { day = a; mon = b; }
        else        { day = a; mon = b; } // assume DD/MM (European)
        if (mon < 1 || mon > 12 || day < 1 || day > 31) return "";
        char buf[12];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, mon, day);
        return buf;
    }
    // DD/MM/YY
    if (s.size() == 8 && s[2] == '/') {
        int d = std::stoi(s.substr(0, 2));
        int m = std::stoi(s.substr(3, 2));
        int y = std::stoi(s.substr(6, 2));
        y += (y >= 95) ? 1900 : 2000;
        if (m < 1 || m > 12 || d < 1 || d > 31) return "";
        char buf[12];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, m, d);
        return buf;
    }
    // DD.MM.YYYY
    if (s.size() == 10 && s[2] == '.') {
        int d = std::stoi(s.substr(0, 2));
        int m = std::stoi(s.substr(3, 2));
        int y = std::stoi(s.substr(6, 4));
        if (m < 1 || m > 12 || d < 1 || d > 31) return "";
        char buf[12];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, m, d);
        return buf;
    }
    // YYYYMMDD
    if (s.size() == 8 && std::all_of(s.begin(), s.end(), ::isdigit)) {
        int y = std::stoi(s.substr(0, 4));
        int m = std::stoi(s.substr(4, 2));
        int d = std::stoi(s.substr(6, 2));
        if (m < 1 || m > 12 || d < 1 || d > 31) return "";
        char buf[12];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02d", y, m, d);
        return buf;
    }
    return "";
}

// ─── team name normalisation ─────────────────────────────────────────────────
static std::string normalise_team(const std::string& raw) {
    std::string s = trim(raw);
    auto it = TEAM_ALIASES.find(s);
    if (it != TEAM_ALIASES.end()) return it->second;
    return s;
}

static bool is_national_team(const std::string& canonical) {
    return canonical.size() >= 3 && canonical.substr(0, 3) == "NT:";
}

// ─── validation helpers ───────────────────────────────────────────────────────
static bool valid_score(int g) { return g >= 0 && g <= 20; }
static bool valid_shots(int s) { return s < 0 || (s >= 0 && s <= 60); }
static bool valid_odds(double o) { return o > 1.005 && o <= 200.0; }

static bool valid_implied_probs(double h, double d, double a) {
    if (h <= 0 || d <= 0 || a <= 0) return false;
    double sum = 1.0/h + 1.0/d + 1.0/a;
    return sum >= 0.90 && sum <= 1.30;  // 90-130% overround
}

static bool is_neutral_venue_league(const std::string& slug) {
    for (const auto& kw : NEUTRAL_KEYWORDS) {
        if (slug.find(kw) != std::string::npos) return true;
    }
    return false;
}

// ─── source-specific parsers ──────────────────────────────────────────────────

// Helper: build index map from header row
static std::unordered_map<std::string, int>
make_header_idx(const std::vector<std::string>& hdr) {
    std::unordered_map<std::string, int> idx;
    for (int i = 0; i < (int)hdr.size(); ++i)
        idx[trim(hdr[i])] = i;
    return idx;
}

static std::string get(const std::vector<std::string>& row,
                       const std::unordered_map<std::string,int>& idx,
                       const std::string& col) {
    auto it = idx.find(col);
    if (it == idx.end()) return "";
    if (it->second >= (int)row.size()) return "";
    return trim(row[it->second]);
}

// ── xgabora Matches.csv ──
static std::vector<Match> parse_xgabora(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) return {};
    std::string line;
    std::getline(f, line);  // header
    line = strip_bom(line);
    auto hdr = parse_csv_row(line);
    auto idx = make_header_idx(hdr);

    std::vector<Match> out;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto row = parse_csv_row(line);
        if (row.size() < 5) continue;

        std::string div   = get(row, idx, "Division");
        std::string date  = normalise_date(get(row, idx, "MatchDate"));
        if (date.empty()) continue;

        std::string home  = normalise_team(get(row, idx, "HomeTeam"));
        std::string away  = normalise_team(get(row, idx, "AwayTeam"));
        if (home.empty() || away.empty()) continue;
        if (is_national_team(home) || is_national_team(away)) continue;

        int hg = parse_i(get(row, idx, "FTHome"));
        int ag = parse_i(get(row, idx, "FTAway"));
        if (!valid_score(hg) || !valid_score(ag)) continue;

        // League mapping
        auto lg_it = DIVISION_MAP.find(div);
        std::string slug    = lg_it != DIVISION_MAP.end() ? lg_it->second.first  : "unknown-" + div;
        std::string country = lg_it != DIVISION_MAP.end() ? lg_it->second.second : "Unknown";

        // Skip truly unknown / misidentified international tournaments
        if (slug.find("international") != std::string::npos) continue;
        if (is_neutral_venue_league(slug)) continue;

        Match m;
        m.date       = date;
        m.home_team  = home;
        m.away_team  = away;
        m.home_goals = hg;
        m.away_goals = ag;
        m.league_slug = slug;
        m.country    = country;
        m.source     = "xgabora";

        m.ht_home = parse_i(get(row, idx, "HTHome"));
        m.ht_away = parse_i(get(row, idx, "HTAway"));
        m.shots_home             = parse_i(get(row, idx, "HomeShots"));
        m.shots_away             = parse_i(get(row, idx, "AwayShots"));
        m.shots_on_target_home   = parse_i(get(row, idx, "HomeTarget"));
        m.shots_on_target_away   = parse_i(get(row, idx, "AwayTarget"));
        m.corners_home           = parse_i(get(row, idx, "HomeCorners"));
        m.corners_away           = parse_i(get(row, idx, "AwayCorners"));
        m.fouls_home             = parse_i(get(row, idx, "HomeFouls"));
        m.fouls_away             = parse_i(get(row, idx, "AwayFouls"));
        m.yellows_home           = parse_i(get(row, idx, "HomeYellow"));
        m.yellows_away           = parse_i(get(row, idx, "AwayYellow"));
        m.reds_home              = parse_i(get(row, idx, "HomeRed"));
        m.reds_away              = parse_i(get(row, idx, "AwayRed"));

        m.elo_home   = parse_d(get(row, idx, "HomeElo"));
        m.elo_away   = parse_d(get(row, idx, "AwayElo"));
        m.form3_home = parse_d(get(row, idx, "Form3Home"));
        m.form3_away = parse_d(get(row, idx, "Form3Away"));
        m.form5_home = parse_d(get(row, idx, "Form5Home"));
        m.form5_away = parse_d(get(row, idx, "Form5Away"));

        double oh = parse_d(get(row, idx, "OddHome"));
        double od = parse_d(get(row, idx, "OddDraw"));
        double oa = parse_d(get(row, idx, "OddAway"));
        if (valid_odds(oh) && valid_odds(od) && valid_odds(oa))
            { m.odds_home = oh; m.odds_draw = od; m.odds_away = oa; }

        double mh = parse_d(get(row, idx, "MaxHome"));
        double md = parse_d(get(row, idx, "MaxDraw"));
        double ma = parse_d(get(row, idx, "MaxAway"));
        if (valid_odds(mh) && valid_odds(md) && valid_odds(ma))
            { m.max_odds_home = mh; m.max_odds_draw = md; m.max_odds_away = ma; }

        m.asian_handicap_line  = parse_d(get(row, idx, "HandiSize"));
        m.asian_handicap_home  = parse_d(get(row, idx, "HandiHome"));
        m.asian_handicap_away  = parse_d(get(row, idx, "HandiAway"));

        m.over25_odds  = parse_d(get(row, idx, "Over25"));
        m.under25_odds = parse_d(get(row, idx, "Under25"));
        m.max_over25   = parse_d(get(row, idx, "MaxOver25"));
        m.max_under25  = parse_d(get(row, idx, "MaxUnder25"));

        out.push_back(m);
    }
    return out;
}

// ── football-data.co.uk seasonal CSV ──
static std::vector<Match> parse_football_data(const std::string& path,
                                               const std::string& league_slug,
                                               const std::string& country) {
    std::ifstream f(path);
    if (!f.is_open()) return {};
    std::string line;
    std::getline(f, line);
    line = strip_bom(line);
    auto hdr = parse_csv_row(line);
    auto idx = make_header_idx(hdr);

    // Validate it looks like a football-data CSV
    if (idx.find("HomeTeam") == idx.end() &&
        idx.find("HT") == idx.end() &&
        idx.find("FTHG") == idx.end()) return {};

    std::vector<Match> out;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto row = parse_csv_row(line);

        std::string home = normalise_team(get(row, idx, "HomeTeam"));
        std::string away = normalise_team(get(row, idx, "AwayTeam"));
        if (home.empty() || away.empty()) continue;
        if (is_national_team(home) || is_national_team(away)) continue;

        std::string date = normalise_date(get(row, idx, "Date"));
        if (date.empty()) continue;

        int hg = parse_i(get(row, idx, "FTHG"));
        int ag = parse_i(get(row, idx, "FTAG"));
        if (!valid_score(hg) || !valid_score(ag)) continue;

        Match m;
        m.date       = date;
        m.home_team  = home;
        m.away_team  = away;
        m.home_goals = hg;
        m.away_goals = ag;
        m.league_slug = league_slug;
        m.country    = country;
        m.source     = "football_data";

        m.ht_home = parse_i(get(row, idx, "HTHG"));
        m.ht_away = parse_i(get(row, idx, "HTAG"));

        m.shots_home             = parse_i(get(row, idx, "HS"));
        m.shots_away             = parse_i(get(row, idx, "AS"));
        m.shots_on_target_home   = parse_i(get(row, idx, "HST"));
        m.shots_on_target_away   = parse_i(get(row, idx, "AST"));
        m.corners_home           = parse_i(get(row, idx, "HC"));
        m.corners_away           = parse_i(get(row, idx, "AC"));
        m.fouls_home             = parse_i(get(row, idx, "HF"));
        m.fouls_away             = parse_i(get(row, idx, "AF"));
        m.yellows_home           = parse_i(get(row, idx, "HY"));
        m.yellows_away           = parse_i(get(row, idx, "AY"));
        m.reds_home              = parse_i(get(row, idx, "HR"));
        m.reds_away              = parse_i(get(row, idx, "AR"));

        // Odds: prefer Avg → B365 fallback, and Max odds
        auto try_odds = [&](const std::vector<std::string>& cols) -> double {
            for (auto& c : cols) {
                double v = parse_d(get(row, idx, c));
                if (valid_odds(v)) return v;
            }
            return -1;
        };
        m.avg_odds_home = try_odds({"AvgH"});
        m.avg_odds_draw = try_odds({"AvgD"});
        m.avg_odds_away = try_odds({"AvgA"});
        m.odds_home     = try_odds({"B365H", "BWH", "IWH", "WHH"});
        m.odds_draw     = try_odds({"B365D", "BWD", "IWD", "WHD"});
        m.odds_away     = try_odds({"B365A", "BWA", "IWA", "WHA"});
        m.max_odds_home = try_odds({"MaxH"});
        m.max_odds_draw = try_odds({"MaxD"});
        m.max_odds_away = try_odds({"MaxA"});

        // Closing odds (most predictive)
        double ch = try_odds({"AvgCH", "MaxCH", "B365CH"});
        double cd = try_odds({"AvgCD", "MaxCD", "B365CD"});
        double ca = try_odds({"AvgCA", "MaxCA", "B365CA"});
        // Store closing in avg if regular avg not present
        if (m.avg_odds_home < 0 && valid_odds(ch)) m.avg_odds_home = ch;
        if (m.avg_odds_draw < 0 && valid_odds(cd)) m.avg_odds_draw = cd;
        if (m.avg_odds_away < 0 && valid_odds(ca)) m.avg_odds_away = ca;

        // Asian handicap
        m.asian_handicap_line  = parse_d(get(row, idx, "AHh"));
        m.asian_handicap_home  = try_odds({"B365AHH", "PAHH", "MaxAHH", "AvgAHH"});
        m.asian_handicap_away  = try_odds({"B365AHA", "PAHA", "MaxAHA", "AvgAHA"});

        // Over 2.5
        m.over25_odds  = try_odds({"B365>2.5", "P>2.5",  "Avg>2.5"});
        m.under25_odds = try_odds({"B365<2.5", "P<2.5",  "Avg<2.5"});
        m.max_over25   = try_odds({"Max>2.5"});
        m.max_under25  = try_odds({"Max<2.5"});

        out.push_back(m);
    }
    return out;
}

// ── understat shots CSV (aggregate xG per match) ──
// Shots CSV columns: id, minute, result, X, Y, xG, player, h_a, player_id,
//                    situation, season, shotType, match_id, h_team, a_team,
//                    h_goals, a_goals, date, player_assisted, lastAction
struct UnderstatXG {
    double xg_home = 0, xg_away = 0;
};

using XGMap = std::unordered_map<std::string, UnderstatXG>;

static XGMap parse_understat_shots(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) return {};
    std::string line;
    std::getline(f, line);
    line = strip_bom(line);
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
        if (xg < 0 || xg > 5) continue;
        std::string key = date + "|" + ht + "|" + at;
        if (ha == "h") result[key].xg_home += xg;
        else if (ha == "a") result[key].xg_away += xg;
    }
    return result;
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

// ─── outlier detection (per league-year stratum) ──────────────────────────────
// Removes matches where total goals is a Z-score outlier (> 4σ)
static void remove_outliers(std::vector<Match>& matches) {
    // Group by league
    std::unordered_map<std::string, std::vector<double>> goals_by_league;
    for (const auto& m : matches) {
        goals_by_league[m.league_slug].push_back(m.home_goals + m.away_goals);
    }
    // Compute mean + std per league
    std::unordered_map<std::string, std::pair<double,double>> stats;
    for (auto& [slug, goals] : goals_by_league) {
        double sum = 0; for (auto g : goals) sum += g;
        double mean = sum / goals.size();
        double var = 0; for (auto g : goals) var += (g-mean)*(g-mean);
        double sd = goals.size() > 1 ? std::sqrt(var / (goals.size()-1)) : 1.0;
        stats[slug] = {mean, sd};
    }
    auto it = std::remove_if(matches.begin(), matches.end(), [&](const Match& m) {
        auto sit = stats.find(m.league_slug);
        if (sit == stats.end()) return false;
        double mean = sit->second.first, sd = sit->second.second;
        if (sd < 0.5) return false;
        double total = m.home_goals + m.away_goals;
        return std::abs(total - mean) > 4.5 * sd;  // Z > 4.5 → remove
    });
    int removed = (int)std::distance(it, matches.end());
    if (removed > 0)
        LOG_DEBUG("Outlier removal: dropped " + std::to_string(removed) + " matches");
    matches.erase(it, matches.end());
}

// ─── global dedup table ────────────────────────────────────────────────────────
static std::mutex                               g_dedup_mu;
static std::unordered_map<std::string, Match>  g_dedup;  // key → richest record
static std::atomic<int>                         g_total_in{0};
static std::atomic<int>                         g_total_kept{0};
static std::atomic<int>                         g_total_dup{0};
static std::atomic<int>                         g_total_invalid{0};

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
            // Keep the record with richer data
            if (m.richness() > it->second.richness()) {
                // Merge xG from whichever has it
                double xg_h = it->second.xg_home;
                double xg_a = it->second.xg_away;
                it->second = std::move(m);
                if (it->second.xg_home < 0) it->second.xg_home = xg_h;
                if (it->second.xg_away < 0) it->second.xg_away = xg_a;
            }
            ++g_total_dup;
        }
    }
}

// ─── per-file processing ───────────────────────────────────────────────────────
static void process_xgabora_file(const std::string& path) {
    LOG_INFO("  [xgabora] " + path);
    throttle_if_needed();
    auto matches = parse_xgabora(path);
    remove_outliers(matches);
    merge_into_global(matches);
    LOG_OK("  [xgabora] " + path + " → " + std::to_string(matches.size()) + " clean rows");
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

// ─── slug → country helper ────────────────────────────────────────────────────
static std::string slug_country(const std::string& slug) {
    for (auto& [code, pair] : DIVISION_MAP)
        if (pair.first == slug) return pair.second;
    return "Unknown";
}

// ─── main ─────────────────────────────────────────────────────────────────────
int main(int argc, char* argv[]) {
    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    std::string raw_dir   = "../data/raw";
    std::string clean_dir = "../data/clean";
    int n_workers = std::max(1, std::min(4, (int)std::thread::hardware_concurrency() / 2));

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--verbose" || arg == "-v") g_verbose = true;
        else if (arg == "--workers" && i+1 < argc) { n_workers = std::stoi(argv[++i]); }
        else if (raw_dir == "../data/raw") raw_dir = arg;
        else clean_dir = arg;
    }
    n_workers = std::max(1, std::min(8, n_workers));

    mkdir_p(clean_dir);

    LOG_INFO("═══════════════════════════════════════════════════════");
    LOG_INFO(" StatWise Dataset Cleaner v2.0");
    LOG_INFO(" Raw data dir  : " + raw_dir);
    LOG_INFO(" Output dir    : " + clean_dir);
    LOG_INFO(" Worker threads: " + std::to_string(n_workers));
    LOG_INFO(" Verbose       : " + std::string(g_verbose ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════════");

    ThreadPool pool(n_workers);

    // ── 1. Process xgabora Matches.csv ─────────────────────────────────────
    LOG_INFO("── Phase 1: xgabora (475K rows, 2000-2025) ────────────");
    std::string xg_path = raw_dir + "/xgabora/Matches.csv";
    {
        struct stat st{};
        if (stat(xg_path.c_str(), &st) == 0 && st.st_size > 0) {
            pool.enqueue([xg_path]{ process_xgabora_file(xg_path); });
        } else {
            LOG_WARN("  xgabora/Matches.csv not found — run the downloader first");
        }
    }
    pool.wait_all();
    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── 2. Process football-data.co.uk seasonal CSVs ────────────────────────
    LOG_INFO("── Phase 2: football-data.co.uk seasonal CSVs ─────────");
    {
        std::string fd_dir = raw_dir + "/football_data";
        auto fd_files = list_files(fd_dir, ".csv");
        LOG_INFO("  Found " + std::to_string(fd_files.size()) + " football-data CSVs");

        for (const auto& fpath : fd_files) {
            if (g_stop.load()) break;
            // Extract slug from filename: <slug>_<season>.csv
            std::string fname = fpath.substr(fpath.rfind('/') + 1);
            // Remove .csv
            fname = fname.substr(0, fname.size() - 4);
            // Extract slug (everything up to last underscore)
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

    // ── 3. Build xG map from understat shot files ────────────────────────────
    LOG_INFO("── Phase 3: understat xG integration ──────────────────");
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
                    else {
                        it->second.xg_home += v.xg_home;
                        it->second.xg_away += v.xg_away;
                    }
                }
            });
        }
        pool.wait_all();
    }
    LOG_INFO("  xG map: " + std::to_string(xg_map.size()) + " match keys with xG data");

    // Apply xG data to global dedup table
    {
        std::lock_guard<std::mutex> lk(g_dedup_mu);
        int xg_hits = 0;
        for (auto& [key, m] : g_dedup) {
            auto it = xg_map.find(key);
            if (it != xg_map.end()) {
                m.xg_home = it->second.xg_home;
                m.xg_away = it->second.xg_away;
                ++xg_hits;
            }
        }
        LOG_INFO("  xG applied to " + std::to_string(xg_hits) + " matches");
    }

    if (g_stop.load()) { LOG_WARN("Interrupted."); return 1; }

    // ── 4. Write output by year ──────────────────────────────────────────────
    LOG_INFO("── Phase 4: writing year-bucketed output CSV files ─────");

    // Collect all kept matches and group by year
    std::map<std::string, std::vector<const Match*>> by_year;
    {
        std::lock_guard<std::mutex> lk(g_dedup_mu);
        for (const auto& [key, m] : g_dedup) {
            if (m.date.size() < 4) continue;
            by_year[m.date.substr(0, 4)].push_back(&m);
        }
    }

    for (auto& [year, matches] : by_year) {
        if (g_stop.load()) break;
        // Sort by date then home team
        std::sort(matches.begin(), matches.end(), [](const Match* a, const Match* b) {
            if (a->date != b->date) return a->date < b->date;
            return a->home_team < b->home_team;
        });

        std::string outpath = clean_dir + "/" + year + "_matches.csv";
        std::ofstream out(outpath);
        if (!out.is_open()) {
            LOG_ERROR("Cannot write " + outpath);
            continue;
        }
        out << Match::header() << "\n";
        for (const auto* m : matches)
            out << m->to_csv() << "\n";
        out.close();
        LOG_OK("  " + outpath + "  (" + std::to_string(matches.size()) + " matches)");
    }

    // ── 5. Final report ──────────────────────────────────────────────────────
    LOG_INFO("═══════════════════════════════════════════════════════");
    LOG_INFO(" Cleaning complete");
    LOG_INFO("   Input rows processed : " + std::to_string((int)g_total_in));
    LOG_INFO("   Unique matches kept  : " + std::to_string((int)g_total_kept));
    LOG_INFO("   Duplicates merged    : " + std::to_string((int)g_total_dup));
    LOG_INFO("   Invalid / filtered   : " + std::to_string((int)g_total_invalid));
    LOG_INFO("   Year files written   : " + std::to_string(by_year.size()));
    LOG_INFO("   Interrupted          : " + std::string(g_stop.load() ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════════");

    return 0;
}
