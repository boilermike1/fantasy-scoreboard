# DFS Tools

DraftKings NFL optimizer (+ tournament tool and live leaderboard, coming) built on
Establish The Run projection CSVs.

```
core/       optimizer, loaders, value analysis (pure Python, no web)
api/        FastAPI server
web/        browser UI
data/       uploads + settings (shared between users; not code)
dk_optimizer.py   command-line version
run.sh            start the web server
```

## Live site

The public scoreboard runs at **https://fantasyscoreboard.mikemendel.com** (Fly.io app
`fantasy-scoreboard`, Chicago region, idles when nobody is on it).

Code lives at **https://github.com/boilermike1/fantasy-scoreboard** (private). Pushing to `main`
deploys the site automatically via GitHub Actions (`.github/workflows/deploy.yml`), using a Fly deploy
token stored as the repo secret `FLY_API_TOKEN`.

To publish a change:

```bash
cd "/Users/Owner/Documents/Claude/Claude Code Projects/DFS" && git add -A && git commit -m "what changed" && git push
```

Watch it with `gh run list` or `gh run watch`. `fly deploy` still works for a manual push.

Do **not** run `fly launch` again — the app already exists, and re-running it rewrites `fly.toml`
and can create a duplicate app.

`.gitignore` keeps `data/` (site password, ETR projections) and every CSV out of the repo; the
`.dockerignore` does the same for what reaches the server.

Useful checks: `fly status`, `fly logs`, `fly certs check fantasyscoreboard.mikemendel.com`.
League settings live in Fly secrets (`fly secrets list`), not in `data/config.json`.

DNS is at Cloudflare: an A record to `66.241.124.182` and an AAAA to `2a09:8280:1::1ac:89bb:0`,
both set to "DNS only" (grey cloud) — Cloudflare's proxy blocks certificate renewal.

## Two ways to run it

`DFS_MODE` decides which version starts:

- **full** (default) — every tab, password protected. This is what runs at home.
- **scoreboard** — Live and Scoreboard only, no password, no uploads, no salaries. This is the
  public site. Private endpoints return 404 and the optimizer tabs are removed from the page.

The public mode never serves ETR content: `data/` is excluded from what gets uploaded to the host
(see `.dockerignore`), `/api/state` is blocked, and salaries are switched off in code rather than
merely being absent.

Sleeper league settings can come from `SLEEPER_LEAGUE_ID` / `SLEEPER_USER_ID` environment variables
instead of `data/config.json`, so a hosted copy needs no config file.

## Start the web app

```bash
./run.sh
```

Then open http://localhost:8000. Stop it with Ctrl-C. (`--reload` is on, so code edits apply without restarting.)

### Set a password (required before sharing)

Either export `DFS_PASSWORD=something` before `./run.sh`, or create `data/config.json`:

```json
{ "password": "something" }
```

With no password set, the app is open — fine on your own machine only.

## Share it with a friend (plain URL, free)

Option A — **Cloudflare quick tunnel** (no account, URL changes each time you restart):

```bash
brew install cloudflared
./run.sh                                   # terminal 1
cloudflared tunnel --url http://localhost:8000   # terminal 2 -> prints https://xxxx.trycloudflare.com
```

Option B — **Tailscale Funnel** (free account, *stable* URL, friend installs nothing):

```bash
brew install --cask tailscale     # sign in once
./run.sh
tailscale funnel 8000             # prints https://<your-mac>.<tailnet>.ts.net
```

Your Mac has to be on and awake for either. If that gets old, the same code runs on a
$5/month VPS unchanged.

## Command line

```bash
python3 dk_optimizer.py projections.csv
python3 dk_optimizer.py projections.csv -n 10 --include "Gibbs" --exclude "Achane" "Mayer"
python3 dk_optimizer.py projections.csv --top-plays top_plays.csv --top-play-bonus 3 --min-top-plays 4
python3 dk_optimizer.py projections.csv --stack --double-stack-bonus 1.5 --bringback-bonus 1 --block-dst
python3 dk_optimizer.py projections.csv --out output/     # writes lineups.csv + dk_upload.csv
```

## Paste from Claude

The paste box accepts the block produced by the DFS Analysis workstation (Cowork):

```
TOP PLAYS
Name, tier
INCLUDE
Name
EXCLUDE
Name
```

Sections are optional; plain names with no headers are treated as top plays. Include/exclude picks are
saved on the server, so both users see the same chips. You can also upload a top-plays CSV
(needs a `Player`/`Name` column; optional `Tier` or `Weight`).
The solver treats each top play as worth +3 projected points (tier 2 = 75%, tier 3 = 50%), so it
fits them in whenever the cost in real projection is small. Top plays are tagged in the lineup table;
the `pts` shown is always the raw ETR projection. Change the bonus via `DEFAULT_TOP_PLAY_BONUS` in
`api/main.py`.

## Web app tabs

**Main Slate** — classic DK ($50,000; QB/RB/RB/WR/WR/WR/TE/FLEX/DST). Returns the top 10 lineups with
no stacking, ownership, or DST rules. Those options still exist in `core/optimizer.py` and the CLI.

**Showdown** — single-game Captain Mode ($50,000; 1 CPT at 1.5x points and salary + 5 FLEX, both teams
required). Returns the top 10 lineups. Takes a DK showdown projections CSV with `Player, Pos, Team,
Salary, Proj` and optionally `CPT Salary` / `CPT Proj` (computed as 1.5x if absent). Controls:

- *Captain must be one of* — restricts the CPT slot to those players (one player = a hard lock)
- *Never captain* — bars a player from CPT; they can still appear at FLEX
- *Include / exclude* — same as the main slate

Showdown CSVs have no `id` column, so the DK upload export falls back to player names.

**Scoreboard** — every NFL game for the week. In-progress games (green pulsing dot) sort to the top,
upcoming next, finals at the bottom. Refresh cadence adapts the same way the Live tab's does. Click any game for a fantasy box
score: QB/RB/WR/TE, K and DST for both teams, grouped by position and sorted by points.

While a game is in progress its card also shows possession (a green arrow beside the team with the
ball), down and distance, the ball spot, and the last play; opening it adds a situation banner (red
when inside the 20) and the **last five plays**, newest first, with scoring plays highlighted. Plays
come from ESPN's game-summary feed, cached 15 seconds.

The **Scoring** menu is shared with the Live tab — changing it in either place updates both and is
remembered per browser.

Scores and clock come from ESPN's public scoreboard feed; the per-player fantasy numbers come from the
same Sleeper stat line the Live tab uses, so a player's points match in both places. ESPN and Sleeper
agree on all 32 team abbreviations except Washington (`WSH` vs `WAS`), which `TEAM_FIX` in
`core/scoreboard.py` reconciles.

**Live** — DKLive-style leaderboard of the top scorers by position (QB/RB/WR/TE/FLEX/DST/K) for
one NFL week, Thursday through Monday. Data comes from the [Sleeper API](https://docs.sleeper.com),
which is public and needs no key or account. Pick any week of the season; the current week
auto-refreshes while the tab is open, at a cadence that follows the slate: every 30 seconds while
games are being played, every 5 minutes while waiting for a kickoff, and not at all once every game is
final (the toolbar says which). Season to date never polls. The week menu also offers **Season to date**. A **Slate** menu filters to All games, Main slate
(Sunday 1pm + 4pm windows), Early only, Late only, or any single island game (Thursday, international, Sunday
night, Monday) — windows are classified from each kickoff's Eastern start time, so they follow the real
schedule each week. It defaults to All games, resets when you change weeks, and is hidden for
Season to date. Each position card lists up to 50 players and grows
with the window (about 11 rows on a 950px-tall screen, more on a larger one), with the rest behind a
scrollbar.
Checkboxes show or hide each position. **FLEX** pools RB/WR/TE into one ranking (each row tagged with
its real position); FLEX and K are off by default. Both choices are remembered per
browser, so your settings and Blake's are independent.

A green pulsing dot marks a player whose game is still in progress; a grey dot means final. Game
status comes from Sleeper's schedule endpoint and is refreshed every 60 seconds.

All times are shown in **US Central** regardless of where the server runs, including kickoff times
(ESPN reports those in Eastern, so they're converted).

Players hurt **during** a game show a badge — red `OUT`, amber `Q`/`D` — with the body part on hover.

Only genuine in-game designations appear. ESPN timestamps each one, so a designation is kept only when
it was made after kickoff; pre-game scratches (`fantasyStatus: INACTIVE`) and roster moves like Injured
Reserve are filtered out. In practice this is the difference between ~37 entries for a slate and the
2-3 that actually happened during play. Reports come from ESPN's per-game feed for games in progress,
fetched in parallel and cached 2 minutes.

**A defense that has not yet allowed a point scores its +10 shutout bonus live.** Sleeper omits the
`pts_allow` field entirely rather than sending zero, so `normalize_dst()` treats a missing value as zero
once the game is under way. Scoreless defenses also stay on the board while their game is live, since
0 points for a defense on the field is real information.

### Salaries and the optimal lineup

DraftKings has **no public salary API**, so salaries come from the projection files you already upload:
`data/uploads/projections.csv` for the main slate and `data/uploads/showdown.csv` for a single game.
They appear on the Live tab only under **DraftKings** scoring, and only for a slate the uploaded files
cover. Matching is by normalized name for skill players and by team for defenses, since ETR names
defenses by nickname ("Jaguars") while Sleeper uses the full name. ETR's `LA` is mapped to `LAR`.
Kickers have no classic salary because DK classic has no K slot.

Picking a **single game** in the Slate menu switches the board to showdown shape: one combined list of
every position including K and DST, priced with that game's Captain-mode salaries.

Ticking **Optimal** (the pill right of K) adds a card showing the best lineup that could have been
built from points already scored — the hindsight-optimal lineup, not a projection. It respects the
$50,000 cap and shows salary used and remaining, and it runs the classic solver on a multi-game slate
and the Captain-mode solver on a single game. It updates with the 30-second refresh.

### Scoring systems

Defined in `SCORING` at the top of `core/live.py` — edit that dict to add or change one.

| System | Pass yd | Pass TD | Yardage bonuses |
|---|---|---|---|
| DraftKings | 1 per 25 (0.04) | 4 | 100 rush / 100 rec / 300 pass = +3 |
| PPR | 1 per 25 (0.04) | 4 | none |
| Fan Free Zone | 1 per 20 (0.05) | 6 | none |
| *(your Sleeper league)* | pulled from the league | pulled from the league | pulled from the league |

### Sleeper league mode

Optional. Add to `data/config.json`:

```json
{ "sleeper": { "league_id": "...", "user_id": "..." } }
```

The league then appears in the Scoring menu by name, scored with its own settings pulled live from
Sleeper (so it stays correct if the commissioner changes anything). While that mode is selected, every
player on the Live leaderboards and in box scores is tagged with the fantasy team that owns them, and
your own players are highlighted in green. Free agents show no tag.

A **My team** picker appears next to the Scoring menu whenever league scoring is active, listing every
team (eliminated ones marked "(out)" and sorted last). It defaults to the team named by `user_id` in
the config and is saved per browser, so you and Blake can each highlight your own without changing the
other's view.

Rosters are cached 5 minutes, scoring settings 1 hour. Remove the `sleeper` block to turn it all off —
nothing else depends on it.

All three share: 0.1 per rush/rec yard, 6 per rush/rec TD, 1 per reception, -1 per interception,
-1 per lost fumble, 2 per two-point conversion, and DK's DST and kicker rules.

Stat lines show carries for runners (`19 car, 94 rush yd, 2 TD`) and catches/targets for receivers
(`5/9 rec, 115 yd`). Headshots and team logos come from Sleeper's public CDN
(`sleepercdn.com`), loaded directly by each viewer's browser — no bandwidth through this server.
A missing headshot falls back to the team logo.

DK points are computed locally from Sleeper's raw stat lines (`core/live.py`), not taken from Sleeper's
own scoring — their defaults differ from DraftKings. Sleeper's player dictionary (~15MB) is cached in
`data/cache/players.json` and refreshed daily; weekly stats are cached for 20 seconds so two users
watching at once make one upstream call.

## Requirements

Python 3.9+, `pip install -r requirements.txt`.
