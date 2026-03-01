"""
NHL fetcher — uses the official NHL web API (no key required).

Base URL: https://api-web.nhle.com/v1

Endpoints used
--------------
GET /score/{date}        — completed and live game scores
GET /schedule/{date}     — scheduled games
GET /standings/now       — current league standings
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

_NHL_API = "https://api-web.nhle.com/v1"
_FINAL_STATES = {"FINAL", "OFF"}
_UPCOMING_STATES = {"FUT", "PRE"}


class NHLFetcher(BaseLeagueFetcher):
    """Fetches NHL scores, schedule, and standings."""

    league_name = "nhl"

    def __init__(self, config: Config, cache: Cache) -> None:
        super().__init__(config, cache)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch(self) -> LeagueData:
        """Fetch NHL standings, recent scores, and upcoming games.

        Returns:
            Populated ``LeagueData``.
        """
        data = LeagueData(league="nhl")

        try:
            standings = self._fetch_standings()
            data.standings = standings
            standing_map = self._build_standing_map(standings)

            # Recent results — last 3 days
            for days_ago in range(0, 3):
                date = datetime.now() - timedelta(days=days_ago)
                results = self._fetch_scores(date, standing_map)
                data.recent_results.extend(results)

            # Upcoming — next 2 days
            for days_ahead in range(1, 3):
                date = datetime.now() + timedelta(days=days_ahead)
                upcoming = self._fetch_schedule(date, standing_map)
                data.upcoming_matches.extend(upcoming)

        except Exception as exc:
            logger.error("NHL fetch failed: %s", exc)
            data.fetch_error = str(exc)

        return data

    # ------------------------------------------------------------------
    # Standings
    # ------------------------------------------------------------------

    def _fetch_standings(self) -> list[TeamStanding]:
        """Return current NHL standings."""
        cache_key = f"nhl_standings_{datetime.now().strftime('%Y-%m-%d')}"
        cached = self.cache.get(cache_key)
        if cached:
            return [TeamStanding(**s) for s in cached]

        raw = get_json(f"{_NHL_API}/standings/now", timeout=20)
        if not raw:
            return []

        standings: list[TeamStanding] = []
        for entry in raw.get("standings", []):
            team_name = (
                entry.get("teamName", {}).get("default", "Unknown")
                if isinstance(entry.get("teamName"), dict)
                else str(entry.get("teamName", "Unknown"))
            )
            standings.append(
                TeamStanding(
                    team_name=team_name,
                    position=int(entry.get("conferenceSequence", entry.get("leagueSequence", 99))),
                    points=int(entry.get("points", 0)),
                    wins=int(entry.get("wins", 0)),
                    losses=int(entry.get("losses", 0)),
                    league="nhl",
                    extra={
                        "ot_losses": int(entry.get("otLosses", 0)),
                        "goals_for": int(entry.get("goalFor", 0)),
                        "goals_against": int(entry.get("goalAgainst", 0)),
                        "division": str(entry.get("divisionName", "")),
                        "conference": str(entry.get("conferenceName", "")),
                    },
                )
            )

        # Sort by points descending and reassign league-wide position
        standings.sort(key=lambda s: s.points, reverse=True)
        for pos, s in enumerate(standings, start=1):
            s.position = pos

        self.cache.set(cache_key, [_standing_to_dict(s) for s in standings])
        return standings

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------

    def _fetch_scores(
        self, date: datetime, standing_map: dict[str, int]
    ) -> list[GameEvent]:
        """Return completed games for *date*."""
        date_str = date.strftime("%Y-%m-%d")
        cache_key = f"nhl_scores_{date_str}"
        cached = self.cache.get(cache_key)

        raw: Optional[Any] = None
        if cached:
            raw = cached
        else:
            raw = get_json(f"{_NHL_API}/score/{date_str}", timeout=20)
            if raw:
                self.cache.set(cache_key, raw)

        if not raw:
            return []

        events: list[GameEvent] = []
        for game in raw.get("games", []):
            if game.get("gameState") not in _FINAL_STATES:
                continue
            event = self._parse_score(game, date, standing_map)
            if event:
                events.append(event)

        return events

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def _fetch_schedule(
        self, date: datetime, standing_map: dict[str, int]
    ) -> list[UpcomingMatch]:
        """Return upcoming games scheduled for *date*."""
        date_str = date.strftime("%Y-%m-%d")
        cache_key = f"nhl_schedule_{date_str}"
        cached = self.cache.get(cache_key)

        raw: Optional[Any] = None
        if cached:
            raw = cached
        else:
            raw = get_json(f"{_NHL_API}/schedule/{date_str}", timeout=20)
            if raw:
                self.cache.set(cache_key, raw)

        if not raw:
            return []

        # The NHL schedule endpoint returns a full week of games.
        # Filter to only the target date to avoid duplicates when we call
        # this function for day+1 and day+2 separately.
        target_date_str = date.strftime("%Y-%m-%d")

        upcoming: list[UpcomingMatch] = []
        for game_week in raw.get("gameWeek", []):
            if game_week.get("date") != target_date_str:
                continue  # Skip other days in the week view
            for game in game_week.get("games", []):
                if game.get("gameState") in _FINAL_STATES:
                    continue
                match = self._parse_upcoming(game, date, standing_map)
                if match:
                    upcoming.append(match)

        return upcoming

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_score(
        self, game: dict, date: datetime, standing_map: dict[str, int]
    ) -> Optional[GameEvent]:
        """Build a ``GameEvent`` from a raw game dict from /score."""
        try:
            home_data: dict = game["homeTeam"]
            away_data: dict = game["awayTeam"]

            home_name = self._common_name(home_data)
            away_name = self._common_name(away_data)
            home_score = int(home_data.get("score", 0))
            away_score = int(away_data.get("score", 0))
            winner = home_name if home_score > away_score else away_name

            # Period context
            period_desc: dict = game.get("periodDescriptor", {})
            period_type: str = period_desc.get("periodType", "REG")

            context_parts: list[str] = []
            if period_type == "OT":
                context_parts.append("overtime")
            elif period_type == "SO":
                context_parts.append("shootout")
            if abs(home_score - away_score) <= 1 and period_type == "REG":
                context_parts.append("one-goal game")

            return GameEvent(
                league="nhl",
                event_type="game",
                home_team=home_name,
                away_team=away_name,
                home_score=float(home_score),
                away_score=float(away_score),
                date=date,
                home_standing=standing_map.get(home_name.lower()),
                away_standing=standing_map.get(away_name.lower()),
                is_rivalry=is_rivalry(
                    "nhl",
                    home_name,
                    away_name,
                    self.config.custom_rivalries("nhl"),
                ),
                is_favorite_involved=(
                    self.is_favorite_team(home_name)
                    or self.is_favorite_team(away_name)
                ),
                winner=winner,
                headline=f"{away_name} {away_score} @ {home_name} {home_score}",
                context=", ".join(context_parts),
            )

        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to parse NHL score: %s", exc)
            return None

    def _parse_upcoming(
        self, game: dict, date: datetime, standing_map: dict[str, int]
    ) -> Optional[UpcomingMatch]:
        """Build an ``UpcomingMatch`` from a raw schedule game dict."""
        try:
            home_data: dict = game["homeTeam"]
            away_data: dict = game["awayTeam"]

            home_name = self._common_name(home_data)
            away_name = self._common_name(away_data)

            kickoff_str: str = game.get("startTimeUTC", "")
            if kickoff_str:
                kickoff = datetime.fromisoformat(
                    kickoff_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            else:
                kickoff = date

            return UpcomingMatch(
                league="nhl",
                home_team=home_name,
                away_team=away_name,
                kickoff=kickoff,
                home_standing=standing_map.get(home_name.lower()),
                away_standing=standing_map.get(away_name.lower()),
                is_rivalry=is_rivalry(
                    "nhl",
                    home_name,
                    away_name,
                    self.config.custom_rivalries("nhl"),
                ),
                is_favorite_involved=(
                    self.is_favorite_team(home_name)
                    or self.is_favorite_team(away_name)
                ),
            )

        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to parse NHL upcoming game: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _common_name(team_data: dict) -> str:
        """Extract the team's common name from a team sub-dict."""
        common = team_data.get("commonName")
        if isinstance(common, dict):
            return str(common.get("default", "Unknown"))
        if isinstance(common, str):
            return common
        # Fallback: use place name + team name
        place = team_data.get("placeName", {})
        name = team_data.get("name", {})
        if isinstance(place, dict) and isinstance(name, dict):
            return f"{place.get('default', '')} {name.get('default', '')}".strip()
        return "Unknown"

    @staticmethod
    def _build_standing_map(standings: list[TeamStanding]) -> dict[str, int]:
        """Map lower-cased team name variants → league position."""
        mapping: dict[str, int] = {}
        for s in standings:
            mapping[s.team_name.lower()] = s.position
            # Also map the last word (e.g. "Maple Leafs" → "leafs")
            parts = s.team_name.strip().split()
            if parts:
                mapping[parts[-1].lower()] = s.position
        return mapping


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------

def _standing_to_dict(s: TeamStanding) -> dict:
    return {
        "team_name": s.team_name,
        "position": s.position,
        "points": s.points,
        "wins": s.wins,
        "losses": s.losses,
        "league": s.league,
        "extra": s.extra,
    }
