"""DraftKings player props, as published in ESPN's core API.

ESPN carries DK's lines (current and opening, no prices) for every game from about two or
three days before kickoff, and keeps them after the game. Only the main over/under markets
are kept; milestone ladders, quarter/half splits and the unpriced touchdown-scorer markets
are dropped.

Lines are cached in memory for a few minutes and archived per game under
data/props/<season>/w<week>/<game_id>.json, so a finished game's closing lines never depend
on ESPN keeping them. ESPN identifies players by its own athlete id; names, positions and
teams are looked up once and kept in data/props/athletes.json.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .loader import _norm

CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
DIR = Path(__file__).resolve().parent.parent / "data" / "props"
ATHLETES = DIR / "athletes.json"
TTL = 15 * 60                  # live lines move slowly; one fetch per game per 15 minutes
GAME_ID = re.compile(r"^\d{6,12}$")
TEAM_FIX = {"WSH": "WAS"}
POS_FIX = {"PK": "K"}

# ESPN market name -> (key, label). Order is the display order in the player panel.
MARKETS = {
    "Total Passing Yards (incl. overtime)": ("pass_yd", "Pass yds"),
    "Total Pass Completions (incl. overtime)": ("pass_cmp", "Completions"),
    "Total Passing Attempts (incl. overtime)": ("pass_att", "Pass att"),
    "Total Passing Touchdowns (incl. overtime)": ("pass_td", "Pass TD"),
    "Total Passing Interceptions (incl. overtime)": ("pass_int", "INT"),
    "Longest Passing Completion (incl. overtime)": ("pass_lng", "Longest comp"),
    "Total Passing Plus Rushing Yards (incl. overtime)": ("pass_rush_yd", "Pass + rush yds"),
    "Total Rushing Yards (incl. overtime)": ("rush_yd", "Rush yds"),
    "Total Carries (incl. overtime)": ("rush_att", "Carries"),
    "Longest Rush (incl. overtime)": ("rush_lng", "Longest rush"),
    "Total Receptions (incl. overtime)": ("rec", "Receptions"),
    "Total Receiving Yards (incl. overtime)": ("rec_yd", "Rec yds"),
    "Longest Reception (incl. overtime)": ("rec_lng", "Longest rec"),
    "Total Rushing Plus Receiving Yards (incl. overtime)": ("rush_rec_yd", "Rush + rec yds"),
    "Total Kicking Points (incl. overtime)": ("kick_pts", "Kicking pts"),
    "Total Field Goals Made (incl. overtime)": ("fgm", "FG made"),
    "Total Extra Points Made (incl. overtime)": ("xpm", "XP made"),
}
ORDER = [k for k, _ in MARKETS.values()]
LABELS = dict(MARKETS.values())

_cache = {}                    # game_id -> (fetched_at, payload)
_lock = threading.Lock()


def _get(url):
    req = urllib.request.Request(url.replace("http://", "https://"), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _ref_id(ref, kind):
    m = re.search(rf"/{kind}/(\d+)", ref or "")
    return m.group(1) if m else None


def _athletes():
    try:
        return json.loads(ATHLETES.read_text())
    except (OSError, ValueError):
        return {}


def _resolve(refs, known):
    """ESPN athlete refs -> {id: {name, pos, team_id}}, fetching only the ones not seen before."""
    todo = [r for r in refs if _ref_id(r, "athletes") not in known]

    def one(ref):
        try:
            a = _get(ref)
            return a.get("id"), {"name": a.get("displayName", ""),
                                 "pos": (a.get("position") or {}).get("abbreviation", ""),
                                 "team_id": _ref_id((a.get("team") or {}).get("$ref"), "teams")}
        except Exception:
            return None, None

    if todo:
        with ThreadPoolExecutor(12) as ex:
            for aid, info in ex.map(one, todo):
                if aid:
                    known[aid] = info
        DIR.mkdir(parents=True, exist_ok=True)
        ATHLETES.write_text(json.dumps(known))
    return known


def _teams(game_id):
    """ESPN team id -> abbreviation for the two teams in a game."""
    comp = _get(f"{CORE}/events/{game_id}/competitions/{game_id}")
    out = {}
    for c in comp.get("competitors", []):
        tid = c.get("id") or _ref_id((c.get("team") or {}).get("$ref"), "teams")
        try:
            abbr = _get(c["team"]["$ref"]).get("abbreviation", "")
        except Exception:
            abbr = ""
        out[str(tid)] = TEAM_FIX.get(abbr, abbr)
    return out


def _fetch(game_id):
    base = f"{CORE}/events/{game_id}/competitions/{game_id}/odds/100/propBets?limit=1000"
    try:
        first = _get(base)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []                  # not posted yet
        raise
    items = first.get("items", [])
    for page in range(2, int(first.get("pageCount", 1)) + 1):
        items += _get(f"{base}&page={page}").get("items", [])
    return items


def _build(game_id):
    items = [i for i in _fetch(game_id)
             if "athlete" in i and (i.get("type") or {}).get("name") in MARKETS]
    if not items:
        return {"game_id": game_id, "players": [], "posted": False, "fetched": time.time()}
    known = _resolve(sorted({i["athlete"]["$ref"] for i in items}), _athletes())
    teams = _teams(game_id)
    players = {}
    for i in items:
        aid = _ref_id(i["athlete"]["$ref"], "athletes")
        who = known.get(aid)
        if not who:
            continue
        key, _ = MARKETS[i["type"]["name"]]
        cur = ((i.get("current") or {}).get("target") or {}).get("value")
        opn = ((i.get("open") or {}).get("target") or {}).get("value")
        if cur is None:
            continue
        p = players.setdefault(aid, {"espn_id": aid, "name": who["name"], "norm": _norm(who["name"]),
                                     "pos": POS_FIX.get(who["pos"], who["pos"]),
                                     "team": teams.get(str(who.get("team_id")), ""),
                                     "lines": {}})
        p["lines"][key] = {"now": cur, "open": opn}
    return {"game_id": game_id, "players": list(players.values()), "posted": True, "fetched": time.time()}


def _archive(season, week, game_id):
    return DIR / str(season) / f"w{int(week)}" / f"{game_id}.json"


def game_props(game_id, season=None, week=None, final=False):
    """Main over/under lines for every player in one game.

    A final game is served from its archive when there is one; otherwise lines come from
    ESPN (cached for TTL) and are archived as they arrive, so the last fetch before or
    during a game becomes its closing line.
    """
    if not GAME_ID.match(str(game_id)):
        raise ValueError("Bad game id.")
    path = _archive(season, week, game_id) if season and week else None
    if final and path and path.exists():
        return json.loads(path.read_text())
    with _lock:
        hit = _cache.get(game_id)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    data = _build(game_id)
    with _lock:
        _cache[game_id] = (time.time(), data)
    if path and data["players"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data


def actual(stats, key):
    """The stat a line settles on, from a Sleeper stat line. None when not tracked."""
    g = lambda k: float(stats.get(k, 0) or 0)
    combos = {"pass_rush_yd": ("pass_yd", "rush_yd"), "rush_rec_yd": ("rush_yd", "rec_yd")}
    if key in combos:
        return sum(g(k) for k in combos[key])
    if key == "kick_pts":
        return 3 * g("fgm") + g("xpm")
    return g(key) if key in LABELS else None


def with_sleeper_ids(players):
    """Copies of the prop players with their Sleeper id, matched by name and team."""
    from . import live
    index = {}
    for pid, m in live.get_players().items():
        index.setdefault((_norm(m["name"]), (m["team"] or "").upper()), pid)
        index.setdefault((_norm(m["name"]), ""), pid)
    return [{**p, "pid": index.get((p["norm"], p["team"])) or index.get((p["norm"], ""))} for p in players]


def player_history(pid, season, scoring, current_week):
    """Week by week this season: fantasy points under `scoring`, and each main prop line
    against what the player did. Lines come from the weekly archive, back-filled from ESPN
    the first time a past game is asked for."""
    from . import live
    from .scoreboard import get_scoreboard
    meta = live.get_players().get(str(pid))
    if not meta:
        raise ValueError("Unknown player.")
    team, pos, norm = (meta["team"] or "").upper(), meta["pos"], _norm(meta["name"])

    def week_row(w):
        try:
            stats = live.get_stats(season, w, ttl=live.PAST_WEEK_TTL if w < current_week else live.STATS_TTL)
        except Exception:
            stats = {}
        s = stats.get(str(pid)) or {}
        played = bool(s.get("gp"))
        row = {"week": w, "played": played, "pts": None, "line": "", "opp": "", "props": {}}
        games = live.week_games(season, w)
        g = next((x for x in games if team in (x.get("home"), x.get("away"))), None)
        if g:
            row["opp"] = ("@" + g["home"]) if g.get("away") == team else ("vs " + g["away"])
            row["final"] = g.get("status") == "complete"
        if played:
            if pos == "DST":
                s = live.normalize_dst(s, True)
            row["pts"] = live.dk_points(s, pos, scoring)
            row["line"] = live._statline(s, pos)
        # the week's lines, by ESPN game id from the scoreboard
        try:
            sb = get_scoreboard(season, w)
            espn = next((x for x in sb["games"] if team in (x["home"], x["away"])), None)
        except Exception:
            espn = None
        if espn and espn.get("id") and (espn.get("state") != "pre" or w == current_week):
            try:
                gp = game_props(espn["id"], season, w, final=espn.get("state") == "post")
            except Exception:
                gp = {"players": []}
            mine = next((p for p in gp["players"] if p["norm"] == norm), None)
            if mine:
                for key in ORDER:
                    if key in mine["lines"]:
                        ln = mine["lines"][key]
                        act = actual(s, key) if played and espn.get("state") != "pre" else None
                        row["props"][key] = {"line": ln["now"], "open": ln["open"], "actual": act,
                                             "hit": None if act is None else
                                             ("over" if act > ln["now"] else "under" if act < ln["now"] else "push")}
        return row

    with ThreadPoolExecutor(6) as ex:
        weeks = list(ex.map(week_row, range(1, int(current_week) + 1)))
    return {"pid": str(pid), "name": meta["name"], "team": team, "pos": pos,
            "labels": LABELS, "order": ORDER, "weeks": weeks}
