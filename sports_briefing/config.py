"""
Configuration loader.

Reads config.yaml (or user-supplied path), deep-merges over safe defaults,
and exposes typed accessors.  API keys come exclusively from .env / environment
variables — never from the config file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Defaults — every key here can be overridden in config.yaml
# ---------------------------------------------------------------------------

_DEFAULTS: dict = {
    "output": {
        "briefs_dir": "./briefs",
    },
    "leagues": {
        "nba": {
            "enabled": True,
            "favorite_teams": [],
            "favorite_players": [],
        },
        "premier_league": {
            "enabled": True,
            "favorite_teams": [],
            "favorite_players": [],
        },
        "formula1": {
            "enabled": True,
            "favorite_drivers": [],
            "favorite_teams": [],
        },
        "nhl": {
            "enabled": True,
            "favorite_teams": [],
            "favorite_players": [],
        },
    },
    "scoring": {
        "weights": {
            "upset": 2.0,
            "rivalry": 1.5,
            "close_game": 1.2,
            "standings_change": 1.5,
            "favorite_team": 3.0,
            "high_stakes": 2.0,
            "recency": 0.5,
        },
        "skip_threshold": 2.5,
        "custom_rivalries": {
            "nba": [],
            "premier_league": [],
            "nhl": [],
        },
    },
    "cache": {
        "ttl_hours": 6,
        "cache_dir": "./cache",
    },
}


class Config:
    """Merges user config.yaml over built-in defaults and exposes typed properties."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        """Load and merge configuration from *config_path*.

        Args:
            config_path: Path to the YAML config file.  Missing file is silently
                         ignored and defaults are used.
        """
        self._data = self._load(config_path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self, path: str) -> dict:
        """Return merged config dict."""
        config_file = Path(path)
        if not config_file.exists():
            return _DEFAULTS

        with config_file.open(encoding="utf-8") as fh:
            user_config: dict = yaml.safe_load(fh) or {}

        return self._deep_merge(_DEFAULTS, user_config)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Recursively merge *override* into a copy of *base*."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = Config._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    @property
    def briefs_dir(self) -> Path:
        """Directory where daily brief Markdown files are saved."""
        return Path(self._data["output"]["briefs_dir"])

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    @property
    def cache_dir(self) -> Path:
        """Root directory for the local JSON cache."""
        return Path(self._data["cache"]["cache_dir"])

    @property
    def cache_ttl_hours(self) -> int:
        """How many hours a cached response is considered fresh."""
        return int(self._data["cache"]["ttl_hours"])

    # ------------------------------------------------------------------
    # Leagues
    # ------------------------------------------------------------------

    def league_config(self, league: str) -> dict:
        """Raw config dict for *league*."""
        return self._data["leagues"].get(league, {})

    def is_league_enabled(self, league: str) -> bool:
        """Return True if *league* should be fetched."""
        return bool(self.league_config(league).get("enabled", True))

    def favorite_teams(self, league: str) -> list[str]:
        """Lower-cased favorite team / driver names for *league*."""
        lc = self.league_config(league)
        teams = lc.get("favorite_teams", []) or []
        drivers = lc.get("favorite_drivers", []) or []
        return [t.lower() for t in teams + drivers]

    def favorite_players(self, league: str) -> list[str]:
        """Lower-cased favorite player names for *league*."""
        lc = self.league_config(league)
        return [p.lower() for p in (lc.get("favorite_players", []) or [])]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @property
    def scoring_weights(self) -> dict:
        """Factor weights used by the scoring engine."""
        return self._data["scoring"]["weights"]

    @property
    def skip_threshold(self) -> float:
        """Events with impact_score ≤ this value appear in 'can skip'."""
        return float(self._data["scoring"].get("skip_threshold", 2.5))

    def custom_rivalries(self, league: str) -> list[list[str]]:
        """User-defined rivalry pairs for *league*."""
        return self._data["scoring"]["custom_rivalries"].get(league, []) or []

    # ------------------------------------------------------------------
    # API keys  (env-only — never stored in config file)
    # ------------------------------------------------------------------

    @property
    def api_football_key(self) -> Optional[str]:
        """API-Football key loaded from environment."""
        return os.getenv("API_FOOTBALL_KEY")
