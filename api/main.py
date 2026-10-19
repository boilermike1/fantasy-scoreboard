"""FastAPI server for the public fantasy scoreboard: Live, Scoreboard and Settings."""
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from core.live import (leaderboard as live_leaderboard, get_state as live_state,
                       BASE_KEYS as live_base_keys, RANGE_MODES)
from core.scoreboard import get_scoreboard, game_detail
from core import sleeper as sleeper_league
from core import salaries as dk_salaries
from core.optimal import optimal_lineup
from core import props as espn_props

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
WEB = ROOT / "web"
UPLOADS.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- app ----
app = FastAPI(title="Fantasy Scoreboard")


NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


class NoCacheStatic(StaticFiles):
    """Always revalidate: a changed app.js/style.css must never be served stale."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = NO_CACHE["Cache-Control"]
        return resp


def _asset_version():
    try:
        return str(int(max((WEB / f).stat().st_mtime for f in ("app.js", "style.css", "espn.js"))))
    except OSError:
        return "0"


# Cloudflare Web Analytics. The site is grey-cloud (not proxied), so
# visits are counted by this browser-side beacon; view under the mikemendel.com site.
CF_ANALYTICS = ("<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
                "data-cf-beacon='{\"token\": \"32364fb3064143768104bb94b701d5c4\"}'></script>")


@app.get("/")
def index():
    """Serve the page with version-stamped asset URLs so browsers can't cache across deploys."""
    html = (WEB / "index.html").read_text()
    v = _asset_version()
    html = (html.replace("/static/style.css", f"/static/style.css?v={v}")
                .replace("/static/app.js", f"/static/app.js?v={v}")
                .replace("/static/espn.js", f"/static/espn.js?v={v}")
                .replace("</body>", CF_ANALYTICS + "\n</body>"))
    return HTMLResponse(html, headers=NO_CACHE)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(WEB / "logo.png", media_type="image/png")


app.mount("/static", NoCacheStatic(directory=WEB), name="static")


# ---------------------------------------------------------------- live ----
@app.get("/api/live")
def live(season: str = None, week: str = None, top: int = 50, scoring: str = "dk",
         my_team: str = None, slate: str = "all", optimal: bool = False,
         leagues: str = None, slate_teams: str = None, single_game: bool = False):
    """Top scorers by position for one NFL week (Thu-Mon), or a multi-week view (RANGE_MODES)."""
    try:
        # register whichever leagues this visitor picked (falling back to the env default)
        # leagues=None means "visitor has no settings yet" -> show the default league.
        # leagues="" means "visitor deliberately chose none" -> show no leagues.
        if leagues is None:
            wanted = [x for x in [sleeper_league.default_ids()[0]] if x]
        else:
            wanted = [x.strip() for x in leagues.split(",") if x.strip()]
        sleeper_league.register(wanted)

        league_id = sleeper_league.league_id_from_key(scoring)
        owners = sleeper_league.ownership(league_id, my_team) if league_id else None

        season_mode = str(week).lower() in RANGE_MODES
        # The slate's teams come from the client, which derives them from ESPN kickoff
        # times. Slate options themselves are built in the browser.
        slates, teams, active = [], None, "all"
        if not season_mode and slate_teams:
            picked = {t.strip().upper() for t in slate_teams.split(",") if t.strip()}
            if picked:
                teams, active = picked, (slate or "custom")

        hurt = {}          # injuries now come from the browser (see web/espn.js)

        # DK's own salary export only.
        # Salaries are kept per week; season-to-date borrows the current week's.
        st = live_state()
        sal_week = st["week"] if season_mode or not week else int(week)
        table = (dk_salaries.for_slate(sorted(teams) if teams else None,
                                       season=season or st["season"], week=sal_week,
                                       prefer_showdown=single_game)
                 if scoring == "dk" else None)
        showdown_mode = bool(table and table.get("showdown") and single_game)

        depth = max(top, 500) if optimal else top
        visible_keys = list(live_base_keys) + [sleeper_league.scoring_key(x) for x in wanted]
        out = live_leaderboard(season, week, depth, scoring, owners, teams, hurt, table,
                               scoring_keys=visible_keys)

        opt = None
        if optimal:
            rows = [r for pos in out["positions"] if pos != "FLEX" for r in out["players"][pos]]
            opt = optimal_lineup(rows, table, showdown=showdown_mode)
            for pos in out["positions"]:
                out["players"][pos] = out["players"][pos][:top]

        if single_game:
            # one game behaves like showdown: a single pool of every position
            everyone = [r for pos in out["positions"] if pos != "FLEX" for r in out["players"][pos]]
            everyone.sort(key=lambda r: -r["pts"])
            out["players"] = {"ALL": everyone[:top]}
            out["positions"] = ["ALL"]

        out["slates"] = slates
        out["slate"] = active
        out["salaries"] = bool(table)
        out["showdown_mode"] = showdown_mode
        out["single_game"] = single_game
        out["optimal"] = opt
        return out
    except Exception as e:
        raise HTTPException(502, f"Could not reach Sleeper: {e}")


@app.get("/api/live/state")
def live_state_endpoint():
    try:
        return live_state()
    except Exception as e:
        raise HTTPException(502, f"Could not reach Sleeper: {e}")


# ---------------------------------------------------------------- scoreboard ----
@app.get("/api/scoreboard")
def scoreboard(season: str = None, week: int = None):
    """Every game for the week, in-progress first and finals last.

    Returns {"unavailable": true} rather than failing when ESPN refuses the server's IP —
    the browser fetches ESPN directly in that case.
    """
    try:
        return get_scoreboard(season, week)
    except Exception as e:
        return {"games": [], "live_games": 0, "upcoming_games": 0,
                "unavailable": True, "error": str(e)}


@app.get("/api/props")
def props(game_id: str, season: str = None, week: int = None, final: bool = False):
    """DraftKings player prop lines for one game (ESPN's copy), main over/unders only."""
    try:
        data = espn_props.game_props(game_id, season, week, final)
        return {**data, "players": espn_props.with_sleeper_ids(data["players"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        return {"game_id": game_id, "players": [], "posted": False, "error": str(e)}


@app.get("/api/player")
def player(pid: str, scoring: str = "dk", leagues: str = None, season: str = None):
    """One player's season: weekly fantasy points plus each prop line against the result."""
    try:
        if leagues:
            sleeper_league.register([x.strip() for x in leagues.split(",") if x.strip()])
        st = live_state()
        return espn_props.player_history(pid, season or st["season"], scoring, int(st["week"]))
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not load the player: {e}")


@app.get("/api/boxscore")
def boxscore(away: str, home: str, season: str = None, week: int = None, scoring: str = "dk",
             my_team: str = None, leagues: str = None):
    """Fantasy box score for one game: skill players and DST, both teams."""
    try:
        # register whichever leagues this visitor picked (falling back to the env default)
        # leagues=None means "visitor has no settings yet" -> show the default league.
        # leagues="" means "visitor deliberately chose none" -> show no leagues.
        if leagues is None:
            wanted = [x for x in [sleeper_league.default_ids()[0]] if x]
        else:
            wanted = [x.strip() for x in leagues.split(",") if x.strip()]
        sleeper_league.register(wanted)

        league_id = sleeper_league.league_id_from_key(scoring)
        owners = sleeper_league.ownership(league_id, my_team) if league_id else None
        box = game_detail(season, week, away, home, scoring, owners)
        # DK salaries alongside DraftKings scoring, from DK's own (public) export only
        table = (dk_salaries.for_slate([away, home], season=box["season"],
                                       week=box["week"]) if scoring == "dk" else None)
        if table:
            for side in box["teams"]:
                for p in side["players"]:
                    hit = dk_salaries.lookup(table, p["name"], p["team"], p["pos"])
                    p["sal"] = hit["sal"] if hit else None
        box["salaries"] = bool(table)
        return box
    except Exception as e:
        raise HTTPException(502, f"Could not load the box score: {e}")


@app.get("/api/sleeper/user")
def sleeper_user(who: str):
    """Look up a Sleeper account by username or id, and list its NFL leagues."""
    user = sleeper_league.resolve_user(who)
    if not user or not user.get("user_id"):
        raise HTTPException(404, f"No Sleeper account found for '{who}'.")
    return {"user_id": user["user_id"], "username": user.get("username"),
            "display_name": user.get("display_name"),
            "leagues": sleeper_league.user_leagues(user["user_id"])}


@app.get("/api/sleeper/teams")
def sleeper_teams(league_id: str = None):
    """Teams in one league, for the "my team" picker."""
    lid = league_id or sleeper_league.default_ids()[0]
    if not lid:
        return {"teams": [], "default": ""}
    return {"teams": sleeper_league.teams(lid), "default": sleeper_league.default_ids()[1]}


@app.get("/api/settings")
def settings_state():
    """What the server holds that every visitor shares — the DK salary files, by week."""
    return {"salary_files": dk_salaries.saved_dk(),
            "default_league": sleeper_league.default_ids()[0]}


@app.post("/api/settings/salaries")
async def upload_dk_salaries(file: UploadFile = File(...)):
    """Upload DraftKings' public salary export. Filed under its week; shared by everyone."""
    raw = await file.read()
    try:
        saved = dk_salaries.save_dk(raw, file.filename)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **saved}


@app.delete("/api/settings/salaries")
def clear_dk_salaries(id: str):
    try:
        dk_salaries.delete_dk(id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
