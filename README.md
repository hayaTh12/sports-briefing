# Sports-briefing

A personalized sports control center that runs every morning, fetches live data
from multiple leagues, scores each event by impact, and outputs a prioritized
daily brief — both in the terminal (Rich) and as a Markdown file committed to
your repository.

## Leagues covered

| League | API | Auth needed? |
|--------|-----|-------------|
| 🏀 NBA | [nba_api](https://github.com/swar/nba_api) (unofficial) | No |
| ⚽ Premier League | [API-Football](https://www.api-football.com/) | Yes (free tier) |
| 🏎️ Formula 1 | [FastF1](https://github.com/theOehrly/Fast-F1) + Ergast | No |
| 🏒 NHL | [NHL official API](https://api-web.nhle.com/v1/) | No |

---

## Quick start

### 1. Clone & install

```bash
git clone https://github.com/your-username/sports-briefing.git
cd sports-briefing

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install -e .                 # registers the `sports-briefing` command
```

### 2. Configure API keys

```bash
cp .env.example .env
# Open .env and paste your API-Football key
```

Get a free key at <https://dashboard.api-football.com/register>.
The free tier allows 100 requests/day — enough for multiple daily runs.

### 3. Personalise `config.yaml`

Open `config.yaml` and fill in your favourite teams / drivers:

```yaml
leagues:
  nba:
    favorite_teams:
      - Lakers
  premier_league:
    favorite_teams:
      - Arsenal
  formula1:
    favorite_drivers:
      - Max Verstappen
  nhl:
    favorite_teams:
      - Maple Leafs
```

Favourite teams receive a **+3.0 impact bonus**, so they always surface
in your top storylines and "must watch" sections.

### 4. Run it

```bash
# Full brief (all enabled leagues)
sports-briefing run

# Single league only
sports-briefing run --league nba

# Show API / debug logs
sports-briefing run --verbose

# Terminal only — don't save a Markdown file
sports-briefing run --no-save

# Wipe the local cache
sports-briefing clear-cache
```

---

## Output

Each run produces:

1. **Terminal summary** — Rich-formatted panels with colour, impact bars, and
   ranked tables.
2. **`briefs/YYYY-MM-DD.md`** — A full Markdown brief saved (and committed) to
   the repo.

### Brief structure

| Section | Content |
|---------|---------|
| 🔥 Top 5 Storylines | Highest-impact completed events across all leagues |
| ⚡ 60 Seconds | One-liner per league — skim everything fast |
| 📺 Must Watch | Top upcoming matches with "why watch" context |
| 💤 Can Skip | Low-impact events (impact score ≤ 2.5 by default) |
| 📋 Full Results | Collapsible complete results list |

---

## Impact scoring

Every event gets a numeric **impact score** built from weighted factors:

| Factor | Default weight | Triggered when… |
|--------|---------------|-----------------|
| Favourite team | 3.0 | Your team is involved |
| Upset | 2.0 | Lower-ranked team wins (scales with rank gap) |
| High stakes | 2.0 | Both teams in the top 6 of the table |
| Rivalry | 1.5 | Known rivalry match |
| Standings change | 1.5 | (future enhancement) |
| Close game | 1.2 | ≤ 5 pts margin (NBA) / ≤ 1 goal (soccer / hockey) |
| Drama | 0.5 | OT / shootout / penalties |
| Recency | 0.5 | Events from the last few hours score slightly higher |

All weights are tunable in `config.yaml → scoring.weights`.

### Built-in rivalries

Rivalries are detected automatically (add your own in `config.yaml`):

- **NBA**: Lakers–Celtics, Warriors–Cavaliers, Knicks–Nets, Heat–Knicks, …
- **PL**: Man Utd–Liverpool, Arsenal–Tottenham (NLD), Man City–Man Utd, …
- **NHL**: Bruins–Canadiens, Rangers–Islanders, Capitals–Penguins, …

---

## Caching

All API responses are cached as JSON files in `./cache/` with a configurable
TTL (default: 6 hours), so repeated runs within a session never re-hit the
network.

| League | Cache strategy |
|--------|---------------|
| NBA | Per-date ScoreboardV2 + Standings |
| Premier League | Per date-range + status (`FT` / `NS`) |
| Formula 1 | FastF1 built-in cache (binary) + Ergast standings per day |
| NHL | Per-date score + schedule responses |

Clear manually:

```bash
sports-briefing clear-cache
```

---

## GitHub Actions — automated daily brief

The workflow at [`.github/workflows/daily_brief.yml`](.github/workflows/daily_brief.yml)
runs every day at **08:00 UTC**, commits the Markdown to `briefs/`, and
keeps a full historical archive on GitHub.

### Setup

1. Go to **Settings → Secrets and variables → Actions** in your repo.
2. Add a secret named `API_FOOTBALL_KEY`.
3. Push — the workflow activates automatically.

You can also trigger it manually from the **Actions** tab, optionally
specifying a single league via the `league` input.

---

## Project structure

```
sports-briefing/
├── sports_briefing/
│   ├── main.py                  # Typer CLI (run / clear-cache)
│   ├── config.py                # YAML config loader with typed accessors
│   ├── models.py                # Shared dataclasses
│   ├── leagues/
│   │   ├── base.py              # Abstract fetcher base class
│   │   ├── nba.py               # NBA  — nba_api
│   │   ├── premier_league.py    # PL   — API-Football
│   │   ├── formula1.py          # F1   — FastF1 + Ergast
│   │   └── nhl.py               # NHL  — official NHL API (no key)
│   ├── scoring/
│   │   ├── engine.py            # Impact scoring engine
│   │   └── rivalries.py         # Built-in + user-defined rivalry registry
│   ├── output/
│   │   ├── renderer.py          # Markdown file generator
│   │   └── terminal.py          # Rich terminal display
│   └── utils/
│       ├── cache.py             # JSON disk cache with TTL
│       └── http.py              # httpx wrapper with structured error handling
├── briefs/                      # Generated briefs (committed to git)
├── cache/                       # Local API cache (git-ignored)
├── config.yaml                  # User configuration
├── .env.example                 # API key template
├── requirements.txt
├── pyproject.toml
└── .github/workflows/
    └── daily_brief.yml          # Scheduled GitHub Actions workflow
```

---

## Requirements

- Python **3.11+**
- API-Football key (free) for Premier League
- Internet connection

NBA, F1, and NHL data require **no API key**.

---

## License

MIT
