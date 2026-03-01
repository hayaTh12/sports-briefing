"""
Markdown renderer for the daily brief.

Produces a single self-contained Markdown file per day.  The file is
stored under briefs/YYYY-MM-DD.md and committed by the GitHub Actions
workflow so you accumulate a full historical archive.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from sports_briefing.models import DailyBrief, GameEvent, UpcomingMatch

# Display names and icons used in section headers
_LEAGUE_LABEL: dict[str, str] = {
    "nba": "🏀 NBA",
    "premier_league": "⚽ Premier League",
    "formula1": "🏎️ Formula 1",
    "nhl": "🏒 NHL",
}


def render_markdown(brief: DailyBrief) -> str:
    """Render *brief* as a Markdown string.

    Args:
        brief: Compiled daily brief.

    Returns:
        Full Markdown content ready to be written to disk.
    """
    lines: list[str] = []
    date_str = brief.date.strftime("%B %d, %Y")

    lines += [
        f"# 📊 Sports Briefing — {date_str}",
        "",
        f"> Generated at **{brief.date.strftime('%H:%M UTC')}** "
        f"by [sports-briefing](https://github.com/your-username/sports-briefing)",
        "",
        "---",
        "",
    ]

    lines += _section_top_storylines(brief.top_storylines)
    lines += _section_60_seconds(brief.league_summaries)
    lines += _section_must_watch(brief.must_watch)
    lines += _section_can_skip(brief.can_skip)
    lines += _section_full_results(brief.all_results)

    lines += [
        "---",
        "",
        f"*Brief generated on {brief.date.strftime('%Y-%m-%d')} at {brief.date.strftime('%H:%M')} UTC.*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _section_top_storylines(storylines: list[GameEvent]) -> list[str]:
    lines = [
        "## 🔥 Top 5 Storylines of the Day",
        "",
    ]

    if not storylines:
        lines += ["*No results in the last 48 hours.*", ""]
        return lines

    for i, event in enumerate(storylines[:5], 1):
        label = _LEAGUE_LABEL.get(event.league, event.league.upper())
        score_stars = _impact_stars(event.impact_score)
        lines.append(f"### {i}. {event.headline}")
        lines.append(f"*{label} · Impact {score_stars} ({event.impact_score:.1f})*")
        if event.context:
            lines.append(f"> {event.context.capitalize()}")
        lines.append("")

    return lines


def _section_60_seconds(summaries: dict[str, str]) -> list[str]:
    lines = [
        "## ⚡ What You Missed in 60 Seconds",
        "",
    ]

    league_order = ["nba", "premier_league", "formula1", "nhl"]
    all_leagues = league_order + [k for k in summaries if k not in league_order]

    for league in all_leagues:
        if league not in summaries:
            continue
        label = _LEAGUE_LABEL.get(league, league.upper())
        lines.append(f"- **{label}:** {summaries[league]}")

    lines.append("")
    return lines


def _section_must_watch(matches: list[UpcomingMatch]) -> list[str]:
    lines = [
        "## 📺 Matches Worth Watching",
        "",
    ]

    if not matches:
        lines += ["*No upcoming matches in the next 48 hours.*", ""]
        return lines

    for match in matches[:5]:
        label = _LEAGUE_LABEL.get(match.league, match.league.upper())
        time_str = match.kickoff.strftime("%H:%M UTC") if match.kickoff else "TBD"
        rank_str = _rank_context(match.home_standing, match.away_standing)
        score_stars = _impact_stars(match.impact_score)

        lines.append(f"### {match.home_team} vs {match.away_team}")
        lines.append(
            f"*{label} · {time_str}"
            + (f" · {rank_str}" if rank_str else "")
            + f" · Impact {score_stars}*"
        )
        if match.watch_reason:
            lines.append(f"> **Why watch:** {match.watch_reason.capitalize()}")
        lines.append("")

    return lines


def _section_can_skip(events: list[GameEvent]) -> list[str]:
    lines = [
        "## 💤 What You Can Skip",
        "",
    ]

    if not events:
        lines += ["*Everything was worth watching today!*", ""]
        return lines

    for event in events[:8]:
        label = _LEAGUE_LABEL.get(event.league, event.league.upper())
        lines.append(
            f"- {label}: {event.headline} *(impact: {event.impact_score:.1f})*"
        )

    lines.append("")
    return lines


def _section_full_results(results: list[GameEvent]) -> list[str]:
    """Collapsible full results section — useful reference, not cluttered."""
    if not results:
        return []

    lines = [
        "## 📋 Full Results",
        "",
        "<details>",
        "<summary>Click to expand all results</summary>",
        "",
    ]

    by_league: dict[str, list[GameEvent]] = {}
    for e in results:
        by_league.setdefault(e.league, []).append(e)

    league_order = ["nba", "premier_league", "formula1", "nhl"]
    all_leagues = league_order + [k for k in by_league if k not in league_order]

    for league in all_leagues:
        if league not in by_league:
            continue
        label = _LEAGUE_LABEL.get(league, league.upper())
        lines.append(f"### {label}")
        lines.append("")
        for event in by_league[league]:
            lines.append(f"- {event.headline}" + (f" — {event.context}" if event.context else ""))
        lines.append("")

    lines += ["</details>", ""]
    return lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _impact_stars(score: float) -> str:
    """Convert a numeric impact score into a star string (max 5 ⭐)."""
    stars = min(5, max(1, round(score / 2)))
    return "⭐" * stars


def _rank_context(home_rank: Optional[int], away_rank: Optional[int]) -> str:
    """Build a short rank string like '#2 vs #5'."""
    if home_rank and away_rank:
        return f"#{home_rank} vs #{away_rank}"
    return ""


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def save_brief(brief: DailyBrief, briefs_dir: Path) -> Path:
    """Render *brief* to Markdown and write it to *briefs_dir*.

    Args:
        brief:      The compiled daily brief.
        briefs_dir: Directory where the file should be saved.

    Returns:
        Path to the written file.
    """
    briefs_dir.mkdir(parents=True, exist_ok=True)
    filename = brief.date.strftime("%Y-%m-%d") + ".md"
    output_path = briefs_dir / filename

    content = render_markdown(brief)
    output_path.write_text(content, encoding="utf-8")

    return output_path
