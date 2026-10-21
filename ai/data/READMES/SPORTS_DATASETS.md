# StatWise Multi-Sport Datasets

Raw historical match data harvested for the upcoming **Tennis, Basketball, Baseball
and Volleyball** modules. Nothing here is wired into the football model yet — this
is the raw material for the next phase of work.

## Harvesting

```bash
.venv/bin/python ai/gather_sports_data.py              # all sports
.venv/bin/python ai/gather_sports_data.py tennis       # single sport
```

Files land in `ai/data/<sport>/raw/` and a `ai/data/<sport>/MANIFEST.json` records
the source URL, row count and column list for every file.

The raw directories are git-ignored because they are large and fully reproducible
from the manifest.

---

## Tennis — `ai/data/tennis/`

Source: **Jeff Sackmann – Tennis Match Charting Project** (point-level charting of
ATP/WTA matches) + **FiveThirtyEight tennis serve timing**.

| File | Rows | Contents |
|---|---|---|
| `m_matches.csv` | ~7.5k | Men's match-level results with surface, round, score, seed |
| `w_matches.csv` | ~4.3k | Women's match-level results |
| `m_points_2020s.csv` | ~547k | Men's point-level charting (2020+) |
| `m_points_2010s.csv` | ~357k | Men's point-level charting (2010-2019) |
| `m_points_to_2009.csv` | ~380k | Men's point-level charting (pre-2010) |
| `w_points_2020s.csv` | ~354k | Women's point-level charting (2020+) |
| `w_points_2010s.csv` | ~179k | Women's point-level charting (2010-2019) |
| `w_points_to_2009.csv` | ~61k | Women's point-level charting (pre-2010) |
| `m_stats_serve_basics.csv` | — | Per-player serve aggregate stats |
| `m_stats_serve_influence.csv` | — | Serve influence on return outcomes |
| `m_stats_rally.csv` | — | Rally length distributions |
| `m_stats_sv_break_total.csv` | — | Break-point conversion rates |
| `m_stats_net_points.csv` | — | Net play stats |
| `m_stats_overview.csv` | — | Per-player overall summary |
| `w_stats_*.csv` | — | Women's equivalents |
| `538_serve_times.csv` | — | Average serve speeds / times |

**Why tennis is the best first sport:** no draws, no venue effect, surface matters
hugely, and ELO-style ratings are near-optimal. This is the ideal candidate for
the new certainty/abstain logic because far more matches are genuine coin flips
than in football.

---

## Basketball — `ai/data/basketball/`

Source: **FiveThirtyEight NBA datasets**.

| File | Rows | Contents |
|---|---|---|
| `nbaallelo.csv` | ~13k | Every NBA game since 1946 with team ELO ratings, plus 4 betting-market columns and 7 star-player indicators |
| `modern_raptor_by_team.csv` | — | RAPOR-style player impact by team |
| `modern_raptor_by_player.csv` | — | RAPOR-style player impact by player |
| `historical_raptor_by_team.csv` | — | Historical RAPOR team ratings |

**Notes:** the ELO file is the backbone — it is already a strong standalone model.
The RAPOR files add player-level value, which is what separates a good NBA model
from a great one. Market columns (`f1_win`, `f2_win`, `f1_odds`, …) let us measure
edge directly.

---

## Baseball — `ai/data/baseball/`

Source: **Lahman Baseball Database** (via the Rdatasets CRAN mirror) + **FiveThirtyEight
MLB datasets**.

| File | Rows | Contents |
|---|---|---|
| `lahman_batting.csv` | ~128k | Every at-bat, 1871–present: hits, HR, RBI, BB, SO, HBP, SF, SH, IBB, Pos, league/team IDs |
| `lahman_batting_post.csv` | ~18.7k | Postseason batting records |
| `lahman_pitching.csv` | ~106k | Every pitching line: W/L, ERA, G, GS, CG, SHO, SV, IP, H, ER, HR, BB, SO, WHIP, H5, etc. |
| `lahman_teams.csv` | ~2.9k | Every team-season: park, attendance, park factors, team batting/pitching ranks |
| `lahman_people.csv` | ~19.7k | Player biographical data |
| `lahman_appearances.csv` | ~109k | Per-player games played at each position |
| `lahman_fielding.csv` | ~174k | Per-player fielding records by position |
| `lahman_parks.csv` | ~345 | Park dimensions and characteristics |
| `lahman_awards_players.csv` | ~12.7k | MVP, Cy Young, Gold Glove, Rookie of the Year, All-Star |
| `quasi_winshares.csv` | ~98.8k | Career quasi-winshares for hitters and pitchers |
| `allstar_player_talent.csv` | ~3.9k | All-Star game talent ratings |

**Notes:** this is the deepest dataset of the four — 150+ years. The real modelling
work is in **pitcher vs. batter matchup** (a starter's ERA against a specific
opponent), **times-through-the-order bullpen effects**, and **park factors** (all
present in `lahman_teams.csv`). The 538 `mlb_elo.csv` file was retired when the 538
site was decommissioned, so the Lahman tables replace it.

---

## Volleyball — `ai/data/volleyball/`

Source: **bigtimestats/beach-volleyball** — full AVP + FIVB tour archive with
per-match statistics.

| File | Rows | Contents |
|---|---|---|
| `beach_volleyball_full.csv` | ~85.5k | Complete archive 2000–2022: circuit, tournament, country, date, gender, player names, birthdates, ages, heights, nationalities, rankings, score, and per-match stat lines |
| `bvb_fivb_2021/2022.csv` | ~2.9k each | FIVB tour only, by year |
| `bvb_avp_2021/2022.csv` | ~1.3k each | AVP tour only, by year |
| `bvb_2016`–`bvb_2022.csv` | ~3.4k–6.3k each | All tours, by year |

**Notes:** beach volleyball (2v2) rather than indoor — but the *modelling* is
identical and the dataset is far better documented and richer than anything
freely available for indoor. Player height, age and birthdate are included,
which are genuinely predictive in beach volleyball. The 2v2 format also means
**no draws and no bench** — every match produces a winner, which suits the
abstain-when-unsure framework perfectly.

---

## Harvest summary

| Sport | Files | Total rows |
|---|---|---|
| Tennis | 20 | ~2,451,761 |
| Basketball | 4 | ~168,264 |
| Baseball | 12 | ~651,553 |
| Volleyball | 11 | ~119,766 |

All four harvests complete with **0 failures**. Per-file row counts and column
lists live in `ai/data/<sport>/MANIFEST.json`.

---

## Next steps (not yet implemented)

1. **Normalisers** — one parser per sport producing a common match table:
   `date, home, away, home_score, away_score, league, surface/venue`.
2. **Feature builders** — tennis: ELO + surface + serve stats; NBA: pace/EPM/RAPTOR
   + rest; MLB: pitcher matchup + wOBA; volleyball: set efficiency + rotation.
3. **Separate models** — one per sport, reusing the LightGBM + isotonic calibration
   + market blend stack already in `ai/model/trainer.py`.
4. **Unified uncertainty** — the entropy / head-disagreement / certainty score
   introduced for football applies directly to every sport.
5. **App routing** — a `sport` column on `predictions` plus a sport filter on the
   home tab.
