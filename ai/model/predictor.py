"""
PredictionEngine – loads the trained model and generates predictions for
upcoming matches, then pushes them to Supabase.
Also handles backtesting: settling past predictions with actual results.
"""
from __future__ import annotations
import os, sys, json, logging, datetime
import pandas as pd
from typing import Optional

from .trainer import FootballPredictor
from .live_fetcher import fetch_upcoming_matches, _mock_allowed
from .downloader import load_training_data
from .leagues import canon_league

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://fwpriiugfjhtoymcbmso.supabase.co"
)
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    os.environ.get(
        "SUPABASE_ANON_KEY",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ3cHJpaXVnZmpodG95bWNibXNvIiwi"
        "cm9sZSI6ImFub24iLCJpYXQiOjE3NTY5MDc3MTgsImV4cCI6MjA3MjQ4MzcxOH0."
        "Sy3iehhGx2UNi28UBEjfNd81TjMxOVXJjY9HJS3xVjs"
    )
)

TIER_THRESHOLDS = {
    "Free Tier":    (0,   40),
    "Premium Tier": (40,  55),
    "VIP Tier":     (55,  65),
    "VVIP Tier":    (65,  101),
}

# Certainty at/above which the model is considered genuinely sure -> "AI Pick"
AI_PICK_CERTAINTY = 65.0

# ── inference history ───────────────────────────────────────────────────────
# Live inference used to run on an EMPTY frame whenever the engine merely
# loaded the pickle — the normal path, since load_or_train() only ever filled
# self.history after an in-process retrain.  With no history, form, H2H,
# streaks, Poisson lambdas and travel all read nothing: 103 of 161 features
# changed, 55 of them outside the training p5-p95 band (home_win_rate
# 0.607 -> 0, lambda_h 0.418 -> 0.100), leaving live predictions running on
# Elo plus the market blend.  The committed season files are the very rows
# retrain.py trains on, so that is what inference should see: ~6s and ~33MB,
# once per process, cached.
_LOCAL_HISTORY: Optional[pd.DataFrame] = None


def _load_local_history() -> Optional[pd.DataFrame]:
    """The committed season files (ai/data/clean/*.csv) as inference history."""
    global _LOCAL_HISTORY
    if _LOCAL_HISTORY is not None:
        return _LOCAL_HISTORY
    import glob
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../statwise/ai
    paths = sorted(glob.glob(os.path.join(base, 'data', 'clean', '????_matches.csv')))
    if not paths:
        logger.warning("no season files under ai/data/clean — inference history unavailable")
        return None
    usecols = {'date', 'home_team', 'away_team', 'home_goals', 'away_goals'}
    frames = []
    for p in paths:
        try:
            frames.append(pd.read_csv(p, usecols=lambda c: c in usecols, low_memory=False))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"season file unreadable ({os.path.basename(p)}): {e}")
    if not frames:
        return None
    hist = pd.concat(frames, ignore_index=True)
    hist['date'] = pd.to_datetime(hist['date'], errors='coerce')
    # Categorical team names: 377k rows cost ~33MB instead of ~83MB, and every
    # downstream lookup is an equality test, which categoricals answer natively.
    for c in ('home_team', 'away_team'):
        if c in hist.columns:
            hist[c] = hist[c].astype('category')
    hist = hist.dropna(subset=['date', 'home_team', 'away_team'])
    hist = hist.sort_values('date', kind='stable').reset_index(drop=True)
    _LOCAL_HISTORY = hist
    logger.info(f"Loaded inference history: {len(hist):,} rows from {len(paths)} season files")
    return hist

#: How many historical matches a league needs before the model can claim
#: league-specific form. football-data.co.uk only publishes domestic seasons,
#: so cup competitions (Europa League, Conference League, Copa Libertadores)
#: have no season CSV of their own — Pico predicts those from pooled priors.
#: Rather than quietly implying deep league knowledge, such fixtures are
#: flagged and held to a higher certainty bar before they can become an AI Pick.
MIN_LEAGUE_SAMPLES = 200
TIER_DB = {
    "Free Tier":    "free",
    "Premium Tier": "premium",
    "VIP Tier":     "vip",
    "VVIP Tier":    "vvip",
}


def _tier_for_confidence(confidence: int) -> tuple[str, str]:
    """Map a certainty score (0-100) to a tier."""
    for tier_name, (lo, hi) in TIER_THRESHOLDS.items():
        if lo <= confidence < hi:
            return tier_name, TIER_DB[tier_name]
    return "VVIP Tier", "vvip"


def _picked_odds(fix: dict, prediction: str) -> float | None:
    """Market price of the outcome we picked, or None when the book had none.

    The old code stored the home price for every pick and fell back to a
    hard-coded 1.90, so the app displayed a price that did not exist.
    """
    key = {
        "Home Win": "odds_home",
        "Draw": "odds_draw",
        "Away Win": "odds_away",
    }.get(prediction)
    if not key:
        return None
    try:
        v = float(fix.get(key) or 0)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if v > 1.01 else None


def _get_supabase_client():
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Cannot create Supabase client: {e}")
        return None


def _row_for_db(p: dict) -> dict | None:
    """The Supabase payload for a prediction, or None if it must not be written.

    Two rules, both learned the hard way:

    * a row with no model probabilities is not a Pico prediction — the legacy
      seeded-PRNG fetch job invented H/D/A picks exactly this way and they
      polluted the accuracy screen (see tools/quarantine_invented_rows.py);
    * never blank a price odds_sync already wrote: the fixture feeds carry no
      odds of their own, so an explicit ``odds=None`` would wipe the price
      until the next successful odds sync (predict runs 4x/day).
    """
    row = {k: v for k, v in p.items()
           if k in PredictionEngine._DB_COLUMNS
           and not (k == "odds" and v is None)}
    stats = row.get("stats")
    if not isinstance(stats, dict) or stats.get("prob_home") is None:
        return None
    # copy: the caller's dict must not be mutated as a side effect
    row["stats"] = {**stats, "writer": stats.get("writer", "pico")}
    return row


class PredictionEngine:
    def __init__(self, predictor: Optional[FootballPredictor] = None,
                 history: Optional[pd.DataFrame] = None):
        self.predictor = predictor
        self.history   = history if history is not None else pd.DataFrame()
        self._supabase = None

    def _ensure_supabase(self):
        if self._supabase is None:
            self._supabase = _get_supabase_client()
        return self._supabase

    def load_or_train(self, force_retrain: bool = False) -> "PredictionEngine":
        model_path = os.path.join(
            os.path.dirname(__file__), '..', 'models', 'football_predictor.pkl'
        )
        if not force_retrain and os.path.exists(model_path):
            try:
                self.predictor = FootballPredictor.load(model_path)
                logger.info("Loaded existing trained model.")
                return self
            except (ValueError, Exception) as e:
                logger.warning(f"Could not load model ({e}) – retraining…")

        logger.info("Downloading training data…")
        df = load_training_data()
        if df.empty:
            logger.error("No training data available.")
            return self

        self.history = df.copy()
        self.predictor = FootballPredictor()
        try:
            self.predictor.train(df)
            self.predictor.save(model_path)
        except ValueError as e:
            logger.error(f"Training failed: {e}")
            self.predictor = None
        return self

    def run(self, leagues: list[str] | None = None, days_ahead: int = 7) -> list[dict]:
        if self.predictor is None:
            logger.error("No trained model – cannot generate predictions.")
            return []

        fixtures = fetch_upcoming_matches(leagues=leagues, days_ahead=days_ahead)
        if not fixtures:
            logger.warning("No upcoming fixtures found.")
            return []

        # Hard guard: placeholder fixtures must never be published as real
        # predictions. They are only permitted when explicitly opted in.
        if not _mock_allowed():
            fixtures = [f for f in fixtures if not str(f.get('match_id', '')).startswith('mock_')]
            if not fixtures:
                logger.error("Only mock fixtures were returned – refusing to publish.")
                return []
        else:
            logger.warning(
                "ALLOW_MOCK_FIXTURES is set – placeholder fixtures may be published."
            )

        # Per-league training support, so a fixture backed by little history can
        # be flagged instead of being presented as if the model knew the league.
        # Prefer the counts persisted INSIDE the model: self.history is only
        # populated when a retrain happened in this process, so reading it alone
        # would leave the map empty on every ordinary "load the pickle" run.
        # Counts are keyed on the CANONICAL slug (they are written by
        # features.fit), so lookups below must canonicalise too.
        try:
            league_n = {canon_league(k): int(v)
                        for k, v in (getattr(self.predictor, 'league_counts', None) or {}).items()}
            if not league_n and self.history is not None and 'league' in self.history:
                league_n = self.history['league'].value_counts().to_dict()
        except Exception:
            league_n = {}

        # Feature history for THIS run: load-once local season files when the
        # engine merely loaded a pickle (the normal path), otherwise the frame
        # from an in-process retrain.  Without this, every fixture is scored on
        # an empty frame and the form/H2H/lambda/travel features all read zero.
        hist = self.history if (self.history is not None and not self.history.empty) \
            else _load_local_history()
        if hist is None or hist.empty:
            logger.warning(
                "No inference history available — form/H2H/lambda features will be empty. "
                "Re-run the data pipeline so ai/data/clean/*.csv exist."
            )
            hist = pd.DataFrame(columns=['date', 'home_team', 'away_team',
                                         'home_goals', 'away_goals'])

        # Travel (141-145) and recent-form (146-154) are one pass over the whole
        # history frame (~35s on 377k rows).  Derive them ONCE for the entire
        # fixture list and slice per match — recomputing inside the loop would
        # cost 35s x N fixtures.
        try:
            blocks = self.predictor.feature_pipe.compute_blocks(fixtures, hist)
        except Exception as be:  # noqa: BLE001
            logger.warning(f"batch form/travel blocks failed ({be}) — falling back to per-fixture")
            blocks = None

        results = []
        for idx, fix in enumerate(fixtures):
            try:
                # Match-time weather (Open-Meteo forecast) — a live model feature (slots 133-140)
                kickoff    = fix.get('kickoff_time', '')
                match_date = kickoff[:10] if kickoff else str(datetime.date.today())
                weather = None
                try:
                    from .team_venues import resolve_venue, fetch_forecast_weather
                    v = resolve_venue(fix['home_team'], None)
                    if v and v.get("lat") is not None:
                        weather = fetch_forecast_weather(v["lat"], v["lon"], match_date)
                except Exception as we:
                    logger.debug(f"Weather fetch failed for {fix.get('match_title', '?')}: {we}")

                pred = self.predictor.predict_match(
                    home  = fix['home_team'],
                    away  = fix['away_team'],
                    league= fix.get('league_slug', 'all'),
                    history= hist,
                    # Recent results for the analysis panel (form, H2H). Kept
                    # separate from `history` so the feature pipeline is
                    # unaffected by the compact snapshot.
                    insight_history= self.predictor.recent_history,
                    # market odds feed BOTH the odds features (has_odds etc.)
                    # and the post-hoc alpha blend with the de-vigged market
                    odds_home=float(fix.get('odds_home') or 0),
                    odds_draw=float(fix.get('odds_draw') or 0),
                    odds_away=float(fix.get('odds_away') or 0),
                    weather= weather,
                    # Fixture kickoff: the travel/rest slots are the only
                    # features keyed on kickoff rather than "now", and without
                    # it they read NaT -> zeros for every fixture.
                    match_date= match_date,
                    # Precomputed batch slice — see compute_blocks() above.
                    feature_blocks=None if blocks is None else
                                   (blocks[0][idx:idx + 1], blocks[1][idx:idx + 1]),
                )
                uncertainty = pred.get('uncertainty') or {}
                is_uncertain = bool(uncertainty.get('is_uncertain'))
                certainty = float(uncertainty.get('certainty') or 0)
                raw_conf = int(pred.get('confidence', 50))

                # How much league-specific history actually backs this fixture.
                # Cup ties (Europa/Conference League, Libertadores) have no
                # season CSV of their own, so Pico leans on pooled priors there
                # — worth saying out loud rather than implying deep knowledge.
                league  = canon_league(fix.get('league_slug', 'all'))
                # An empty map means the model predates per-league accounting:
                # treat support as UNKNOWN rather than thin, so a stale pickle
                # cannot quietly suppress every AI Pick on the board.
                samples = int(league_n.get(league, 0)) if league_n else -1
                reduced = bool(0 <= samples < MIN_LEAGUE_SAMPLES)

                # "AI Pick" = model is genuinely sure (high certainty score).
                # A fixture with thin league support must be surer still, since
                # AI Picks are exactly what the booking code is built from.
                pick_bar = AI_PICK_CERTAINTY + (10.0 if reduced else 0.0)
                is_ai_pick = bool(certainty >= pick_bar and not is_uncertain)

                # Tier is driven by certainty, not raw confidence.
                # Uncertain picks are forced down to the free tier.
                tier_score = raw_conf if is_uncertain else max(certainty, raw_conf * 0.6)
                if is_uncertain:
                    tier_score = min(tier_score, 39)
                tier_name, tier_db = _tier_for_confidence(int(round(tier_score)))

                stats = pred.get('stats') or {}
                stats['certainty'] = round(certainty, 1)
                stats['is_ai_pick'] = is_ai_pick
                stats['uncertainty'] = uncertainty
                stats['league_samples'] = samples if samples >= 0 else None
                stats['reduced_confidence'] = reduced
                stats['prob_home'] = pred.get('prob_home')
                stats['prob_draw'] = pred.get('prob_draw')
                stats['prob_away'] = pred.get('prob_away')
                stats['prob_over25'] = pred.get('prob_over25')
                # plan 1.6 goal markets — null on older pickles (no aux
                # heads), which the match-detail modal treats as "no row"
                stats['prob_btts'] = pred.get('prob_btts')
                stats['prob_over35'] = pred.get('prob_over35')
                # Which upstream feed produced this fixture. Derived from the
                # canonical match_id namespace so it cannot drift from the row.
                mid = str(fix.get('match_id') or '')
                if mid.startswith('espn_'):
                    stats['source'] = 'espn'
                elif mid.startswith('fdorg_'):
                    stats['source'] = 'football-data.org'
                else:
                    stats['source'] = fix.get('source') or 'unknown'
                # Value edge vs market: model prob for our pick minus implied prob
                # from the available odds. Positive = the model likes it more
                # than the book does.
                edge = None
                try:
                    oh = float(fix.get('odds_home') or 0)
                    od = float(fix.get('odds_draw') or 0)
                    oa = float(fix.get('odds_away') or 0)
                    if oh > 1.01 and od > 1.01 and oa > 1.01:
                        implied = [1.0 / oh, 1.0 / od, 1.0 / oa]
                        tot = sum(implied)
                        implied = [x / tot for x in implied]
                        pick_idx = {'Home Win': 0, 'Draw': 1, 'Away Win': 2}.get(pred['prediction'], 0)
                        model_prob = float(pred.get('prob_home' if pick_idx == 0 else ('prob_draw' if pick_idx == 1 else 'prob_away')) or 0)
                        if model_prob > 0:
                            edge = round((model_prob / 100.0 - implied[pick_idx]) * 100, 1)
                except Exception:
                    edge = None
                if edge is not None:
                    stats['value_edge'] = edge
                # Keep the raw market odds so the app can show model vs market
                # side-by-side without needing extra columns.
                for k in ('odds_home', 'odds_draw', 'odds_away'):
                    v = fix.get(k)
                    if v:
                        stats[k] = float(v)
                if weather:
                    stats['details'] = {**(stats.get('details') or {}), 'weather': weather}

                row = {
                    "match_id":      fix['match_id'],
                    "match_title":   fix.get('match_title') or f"{fix.get('home_team')} vs {fix.get('away_team')}",
                    "home_team":     fix['home_team'],
                    "away_team":     fix['away_team'],
                    "league":        fix.get('league_name', fix.get('league_slug', 'Unknown')),
                    "league_slug":   fix.get('league_slug', 'all'),
                    "prediction":    pred['prediction'],
                    "confidence":    pred['confidence'],
                    # Price of the outcome we actually picked, from the real
                    # market. Was hard-coded to 1.90 when unavailable, and
                    # always took the home price even for an away pick.
                    "odds":          _picked_odds(fix, pred['prediction']),
                    "reasoning":     pred['reasoning'],
                    "stats":         stats,
                    "kickoff_time":  kickoff,
                    "match_date":    match_date,
                    "tier":          tier_db,
                    "tier_required": tier_name,
                    "status":        "upcoming",
                }
                results.append(row)
            except Exception as e:
                logger.warning(f"Prediction failed for {fix.get('match_title', '?')}: {e}")

        logger.info(f"Generated {len(results)} predictions.")
        return results

    _DB_COLUMNS = {
        "match_id", "match_title", "home_team", "away_team",
        "league", "league_slug", "prediction", "confidence",
        "odds", "reasoning", "stats", "kickoff_time", "match_date",
        "tier", "tier_required", "status",
        "actual_result", "settled_at",
    }

    def push_to_supabase(self, predictions: list[dict]) -> int:
        if not predictions:
            return 0
        sb = self._ensure_supabase()
        if sb is None:
            logger.error("Supabase client unavailable – predictions not saved.")
            return 0

        # enrich_match_details.py writes team_stats/details into the SAME
        # JSON column.  A full-row upsert from here silently erased that
        # enrichment on every predict run (4x/day).  One batched read first,
        # then merge so pico's keys win and everything else survives.
        existing: dict[str, dict] = {}
        wanted = {str(p.get("match_id")) for p in predictions if p.get("match_id")}
        try:
            page = 0
            while wanted and page < 100:
                q = sb.table("predictions").select("match_id,stats").in_(
                    "match_id", list(wanted)).limit(1000)
                if page:
                    q = q.range(page * 1000, (page + 1) * 1000 - 1)
                got = q.execute().data or []
                if not got:
                    break
                for r in got:
                    st = r.get("stats")
                    if isinstance(st, str):
                        try:
                            st = json.loads(st)
                        except Exception:
                            st = None
                    existing[str(r.get("match_id"))] = st if isinstance(st, dict) else {}
                if len(got) < 1000:
                    break
                page += 1
        except Exception as e:
            logger.warning(f"Could not read existing stats for merge ({e}) "
                           "— enrichment keys may be overwritten this run")
            existing = {}

        saved = 0
        refused = 0
        for p in predictions:
            try:
                row = _row_for_db(p)
                if row is None:
                    refused += 1
                    logger.error(
                        f"Refusing to save {p.get('match_id')}: stats has no "
                        f"prob_home — model output missing, not a real prediction."
                    )
                    continue
                prev = existing.get(str(row.get("match_id"))) or {}
                if prev:
                    merged = {**prev, **row["stats"]}
                    merged["writer"] = row["stats"].get("writer", "pico")
                    row["stats"] = merged
                sb.table("predictions").upsert(row, on_conflict="match_id").execute()
                saved += 1
            except Exception as e:
                logger.warning(f"Failed to save prediction {p.get('match_id')}: {e}")

        logger.info(f"Saved {saved}/{len(predictions)} predictions to Supabase."
                    + (f" {refused} refused (no model probabilities)." if refused else ""))
        self.prune_duplicate_predictions({p.get("match_id") for p in predictions if p.get("match_id")})
        return saved

    def prune_duplicate_predictions(self, keep_match_ids: set[str]) -> int:
        """Remove rows that duplicate a fixture already stored under another id.

        The same fixture can end up in the table twice when the id scheme
        changes (e.g. a bare ``575368`` from an older writer and the current
        ``fdorg_575368``). Those stale copies have no model output attached,
        so the app would render the same match twice — once as a fresh
        prediction and once as an empty duplicate.
        """
        sb = self._ensure_supabase()
        if sb is None or not keep_match_ids:
            return 0
        try:
            resp = sb.table("predictions") \
                .select("id, match_id, home_team, away_team, match_date") \
                .eq("status", "upcoming") \
                .execute()
            rows = resp.data or []
        except Exception as e:
            logger.warning(f"Could not fetch predictions for de-duplication: {e}")
            return 0

        def _norm(v: str) -> str:
            return "".join(ch for ch in (v or "").lower() if ch.isalnum())

        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            key = (_norm(r.get("home_team", "")), _norm(r.get("away_team", "")),
                   str(r.get("match_date", ""))[:10])
            groups.setdefault(key, []).append(r)

        stale_ids: list[str] = []
        for key, group in groups.items():
            if len(group) < 2:
                continue
            # Prefer the row whose id we just wrote; otherwise keep the
            # lexicographically smallest id and drop the rest.
            preferred = sorted(
                group,
                key=lambda r: (r.get("match_id") not in keep_match_ids, str(r.get("match_id"))),
            )[0]
            for r in group:
                if r["id"] != preferred["id"]:
                    stale_ids.append(r["id"])

        if not stale_ids:
            return 0

        deleted = 0
        for i in range(0, len(stale_ids), 50):
            chunk = stale_ids[i:i + 50]
            try:
                sb.table("predictions").delete().in_("id", chunk).execute()
                deleted += len(chunk)
            except Exception as e:
                logger.warning(f"Could not delete {len(chunk)} duplicate predictions: {e}")
        if deleted:
            logger.info(f"Pruned {deleted} duplicate prediction row(s).")
        return deleted

    def settle_past_predictions(self) -> int:
        """Settle finished matches with real scores.

        The actual work lives in ``ai/settle_predictions.py`` so it can run
        on its own CI schedule without loading LightGBM; this wrapper only
        supplies the engine's Supabase client.
        """
        sb = self._ensure_supabase()
        if sb is None:
            return 0
        try:
            from settle_predictions import settle as _settle
        except ModuleNotFoundError:
            # ai/ is not on sys.path (e.g. package-style import) — fix it up.
            ai_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if ai_dir not in sys.path:
                sys.path.insert(0, ai_dir)
            from settle_predictions import settle as _settle
        return _settle(sb)

    def run_and_push(self, leagues: list[str] | None = None,
                     days_ahead: int = 14) -> int:
        preds = self.run(leagues=leagues, days_ahead=days_ahead)
        return self.push_to_supabase(preds)
