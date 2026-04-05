/*
 * StatWise Dataset Downloader — C++17  v3.0
 * ==========================================
 * Downloads all football datasets for model training:
 *   1. football-data.co.uk        — 35 leagues, seasons 1993/94 → 2024/25
 *   2. xgabora                    — 475K rows, 42 leagues, 2000-2025
 *   3. douglasbc/understat        — shot-level xG, 5 leagues, 2014-2022
 *   4. martj42/international      — 47K+ international matches since 1872
 *   5. jfjelstul/worldcup         — FIFA World Cup data 1930-2022
 *   6. openfootball/football.json — JSON fixtures, top EU leagues, 2011-2025
 *   7. statsbomb/open-data        — competition metadata JSON
 *
 * Design constraints:
 *   - Uses popen(curl) — zero external lib dependencies
 *   - CPU-throttled: sleeps between downloads, no CPU saturation
 *   - RAM-aware: pauses if free RAM < 512 MB
 *   - Graceful SIGTERM/SIGINT handling
 *   - Detailed timestamped console logging
 *   - Idempotent: skips existing non-empty files (use --force to re-download)
 *   - Continues on partial failure — logs errors but doesn't abort
 *
 * Usage:
 *   ./dataset_downloader [output_dir] [--force]
 *   Default output_dir: ../data/raw
 */

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <vector>
#include <signal.h>
#include <unistd.h>

// ─── Global state ─────────────────────────────────────────────────────────────
static std::atomic<bool> g_stop{false};
static void handle_signal(int) { g_stop.store(true); }

// ─── Logging ──────────────────────────────────────────────────────────────────
static void log(const std::string& level, const std::string& msg) {
    auto now = std::chrono::system_clock::now();
    auto t   = std::chrono::system_clock::to_time_t(now);
    struct tm tm_buf{};
    localtime_r(&t, &tm_buf);
    char ts[32];
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &tm_buf);
    std::cout << "[" << ts << "] [" << std::setw(5) << std::left << level << "] "
              << msg << "\n" << std::flush;
}
#define LOG_INFO(m)  log("INFO",  m)
#define LOG_WARN(m)  log("WARN",  m)
#define LOG_ERROR(m) log("ERROR", m)
#define LOG_OK(m)    log("OK",    m)

// ─── Filesystem helpers ───────────────────────────────────────────────────────
static bool file_exists_nonempty(const std::string& path) {
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) return false;
    return st.st_size > 0;
}

static bool mkdir_p(const std::string& path) {
    struct stat st{};
    if (stat(path.c_str(), &st) == 0) return S_ISDIR(st.st_mode);
    auto pos = path.rfind('/');
    if (pos != std::string::npos) mkdir_p(path.substr(0, pos));
    return mkdir(path.c_str(), 0755) == 0;
}

// ─── System resource monitoring ──────────────────────────────────────────────
static long free_ram_mb() {
    std::ifstream f("/proc/meminfo");
    if (!f.is_open()) return 99999;
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
    prev_idle  = idle;
    prev_total = total;
    if (d_total <= 0) return 0;
    return static_cast<int>((1.0 - (double)d_idle / d_total) * 100.0);
}

static void wait_for_resources(long min_ram_mb = 400) {
    // Sample CPU twice 200ms apart for a real delta
    cpu_usage_pct();
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    int cpu = cpu_usage_pct();
    if (cpu > 75) {
        LOG_WARN("CPU at " + std::to_string(cpu) + "% — throttling 2s");
        std::this_thread::sleep_for(std::chrono::seconds(2));
    }
    while (!g_stop.load()) {
        long avail = free_ram_mb();
        if (avail >= min_ram_mb) break;
        LOG_WARN("RAM low (" + std::to_string(avail) + " MB free) — pausing 15s…");
        std::this_thread::sleep_for(std::chrono::seconds(15));
    }
}

// ─── Download task ────────────────────────────────────────────────────────────
struct DownloadTask {
    std::string url;
    std::string dest_path;
    std::string description;
    long        min_bytes = 0;
    bool        optional  = false;   // if true: 404/failure is logged at INFO not ERROR
};

struct DownloadStats {
    int ok = 0, skipped = 0, failed = 0, optional_miss = 0;
};

static bool download_file(const DownloadTask& task, bool force, DownloadStats& stats) {
    if (!force && file_exists_nonempty(task.dest_path)) {
        struct stat st{};
        stat(task.dest_path.c_str(), &st);
        if (task.min_bytes == 0 || st.st_size >= task.min_bytes) {
            LOG_INFO("  SKIP  " + task.description);
            ++stats.skipped;
            return true;
        }
    }

    auto dir_end = task.dest_path.rfind('/');
    if (dir_end != std::string::npos)
        mkdir_p(task.dest_path.substr(0, dir_end));

    wait_for_resources();
    if (g_stop.load()) return false;

    LOG_INFO("  GET   " + task.description);

    std::string tmp = task.dest_path + ".tmp";
    std::ostringstream cmd;
    cmd << "curl -fsSL --retry 3 --retry-delay 5 --max-time 180 "
        << "--connect-timeout 20 "
        << "-H \"User-Agent: StatWise-Downloader/3.0\" "
        << "\"" << task.url << "\" "
        << "-o \"" << tmp << "\" 2>&1";

    int rc = system(cmd.str().c_str());
    if (rc != 0) {
        if (task.optional) {
            LOG_INFO("  MISS  (optional) " + task.description);
            ++stats.optional_miss;
        } else {
            LOG_ERROR("  FAIL  curl exit=" + std::to_string(rc) + " → " + task.url);
            ++stats.failed;
        }
        remove(tmp.c_str());
        return false;
    }

    struct stat st{};
    if (stat(tmp.c_str(), &st) != 0 || st.st_size == 0) {
        if (task.optional) {
            LOG_INFO("  MISS  (empty response, optional) " + task.description);
            ++stats.optional_miss;
        } else {
            LOG_ERROR("  FAIL  empty response → " + task.url);
            ++stats.failed;
        }
        remove(tmp.c_str());
        return false;
    }

    if (task.min_bytes > 0 && st.st_size < task.min_bytes) {
        if (task.optional) {
            LOG_INFO("  MISS  (too small, optional) " + task.description + " (" +
                     std::to_string(st.st_size) + " B)");
            ++stats.optional_miss;
            remove(tmp.c_str());
            return false;
        }
        // For required files: still accept if we got something (might be partial season)
        LOG_WARN("  SMALL " + task.description + " (" + std::to_string(st.st_size) +
                 " B, expected " + std::to_string(task.min_bytes) + " B)");
    }

    if (rename(tmp.c_str(), task.dest_path.c_str()) != 0) {
        LOG_ERROR("  FAIL  rename → " + task.dest_path);
        remove(tmp.c_str());
        ++stats.failed;
        return false;
    }

    LOG_OK("  DONE  " + task.description + "  (" + std::to_string(st.st_size / 1024) + " KB)");
    ++stats.ok;

    // Polite throttle between downloads
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    return true;
}

// ─── Source 1: football-data.co.uk ───────────────────────────────────────────
struct FDLeague {
    std::string slug;
    std::string code;
    std::string country;
};

static const std::vector<FDLeague> FD_LEAGUES = {
    // Tier-1 European (full history available)
    {"premier-league",   "E0",  "England"},
    {"championship",     "E1",  "England"},
    {"league-one",       "E2",  "England"},
    {"league-two",       "E3",  "England"},
    {"la-liga",          "SP1", "Spain"},
    {"la-liga-2",        "SP2", "Spain"},
    {"bundesliga",       "D1",  "Germany"},
    {"bundesliga-2",     "D2",  "Germany"},
    {"serie-a",          "I1",  "Italy"},
    {"serie-b",          "I2",  "Italy"},
    {"ligue1",           "F1",  "France"},
    {"ligue2",           "F2",  "France"},
    {"eredivisie",       "N1",  "Netherlands"},
    {"primeira-liga",    "P1",  "Portugal"},
    {"scottish-prem",    "SC0", "Scotland"},
    {"scottish-div1",    "SC1", "Scotland"},
    {"scottish-div2",    "SC2", "Scotland"},
    {"scottish-div3",    "SC3", "Scotland"},
    {"belgian-pro",      "B1",  "Belgium"},
    {"super-lig",        "T1",  "Turkey"},
    {"greek-super",      "G1",  "Greece"},
    // Extended / overseas (available from ~2012 onwards)
    {"primera-division", "ARG", "Argentina"},
    {"serie-a-br",       "BRA", "Brazil"},
    {"chinese-super",    "CHN", "China"},
    {"danish-superliga", "DEN", "Denmark"},
    {"austrian-bl",      "AUT", "Austria"},
    {"finnish-veikkaus", "FIN", "Finland"},
    {"irish-prem",       "IRL", "Ireland"},
    {"norwegian-elit",   "NOR", "Norway"},
    {"swedish-allsv",    "SWE", "Sweden"},
    {"swiss-super",      "SWI", "Switzerland"},
    {"mls",              "USA", "USA"},
    {"j-league",         "JPN", "Japan"},
};

// Seasons that exist for main leagues — includes historic data back to 1993/94
static const std::vector<std::string> FD_SEASONS_MAIN = {
    "2425", "2324", "2223", "2122", "2021", "1920", "1819", "1718",
    "1617", "1516", "1415", "1314", "1213", "1112", "1011", "0910",
    "0809", "0708", "0607", "0506", "0405", "0304", "0203", "0102",
    "0001", "9900", "9899", "9798", "9697", "9596", "9495", "9394",
};

// Overseas leagues only have data from roughly 2012 onwards
static const std::vector<std::string> FD_SEASONS_EXT = {
    "2425", "2324", "2223", "2122", "2021", "1920", "1819", "1718",
    "1617", "1516", "1415", "1314", "1213",
};

// Codes that have the full historic dataset (back to 1993/94)
static const std::vector<std::string> FD_MAIN_CODES = {
    "E0", "E1", "E2", "E3", "SP1", "SP2", "D1", "D2",
    "I1", "I2", "F1", "F2", "N1", "P1", "SC0", "SC1",
    "SC2", "SC3", "B1", "T1", "G1"
};

static bool is_main_code(const std::string& code) {
    for (auto& c : FD_MAIN_CODES) if (c == code) return true;
    return false;
}

static void build_football_data_tasks(const std::string& outdir,
                                       std::vector<DownloadTask>& tasks) {
    const std::string base = "https://www.football-data.co.uk/mmz4281";
    for (const auto& lg : FD_LEAGUES) {
        const auto& seasons = is_main_code(lg.code) ? FD_SEASONS_MAIN : FD_SEASONS_EXT;
        for (const auto& season : seasons) {
            std::string url  = base + "/" + season + "/" + lg.code + ".csv";
            std::string dest = outdir + "/football_data/" + lg.slug + "_" + season + ".csv";
            tasks.push_back({url, dest, lg.slug + " " + season, 200, true});
        }
    }
}

// ─── Source 2: xgabora ────────────────────────────────────────────────────────
static void build_xgabora_tasks(const std::string& outdir,
                                  std::vector<DownloadTask>& tasks) {
    const std::string base =
        "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data-2000-2025/main/data";
    tasks.push_back({
        base + "/Matches.csv",
        outdir + "/xgabora/Matches.csv",
        "xgabora/Matches.csv (475K rows)",
        1024 * 1024,
        false
    });
    tasks.push_back({
        base + "/EloRatings.csv",
        outdir + "/xgabora/EloRatings.csv",
        "xgabora/EloRatings.csv",
        50 * 1024,
        false
    });
    tasks.push_back({
        base + "/Teams.csv",
        outdir + "/xgabora/Teams.csv",
        "xgabora/Teams.csv",
        1024,
        true
    });
}

// ─── Source 3: understat (shot-level xG) ─────────────────────────────────────
static void build_understat_tasks(const std::string& outdir,
                                   std::vector<DownloadTask>& tasks) {
    const std::string base =
        "https://raw.githubusercontent.com/douglasbc/scraping-understat-dataset/main/datasets";
    const std::vector<std::string> leagues = {
        "epl", "bundesliga", "la_liga", "serie_a", "ligue_1"
    };
    const std::vector<std::string> seasons = {
        "14-15", "15-16", "16-17", "17-18", "18-19", "19-20", "20-21", "21-22"
    };
    const std::vector<std::string> kinds = {"players", "shots"};

    for (const auto& league : leagues) {
        for (const auto& season : seasons) {
            for (const auto& kind : kinds) {
                std::string filename = kind + "_" + league + "_" + season + ".csv";
                std::string url  = base + "/" + league + "/" + filename;
                std::string dest = outdir + "/understat/" + filename;
                tasks.push_back({url, dest, "understat/" + filename, 1000, true});
            }
        }
    }
}

// ─── Source 4: martj42/international_results ──────────────────────────────────
static void build_international_tasks(const std::string& outdir,
                                       std::vector<DownloadTask>& tasks) {
    const std::string base =
        "https://raw.githubusercontent.com/martj42/international_results/master";
    struct IntlFile { std::string name; long min_bytes; };
    const std::vector<IntlFile> files = {
        {"results.csv",     500 * 1024},  // ~47K matches
        {"goalscorers.csv", 200 * 1024},
        {"shootouts.csv",   1  * 1024},
    };
    for (const auto& f : files) {
        tasks.push_back({
            base + "/" + f.name,
            outdir + "/international/" + f.name,
            "international/" + f.name,
            f.min_bytes,
            false
        });
    }
}

// ─── Source 5: jfjelstul/worldcup ─────────────────────────────────────────────
static void build_worldcup_tasks(const std::string& outdir,
                                   std::vector<DownloadTask>& tasks) {
    const std::string base =
        "https://raw.githubusercontent.com/jfjelstul/worldcup/master/data-csv";
    struct WCFile { std::string name; long min_bytes; };
    const std::vector<WCFile> files = {
        {"matches.csv",     50 * 1024},
        {"goals.csv",       50 * 1024},
        {"teams.csv",        5 * 1024},
    };
    for (const auto& f : files) {
        tasks.push_back({
            base + "/" + f.name,
            outdir + "/worldcup/" + f.name,
            "worldcup/" + f.name,
            f.min_bytes,
            false
        });
    }
}

// ─── Source 6: openfootball/football.json ────────────────────────────────────
struct OFCompetition {
    std::string code;        // "en.1"
    std::string league_slug; // for logging
};

static const std::vector<OFCompetition> OF_COMPS = {
    {"en.1",  "premier-league"},
    {"en.2",  "championship"},
    {"en.3",  "league-one"},
    {"de.1",  "bundesliga"},
    {"de.2",  "bundesliga-2"},
    {"es.1",  "la-liga"},
    {"es.2",  "la-liga-2"},
    {"it.1",  "serie-a"},
    {"it.2",  "serie-b"},
    {"fr.1",  "ligue1"},
    {"fr.2",  "ligue2"},
    {"pt.1",  "primeira-liga"},
    {"nl.1",  "eredivisie"},
    {"be.1",  "belgian-pro"},
    {"sc.1",  "scottish-prem"},
    {"tr.1",  "super-lig"},
    {"gr.1",  "greek-super"},
    {"at.1",  "austrian-bl"},
    {"ch.1",  "swiss-super"},
    {"ru.1",  "russian-premier"},
};

// Seasons available in openfootball/football.json (2011-12 → 2024-25)
static std::vector<std::string> of_season_list() {
    std::vector<std::string> seasons;
    // Generate "2024-25", "2023-24", ..., "2011-12"
    for (int y = 2024; y >= 2011; --y) {
        int y2 = (y + 1) % 100;
        char buf[16];
        snprintf(buf, sizeof(buf), "%d-%02d", y, y2);
        seasons.push_back(buf);
    }
    return seasons;
}

static void build_openfootball_tasks(const std::string& outdir,
                                      std::vector<DownloadTask>& tasks) {
    const std::string base =
        "https://raw.githubusercontent.com/openfootball/football.json/master";
    auto seasons = of_season_list();

    for (const auto& season : seasons) {
        for (const auto& comp : OF_COMPS) {
            std::string url  = base + "/" + season + "/" + comp.code + ".json";
            std::string dest = outdir + "/openfootball/" + season + "_" + comp.code + ".json";
            std::string desc = "openfootball/" + season + "/" + comp.code;
            // All openfootball tasks are optional (not all season/league combos exist)
            tasks.push_back({url, dest, desc, 500, true});
        }
    }
}

// ─── Source 7: StatsBomb open-data ────────────────────────────────────────────
static void build_statsbomb_tasks(const std::string& outdir,
                                    std::vector<DownloadTask>& tasks) {
    tasks.push_back({
        "https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json",
        outdir + "/statsbomb/competitions.json",
        "statsbomb/competitions.json",
        500,
        true
    });
}

// ─── Main ─────────────────────────────────────────────────────────────────────
int main(int argc, char* argv[]) {
    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    bool        force  = false;
    std::string outdir = "../data/raw";

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--force") force = true;
        else if (arg.rfind("--", 0) != 0) outdir = arg;
    }

    LOG_INFO("═══════════════════════════════════════════════════════");
    LOG_INFO(" StatWise Dataset Downloader v3.0");
    LOG_INFO(" Output directory : " + outdir);
    LOG_INFO(" Force re-download: " + std::string(force ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════════");

    // Create output subdirectories
    for (const auto& subdir : {"xgabora","understat","football_data",
                                "international","worldcup","openfootball","statsbomb"}) {
        mkdir_p(outdir + "/" + subdir);
    }

    // Build unified task list
    std::vector<DownloadTask> tasks;
    build_xgabora_tasks(outdir,        tasks);
    build_international_tasks(outdir,  tasks);
    build_worldcup_tasks(outdir,       tasks);
    build_understat_tasks(outdir,      tasks);
    build_football_data_tasks(outdir,  tasks);
    build_openfootball_tasks(outdir,   tasks);
    build_statsbomb_tasks(outdir,      tasks);

    LOG_INFO("Total download tasks : " + std::to_string(tasks.size()));
    LOG_INFO("  football-data.co.uk: " +
             std::to_string(FD_LEAGUES.size() * FD_SEASONS_MAIN.size()) + " slots (many optional)");
    LOG_INFO("  openfootball JSON  : " +
             std::to_string(OF_COMPS.size() * 14) + " slots (optional)");
    LOG_INFO("Running as background task — press Ctrl+C to stop gracefully");
    LOG_INFO("═══════════════════════════════════════════════════════");

    DownloadStats stats;
    int idx = 0;
    for (const auto& task : tasks) {
        if (g_stop.load()) {
            LOG_WARN("Interrupted — stopping gracefully.");
            break;
        }
        ++idx;

        // Progress summary every 50 tasks
        if (idx % 50 == 1) {
            LOG_INFO("── Progress " + std::to_string(idx) + "/" +
                     std::to_string(tasks.size()) + " | ok=" + std::to_string(stats.ok) +
                     " skip=" + std::to_string(stats.skipped) +
                     " fail=" + std::to_string(stats.failed) +
                     " miss=" + std::to_string(stats.optional_miss) + " ──");
        }

        download_file(task, force, stats);
    }

    LOG_INFO("═══════════════════════════════════════════════════════");
    LOG_INFO(" Download complete");
    LOG_INFO("   Tasks total    : " + std::to_string(tasks.size()));
    LOG_INFO("   Downloaded     : " + std::to_string(stats.ok));
    LOG_INFO("   Skipped        : " + std::to_string(stats.skipped));
    LOG_INFO("   Failed         : " + std::to_string(stats.failed));
    LOG_INFO("   Optional miss  : " + std::to_string(stats.optional_miss));
    LOG_INFO("   Interrupted    : " + std::string(g_stop.load() ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════════");
    LOG_INFO("Next step: run dataset_cleaner to produce clean yearly CSVs");
    LOG_INFO("═══════════════════════════════════════════════════════");

    return (stats.failed > 0) ? 1 : 0;
}
