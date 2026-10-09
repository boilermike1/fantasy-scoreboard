"""FastAPI server: hosts the optimizer (and, later, tournament + live tabs)."""
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.loader import load_projections, load_top_plays, parse_paste_block, match_names
from core.optimizer import Optimizer, OptimizeError, lineups_to_upload_rows, settings_from_dict
from core.analysis import value_report
from core.live import (leaderboard as live_leaderboard, get_state as live_state,
                       BASE_KEYS as live_base_keys)
from core.scoreboard import get_scoreboard, game_detail, week_slates, injuries as espn_injuries
from core import sleeper as sleeper_league
from core import salaries as dk_salaries
from core.optimal import optimal_lineup
from core.showdown import (load_showdown, ShowdownOptimizer, ShowdownError,
                           showdown_upload_rows, showdown_settings_from_dict)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
WEB = ROOT / "web"
UPLOADS.mkdir(parents=True, exist_ok=True)

PROJ_PATH = UPLOADS / "projections.csv"
TP_PATH = UPLOADS / "top_plays.txt"
SD_PATH = UPLOADS / "showdown.csv"
META_PATH = DATA / "meta.json"
SECRET_PATH = DATA / "secret.key"
COOKIE = "dfs_session"

# Two ways to run the same code:
#   full       - every tab, password protected (what runs at home)
#   scoreboard - Live + Scoreboard only, no password, no uploads (what runs publicly)
MODE = os.environ.get("DFS_MODE", "full").strip().lower()
PUBLIC_MODE = MODE == "scoreboard"
DEFAULT_TOP_PLAY_BONUS = 3.0  # projected pts the solver adds per top play (x tier weight)


# ---------------------------------------------------------------- auth ----
def _password():
    if PUBLIC_MODE:
        return ""                      # nothing here is private
    pw = os.environ.get("DFS_PASSWORD")
    if pw:
        return pw
    cfg = DATA / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text()).get("password", "")
    return ""  # no password configured -> open access (local use)


def _secret():
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_hex(32))
    return SECRET_PATH.read_text().strip()


def _token():
    return hmac.new(_secret().encode(), _password().encode(), hashlib.sha256).hexdigest()


def _authed(req: Request):
    return not _password() or req.cookies.get(COOKIE) == _token()


LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>DFS Tools</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{background:#0e1116;color:#e6e6e6;font-family:-apple-system,Helvetica,Arial,sans-serif;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}form{background:#181c23;padding:32px;border-radius:12px;
width:300px}h1{margin:0 0 16px;font-size:20px}input{width:100%;padding:10px;border-radius:8px;border:1px solid #333;
background:#0e1116;color:#fff;box-sizing:border-box;margin-bottom:12px}button{width:100%;padding:10px;border:0;
border-radius:8px;background:#53d337;color:#000;font-weight:700;cursor:pointer}.err{color:#ff6b6b;font-size:13px}</style>
</head><body><form method="post" action="/login"><h1>DFS Tools</h1>{{ERR}}
<input type="password" name="password" placeholder="Password" autofocus><button>Enter</button></form></body></html>"""


# ---------------------------------------------------------------- app ----
app = FastAPI(title="DFS Tools")


# /api/state carries the uploaded ETR player list (names, salaries, projections) —
# paid content that must never be served from the public site.
PRIVATE_PREFIXES = ("/api/state", "/api/upload", "/api/optimize", "/api/export",
                    "/api/picks", "/api/top_plays", "/api/showdown")
# /api/settings/* stays open on the public site: DK salary exports are public data


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if PUBLIC_MODE and path.startswith(PRIVATE_PREFIXES):
        return JSONResponse({"error": "not available on this site"}, status_code=404)
    if path.startswith("/login") or path.startswith("/static") or _authed(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return HTMLResponse(LOGIN_HTML.replace("{{ERR}}", ""))


@app.get("/login")
def login_page():
    return HTMLResponse(LOGIN_HTML.replace("{{ERR}}", ""))


@app.post("/login")
def login(password: str = Form("")):
    if password == _password():
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, _token(), max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax")
        return resp
    return HTMLResponse(LOGIN_HTML.replace("{{ERR}}", '<div class="err">Wrong password</div>'), status_code=401)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


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


# Cloudflare Web Analytics, public site only. The site is grey-cloud (not proxied), so
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
                .replace("</head>", f'<script>window.DFS_MODE="{MODE}";</script>\n</head>'))
    if PUBLIC_MODE:
        # the public site isn't "DFS Tools" — drop the banner and retitle the page
        html = (html.replace("<title>DFS Tools</title>", "<title>Fantasy Scoreboard</title>")
                    .replace('<div class="brand">DFS <span>TOOLS</span></div>', "")
                    .replace("</body>", CF_ANALYTICS + "\n</body>"))
    return HTMLResponse(html, headers=NO_CACHE)


app.mount("/static", NoCacheStatic(directory=WEB), name="static")


# ---------------------------------------------------------------- data ----
def _meta():
    return json.loads(META_PATH.read_text()) if META_PATH.exists() else {}


def _save_meta(**kw):
    m = _meta(); m.update(kw); META_PATH.write_text(json.dumps(m, indent=2))


def _players():
    if not PROJ_PATH.exists():
        raise HTTPException(400, "No projections uploaded yet.")
    return load_projections(PROJ_PATH)


def _top_plays():
    return load_top_plays(TP_PATH) if TP_PATH.exists() else None


@app.get("/api/state")
def state():
    m = _meta()
    out = {"projections": m.get("projections"), "top_plays": m.get("top_plays"), "players": [],
           "picks": m.get("picks") or {"include": [], "exclude": []},
           "showdown": m.get("showdown"), "sd_players": [],
           "sd_picks": m.get("sd_picks") or {"include": [], "exclude": [],
                                             "captain_pool": [], "captain_exclude": []}}
    if PROJ_PATH.exists():
        df = load_projections(PROJ_PATH)
        out["players"] = df[["name", "pos", "team", "opp", "sal", "proj", "own_small", "own_large"]].to_dict("records")
        out["teams"] = sorted(df.team.unique().tolist())
    tp = _top_plays()
    if tp is not None:
        out["top_play_names"] = tp.name.tolist()
    if SD_PATH.exists():
        try:
            sd = load_showdown(SD_PATH)
            out["sd_players"] = sd[["name", "pos", "team", "sal", "proj", "cpt_sal", "cpt_proj"]].to_dict("records")
        except Exception as e:
            out["showdown_error"] = str(e)
    return out


def _parses_as(loader, raw):
    try:
        loader(raw)
        return True
    except Exception:
        return False


@app.post("/api/upload/projections")
async def upload_projections(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        df = load_projections(raw)
    except Exception as e:
        if _parses_as(load_showdown, raw):
            raise HTTPException(400, "That looks like a showdown slate — upload it on the Showdown tab instead.")
        raise HTTPException(400, f"Could not parse projections: {e}")
    PROJ_PATH.write_bytes(raw)
    _save_meta(projections={"filename": file.filename, "uploaded_at": time.strftime("%Y-%m-%d %H:%M"), "players": len(df)})
    return {"ok": True, "players": len(df)}


@app.post("/api/upload/top_plays")
async def upload_top_plays(file: UploadFile = File(None), text: str = Form(None)):
    """Accepts a top-plays file, or a pasted block with TOP PLAYS / INCLUDE / EXCLUDE sections."""
    if file is not None and file.filename:
        raw = await file.read(); label = file.filename
        block = {"top_plays": load_top_plays(raw), "include": [], "exclude": []}
    elif text:
        raw = text.encode(); label = "pasted list"
        block = parse_paste_block(text)
    else:
        raise HTTPException(400, "Provide a file or text.")

    tp = block["top_plays"]
    out = {"ok": True, "count": int(len(tp)), "report": None, "picks": None}
    if len(tp):
        TP_PATH.write_bytes(tp[["name", "weight"]].rename(columns={"name": "Player", "weight": "Weight"}).to_csv(index=False).encode())
        _save_meta(top_plays={"filename": label, "uploaded_at": time.strftime("%Y-%m-%d %H:%M"), "count": int(len(tp))})
    if PROJ_PATH.exists():
        df = load_projections(PROJ_PATH)
        report = {"matched": [], "unmatched": [], "ambiguous": []}
        if len(tp):
            report = Optimizer(df, settings_from_dict({}), tp).top_play_report
        picks = _meta().get("picks") or {"include": [], "exclude": []}
        for key in ("include", "exclude"):
            for name in block[key]:
                idx, un, amb = match_names(df, [name])
                if idx:
                    real = df.at[idx[0], "name"]
                    if real not in picks[key]:
                        picks[key].append(real)
                    other = "exclude" if key == "include" else "include"
                    if real in picks[other]:
                        picks[other].remove(real)
                report["unmatched"] += un
                report["ambiguous"] += [f"{r} -> {', '.join(o)}" for r, o in amb]
        if block["include"] or block["exclude"]:
            _save_meta(picks=picks)
        out["report"] = report
        out["picks"] = picks
    return out


@app.post("/api/picks")
def save_picks(body: dict):
    picks = {"include": list(body.get("include", [])), "exclude": list(body.get("exclude", []))}
    _save_meta(picks=picks)
    return {"ok": True, "picks": picks}


@app.delete("/api/top_plays")
def clear_top_plays():
    if TP_PATH.exists(): TP_PATH.unlink()
    _save_meta(top_plays=None)
    return {"ok": True}


# ---------------------------------------------------------------- optimize ----
@app.post("/api/optimize")
def optimize(body: dict):
    df = _players()
    settings = settings_from_dict(body)
    tp = _top_plays()
    if tp is not None and "top_play_bonus" not in body:
        settings.top_play_bonus = DEFAULT_TOP_PLAY_BONUS
    opt = Optimizer(df, settings, tp)
    try:
        lineups = opt.solve()
    except OptimizeError as e:
        raise HTTPException(400, str(e))
    under, over = value_report(df, settings.ownership)
    return {"lineups": lineups, "warnings": opt.warnings, "top_play_report": opt.top_play_report,
            "value": {"under": under, "over": over}}


@app.post("/api/export/dk_upload")
def export_dk_upload(body: dict):
    header, rows = lineups_to_upload_rows(body.get("lineups", []))
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(header); w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=dk_upload.csv"})


# ---------------------------------------------------------------- showdown ----
def _sd_players():
    if not SD_PATH.exists():
        raise HTTPException(400, "No showdown projections uploaded yet.")
    return load_showdown(SD_PATH)


@app.post("/api/showdown/upload")
async def upload_showdown(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        df = load_showdown(raw)
    except Exception as e:
        if _parses_as(load_projections, raw):
            raise HTTPException(400, "That looks like a main-slate file — upload it on the Main Slate tab instead.")
        raise HTTPException(400, f"Could not parse showdown projections: {e}")
    SD_PATH.write_bytes(raw)
    teams = sorted(df.team.unique())
    skipped = int(df.attrs.get("skipped_no_proj", 0))
    _save_meta(showdown={"filename": file.filename, "uploaded_at": time.strftime("%Y-%m-%d %H:%M"),
                         "players": len(df), "teams": teams, "skipped": skipped},
               sd_picks={"include": [], "exclude": [], "captain_pool": [], "captain_exclude": []})
    return {"ok": True, "players": len(df), "teams": teams, "skipped": skipped}


@app.post("/api/showdown/picks")
def save_showdown_picks(body: dict):
    picks = {k: list(body.get(k, [])) for k in ("include", "exclude", "captain_pool", "captain_exclude")}
    _save_meta(sd_picks=picks)
    return {"ok": True, "picks": picks}


@app.post("/api/showdown/optimize")
def optimize_showdown(body: dict):
    df = _sd_players()
    opt = ShowdownOptimizer(df, showdown_settings_from_dict(body))
    try:
        lineups = opt.solve()
    except ShowdownError as e:
        raise HTTPException(400, str(e))
    return {"lineups": lineups, "warnings": opt.warnings}


@app.post("/api/showdown/export")
def export_showdown(body: dict):
    header, rows = showdown_upload_rows(body.get("lineups", []))
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(header); w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=dk_showdown_upload.csv"})


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

        # DK's own export is public data and fine everywhere; ETR files are private-only
        table = (dk_salaries.for_slate(sorted(teams) if teams else None, allow_etr=not PUBLIC_MODE)
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
        return game_detail(season, week, away, home, scoring, owners)
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
