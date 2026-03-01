"""
Abstract base class for all league fetchers.

Every league module exposes a single class that inherits from
``BaseLeagueFetcher`` and implements ``fetch()``.  Common helpers
(favourite-team / favourite-player detection) live here so they don't
need to be duplicated across modules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sports_briefing.config import Config
from sports_briefing.models import LeagueData
from sports_briefing.utils.cache import Cache


class BaseLeagueFetcher(ABC):
    """Shared interface and utilities for league data fetchers."""

    def __init__(self, config: Config, cache: Cache) -> None:
        """Initialise the fetcher.

        Args:
            config: Application configuration (weights, favourites, …).
            cache:  Shared disk cache instance.
        """
        self.config = config
        self.cache = cache

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def league_name(self) -> str:
        """Canonical league key used throughout the codebase."""
        ...

    @abstractmethod
    def fetch(self) -> LeagueData:
        """Fetch recent results, upcoming fixtures, and standings.

        Returns:
            ``LeagueData`` populated with whatever could be retrieved.
            If data is unavailable, ``fetch_error`` should be set and
            the lists can remain empty — callers must handle this.
        """
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def is_favorite_team(self, team_name: str) -> bool:
        """Return True if *team_name* matches any configured favourite team.

        Matching is case-insensitive substring search so partial names work
        (e.g. "Lakers" matches "Los Angeles Lakers").

        Args:
            team_name: Team name as returned by the API.
        """
        name_lower = team_name.lower()
        return any(fav in name_lower for fav in self.config.favorite_teams(self.league_name))

    def is_favorite_player(self, player_name: str) -> bool:
        """Return True if *player_name* matches any configured favourite player.

        Args:
            player_name: Player full name as returned by the API.
        """
        name_lower = player_name.lower()
        return any(fav in name_lower for fav in self.config.favorite_players(self.league_name))
