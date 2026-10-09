# DFS Tools — project context

Read this first. It covers what exists, why it's built this way, and the traps that
already bit us. Usage instructions live in README.md.

## What this is

Two products from one codebase, selected by the `DFS_MODE` environment variable:

| Mode | Tabs | Password | Data |
|---|---|---|---|
| `scoreboard` | Live, Scoreboard, Settings | none | Sleeper + ESPN + DK salaries (all public) |
| `full` (default) | the above plus Main Slate, Showdown | yes | adds ETR projections (paid) |

`scoreboard` is the public site. `full` runs on Mike's Mac and at
`dfstools.mikemendel.com` (Fly app `dfs-tools`) for his own DFS work.

## Layout

```
core/      pure logic, no web framework
  live.py        Sleeper stats, DK/PPR/custom scoring tables, the leaderboard
  scoreboard.py  ESPN scoreboard + box scores (server-side copy; see ESPN note below)
  sleeper.py     multi-league support: scoring systems and roster ownership
  salaries.py    picks a salary table for a slate (DK file preferred, ETR fallback)
  dksalaries.py  parses DraftKings' own salary export
  optimizer.py   classic lineup solver (MILP via scipy)
  showdown.py    captain-mode solver
  optimal.py     hindsight-best lineup from points already scored
  loader.py      ETR CSV parsing, name normalization (`_norm`)
api/main.py      FastAPI: routing, auth, mode gating
web/             index.html, app.js, espn.js, style.css
```

## Decisions that are load-bearing

**ESPN blocks datacenter IPs.** Every ESPN endpoint returns 403 from the hosted server
(a browser User-Agent does not help — it is IP-based). ESPN does send CORS headers, so
`web/espn.js` fetches the scoreboard, slate windows, game situation, play-by-play and
in-game injuries from each visitor's browser. The server handles Sleeper only. The
server-side ESPN code in `core/scoreboard.py` still works locally, where requests come
from a residential IP. **Do not move ESPN calls back to the server.**

**ETR projections are paid; DK salary exports are not.** The public site must never
serve ETR data. Three guards: `data/` is excluded from both `.gitignore` and
`.dockerignore`; `/api/state` is in `PRIVATE_PREFIXES`; and `for_slate(allow_etr=False)`
in public mode. DK's own export is public and is preferred when it covers the slate.

**Settings are per-visitor, except salaries.** Leagues, chosen team, scoring system,
hidden positions and the Optimal toggle live in the visitor's browser and travel with
each request. The scoring menu lists only base systems plus that visitor's leagues —
`live.SCORING` is a process-wide cache, so never build the menu from it directly or one
visitor's leagues leak into everyone's. The DK salary file is shared server state.

**Scoring is rule tables, not hardcoded math** (`live.OFFENSE/DST/KICKER`). That's what
lets a Sleeper league's own settings become a scoring system. Changing these changes
displayed scores — snapshot before and after.

**Sleeper omits `pts_allow` for a shutout** rather than sending zero, which silently
cost defenses their +10. `normalize_dst()` handles it; don't "simplify" it away.

**Optimizer tabs are one per DFS slate, built weekly from ESPN's schedule** in the
browser (`dfsWeek`/`dfsSlates` in `web/espn.js`). Rules, Eastern time: Sunday 12–6pm is
the Main slate; other Sunday games (international mornings, SNF) are showdowns; a
non-Sunday day with one game is a showdown; with 2+ games it's one classic day slate
(Thanksgiving, Saturdays) — and on Monday each game also gets a showdown. The week rolls
over at 6am ET the morning after its last game is final. Each slate's uploads and picks
live in `data/slates/<sid>/` (sid like `2026-2-5-main`, `2026-2-5-g-BAL-ATL`); the upload
also stores the slate's label/week so `/api/slates` can list past weeks. No sid means
the original single-slot files, used only when the schedule can't load.

**Times are pinned to US Central** regardless of server locale. ESPN reports Eastern.

## Deployment

- Host: Fly.io app `fantasy-scoreboard`, region `ord`, auto-suspends when idle.
- Domain: `fantasyscoreboard.mikemendel.com`, DNS at Cloudflare — A to `66.241.124.182`,
  AAAA to `2a09:8280:1::1ac:89bb:0`, both **DNS only / grey cloud**. The orange proxy
  breaks certificate issuance and renewal.
- Repo: https://github.com/boilermike1/fantasy-scoreboard (private). Push to `main`
  deploys via GitHub Actions using repo secret `FLY_API_TOKEN`.
- League defaults: Fly secrets `SLEEPER_LEAGUE_ID` / `SLEEPER_USER_ID`.

- Private tools site: Fly app `dfs-tools` (config `fly.tools.toml`, `DFS_MODE=full`),
  domain `dfstools.mikemendel.com` — A to `66.241.124.121`, AAAA to
  `2a09:8280:1::1ae:d650:0`, grey cloud. Password is the Fly secret `DFS_PASSWORD`.
  `data/` is the 1 GB volume `dfs_data`, so uploads survive restarts. Hosted full mode
  with no password locks everyone out rather than opening up (`_authed`).
  The same push to `main` deploys both apps (tools uses secret `FLY_API_TOKEN_TOOLS`,
  a deploy token scoped to `dfs-tools` that expires 2027-10-09).

**Never run `fly launch` again** — the app exists; re-running it rewrites `fly.toml`
and creates duplicate apps. Use `fly deploy`, or just push to `main`.

## Traps already hit

- Static files were served with no cache headers, so browsers ran stale JS. Fixed with
  `no-cache` plus version-stamped asset URLs. If a change "doesn't appear", suspect this.
- ETR's showdown export bundles several slates in one file and sometimes ships with the
  `Proj` column zeroed. Both now produce clear errors.
- Position visibility stores *hidden* positions, not shown ones, so a newly added
  position (e.g. `ALL` for single-game view) defaults to visible.
- macOS caches failed DNS lookups; a new subdomain can appear dead on a machine that
  checked before the records existed. `sudo dscacheutil -flushcache`.

## Open items

- Billing is live (card added 2026-10-08) and the app runs on **one** machine that
  suspends when idle. Expect well under $1/month at this usage.
- Yahoo leagues: scoped but not built — needs OAuth, per-user token storage, XML mapping.
- DK salary upload is shared and unauthenticated on the public site.
- Tournament tool was dropped in favour of doing analysis in Cowork.
