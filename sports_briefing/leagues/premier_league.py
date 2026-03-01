"""
Premier League fetcher — uses the API-Football v3 REST API.

Free tier: 100 calls / day.  We make at most 3 calls per run
(standings + recent fixtures + upcoming fixtures) so 30+ runs/day
are within quota.

Env vars required
-----------------
API_FOOTBALL_KEY   Your API-Football key (https://www.api-football.com)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sports_briefing.config import Config
from sports_briefing.leagues.base import BaseLeagueFetcher
from sports_briefing.models import GameEvent, LeagueData, TeamStanding, UpcomingMatch
from sports_briefing.scoring.rivalries import is_rivalry
from sports_briefing.utils.cache import Cache
from sports_briefing.utils.http import get_json

logger = logging.getLogger(__name__)

_API_BASE = "https://v3.football.api-sports.io"
_LEAGUE_ID = 39   # Premier League


def _current_pl_season() -> int:
    """Return the API-Football season year for today (e.g. 2025).

    The Premier League season starts in August.  From August onward the
    season label is the current year; before August it is the previous year.
    """
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1


_SEASON = _current_pl_season()


class PremierLeagueFetcher(BaseLeagueFetcher):
    """Fetches Premier League data from API-Football."""

    league_name = "premier_league"

    def __init__(self, config: Config, cache: Cache) -> None:
        super().__init__(config, cache)
        self._key = config.api_football_key
        self._headers: dict[str, str] = (
            {"x-apisports-key": self._key} if self._key else {}
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch(self) -> LeagueData:
        """Fetch standings, recent results, and upcoming PL fixtures.

        Returns:
            Populated ``LeagueData``.  Sets ``fetch_error`` if the API key
            is missing or the API is unreachable.
        """
        data = LeagueData(league="premier_league")

        if not self._key:
            data.fetch_error = "API_FOOTBALL_KEY not set in .env"
            logger.warning("Premier League: API_FOOTBALL_KEY not configured")
            return data

        try:
            standings = self._fetch_standings()
            data.standings = standings
            standing_map = {s.team_name.lower(): s.position for s in standings}

            # Results from the last 48 h
            yesterday = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")
            today = datetime.now().strftime("%Y-%m-%d")
            data.recent_results = self._fetch_fixtures(
                from_date=yesterday, to_date=today, status="FT", standing_map=standing_map
            )

            # Upcoming fixtures in the next 48 h
            tomorrow_plus = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d")
            data.upcoming_matches = self._fetch_upcoming(
                from_date=today, to_date=tomorrow_plus, standing_map=standing_map
            )

        except Exception as exc:
            logger.error("Premier League fetch failed: %s", exc)
            data.fetch_error = str(exc)

        return data

    # ------------------------------------------------------------------
    # Standings
    # ------------------------------------------------------------------

    def _fetch_standings(self) -> list[TeamStanding]:
        """Return the current Premier League table."""
        cache_key = f"pl_standings_{datetime.now().strftime('%Y-%m-%d')}"
        cached = self.cache.get(cache_key)
        if cached:
            return [TeamStanding(**entry) for entry in cached]

        raw = get_json(
            f"{_API_BASE}/standings",
            headers=self._headers,
            params={"league": _LEAGUE_ID, "season": _SEASON},
            timeout=20,
        )
        if not raw:
            return []

        standings: list[TeamStanding] = []
        try:
            table: list[dict] = raw["response"][0]["league"]["standings"][0]
            for entry in table:
                all_stats: dict = entry.get("all", {})
                goals: dict = all_stats.get("goals", {})
                standings.append(
                    TeamStanding(
                        team_name=entry["team"]["name"],
                        position=int(entry["rank"]),
                        points=int(entry["points"]),
                        wins=int(all_stats.get("win", 0)),
                        losses=int(all_stats.get("lose", 0)),
                        league="premier_league",
                        extra={
                            "goals_for": int(goals.get("for", 0)),
                            "goals_against": int(goals.get("against", 0)),
                            "goal_diff": int(entry.get("goalsDiff", 0)),
                            "form": str(entry.get("form", "")),
                            "played": int(all_stats.get("played", 0)),
                        },
                    )
                )
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Failed to parse PL standings: %s", exc)
            return []

        self.cache.set(cache_key, [_standing_to_dict(s) for s in standings])
        return standings

    # ------------------------------------------------------------------
    # Completed fixtures
    # ------------------------------------------------------------------

    def _fetch_fixtures(
        self,
        from_date: str,
        to_date: str,
        status: str,
        standing_map: dict[str, int],
    ) -> list[GameEvent]:
        """Fetch finished fixtures between *from_date* and *to_date*."""
        cache_key = f"pl_fixtures_{from_date}_{to_date}_{status}"
        cached = self.cache.get(cache_key)

        raw: Optional[Any] = None
        if cached:
            raw = cached
        else:
            raw = get_json(
                f"{_API_BASE}/fixtures",
                headers=self._headers,
                params={
                    "league": _LEAGUE_ID,
                    "season": _SEASON,
                    "from": from_date,
                    "to": to_date,
                    "status": status,
                },
                timeout=20,
            )
            if raw:
                self.cache.set(cache_key, raw)

        if not raw:
            return []

        events: list[GameEvent] = []
        for fixture in raw.get("response", []):
            event = self._parse_fixture(fixture, standing_map)
            if event:
                events.append(event)

        return events

    # ------------------------------------------------------------------
    # Upcoming fixtures
    # ------------------------------------------------------------------

    def _fetch_upcoming(
        self,
        from_date: str,
        to_date: str,
        standing_map: dict[str, int],
    ) -> list[UpcomingMatch]:
        """Fetch not-yet-played fixtures between *from_date* and *to_date*."""
        cache_key = f"pl_upcoming_{from_date}_{to_date}"
        cached = self.cache.get(cache_key)

        raw: Optional[Any] = None
        if cached:
            raw = cached
        else:
            raw = get_json(
                f"{_API_BASE}/fixtures",
                headers=self._headers,
                params={
                    "league": _LEAGUE_ID,
                    "season": _SEASON,
                    "from": from_date,
                    "to": to_date,
                    "status": "NS",
                },
                timeout=20,
            )
            if raw:
                self.cache.set(cache_key, raw)

        if not raw:
            return []

        upcoming: list[UpcomingMatch] = []
        for fixture in raw.get("response", []):
            match = self._parse_upcoming_fixture(fixture, standing_map)
            if match:
                upcoming.append(match)

        return upcoming

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_fixture(
        self, fixture: dict, standing_map: dict[str, int]
    ) -> Optional[GameEvent]:
        """Build a ``GameEvent`` from a single API-Football fixture dict."""
        try:
            teams: dict = fixture["teams"]
            goals: dict = fixture["goals"]
            home_name: str = teams["home"]["name"]
            away_name: str = teams["away"]["name"]
            home_goals: int = int(goals.get("home") or 0)
            away_goals: int = int(goals.get("away") or 0)

            date_str: str = fixture["fixture"]["date"]
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(
                tzinfo=None
            )

            margin = abs(home_goals - away_goals)
            context_parts: list[str] = []
            if margin == 0:
                context_parts.append("draw")
            elif margin == 1:
                context_parts.append("one-goal thriller")

            # Detect extra time / penalties from fixture status
            fixture_status: str = fixture.get("fixture", {}).get("status", {}).get(
                "short", ""
            )
            if fixture_status == "AET":
                context_parts.append("extra time")
            elif fixture_status == "PEN":
                context_parts.append("penalties")

            winner: Optional[str] = None
            if home_goals > away_goals:
                winner = home_name
            elif away_goals > home_goals:
                winner = away_name

            return GameEvent(
                league="premier_league",
                event_type="game",
                home_team=home_name,
                away_team=away_name,
                home_score=float(home_goals),
                away_score=float(away_goals),
                date=date,
                home_standing=standing_map.get(home_name.lower()),
                away_standing=standing_map.get(away_name.lower()),
                is_rivalry=is_rivalry(
                    "premier_league",
                    home_name,
                    away_name,
                    self.config.custom_rivalries("premier_league"),
                ),
                is_favorite_involved=(
                    self.is_favorite_team(home_name)
                    or self.is_favorite_team(away_name)
                ),
                winner=winner,
                headline=f"{home_name} {home_goals}–{away_goals} {away_name}",
                context=", ".join(context_parts),
            )

        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to parse PL fixture: %s", exc)
            return None

    def _parse_upcoming_fixture(
        self, fixture: dict, standing_map: dict[str, int]
    ) -> Optional[UpcomingMatch]:
        """Build an ``UpcomingMatch`` from a single API-Football fixture dict."""
        try:
            teams: dict = fixture["teams"]
            home_name: str = teams["home"]["name"]
            away_name: str = teams["away"]["name"]

            date_str: str = fixture["fixture"]["date"]
            kickoff = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(
                tzinfo=None
            )

            return UpcomingMatch(
                league="premier_league",
                home_team=home_name,
                away_team=away_name,
                kickoff=kickoff,
                home_standing=standing_map.get(home_name.lower()),
                away_standing=standing_map.get(away_name.lower()),
                is_rivalry=is_rivalry(
                    "premier_league",
                    home_name,
                    away_name,
                    self.config.custom_rivalries("premier_league"),
                ),
                is_favorite_involved=(
                    self.is_favorite_team(home_name)
                    or self.is_favorite_team(away_name)
                ),
            )

        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to parse PL upcoming fixture: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------

def _standing_to_dict(s: TeamStanding) -> dict:
    """Convert a ``TeamStanding`` to a JSON-serialisable dict."""
    return {
        "team_name": s.team_name,
        "position": s.position,
        "points": s.points,
        "wins": s.wins,
        "losses": s.losses,
        "league": s.league,
        "extra": s.extra,
    }
