# Fantasy Scoreboard — project context

Read this first. It covers what exists, why it's built this way, and the traps that
already bit us. Usage instructions live in README.md.

## What this is

The public NFL fantasy scoreboard: Live (leaderboard by position), Scoreboard (every game
with fantasy box scores) and Settings (per-visitor Sleeper leagues, shared DK salary
upload). No password; all data is public (Sleeper, ESPN, DraftKings' own salary export).

Until 2026-10-09 this shared a codebase with Mike's private DFS optimizer; that now lives
in its own repo (`dfs-tools`, local folder `Claude Code Projects/DFS`). This repo keeps its
own copies of `optimizer.py`, `showdown.py` and `loader.py` for the hindsight-optimal
lineup — they no longer sync with the optimizer's.

## Layout

```
core/      pure logic, no web framework
  live.py        Sleeper stats, DK/PPR/custom scoring tables, the leaderboard
  scoreboard.py  ESPN scoreboard + box scores (server-side copy; see ESPN note below)
  sleeper.py     multi-league support: scoring systems and roster ownership
  props.py       DraftKings player prop lines from ESPN's core API, weekly archive, player history
  salaries.py    picks a salary table for a slate (DK upload only here)
  dksalaries.py  parses DraftKings' own salary export
  optimal.py     hindsight-best lineup from points already scored
  optimizer.py, showdown.py   classic and captain-mode solvers, used only by optimal.py
  loader.py      name normalization (`_norm`) and matching
api/main.py      FastAPI: routing
web/             index.html, app.js, espn.js, style.css
```

## Decisions that are load-bearing

**ESPN blocks datacenter IPs.** Every ESPN endpoint returns 403 from the hosted server
(a browser User-Agent does not help — it is IP-based). ESPN does send CORS headers, so
`web/espn.js` fetches the scoreboard, slate windows, game situation, play-by-play and
in-game injuries from each visitor's browser. The server handles Sleeper only. The
server-side ESPN code in `core/scoreboard.py` still works locally, where requests come
from a residential IP. **Do not move ESPN calls back to the server.**

**Player props come from ESPN's core API** (`sports.core.api.espn.com/.../odds/100/propBets`,
provider 100 = DraftKings), fetched on the server: on 2026-10-20 Fly could reach it (and the site API).
Lines only, no prices; touchdown-scorer markets are listed without any price, so they are dropped.
Props post about 2–3 days before kickoff and stay after the game. `core/props.py` keeps only the main
over/unders, resolves ESPN athlete ids to names once (`data/props/athletes.json`) and archives each
game's lines (`data/props/<season>/w<week>/<game_id>.json`), so closing lines survive ESPN. Players
are matched to Sleeper by normalized name + team (Sleeper's `espn_id` is missing for most current
players). Box scores: before kickoff they list the players with lines; hover shows core lines, click
opens the player panel (`/api/player`: weekly points + line vs result). All hidden by "Show betting lines".

**Never bring ETR projections here.** They're paid and belong to DFS Tools. The ETR file
readers were removed from this repo on 2026-10-19 so it can be public; salaries come only from
DK's own export. `data/` is excluded from both `.gitignore` and `.dockerignore`.

**Settings are per-visitor, except salaries.** Leagues, chosen team, scoring system,
hidden positions and the Optimal toggle live in the visitor's browser and travel with
each request. The scoring menu lists only base systems plus that visitor's leagues —
`live.SCORING` is a process-wide cache, so never build the menu from it directly or one
visitor's leagues leak into everyone's. The DK salary files are shared server state.

**DK salaries are kept per NFL week**, under `data/salaries/<season>/w<week>/`. An upload's week
comes from matching its `Game Info` games against Sleeper's schedule; a file is identified by its
set of games, so a re-export of the same slate replaces it and a different slate (showdown, Thu–Mon)
sits beside it. `dk_for_week` picks per request: classic first, the file covering the most of the
requested teams, or an exact showdown file for a single-game view. The old single
`data/uploads/dk_salaries.csv` is migrated in automatically on first read.

**Scoring is rule tables, not hardcoded math** (`live.OFFENSE/DST/KICKER`). That's what
lets a Sleeper league's own settings become a scoring system. Changing these changes
displayed scores — snapshot before and after.

**Sleeper omits `pts_allow` for a shutout** rather than sending zero, which silently
cost defenses their +10. `normalize_dst()` handles it; don't "simplify" it away.

**Kickoff times show in the visitor's own time zone** (no zone label), built in the browser from
`start` (`kickoffTime` in espn.js), since 2026-10-20. Server-side timestamps ("updated …") stay US Central
regardless of server locale. Slate windows (early/late/island) are classified in Eastern, as the NFL
defines them. ESPN reports Eastern.

## Deployment

- Host: Fly.io app `fantasy-scoreboard`, region `ord`, auto-suspends when idle, one machine.
- `data/` is the 1 GB volume `scoreboard_data` (added 2026-10-11), so the uploaded DK salary
  file survives deploys and restarts. Before it existed, every deploy silently wiped the upload.
  A volume pins the app to one machine; scaling out would need a second volume.
- Domain: `fantasyscoreboard.mikemendel.com`, DNS at Cloudflare — A to `66.241.124.182`,
  AAAA to `2a09:8280:1::1ac:89bb:0`, both **DNS only / grey cloud**. The orange proxy
  breaks certificate issuance and renewal.
- Repo: https://github.com/boilermike1/fantasy-scoreboard (public since 2026-10-19; never commit data/ or secrets). Push to `main`
  deploys via GitHub Actions using repo secret `FLY_API_TOKEN`.
- League defaults: Fly secrets `SLEEPER_LEAGUE_ID` / `SLEEPER_USER_ID`.
- Cloudflare Web Analytics beacon is injected into the page by `index()`.

**Never run `fly launch` again** — the app exists; re-running it rewrites `fly.toml`
and creates duplicate apps. Use `fly deploy`, or just push to `main`.

## Traps already hit

- Static files were served with no cache headers, so browsers ran stale JS. Fixed with
  `no-cache` plus version-stamped asset URLs. If a change "doesn't appear", suspect this.
- Position visibility stores *hidden* positions, not shown ones, so a newly added
  position (e.g. `ALL` for single-game view) defaults to visible.
- macOS caches failed DNS lookups; a new subdomain can appear dead on a machine that
  checked before the records existed. `sudo dscacheutil -flushcache`.

## Open items

- Billing is live (card added 2026-10-08). Expect well under $1/month at this usage.
- Yahoo leagues: scoped but not built — needs OAuth, per-user token storage, XML mapping.
- DK salary upload is shared and unauthenticated — anyone can add or remove a file.
