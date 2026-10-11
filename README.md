# Fantasy Scoreboard

Public NFL fantasy scoreboard: a live leaderboard by position, every game's fantasy box score, and
per-visitor Sleeper league scoring. Runs at **https://fantasyscoreboard.mikemendel.com** (no
password). The DFS lineup optimizer is a separate private project: `dfs-tools`.

```
core/       Sleeper stats, scoring, ESPN, salaries, hindsight-optimal solver (pure Python, no web)
api/        FastAPI server
web/        browser UI
data/       DK salary upload and caches (not code; never committed)
run.sh      start the web server locally
```

## ESPN is fetched by the browser, not the server

ESPN's public endpoints return **403 to datacenter IPs**, so once hosted the server cannot reach
them (Sleeper has no such restriction). ESPN allows cross-origin browser requests, so `web/espn.js`
fetches the scoreboard, slate windows, live game situation, play-by-play and in-game injuries from
each visitor's own browser, mirroring what `core/scoreboard.py` does locally.

Consequences:
- `/api/live` no longer derives slates itself; the client sends `slate_teams=` (and `single_game=`).
- `/api/scoreboard` returns `{"unavailable": true}` instead of failing when ESPN refuses it; the
  client then fetches ESPN directly.
- The server-side ESPN code still works on the local private app, where requests come from a
  residential IP.

## Hosting

Fly.io app `fantasy-scoreboard` (Chicago, idles when nobody is on it). Code:
**https://github.com/boilermike1/fantasy-scoreboard** (private). Pushing to `main` deploys via GitHub
Actions using the repo secret `FLY_API_TOKEN`.

```bash
cd "/Users/Owner/Documents/Claude/Claude Code Projects/Fantasy Scoreboard" && git add -A && git commit -m "what changed" && git push
```

Do **not** run `fly launch` again — the app exists, and re-running it rewrites `fly.toml`.
Useful checks: `fly status`, `fly logs`, `fly certs check fantasyscoreboard.mikemendel.com`.
League defaults live in Fly secrets `SLEEPER_LEAGUE_ID` / `SLEEPER_USER_ID`.

DNS is at Cloudflare: an A record to `66.241.124.182` and an AAAA to `2a09:8280:1::1ac:89bb:0`,
both "DNS only" (grey cloud) — Cloudflare's proxy blocks certificate renewal.

## Start it locally

```bash
./run.sh
```

Then open http://localhost:8000.

## Tabs

**Scoreboard** — every NFL game for the week. In-progress games (green pulsing dot) sort to the top,
upcoming next, finals at the bottom. Refresh cadence adapts the same way the Live tab's does. Click any game for a fantasy box
score: QB/RB/WR/TE, K and DST for both teams, grouped by position and sorted by points.

While a game is in progress its card also shows possession (a green arrow beside the team with the
ball), down and distance, the ball spot, and the last play; opening it adds a situation banner (red
when inside the 20) and the **last five plays**, newest first, with scoring plays highlighted;
**Show all plays** expands to the whole game. Each play is tinted in its team's colors (the team
that took the ball, for a turnover), stronger for scoring plays, turnovers and 20+ yard plays from
scrimmage. A finished game offers its full **play log** from
kickoff instead, fetched once and kept for the visit. Plays come from ESPN's game-summary feed.

While a box score is open, the week's other games sit in a narrow column on the right (a swipeable
strip on phones); click one to jump to it.

The **Scoring** menu is shared with the Live tab — changing it in either place updates both and is
remembered per browser. On the Live tab the **Slate** menu sits to its right and only appears under
DraftKings scoring; every other system shows the whole week.

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

### Settings tab

Per-visitor, stored in that browser only (so two people using the site at once don't affect each other):

- **Sleeper leagues** — enter a Sleeper username, tick which leagues appear in the Scoring menu, and
  pick your team in each. Scoring keys are `league:<league_id>`; a visitor's leagues are passed to the
  API per request, and the Scoring menu only ever lists the base systems plus that visitor's own.
- **Scoring systems** — untick systems to hide them from the Scoring menu. Stored as the *hidden* list,
  so a newly added system or league shows by default; at least one must stay ticked.
- `SLEEPER_LEAGUE_ID` / `SLEEPER_USER_ID` remain the default for a visitor who hasn't chosen anything.

Server-side and shared by everyone:

- **DraftKings salaries** — upload the CSV from a DK contest's player list (*Export to CSV*). This is
  public DraftKings data, unlike ETR projections, so it works on the public site: it turns on the
  salary column and the Optimal lineup. `core/dksalaries.py` parses both classic and showdown exports
  and reads kickoff times out of the `Game Info` column.

ETR projections are never used here — they're paid, and live in the separate DFS Tools app
(`allow_etr=False`).

### Salaries and the optimal lineup

DraftKings has **no public salary API**, so salaries come from the DraftKings export uploaded on the
Settings tab. They appear on the Live tab only under **DraftKings** scoring, and only for a slate the
uploaded file covers. Matching is by normalized name for skill players and by team for defenses.
Kickers have no classic salary because DK classic has no K slot.

Picking a **single game** in the Slate menu switches the board to showdown shape: one combined list of
every position including K and DST, priced with that game's Captain-mode salaries.

Ticking **Optimal** (the pill right of K) adds a card showing the best lineup that could have been
built from points already scored — the hindsight-optimal lineup, not a projection. It respects the
$50,000 cap and shows salary used and remaining, and it runs the classic solver on a multi-game slate
and the Captain-mode solver on a single game. It updates with the 30-second refresh.

### Scoring systems

Defined in `SCORING` at the top of `core/live.py` — edit that dict to add or change one.

| System | Pass yd | Pass TD | Reception | INT / fumble lost | Yardage bonuses |
|---|---|---|---|---|---|
| DraftKings | 1 per 25 (0.04) | 4 | 1 | −1 | 100 rush / 100 rec / 300 pass = +3 |
| PPR | 1 per 25 (0.04) | 4 | 1 | −2 | none |
| Half PPR | 1 per 25 (0.04) | 4 | 0.5 | −2 | none |
| Standard | 1 per 25 (0.04) | 4 | 0 | −2 | none |
| Fan Free Zone | 1 per 20 (0.05) | 6 | 1 | −2 | none |
| *(your Sleeper league)* | pulled from the league | | | | |

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
