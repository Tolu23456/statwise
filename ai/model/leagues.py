"""Canonical league slugs for every MODEL-facing lookup.

The fixture feeds and the training set name the same competition differently:

    live feed (ai/data/clean -> season CSVs)   training slug (rows)
    -----------------------------------------  --------------------
    efl-championship                       ->  championship          (18,619)
    turkish-super-lig                      ->  super-lig             (11,150)
    brazilian-serie-a                      ->  serie-a-br             (6,208)
    argentina-primera                      ->  primera-division       (6,283)
    europa-league                          ->  champions-league         (539)
    conference-league                      ->  champions-league         (539)
    brasileirao (odds key)                 ->  serie-a-br             (6,208)

Six of the twenty-one slugs the live pipeline fetches had ZERO training rows,
so `league_id` fell through the map, league priors (avg goals, home advantage,
draw rate) came from the default dict, per-league market alphas never matched,
and league support reported the competition as unsupported — the model was
guessing blind in exactly the competitions that play midweek.

Only model-facing lookups are canonicalised. What is written to Supabase keeps
the live slug: settlement and the ESPN scoreboard lookup depend on it. The map
is idempotent — no alias is itself a key — so applying it twice is safe.
"""
from __future__ import annotations

LEAGUE_ALIASES: dict[str, str] = {
    # English second tier
    "efl-championship":  "championship",
    # Turkey
    "turkish-super-lig": "super-lig",
    # South America
    "brazilian-serie-a": "serie-a-br",
    "brasileirao":       "serie-a-br",
    "argentina-primera": "primera-division",
    # UEFA cups: no season CSV of their own, so they are scored against the
    # one European competition the training set does carry.
    "europa-league":     "champions-league",
    "conference-league": "champions-league",
}


def canon_league(slug) -> str:
    """Map a fixture-feed league slug onto its training-set spelling.

    Unknown slugs pass through untouched — a league the training set simply
    does not carry (saudi-pro-league) must stay visible as unsupported rather
    than be silently relabelled as some other competition.
    """
    if slug is None:
        return "all"
    s = str(slug).strip() or "all"
    return LEAGUE_ALIASES.get(s, s)
