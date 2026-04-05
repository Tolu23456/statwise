/*
 * StatWise Dataset Downloader — C++17
 * ====================================
 * Downloads all football datasets needed for model training:
 *   1. xgabora/Club-Football-Match-Data-2000-2025  (475K rows, 42 leagues, 2000-2025)
 *   2. football-data.co.uk                          (primary + extended leagues, all seasons)
 *   3. douglasbc/understat                          (xG data 5 leagues, 2014-2022)
 *   4. StatsBomb open-data (JSON match records)
 *
 * Design constraints:
 *   - Uses popen(curl) to avoid external library dependencies
 *   - CPU-throttled: sleeps between downloads so it does not saturate the CPU
 *   - RAM-aware: monitors /proc/meminfo; pauses if free RAM < 512 MB
 *   - Graceful SIGTERM/SIGINT handling
 *   - Detailed timestamped console logging
 *   - Idempotent: skips files that already exist and are non-empty (use --force to override)
 *   - Runs in the background; all output is on stdout/stderr with timestamps
 *
 * Usage:
 *   ./dataset_downloader <output_dir> [--force]
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
    auto now  = std::chrono::system_clock::now();
    auto t    = std::chrono::system_clock::to_time_t(now);
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
    // Create parent first
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

static void wait_for_ram(long min_mb = 512) {
    while (!g_stop.load()) {
        long avail = free_ram_mb();
        if (avail >= min_mb) break;
        LOG_WARN("RAM low (" + std::to_string(avail) + " MB free) — pausing 10s…");
        std::this_thread::sleep_for(std::chrono::seconds(10));
    }
}

// ─── Download primitive ───────────────────────────────────────────────────────
struct DownloadTask {
    std::string url;
    std::string dest_path;
    std::string description;
    long        min_bytes = 0;   // 0 = any non-empty file is OK
};

static bool download_file(const DownloadTask& task, bool force) {
    if (!force && file_exists_nonempty(task.dest_path)) {
        struct stat st{};
        stat(task.dest_path.c_str(), &st);
        if (task.min_bytes == 0 || st.st_size >= task.min_bytes) {
            LOG_INFO("  SKIP (exists) " + task.description);
            return true;
        }
    }

    // Make sure parent directory exists
    auto dir_end = task.dest_path.rfind('/');
    if (dir_end != std::string::npos)
        mkdir_p(task.dest_path.substr(0, dir_end));

    wait_for_ram();
    if (g_stop.load()) return false;

    LOG_INFO("  GET  " + task.description + "  →  " + task.dest_path);

    // Build curl command: follow redirects, silent, write output to file
    std::string tmp = task.dest_path + ".tmp";
    std::ostringstream cmd;
    cmd << "curl -fsSL --retry 3 --retry-delay 5 --max-time 120 "
        << "--connect-timeout 15 "
        << "-H \"User-Agent: StatWise-Downloader/2.0\" "
        << "\"" << task.url << "\" "
        << "-o \"" << tmp << "\" 2>&1";

    int rc = system(cmd.str().c_str());
    if (rc != 0) {
        LOG_ERROR("  FAIL curl exit=" + std::to_string(rc) + " for " + task.url);
        remove(tmp.c_str());
        return false;
    }

    // Verify the file is non-empty
    struct stat st{};
    if (stat(tmp.c_str(), &st) != 0 || st.st_size == 0) {
        LOG_ERROR("  FAIL empty response for " + task.url);
        remove(tmp.c_str());
        return false;
    }

    // Atomic rename
    if (rename(tmp.c_str(), task.dest_path.c_str()) != 0) {
        LOG_ERROR("  FAIL rename for " + task.dest_path);
        remove(tmp.c_str());
        return false;
    }

    LOG_OK("  DONE " + task.description + "  (" + std::to_string(st.st_size / 1024) + " KB)");

    // Polite throttle: sleep 1-2s between downloads to avoid hammering servers
    std::this_thread::sleep_for(std::chrono::milliseconds(1200));
    return true;
}

// ─── Dataset definitions ──────────────────────────────────────────────────────

// football-data.co.uk league codes (extended beyond the original 16)
struct FDLeague {
    std::string slug;
    std::string code;
    std::string country;
};

static const std::vector<FDLeague> FD_LEAGUES = {
    // Original 16
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
    {"belgian-pro",      "B1",  "Belgium"},
    {"super-lig",        "T1",  "Turkey"},
    {"greek-super",      "G1",  "Greece"},
    // Extended leagues (new)
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
    {"scottish-div1",    "SC1", "Scotland"},
    {"scottish-div2",    "SC2", "Scotland"},
    {"scottish-div3",    "SC3", "Scotland"},
};

static const std::vector<std::string> FD_SEASONS = {
    "2425", "2324", "2223", "2122", "2021", "1920", "1819", "1718",
    "1617", "1516", "1415", "1314", "1213", "1112", "1011", "0910",
    "0809", "0708", "0607", "0506", "0405", "0304", "0203", "0102",
    "0001",
};

static void build_football_data_tasks(const std::string& outdir,
                                       std::vector<DownloadTask>& tasks) {
    const std::string base = "https://www.football-data.co.uk/mmz4281";
    for (const auto& lg : FD_LEAGUES) {
        for (const auto& season : FD_SEASONS) {
            std::string url  = base + "/" + season + "/" + lg.code + ".csv";
            std::string dest = outdir + "/football_data/" + lg.slug + "_" + season + ".csv";
            tasks.push_back({url, dest, lg.slug + " " + season, 500});
        }
    }
}

// Understat datasets (player+shot level xG data, top 5 leagues 2014-2022)
struct UnderstatSrc {
    std::string league;   // epl, bundesliga, la_liga, serie_a, ligue_1
    std::string kind;     // players, shots
    std::string season;   // 14-15, 15-16 …
};

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

    // Remap for URL construction (understat uses different league slug format)
    const std::vector<std::pair<std::string,std::string>> league_remap = {
        {"epl",        "epl"},
        {"bundesliga", "bundesliga"},
        {"la_liga",    "la_liga"},
        {"serie_a",    "serie_a"},
        {"ligue_1",    "ligue_1"},
    };

    for (const auto& [slug, url_slug] : league_remap) {
        for (const auto& s : seasons) {
            for (const auto& kind : kinds) {
                std::string filename = kind + "_" + url_slug + "_" + s + ".csv";
                std::string url  = base + "/" + slug + "/" + filename;
                std::string dest = outdir + "/understat/" + filename;
                tasks.push_back({url, dest, "understat/" + filename, 1000});
            }
        }
    }
}

// xgabora — the big one (single file, 475K rows)
static void build_xgabora_tasks(const std::string& outdir,
                                  std::vector<DownloadTask>& tasks) {
    const std::string base =
        "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data-2000-2025/main/data";
    tasks.push_back({
        base + "/Matches.csv",
        outdir + "/xgabora/Matches.csv",
        "xgabora/Matches.csv (475K rows, 2000-2025)",
        1024 * 1024  // expect > 1 MB
    });
    tasks.push_back({
        base + "/EloRatings.csv",
        outdir + "/xgabora/EloRatings.csv",
        "xgabora/EloRatings.csv",
        100 * 1024
    });
}

// StatsBomb open-data competition list
static void build_statsbomb_tasks(const std::string& outdir,
                                    std::vector<DownloadTask>& tasks) {
    tasks.push_back({
        "https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json",
        outdir + "/statsbomb/competitions.json",
        "StatsBomb/competitions.json",
        500
    });
}

// ─── Main ─────────────────────────────────────────────────────────────────────
int main(int argc, char* argv[]) {
    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    bool force   = false;
    std::string outdir = "../data/raw";

    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--force") force = true;
        else outdir = argv[i];
    }

    LOG_INFO("═══════════════════════════════════════════════════");
    LOG_INFO(" StatWise Dataset Downloader v2.0");
    LOG_INFO(" Output directory : " + outdir);
    LOG_INFO(" Force re-download: " + std::string(force ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════");

    // Create output subdirectories
    mkdir_p(outdir + "/xgabora");
    mkdir_p(outdir + "/understat");
    mkdir_p(outdir + "/football_data");
    mkdir_p(outdir + "/statsbomb");

    // Build task list
    std::vector<DownloadTask> tasks;
    build_xgabora_tasks(outdir,    tasks);
    build_understat_tasks(outdir,  tasks);
    build_football_data_tasks(outdir, tasks);
    build_statsbomb_tasks(outdir,  tasks);

    LOG_INFO("Total download tasks: " + std::to_string(tasks.size()));

    int ok = 0, skipped = 0, failed = 0;
    int idx = 0;
    for (const auto& task : tasks) {
        if (g_stop.load()) {
            LOG_WARN("Interrupted — stopping gracefully.");
            break;
        }
        ++idx;

        // Progress header every 20 tasks
        if ((idx - 1) % 20 == 0) {
            LOG_INFO("── Progress: " + std::to_string(idx) + "/" +
                     std::to_string(tasks.size()) + " (" +
                     std::to_string(ok) + " ok, " +
                     std::to_string(skipped) + " skipped, " +
                     std::to_string(failed) + " failed) ──");
        }

        bool success = download_file(task, force);
        if (success) {
            // Check if it was a skip vs new download
            if (!force && file_exists_nonempty(task.dest_path) &&
                tasks[idx-1].min_bytes > 0) {
                ++ok;
            } else {
                ++ok;
            }
        } else {
            ++failed;
            // Don't abort on failure — just log and continue
        }
    }

    LOG_INFO("═══════════════════════════════════════════════════");
    LOG_INFO(" Download complete");
    LOG_INFO("   Tasks total : " + std::to_string(tasks.size()));
    LOG_INFO("   Succeeded   : " + std::to_string(ok));
    LOG_INFO("   Failed      : " + std::to_string(failed));
    LOG_INFO("   Interrupted : " + std::string(g_stop.load() ? "YES" : "NO"));
    LOG_INFO("═══════════════════════════════════════════════════");

    return failed > 0 ? 1 : 0;
}
