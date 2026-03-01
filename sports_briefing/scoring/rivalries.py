"""
Hardcoded rivalry registry + user-defined rivalry matching.

Rivalry detection uses substring matching so it is robust to slightly
different team name formats across APIs (e.g. "Los Angeles Lakers" vs
"Lakers").
"""

from __future__ import annotations

from typing import Optional

# Each rivalry is a frozenset of two lowercase substrings that identify
# the two clubs.  A team name matches if *any* of its parts contains one
# of these substrings.

_NBA: list[frozenset[str]] = [
    frozenset({"lakers", "celtics"}),
    frozenset({"warriors", "cavaliers"}),
    frozenset({"knicks", "nets"}),
    frozenset({"bulls", "pistons"}),
    frozenset({"heat", "knicks"}),
    frozenset({"lakers", "clippers"}),
    frozenset({"thunder", "spurs"}),
    frozenset({"76ers", "celtics"}),
    frozenset({"bucks", "celtics"}),
]

_PREMIER_LEAGUE: list[frozenset[str]] = [
    frozenset({"manchester united", "liverpool"}),
    frozenset({"arsenal", "tottenham"}),       # North London Derby
    frozenset({"chelsea", "arsenal"}),
    frozenset({"manchester city", "manchester united"}),  # Manchester Derby
    frozenset({"liverpool", "everton"}),       # Merseyside Derby
    frozenset({"chelsea", "tottenham"}),
    frozenset({"newcastle", "sunderland"}),
    frozenset({"leeds", "manchester united"}),
    frozenset({"west ham", "millwall"}),
]

_NHL: list[frozenset[str]] = [
    frozenset({"bruins", "canadiens"}),
    frozenset({"rangers", "islanders"}),
    frozenset({"maple leafs", "canadiens"}),
    frozenset({"capitals", "penguins"}),
    frozenset({"blackhawks", "red wings"}),
    frozenset({"flyers", "penguins"}),
    frozenset({"avalanche", "red wings"}),
    frozenset({"oilers", "flames"}),           # Battle of Alberta
    frozenset({"sharks", "kings"}),
]

_REGISTRY: dict[str, list[frozenset[str]]] = {
    "nba": _NBA,
    "premier_league": _PREMIER_LEAGUE,
    "nhl": _NHL,
}


def _team_matches(team_name: str, identifier: str) -> bool:
    """Return True if *identifier* appears as a substring of *team_name*.

    Both strings are compared in lower-case.

    Args:
        team_name:  Full team name from the API (e.g. "Los Angeles Lakers").
        identifier: Short identifier from the rivalry registry (e.g. "lakers").
    """
    t = team_name.lower()
    i = identifier.lower()
    return i in t


def _pair_matches(team1: str, team2: str, rivalry: frozenset[str]) -> bool:
    """Return True if *team1* and *team2* correspond to the two clubs in *rivalry*."""
    ids = list(rivalry)
    if len(ids) != 2:
        return False
    a, b = ids[0], ids[1]
    return (_team_matches(team1, a) and _team_matches(team2, b)) or (
        _team_matches(team1, b) and _team_matches(team2, a)
    )


def is_rivalry(
    league: str,
    team1: str,
    team2: str,
    custom_pairs: Optional[list[list[str]]] = None,
) -> bool:
    """Detect whether *team1* vs *team2* is a recognised rivalry in *league*.

    Checks the built-in registry first, then any user-defined pairs from
    ``config.yaml``.

    Args:
        league:       League key ("nba", "premier_league", "nhl").
        team1:        Home team name as returned by the API.
        team2:        Away team name as returned by the API.
        custom_pairs: Optional list of [teamA, teamB] pairs from config.

    Returns:
        ``True`` if this is a rivalry match.
    """
    # Built-in
    for rivalry in _REGISTRY.get(league, []):
        if _pair_matches(team1, team2, rivalry):
            return True

    # User-defined
    for pair in custom_pairs or []:
        if len(pair) == 2:
            custom_rivalry: frozenset[str] = frozenset({pair[0].lower(), pair[1].lower()})
            if _pair_matches(team1, team2, custom_rivalry):
                return True

    return False
