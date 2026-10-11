"""FastAPI server for the public fantasy scoreboard: Live, Scoreboard and Settings."""
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import json

from core.live import (leaderboard as live_leaderboard, get_state as live_state,
                       BASE_KEYS as live_base_keys)
from core.scoreboard import get_scoreboard, game_detail
from core import sleeper as sleeper_league
from core import salaries as dk_salaries
from core.optimal import optimal_lineup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
WEB = ROOT / "web"
UPLOADS.mkdir(parents=True, exist_ok=True)
META_PATH = DATA / "meta.json"


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


app.mount("/static", NoCacheStatic(directory=WEB), name="static")


# ---------------------------------------------------------------- data ----
def _meta():
    return json.loads(META_PATH.read_text()) if META_PATH.exists() else {}


def _save_meta(**kw):
    m = _meta(); m.update(kw); META_PATH.write_text(json.dumps(m, indent=2))


# ---------------------------------------------------------------- live ----
@app.get("/api/live")
def live(season: str = None, week: str = None, top: int = 50, scoring: str = "dk",
         my_team: str = None, slate: str = "all", optimal: bool = False,
         leagues: str = None, slate_teams: str = None, single_game: bool = False):
    """Top scorers by position for one NFL week (Thu-Mon), or week="season" for season to date."""
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

        season_mode = str(week).lower() == "season"
        # The slate's teams come from the client, which derives them from ESPN kickoff
        # times. Slate options themselves are built in the browser.
        slates, teams, active = [], None, "all"
        if not season_mode and slate_teams:
            picked = {t.strip().upper() for t in slate_teams.split(",") if t.strip()}
            if picked:
                teams, active = picked, (slate or "custom")

        hurt = {}          # injuries now come from the browser (see web/espn.js)

        # DK's own export only: ETR projections are paid and live in the separate DFS Tools app
        table = (dk_salaries.for_slate(sorted(teams) if teams else None, allow_etr=False)
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
        table = dk_salaries.for_slate([away, home], allow_etr=False) if scoring == "dk" else None
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
    """What the server holds that every visitor shares — currently just the salary file."""
    dk = dk_salaries.dk_salaries()
    return {
        "salaries": None if not dk else {
            "source": dk.get("source", "DraftKings"), "players": dk["count"],
            "teams": sorted(dk["teams"]), "games": dk["games"], "showdown": dk["showdown"],
            "uploaded_at": _meta().get("dk_salaries", {}).get("uploaded_at"),
            "filename": _meta().get("dk_salaries", {}).get("filename"),
        },
        "default_league": sleeper_league.default_ids()[0],
    }


@app.post("/api/settings/salaries")
async def upload_dk_salaries(file: UploadFile = File(...)):
    """Upload DraftKings' public salary export. Shared by everyone using the site."""
    raw = await file.read()
    try:
        from core.dksalaries import load_dk_salaries
        parsed = load_dk_salaries(raw)
    except Exception as e:
        raise HTTPException(400, str(e))
    (UPLOADS / "dk_salaries.csv").write_bytes(raw)
    _save_meta(dk_salaries={"filename": file.filename,
                            "uploaded_at": time.strftime("%Y-%m-%d %H:%M"),
                            "players": parsed["count"]})
    return {"ok": True, "players": parsed["count"], "teams": sorted(parsed["teams"]),
            "games": len(parsed["games"]), "showdown": parsed["showdown"]}


@app.delete("/api/settings/salaries")
def clear_dk_salaries():
    path = UPLOADS / "dk_salaries.csv"
    if path.exists():
        path.unlink()
    _save_meta(dk_salaries=None)
    return {"ok": True}
