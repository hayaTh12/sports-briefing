"""
Formula 1 fetcher — uses the FastF1 library with its built-in disk cache.

FastF1 downloads session data from the official F1 timing API and the
Ergast historical API.  The first load for any session can take tens of
seconds; subsequent loads are instant (data is cached under cache/fastf1/).

Data retrieved
--------------
- Most recent race result (top-5 podium + any favourite driver/team)
- Upcoming race weekends within the next 14 days
- Driver and constructor standings via the Ergast API (lightweight call)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sports_briefing.config import Config
from sports_briefing.leagues.base import BaseLeagueFetcher
from sports_briefing.models import GameEvent, LeagueData, TeamStanding, UpcomingMatch
from sports_briefing.utils.cache import Cache
from sports_briefing.utils.http import get_json

logger = logging.getLogger(__name__)

_ERGAST_BASE = "https://ergast.com/api/f1"


class Formula1Fetcher(BaseLeagueFetcher):
    """Fetches F1 race results and upcoming events."""

    league_name = "formula1"

    def __init__(self, config: Config, cache: Cache) -> None:
        super().__init__(config, cache)
        self._setup_fastf1_cache()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch(self) -> LeagueData:
        """Fetch the latest race result, upcoming events, and standings.

        Returns:
            Populated ``LeagueData``.
        """
        data = LeagueData(league="formula1")

        try:
            import fastf1  # noqa: PLC0415

            year = datetime.now().year
            try:
                schedule = fastf1.get_event_schedule(year, include_testing=False)
            except Exception as exc:
                logger.warning("F1 schedule unavailable for %s: %s", year, exc)
                data.fetch_error = f"Schedule unavailable: {exc}"
                return data

            now = datetime.now()

            # --- Recent result ---
            past = schedule[schedule["EventDate"].dt.tz_localize(None) < now]
            if not past.empty:
                last = past.iloc[-1]
                result = self._fetch_race_result(
                    year, int(last["RoundNumber"]), str(last["EventName"])
                )
                if result:
                    data.recent_results.append(result)

            # --- Upcoming events (next 14 days) ---
            future = schedule[schedule["EventDate"].dt.tz_localize(None) >= now]
            for _, event in future.iterrows():
                event_date = event["EventDate"]
                if hasattr(event_date, "to_pydatetime"):
                    event_dt: datetime = event_date.to_pydatetime().replace(tzinfo=None)
                else:
                    event_dt = datetime.fromisoformat(str(event_date))

                days_until = (event_dt - now).days
                if days_until > 14:
                    break

                event_name = str(event.get("EventName", "Grand Prix"))
                country = str(event.get("Country", ""))

                fav = self._is_fav_event(event_name)

                data.upcoming_matches.append(
                    UpcomingMatch(
                        league="formula1",
                        home_team=event_name,
                        away_team=country,
                        kickoff=event_dt,
                        is_rivalry=False,
                        is_favorite_involved=fav,
                        watch_reason=(
                            "your favourite team/driver is competing"
                            if fav
                            else "F1 race weekend"
                        ),
                    )
                )

            # --- Standings (via Ergast — lightweight) ---
            data.standings = self._fetch_driver_standings(year)

        except ImportError:
            data.fetch_error = "fastf1 not installed (pip install fastf1)"
            logger.warning("fastf1 is not installed — F1 data unavailable")
        except Exception as exc:
            logger.error("F1 fetch failed: %s", exc)
            data.fetch_error = str(exc)

        return data

    # ------------------------------------------------------------------
    # Race result
    # ------------------------------------------------------------------

    def _fetch_race_result(
        self, year: int, round_num: int, event_name: str
    ) -> Optional[GameEvent]:
        """Load the race session for *round_num* and build a ``GameEvent``.

        Uses FastF1's built-in cache so the download only happens once.

        Args:
            year:       Season year.
            round_num:  Race round number (1-based).
            event_name: Human-readable event name.

        Returns:
            A ``GameEvent`` representing the race winner, or ``None``.
        """
        # We use a lightweight cache key to avoid re-parsing the FastF1 session
        # on every run (FastF1 already has its own cache, but session.load()
        # still takes a few seconds even from cache).
        cache_key = f"f1_race_result_{year}_{round_num}"
        cached = self.cache.get(cache_key)
        if cached:
            return self._dict_to_game_event(cached)

        try:
            import fastf1  # noqa: PLC0415

            session = fastf1.get_session(year, round_num, "R")
            # Load only what we need — skip telemetry, weather, and messages
            session.load(telemetry=False, weather=False, messages=False)

            if session.results is None or session.results.empty:
                logger.warning("No results for F1 round %s", round_num)
                return None

            results = session.results
            winner_row = results.iloc[0]
            winner_name = str(winner_row.get("FullName", winner_row.get("Abbreviation", "Unknown")))
            winner_team = str(winner_row.get("TeamName", "Unknown"))

            # P2 for context
            p2_name = (
                str(results.iloc[1].get("FullName", results.iloc[1].get("Abbreviation", "")))
                if len(results) > 1
                else ""
            )

            # Check for favourite involvement in top 10
            fav = any(
                self.is_favorite_team(str(results.iloc[i].get("FullName", "")))
                or self.is_favorite_team(str(results.iloc[i].get("TeamName", "")))
                or self.is_favorite_player(str(results.iloc[i].get("FullName", "")))
                for i in range(min(10, len(results)))
            )

            # Session date
            session_date: datetime = (
                session.date.to_pydatetime().replace(tzinfo=None)
                if hasattr(session.date, "to_pydatetime")
                else datetime.now() - timedelta(days=7)
            )

            context = f"P2: {p2_name}" if p2_name else ""

            raw = {
                "league": "formula1",
                "event_type": "race",
                "home_team": winner_name,
                "away_team": event_name,
                "home_score": 1.0,
                "away_score": 2.0,
                "date": session_date.isoformat(),
                "is_rivalry": False,
                "is_favorite_involved": fav,
                "winner": winner_name,
                "headline": f"{event_name}: {winner_name} wins ({winner_team})",
                "context": context,
            }
            self.cache.set(cache_key, raw)
            return self._dict_to_game_event(raw)

        except Exception as exc:
            logger.warning("Failed to load F1 race %s R%s: %s", year, round_num, exc)
            return None

    # ------------------------------------------------------------------
    # Driver standings (via Ergast)
    # ------------------------------------------------------------------

    def _fetch_driver_standings(self, year: int) -> list[TeamStanding]:
        """Fetch current driver standings from the Ergast API.

        Args:
            year: Season year.

        Returns:
            List of ``TeamStanding`` (one per driver), or empty list.
        """
        cache_key = f"f1_standings_{year}_{datetime.now().strftime('%Y-%m-%d')}"
        cached = self.cache.get(cache_key)
        if cached:
            return [TeamStanding(**s) for s in cached]

        raw = get_json(
            f"{_ERGAST_BASE}/{year}/driverStandings.json",
            timeout=15,
        )
        if not raw:
            return []

        standings: list[TeamStanding] = []
        try:
            table = raw["MRData"]["StandingsTable"]["StandingsLists"]
            if not table:
                return []
            driver_list = table[0]["DriverStandings"]
            for entry in driver_list:
                driver = entry["Driver"]
                constructors = entry.get("Constructors", [{}])
                team = constructors[0].get("name", "Unknown") if constructors else "Unknown"
                standings.append(
                    TeamStanding(
                        team_name=f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                        position=int(entry["position"]),
                        points=int(float(entry.get("points", 0))),
                        wins=int(entry.get("wins", 0)),
                        losses=0,
                        league="formula1",
                        extra={"team": team},
                    )
                )
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.warning("Failed to parse F1 standings: %s", exc)
            return []

        self.cache.set(cache_key, [_standing_to_dict(s) for s in standings])
        return standings

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _setup_fastf1_cache(self) -> None:
        """Enable FastF1's local disk cache."""
        try:
            import fastf1  # noqa: PLC0415

            fastf1_cache = self.cache.cache_dir / "fastf1"
            fastf1_cache.mkdir(parents=True, exist_ok=True)
            fastf1.Cache.enable_cache(str(fastf1_cache))
        except ImportError:
            pass  # Error surfaced in fetch()

    def _is_fav_event(self, event_name: str) -> bool:
        """Check whether any favourite driver/team is expected at this event."""
        # We can't know who'll be there without loading sessions;
        # just return True if any fav string appears in the event name or
        # if any favourites are configured (they'll always be at every race).
        favs = self.config.favorite_teams("formula1")
        if favs:
            return True  # Favourite drivers are at every race
        return False

    @staticmethod
    def _dict_to_game_event(d: dict) -> GameEvent:
        """Reconstruct a ``GameEvent`` from a cache dict."""
        date_val = d.get("date", "")
        if isinstance(date_val, str):
            try:
                date = datetime.fromisoformat(date_val)
            except ValueError:
                date = datetime.now() - timedelta(days=7)
        else:
            date = datetime.now() - timedelta(days=7)

        return GameEvent(
            league=d.get("league", "formula1"),
            event_type=d.get("event_type", "race"),
            home_team=d.get("home_team", ""),
            away_team=d.get("away_team", ""),
            home_score=d.get("home_score"),
            away_score=d.get("away_score"),
            date=date,
            is_rivalry=bool(d.get("is_rivalry", False)),
            is_favorite_involved=bool(d.get("is_favorite_involved", False)),
            winner=d.get("winner"),
            headline=d.get("headline", ""),
            context=d.get("context", ""),
        )


# ---------------------------------------------------------------------------
# Serialisation helpers
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
