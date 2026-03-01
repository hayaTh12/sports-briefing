"""
Impact scoring engine.

Assigns a numeric impact score to every ``GameEvent`` and ``UpcomingMatch``
based on configurable factor weights.  Higher score = more important / more
interesting.

Score composition
-----------------
Completed games
  base (1.0) + upset + rivalry + close_game + favorite_team + recency + drama

Upcoming matches
  base (1.0) + high_stakes + rivalry + favorite_team + imminence
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sports_briefing.config import Config
from sports_briefing.models import GameEvent, LeagueData, UpcomingMatch

logger = logging.getLogger(__name__)

# Close-game margin thresholds per league
_CLOSE_MARGIN: dict[str, float] = {
    "nba": 6,
    "premier_league": 1,
    "nhl": 1,
    "formula1": 0,  # not applicable
}


class ScoringEngine:
    """Scores events and generates human-readable context strings."""

    def __init__(self, config: Config) -> None:
        """Initialise the engine with weights from *config*.

        Args:
            config: Loaded application configuration.
        """
        self._w = config.scoring_weights

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_league_data(self, league_data: LeagueData) -> LeagueData:
        """Score all events in *league_data* in-place and return it.

        Args:
            league_data: Raw league data from a fetcher.

        Returns:
            The same object with ``impact_score``, ``headline``, and
            ``watch_reason`` populated.
        """
        for event in league_data.recent_results:
            event.impact_score = self.score_game(event)
            if not event.headline:
                event.headline = self._game_headline(event)
            event.headline = self._enrich_headline(event)

        for match in league_data.upcoming_matches:
            match.impact_score = self.score_upcoming(match)
            match.watch_reason = self._watch_reason(match)

        return league_data

    def score_game(self, event: GameEvent) -> float:
        """Calculate an impact score for a completed *event*.

        Args:
            event: A finished game or race.

        Returns:
            Numeric impact score (≥ 1.0).
        """
        score = 1.0

        score += self._upset_bonus(event)
        score += self._rivalry_bonus(event.is_rivalry)
        score += self._close_game_bonus(event)
        score += self._drama_bonus(event)
        score += self._favorite_bonus(event.is_favorite_involved)
        score += self._recency_bonus(event.date)

        return round(score, 2)

    def score_upcoming(self, match: UpcomingMatch) -> float:
        """Calculate an impact score for an *upcoming* match.

        Args:
            match: A scheduled fixture.

        Returns:
            Numeric impact score (≥ 1.0).
        """
        score = 1.0

        score += self._high_stakes_bonus(match.home_standing, match.away_standing)
        score += self._rivalry_bonus(match.is_rivalry)
        score += self._favorite_bonus(match.is_favorite_involved)
        score += self._imminence_bonus(match.kickoff)

        return round(score, 2)

    # ------------------------------------------------------------------
    # Score factors
    # ------------------------------------------------------------------

    def _upset_bonus(self, event: GameEvent) -> float:
        """Bonus when a lower-ranked team beats a higher-ranked one."""
        if not (event.home_standing and event.away_standing and event.winner):
            return 0.0

        if event.winner == event.home_team:
            winner_rank, loser_rank = event.home_standing, event.away_standing
        else:
            winner_rank, loser_rank = event.away_standing, event.home_standing

        if winner_rank is None or loser_rank is None:
            return 0.0

        # Higher rank number = worse team.  Upset if winner_rank > loser_rank.
        rank_diff = winner_rank - loser_rank
        if rank_diff <= 0:
            return 0.0

        bonus = min(rank_diff / 5.0, 3.0)  # cap at 3×
        return bonus * self._w.get("upset", 2.0)

    def _rivalry_bonus(self, is_rivalry: bool) -> float:
        """Fixed bonus for rivalry matches."""
        return self._w.get("rivalry", 1.5) if is_rivalry else 0.0

    def _close_game_bonus(self, event: GameEvent) -> float:
        """Bonus for tight results."""
        if event.home_score is None or event.away_score is None:
            return 0.0
        margin = abs(event.home_score - event.away_score)
        threshold = _CLOSE_MARGIN.get(event.league, 1)
        return self._w.get("close_game", 1.2) if margin <= threshold else 0.0

    def _drama_bonus(self, event: GameEvent) -> float:
        """Small bonus for games that went to OT / shootout / extra time."""
        ctx = event.context.lower()
        if any(kw in ctx for kw in ("overtime", "shootout", "extra time", "ot")):
            return 0.5
        return 0.0

    def _favorite_bonus(self, is_favorite: bool) -> float:
        """Large boost when user's favourite team is involved."""
        return self._w.get("favorite_team", 3.0) if is_favorite else 0.0

    def _recency_bonus(self, event_date: datetime) -> float:
        """Decay bonus — fresher events score higher."""
        now = datetime.now()
        event_dt = event_date.replace(tzinfo=None) if event_date.tzinfo else event_date
        hours_ago = (now - event_dt).total_seconds() / 3600
        if hours_ago < 0 or hours_ago > 48:
            return 0.0
        return max(0.0, 1.0 - hours_ago / 48) * self._w.get("recency", 0.5)

    def _high_stakes_bonus(
        self, home_rank: Optional[int], away_rank: Optional[int]
    ) -> float:
        """Bonus when both teams are near the top of the table."""
        if home_rank is None or away_rank is None:
            return 0.0
        avg = (home_rank + away_rank) / 2
        if avg > 8:
            return 0.0
        # Scale: avg=1 → full weight, avg=8 → 0
        fraction = max(0.0, (9 - avg) / 8)
        return fraction * self._w.get("high_stakes", 2.0)

    def _imminence_bonus(self, kickoff: datetime) -> float:
        """Small bonus for matches happening soon (within 12 h)."""
        now = datetime.now()
        ko = kickoff.replace(tzinfo=None) if kickoff.tzinfo else kickoff
        hours_until = (ko - now).total_seconds() / 3600
        if hours_until < 0 or hours_until > 24:
            return 0.0
        return max(0.0, 1.0 - hours_until / 24) * 0.5

    # ------------------------------------------------------------------
    # Text generation
    # ------------------------------------------------------------------

    def _game_headline(self, event: GameEvent) -> str:
        """Build a default headline for a completed event."""
        if event.league == "formula1":
            return f"{event.home_team} wins the {event.away_team}"
        if event.home_score is not None and event.away_score is not None:
            return (
                f"{event.away_team} {int(event.away_score)}"
                f" – {int(event.home_score)} "
                f"{event.home_team}"
            )
        return f"{event.home_team} vs {event.away_team}"

    def _enrich_headline(self, event: GameEvent) -> str:
        """Append contextual tags (rivalry / OT) to an existing headline."""
        tags: list[str] = []
        if event.is_rivalry:
            tags.append("rivalry")
        ctx = event.context.lower()
        if "overtime" in ctx or " ot" in ctx:
            tags.append("OT")
        if "shootout" in ctx:
            tags.append("SO")
        if "extra time" in ctx:
            tags.append("AET")
        if tags:
            return f"{event.headline}  [{', '.join(tags)}]"
        return event.headline

    def _watch_reason(self, match: UpcomingMatch) -> str:
        """Generate a human-readable reason to watch *match*."""
        reasons: list[str] = []

        if match.is_favorite_involved:
            reasons.append("your team is playing")

        if match.is_rivalry:
            reasons.append("fierce rivalry")

        h, a = match.home_standing, match.away_standing
        if h and a:
            if h <= 3 or a <= 3:
                reasons.append(f"top-of-table clash (#{h} vs #{a})")
            elif h <= 6 and a <= 6:
                reasons.append(f"high-stakes top-6 clash (#{h} vs #{a})")
            elif abs(h - a) <= 2 and h <= 10:
                reasons.append(f"tight standings battle (#{h} vs #{a})")

        return ", ".join(reasons) if reasons else "interesting fixture"
