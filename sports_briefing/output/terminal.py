"""
Rich terminal output for the daily brief.

Renders the brief directly in the terminal with colour, icons, impact
bars, and structured panels — making it both functional and visually
impressive for portfolio demos.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from sports_briefing.models import DailyBrief, GameEvent, UpcomingMatch

# force_terminal=True prevents Rich from falling back to the legacy Windows
# console renderer (which uses CP1252 and cannot encode emojis / ✓).
# stdout is reconfigured to UTF-8 in main.py before this module is loaded.
console = Console(force_terminal=True)

# Per-league theming
_ICONS: dict[str, str] = {
    "nba": "🏀",
    "premier_league": "⚽",
    "formula1": "🏎️",
    "nhl": "🏒",
}

_COLORS: dict[str, str] = {
    "nba": "orange1",
    "premier_league": "medium_purple1",
    "formula1": "red1",
    "nhl": "cyan1",
}

_LEAGUE_LABELS: dict[str, str] = {
    "nba": "NBA",
    "premier_league": "Premier League",
    "formula1": "Formula 1",
    "nhl": "NHL",
}


def display_brief(brief: DailyBrief) -> None:
    """Print the full daily brief to the terminal.

    Args:
        brief: The compiled daily brief.
    """
    console.print()
    console.print(
        Rule(
            f"[bold white] Sports Briefing — {brief.date.strftime('%A, %B %d %Y')} [/bold white]",
            style="bright_blue",
            characters="═",
        )
    )
    console.print()

    _render_top_storylines(brief.top_storylines)
    _render_60_seconds(brief.league_summaries)
    _render_must_watch(brief.must_watch)
    _render_can_skip(brief.can_skip)

    console.print(Rule(style="dim", characters="─"))
    console.print(
        f"[dim]  Generated at {brief.date.strftime('%H:%M UTC')}  |  "
        f"{len(brief.all_results)} results · {len(brief.all_upcoming)} upcoming[/dim]"
    )
    console.print()


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_top_storylines(storylines: list[GameEvent]) -> None:
    """Render the 'Top 5 Storylines' panel."""
    console.print(
        Panel(
            "[bold yellow]🔥  Top 5 Storylines of the Day[/bold yellow]",
            border_style="yellow",
            expand=False,
            padding=(0, 1),
        )
    )
    console.print()

    if not storylines:
        console.print("  [dim italic]No results in the last 48 hours.[/dim italic]")
        console.print()
        return

    for i, event in enumerate(storylines[:5], 1):
        icon = _ICONS.get(event.league, "🏆")
        color = _COLORS.get(event.league, "white")
        label = _LEAGUE_LABELS.get(event.league, event.league.upper())

        # Impact bar (1–10 blocks, colour-coded)
        bar_len = min(10, max(1, int(event.impact_score)))
        bar_color = (
            "green" if event.impact_score < 3.5
            else "yellow" if event.impact_score < 6
            else "red"
        )
        bar = f"[{bar_color}]{'█' * bar_len}{'░' * (10 - bar_len)}[/{bar_color}]"

        console.print(
            f"  [bold dim]{i}.[/bold dim] "
            f"[{color} bold]{icon}  {event.headline}[/{color} bold]"
        )
        if event.context:
            console.print(f"      [italic dim]{event.context}[/italic dim]")
        console.print(
            f"      {bar}  [dim]{label} · {event.impact_score:.1f}[/dim]"
        )
        console.print()


def _render_60_seconds(summaries: dict[str, str]) -> None:
    """Render the 'What You Missed' panel."""
    console.print(
        Panel(
            "[bold cyan]⚡  What You Missed in 60 Seconds[/bold cyan]",
            border_style="cyan",
            expand=False,
            padding=(0, 1),
        )
    )
    console.print()

    league_order = ["nba", "premier_league", "formula1", "nhl"]
    all_leagues = league_order + [k for k in summaries if k not in league_order]

    for league in all_leagues:
        if league not in summaries:
            continue
        icon = _ICONS.get(league, "🏆")
        color = _COLORS.get(league, "white")
        label = _LEAGUE_LABELS.get(league, league.upper())
        console.print(
            f"  [{color} bold]{icon}  {label}:[/{color} bold]  {summaries[league]}"
        )

    console.print()


def _render_must_watch(matches: list[UpcomingMatch]) -> None:
    """Render the 'Matches Worth Watching' section as a Rich table."""
    console.print(
        Panel(
            "[bold green]📺  Matches Worth Watching Tonight[/bold green]",
            border_style="green",
            expand=False,
            padding=(0, 1),
        )
    )
    console.print()

    if not matches:
        console.print(
            "  [dim italic]No upcoming matches in the next 48 hours.[/dim italic]"
        )
        console.print()
        return

    table = Table(
        show_header=True,
        header_style="bold dim",
        box=box.SIMPLE_HEAVY,
        padding=(0, 1),
        expand=False,
    )
    table.add_column("Match", style="white", min_width=32)
    table.add_column("League", style="dim", min_width=14)
    table.add_column("Kickoff (UTC)", style="dim", min_width=13)
    table.add_column("Why Watch", style="italic", min_width=28)
    table.add_column("Score", justify="right", min_width=5)

    for match in matches[:6]:
        icon = _ICONS.get(match.league, "🏆")
        color = _COLORS.get(match.league, "white")
        time_str = match.kickoff.strftime("%a %H:%M") if match.kickoff else "TBD"

        tags: list[str] = []
        if match.is_rivalry:
            tags.append("🔥")
        if match.is_favorite_involved:
            tags.append("⭐")
        tag_str = " ".join(tags)

        table.add_row(
            f"{match.home_team} vs {match.away_team} {tag_str}".strip(),
            f"[{color}]{icon} {_LEAGUE_LABELS.get(match.league, '')}[/{color}]",
            time_str,
            match.watch_reason or "—",
            f"[bold]{match.impact_score:.1f}[/bold]",
        )

    console.print(table)
    console.print()


def _render_can_skip(events: list[GameEvent]) -> None:
    """Render the 'What You Can Skip' section."""
    console.print(
        Panel(
            "[bold dim]💤  What You Can Skip[/bold dim]",
            border_style="dim",
            expand=False,
            padding=(0, 1),
        )
    )
    console.print()

    if not events:
        console.print("  [dim italic]Everything was worth watching today![/dim italic]")
        console.print()
        return

    for event in events[:6]:
        icon = _ICONS.get(event.league, "🏆")
        console.print(
            f"  [dim]{icon}  {event.headline}"
            f"  (impact: {event.impact_score:.1f})[/dim]"
        )

    console.print()
