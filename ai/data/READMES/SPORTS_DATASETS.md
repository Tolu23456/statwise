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

Source: **FiveThirtyEight MLB datasets**.

| File | Rows | Contents |
|---|---|---|
| `mlb_elo.csv` | ~2.4k | Every MLB game since 1903 with team ELO ratings and pitcher info |
| `quasi_winshares.csv` | ~5k | Career quasi-winshares for hitters and pitchers |
| `allstar_player_talent.csv` | — | All-Star game talent ratings |

**Notes:** baseball is the most variance-heavy of the four, but `mlb_elo.csv` is
already 100+ years deep. Pitcher-vs-batter matchup data is the missing piece for
a genuinely predictive model.

---

## Volleyball — `ai/data/volleyball/`

Source: **Volleybox** (men's + women's, international leagues and tournaments).

| File | Contents |
|---|---|
| `volleybox_men.html` | Men's league/tournament tables + match results |
| `volleybox_women.html` | Women's equivalents |
| `volleybox_men_wwc.html` | Men's World Championship |
| `volleybox_men_cwc.html` | Men's Club World Championship |

**Notes:** volleyball is the softest market of the four — bookmakers dedicate far
less quantitative attention to it than to the big three. Set-based scoring also
means fewer, more decisive games than football. Good candidate for a "value picks"
product where we only surface high-certainty sets.

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
