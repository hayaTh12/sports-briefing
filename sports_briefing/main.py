"""
sports-briefing CLI entry point.

Commands
--------
run          Fetch data and generate today's brief  (default)
clear-cache  Wipe the local JSON cache
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Optional

# ── Windows UTF-8 fix ──────────────────────────────────────────────────────
# Reconfigure stdout/stderr to UTF-8 before Rich or any other module loads.
# This prevents 'charmap' codec errors when printing emojis or ✓ on Windows
# terminals that default to CP1252.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
# ──────────────────────────────────────────────────────────────────────────

import typer
from rich.console import Console

from sports_briefing.config import Config
from sports_briefing.leagues.formula1 import Formula1Fetcher
from sports_briefing.leagues.nba import NBAFetcher
from sports_briefing.leagues.nhl import NHLFetcher
from sports_briefing.leagues.premier_league import PremierLeagueFetcher
from sports_briefing.models import DailyBrief, LeagueData
from sports_briefing.output.renderer import save_brief
from sports_briefing.output.terminal import console, display_brief
from sports_briefing.scoring.engine import ScoringEngine
from sports_briefing.utils.cache import Cache

app = typer.Typer(
    name="sports-briefing",
    help="🏆  Personalized sports control center — daily briefing across NBA, PL, F1 & NHL.",
    add_completion=False,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------


@app.command()
def run(
    config_path: str = typer.Option(
        "config.yaml", "--config", "-c", help="Path to config YAML file."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show debug logs."
    ),
    no_save: bool = typer.Option(
        False, "--no-save", help="Print to terminal only — don't write a Markdown file."
    ),
    league: Optional[str] = typer.Option(
        None,
        "--league",
        "-l",
        help="Fetch a single league only. Choices: nba, premier_league, formula1, nhl",
    ),
) -> None:
    """Fetch sports data and generate your daily brief.

    Reads config.yaml for favourite teams, API keys from .env, then fetches
    fresh data (or serves from cache), scores every event, and renders both
    a terminal summary and a Markdown brief.
    """
    _configure_logging(verbose)

    config = Config(config_path)
    cache = Cache(config.cache_dir, config.cache_ttl_hours)
    engine = ScoringEngine(config)

    all_fetchers = {
        "nba": NBAFetcher(config, cache),
        "premier_league": PremierLeagueFetcher(config, cache),
        "formula1": Formula1Fetcher(config, cache),
        "nhl": NHLFetcher(config, cache),
    }

    # Determine which leagues to run
    if league:
        if league not in all_fetchers:
            console.print(
                f"[red]Unknown league '[bold]{league}[/bold]'. "
                f"Choices: {', '.join(all_fetchers)}[/red]"
            )
            raise typer.Exit(code=1)
        active = {league: all_fetchers[league]}
    else:
        active = {k: v for k, v in all_fetchers.items() if config.is_league_enabled(k)}

    if not active:
        console.print("[yellow]No leagues enabled in config.yaml.[/yellow]")
        raise typer.Exit()

    # Fetch and score
    all_league_data: list[LeagueData] = []
    with console.status("[bold green]Fetching sports data…[/bold green]") as status:
        for league_key, fetcher in active.items():
            label = league_key.replace("_", " ").title()
            status.update(f"[bold green]Fetching {label}…[/bold green]")
            try:
                data = fetcher.fetch()
                data = engine.score_league_data(data)
                all_league_data.append(data)
            except Exception as exc:
                console.print(f"[yellow]⚠  {label}: {exc}[/yellow]")
                all_league_data.append(
                    LeagueData(league=league_key, fetch_error=str(exc))
                )

    brief = _compile_brief(all_league_data, config)
    display_brief(brief)

    if not no_save:
        output_path = save_brief(brief, config.briefs_dir)
        console.print(f"[green]✓  Brief saved → [bold]{output_path}[/bold][/green]\n")


@app.command(name="clear-cache")
def clear_cache(
    config_path: str = typer.Option(
        "config.yaml", "--config", "-c", help="Path to config YAML file."
    ),
) -> None:
    """Delete all locally cached API responses."""
    config = Config(config_path)
    cache = Cache(config.cache_dir, config.cache_ttl_hours)
    cache.clear()
    console.print(f"[green]✓  Cache cleared at [bold]{config.cache_dir}[/bold][/green]")


# ---------------------------------------------------------------------------
# Brief compilation
# ---------------------------------------------------------------------------


def _compile_brief(all_data: list[LeagueData], config: Config) -> DailyBrief:
    """Combine per-league data into a single ``DailyBrief``.

    Args:
        all_data: Scored league data for every fetched league.
        config:   Application configuration (provides skip_threshold).

    Returns:
        Fully compiled ``DailyBrief``.
    """
    all_results: list = []
    all_upcoming: list = []
    league_summaries: dict[str, str] = {}

    league_order = ["nba", "premier_league", "formula1", "nhl"]

    for data in sorted(
        all_data, key=lambda d: league_order.index(d.league) if d.league in league_order else 99
    ):
        if data.fetch_error and not data.recent_results and not data.upcoming_matches:
            league_summaries[data.league] = f"⚠️  Data unavailable — {data.fetch_error}"
            continue

        all_results.extend(data.recent_results)
        all_upcoming.extend(data.upcoming_matches)
        league_summaries[data.league] = _build_league_summary(data)

    # Sort everything by impact score
    all_results.sort(key=lambda e: e.impact_score, reverse=True)
    all_upcoming.sort(key=lambda m: m.impact_score, reverse=True)

    threshold = config.skip_threshold
    top_storylines = [e for e in all_results if e.impact_score > threshold]
    can_skip = [e for e in all_results if e.impact_score <= threshold]

    return DailyBrief(
        date=datetime.utcnow(),
        top_storylines=top_storylines[:5],
        league_summaries=league_summaries,
        must_watch=all_upcoming[:6],
        can_skip=can_skip[:8],
        all_results=all_results,
        all_upcoming=all_upcoming,
    )


def _build_league_summary(data: LeagueData) -> str:
    """Build a one-liner summary for a league's 60-second section.

    Args:
        data: Scored league data.

    Returns:
        Short string suitable for the "What You Missed" bullet point.
    """
    results = sorted(data.recent_results, key=lambda e: e.impact_score, reverse=True)

    if not results:
        upcoming_count = len(data.upcoming_matches)
        if upcoming_count:
            return f"No recent results · {upcoming_count} game(s) upcoming"
        return "No activity in the last 48 hours"

    top = results[0]
    extra = len(results) - 1

    summary = top.headline
    if extra == 1:
        summary += " (+1 other result)"
    elif extra > 1:
        summary += f" (+{extra} other results)"

    return summary


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    """Configure root logger level.

    Args:
        verbose: If True, show DEBUG messages; otherwise only WARNING+.
    """
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s  %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Package entry point (defined in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()
