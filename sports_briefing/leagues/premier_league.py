"""
Premier League fetcher — uses the football-data.org v4 REST API.

Free tier: 10 requests / minute, competitions limited to 12 per day.
We make at most 3 calls per run (standings + recent + upcoming).

Env vars required
-----------------
FOOTBALL_DATA_KEY   Your football-data.org API token (https://www.football-data.org)
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

_API_BASE = "https://api.football-data.org/v4"
_COMPETITION = "PL"  # Premier League competition code


class PremierLeagueFetcher(BaseLeagueFetcher):
    """Fetches Premier League data from football-data.org."""

    league_name = "premier_league"

    def __init__(self, config: Config, cache: Cache) -> None:
        super().__init__(config, cache)
        self._key = config.football_data_key
        self._headers: dict[str, str] = (
            {"X-Auth-Token": self._key} if self._key else {}
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch(self) -> LeagueData:
        """Fetch standings, recent results, and upcoming PL fixtures."""
        data = LeagueData(league="premier_league")

        if not self._key:
            data.fetch_error = "FOOTBALL_DATA_KEY not set in .env"
            logger.warning("Premier League: FOOTBALL_DATA_KEY not configured")
            return data

        try:
            standings = self._fetch_standings()
            data.standings = standings
            standing_map = {s.team_name.lower(): s.position for s in standings}

            # Results from the last 48 h
            yesterday = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")
            today = datetime.now().strftime("%Y-%m-%d")
            data.recent_results = self._fetch_fixtures(
                from_date=yesterday, to_date=today, standing_map=standing_map
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
            f"{_API_BASE}/competitions/{_COMPETITION}/standings",
            headers=self._headers,
            timeout=20,
        )
        if not raw:
            return []

        standings: list[TeamStanding] = []
        try:
            # Find the TOTAL standings table (as opposed to HOME / AWAY)
            table: list[dict] = []
            for group in raw.get("standings", []):
                if group.get("type") == "TOTAL":
                    table = group["table"]
                    break

            for entry in table:
                standings.append(
                    TeamStanding(
                        team_name=entry["team"]["name"],
                        position=int(entry["position"]),
                        points=int(entry["points"]),
                        wins=int(entry.get("won", 0)),
                        losses=int(entry.get("lost", 0)),
                        league="premier_league",
                        extra={
                            "goals_for": int(entry.get("goalsFor", 0)),
                            "goals_against": int(entry.get("goalsAgainst", 0)),
                            "goal_diff": int(entry.get("goalDifference", 0)),
                            "form": str(entry.get("form", "") or ""),
                            "played": int(entry.get("playedGames", 0)),
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
        standing_map: dict[str, int],
    ) -> list[GameEvent]:
        """Fetch finished fixtures between *from_date* and *to_date*."""
        cache_key = f"pl_fixtures_{from_date}_{to_date}"
        cached = self.cache.get(cache_key)

        raw: Optional[Any] = None
        if cached:
            raw = cached
        else:
            raw = get_json(
                f"{_API_BASE}/competitions/{_COMPETITION}/matches",
                headers=self._headers,
                params={
                    "status": "FINISHED",
                    "dateFrom": from_date,
                    "dateTo": to_date,
                },
                timeout=20,
            )
            if raw:
                self.cache.set(cache_key, raw)

        if not raw:
            return []

        events: list[GameEvent] = []
        for match in raw.get("matches", []):
            event = self._parse_fixture(match, standing_map)
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
                f"{_API_BASE}/competitions/{_COMPETITION}/matches",
                headers=self._headers,
                params={
                    "status": "SCHEDULED",
                    "dateFrom": from_date,
                    "dateTo": to_date,
                },
                timeout=20,
            )
            if raw:
                self.cache.set(cache_key, raw)

        if not raw:
            return []

        upcoming: list[UpcomingMatch] = []
        for match in raw.get("matches", []):
            um = self._parse_upcoming_fixture(match, standing_map)
            if um:
                upcoming.append(um)

        return upcoming

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_fixture(
        self, match: dict, standing_map: dict[str, int]
    ) -> Optional[GameEvent]:
        """Build a ``GameEvent`` from a single football-data.org match dict."""
        try:
            home_name: str = match["homeTeam"]["name"]
            away_name: str = match["awayTeam"]["name"]

            score: dict = match["score"]
            full_time: dict = score.get("fullTime", {})
            home_goals: int = int(full_time.get("home") or 0)
            away_goals: int = int(full_time.get("away") or 0)

            date_str: str = match["utcDate"]
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(
                tzinfo=None
            )

            margin = abs(home_goals - away_goals)
            context_parts: list[str] = []
            if margin == 0:
                context_parts.append("draw")
            elif margin == 1:
                context_parts.append("one-goal thriller")

            duration: str = score.get("duration", "REGULAR")
            if duration == "EXTRA_TIME":
                context_parts.append("extra time")
            elif duration == "PENALTY_SHOOTOUT":
                context_parts.append("penalties")

            winner_code: Optional[str] = score.get("winner")
            winner: Optional[str] = None
            if winner_code == "HOME_TEAM":
                winner = home_name
            elif winner_code == "AWAY_TEAM":
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
        self, match: dict, standing_map: dict[str, int]
    ) -> Optional[UpcomingMatch]:
        """Build an ``UpcomingMatch`` from a single football-data.org match dict."""
        try:
            home_name: str = match["homeTeam"]["name"]
            away_name: str = match["awayTeam"]["name"]

            date_str: str = match["utcDate"]
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
