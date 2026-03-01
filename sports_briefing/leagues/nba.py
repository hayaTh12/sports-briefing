"""
NBA data fetcher — uses the unofficial nba_api library.

nba_api scrapes NBA.com and can be slow (5–30 s per endpoint).  All
responses are aggressively cached so only the first run of the day
hits the network.

Data retrieved
--------------
- League standings (sorted by win percentage)
- Scores for the past 3 days  (filters to Final games)
- Scheduled games for the next 2 days (filters to not-yet-started)
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

logger = logging.getLogger(__name__)

def _current_nba_season() -> str:
    """Return the NBA season string for today (e.g. '2025-26').

    The NBA season starts in October.  In October–December the season
    label uses the current calendar year; from January–September it uses
    the previous calendar year.
    """
    now = datetime.now()
    year = now.year if now.month >= 10 else now.year - 1
    return f"{year}-{str(year + 1)[2:]}"


_SEASON = _current_nba_season()


class NBAFetcher(BaseLeagueFetcher):
    """Fetches NBA results, upcoming games, and standings via nba_api."""

    league_name = "nba"

    def __init__(self, config: Config, cache: Cache) -> None:
        super().__init__(config, cache)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch(self) -> LeagueData:
        """Fetch all NBA data and return a populated ``LeagueData``.

        Returns:
            League data with recent results, upcoming matches, and standings.
            ``fetch_error`` is set if nba_api is unavailable.
        """
        data = LeagueData(league="nba")

        try:
            standings = self._fetch_standings()
            data.standings = standings
            standing_map = self._build_standing_map(standings)

            # Recent results — last 3 days (covers US timezones)
            for days_ago in range(0, 3):
                date = datetime.now() - timedelta(days=days_ago)
                results = self._fetch_scoreboard(date, standing_map, mode="results")
                data.recent_results.extend(results)

            # Upcoming games — next 2 days
            for days_ahead in range(1, 3):
                date = datetime.now() + timedelta(days=days_ahead)
                upcoming = self._fetch_scoreboard(date, standing_map, mode="upcoming")
                data.upcoming_matches.extend(upcoming)  # type: ignore[arg-type]

        except Exception as exc:
            logger.error("NBA fetch failed: %s", exc)
            data.fetch_error = str(exc)

        return data

    # ------------------------------------------------------------------
    # Standings
    # ------------------------------------------------------------------

    def _fetch_standings(self) -> list[TeamStanding]:
        """Return league-wide standings sorted by win percentage."""
        cache_key = f"nba_standings_{datetime.now().strftime('%Y-%m-%d')}"
        cached = self.cache.get(cache_key)
        if cached:
            return [TeamStanding(**s) for s in cached]

        try:
            from nba_api.stats.endpoints import leaguestandings  # noqa: PLC0415

            ls = leaguestandings.LeagueStandings(season=_SEASON, timeout=60)
            df = ls.get_data_frames()[0]
        except Exception as exc:
            logger.warning("NBA standings fetch failed: %s", exc)
            return []

        # Sort by WinPCT descending and assign positions 1..30
        try:
            df = df.sort_values("WinPCT", ascending=False).reset_index(drop=True)
        except KeyError:
            pass  # Column name might differ; use existing order

        standings: list[TeamStanding] = []
        for pos, (_, row) in enumerate(df.iterrows(), start=1):
            standings.append(
                TeamStanding(
                    team_name=str(row.get("TeamName", "Unknown")),
                    position=pos,
                    points=0,  # NBA uses win %
                    wins=int(row.get("WINS", row.get("W", 0))),
                    losses=int(row.get("LOSSES", row.get("L", 0))),
                    league="nba",
                    extra={"win_pct": float(row.get("WinPCT", 0.0))},
                )
            )

        self.cache.set(cache_key, [_standing_to_dict(s) for s in standings])
        return standings

    # ------------------------------------------------------------------
    # Scoreboards (results + upcoming share the same endpoint)
    # ------------------------------------------------------------------

    def _fetch_scoreboard(
        self,
        date: datetime,
        standing_map: dict[str, int],
        mode: str,
    ) -> list[GameEvent | UpcomingMatch]:
        """Fetch ScoreboardV2 for *date* and return either results or upcoming.

        Args:
            date:         Date to query.
            standing_map: Nickname → league position.
            mode:         "results" or "upcoming".
        """
        date_str = date.strftime("%Y-%m-%d")
        cache_key = f"nba_scoreboard_{date_str}"
        cached = self.cache.get(cache_key)

        raw: Optional[dict[str, Any]] = None
        if cached:
            raw = cached
        else:
            raw = self._call_scoreboard(date_str)
            if raw:
                self.cache.set(cache_key, raw)

        if not raw:
            return []

        if mode == "results":
            return self._parse_results(raw, date, standing_map)
        return self._parse_upcoming(raw, date, standing_map)

    def _call_scoreboard(self, date_str: str) -> Optional[dict[str, Any]]:
        """Call the nba_api ScoreboardV2 endpoint.

        Args:
            date_str: Date in YYYY-MM-DD format.

        Returns:
            Raw dict with "games" and "scores" lists, or ``None``.
        """
        try:
            from nba_api.stats.endpoints import scoreboardv2  # noqa: PLC0415

            sb = scoreboardv2.ScoreboardV2(game_date=date_str, timeout=60)
            dfs = sb.get_data_frames()
            return {
                "games": dfs[0].to_dict(orient="records"),
                "scores": dfs[1].to_dict(orient="records"),
            }
        except Exception as exc:
            logger.warning("NBA scoreboard fetch failed for %s: %s", date_str, exc)
            return None

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_results(
        self,
        raw: dict[str, Any],
        date: datetime,
        standing_map: dict[str, int],
    ) -> list[GameEvent]:
        """Extract completed games from a raw scoreboard response."""
        events: list[GameEvent] = []
        score_by_game = self._group_scores(raw.get("scores", []))

        for game in raw.get("games", []):
            status = str(game.get("GAME_STATUS_TEXT", ""))
            if "final" not in status.lower():
                continue

            event = self._build_game_event(game, score_by_game, date, standing_map)
            if event:
                events.append(event)

        return events

    def _parse_upcoming(
        self,
        raw: dict[str, Any],
        date: datetime,
        standing_map: dict[str, int],
    ) -> list[UpcomingMatch]:
        """Extract not-yet-started games from a raw scoreboard response."""
        upcoming: list[UpcomingMatch] = []
        score_by_game = self._group_scores(raw.get("scores", []))

        for game in raw.get("games", []):
            status = str(game.get("GAME_STATUS_TEXT", ""))
            if "final" in status.lower():
                continue  # Already finished

            gid = game.get("GAME_ID")
            game_scores = score_by_game.get(gid, [])
            if len(game_scores) < 2:
                continue

            home_data, away_data = self._split_home_away(game, game_scores)
            if not home_data or not away_data:
                continue

            home_team = self._team_name(home_data)
            away_team = self._team_name(away_data)

            upcoming.append(
                UpcomingMatch(
                    league="nba",
                    home_team=home_team,
                    away_team=away_team,
                    kickoff=date,
                    home_standing=standing_map.get(
                        str(home_data.get("TEAM_NICKNAME", "")).lower()
                    ),
                    away_standing=standing_map.get(
                        str(away_data.get("TEAM_NICKNAME", "")).lower()
                    ),
                    is_rivalry=is_rivalry(
                        "nba",
                        home_team,
                        away_team,
                        self.config.custom_rivalries("nba"),
                    ),
                    is_favorite_involved=(
                        self.is_favorite_team(home_team)
                        or self.is_favorite_team(away_team)
                    ),
                )
            )

        return upcoming

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_game_event(
        self,
        game: dict,
        score_by_game: dict,
        date: datetime,
        standing_map: dict[str, int],
    ) -> Optional[GameEvent]:
        """Build a ``GameEvent`` from a single game dict."""
        gid = game.get("GAME_ID")
        game_scores = score_by_game.get(gid, [])
        if len(game_scores) < 2:
            return None

        home_data, away_data = self._split_home_away(game, game_scores)
        if not home_data or not away_data:
            return None

        home_team = self._team_name(home_data)
        away_team = self._team_name(away_data)
        home_pts = int(home_data.get("PTS") or 0)
        away_pts = int(away_data.get("PTS") or 0)
        winner = home_team if home_pts > away_pts else away_team

        status = str(game.get("GAME_STATUS_TEXT", ""))
        context_parts: list[str] = []
        margin = abs(home_pts - away_pts)
        if "OT" in status.upper():
            context_parts.append("overtime")
        if margin <= 5:
            context_parts.append(f"won by {margin}")

        return GameEvent(
            league="nba",
            event_type="game",
            home_team=home_team,
            away_team=away_team,
            home_score=float(home_pts),
            away_score=float(away_pts),
            date=date,
            home_standing=standing_map.get(
                str(home_data.get("TEAM_NICKNAME", "")).lower()
            ),
            away_standing=standing_map.get(
                str(away_data.get("TEAM_NICKNAME", "")).lower()
            ),
            is_rivalry=is_rivalry(
                "nba",
                home_team,
                away_team,
                self.config.custom_rivalries("nba"),
            ),
            is_favorite_involved=(
                self.is_favorite_team(home_team) or self.is_favorite_team(away_team)
            ),
            winner=winner,
            headline=f"{away_team} {away_pts} – {home_pts} {home_team}",
            context=", ".join(context_parts),
        )

    @staticmethod
    def _group_scores(scores: list[dict]) -> dict[Any, list[dict]]:
        """Group LineScore rows by GAME_ID."""
        grouped: dict[Any, list[dict]] = {}
        for row in scores:
            gid = row.get("GAME_ID")
            grouped.setdefault(gid, []).append(row)
        return grouped

    @staticmethod
    def _split_home_away(
        game: dict, game_scores: list[dict]
    ) -> tuple[Optional[dict], Optional[dict]]:
        """Return (home_row, away_row) from a list of two LineScore rows."""
        home_id = game.get("HOME_TEAM_ID")
        visitor_id = game.get("VISITOR_TEAM_ID")
        home = next((s for s in game_scores if s.get("TEAM_ID") == home_id), None)
        away = next((s for s in game_scores if s.get("TEAM_ID") == visitor_id), None)
        return home, away

    @staticmethod
    def _team_name(row: dict) -> str:
        """Build a full team name from a LineScore row."""
        city = str(row.get("TEAM_CITY_NAME", "")).strip()
        nick = str(row.get("TEAM_NICKNAME", "")).strip()
        return f"{city} {nick}".strip()

    @staticmethod
    def _build_standing_map(standings: list[TeamStanding]) -> dict[str, int]:
        """Map lower-cased team nickname → position."""
        mapping: dict[str, int] = {}
        for s in standings:
            # Store by last word (nickname) for matching LineScore TEAM_NICKNAME
            parts = s.team_name.strip().split()
            if parts:
                mapping[parts[-1].lower()] = s.position
            mapping[s.team_name.lower()] = s.position
        return mapping


# ---------------------------------------------------------------------------
# Serialisation helper (datetime-safe)
# ---------------------------------------------------------------------------

def _standing_to_dict(s: TeamStanding) -> dict:
    """Convert a TeamStanding to a JSON-serialisable dict."""
    return {
        "team_name": s.team_name,
        "position": s.position,
        "points": s.points,
        "wins": s.wins,
        "losses": s.losses,
        "league": s.league,
        "extra": s.extra,
    }
