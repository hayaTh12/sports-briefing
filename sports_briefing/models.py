"""
Shared data models for the sports-briefing pipeline.

All league fetchers produce these standard types; the scoring engine
and output renderers consume them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TeamStanding:
    """A single team's current position in its league table."""

    team_name: str
    position: int
    points: int
    wins: int
    losses: int
    league: str
    extra: dict = field(default_factory=dict)  # league-specific data (goals, OT losses …)


@dataclass
class GameEvent:
    """A completed match / race result."""

    league: str
    event_type: str          # "game" | "race" | "qualifying"
    home_team: str
    away_team: str
    home_score: Optional[float]
    away_score: Optional[float]
    date: datetime
    home_standing: Optional[int] = None
    away_standing: Optional[int] = None
    is_rivalry: bool = False
    is_favorite_involved: bool = False
    winner: Optional[str] = None
    impact_score: float = 0.0
    headline: str = ""
    context: str = ""        # e.g. "overtime thriller", "5-goal margin"


@dataclass
class UpcomingMatch:
    """A scheduled fixture that has not yet been played."""

    league: str
    home_team: str
    away_team: str
    kickoff: datetime
    home_standing: Optional[int] = None
    away_standing: Optional[int] = None
    is_rivalry: bool = False
    is_favorite_involved: bool = False
    impact_score: float = 0.0
    watch_reason: str = ""


@dataclass
class LeagueData:
    """All fetched data for a single league, post-fetch."""

    league: str
    recent_results: list[GameEvent] = field(default_factory=list)
    upcoming_matches: list[UpcomingMatch] = field(default_factory=list)
    standings: list[TeamStanding] = field(default_factory=list)
    fetch_error: Optional[str] = None


@dataclass
class DailyBrief:
    """The final compiled brief, ready for rendering."""

    date: datetime
    top_storylines: list[GameEvent]
    league_summaries: dict[str, str]   # league → one-liner
    must_watch: list[UpcomingMatch]
    can_skip: list[GameEvent]
    all_results: list[GameEvent]
    all_upcoming: list[UpcomingMatch]
