"""DraftKings salaries, taken from the projection files the user uploads.

DraftKings has no public API for salaries, so these come from the ETR exports already
loaded on the Main Slate and Showdown tabs. Classic salaries come from the main-slate
file; a single-game slate uses the showdown file (which carries Captain pricing too).

Matching: offensive players by normalized name, defenses by team — ETR names defenses
by nickname ("Jaguars") while Sleeper uses the full name ("Jacksonville Jaguars").
"""
import time
from pathlib import Path

from .loader import load_projections, _norm
from .showdown import load_showdown

UPLOADS = Path(__file__).resolve().parent.parent / "data" / "uploads"
PROJ_PATH = UPLOADS / "projections.csv"
SD_PATH = UPLOADS / "showdown.csv"
TTL = 60

# ETR's abbreviations differ from Sleeper's in one place
TEAM_ALIASES = {"LA": "LAR", "WSH": "WAS", "JAC": "JAX"}

_cache = {}


def _team(t):
    t = (t or "").upper()
    return TEAM_ALIASES.get(t, t)


def _build(df, showdown):
    """name/team -> salary lookup, plus the set of teams the file covers."""
    out = {"by_name": {}, "by_dst": {}, "teams": set(), "showdown": showdown}
    for _, r in df.iterrows():
        team = _team(r["team"])
        out["teams"].add(team)
        entry = {"sal": int(r["sal"])}
        if showdown:
            entry["cpt_sal"] = int(r["cpt_sal"])
        if r["pos"] == "DST":
            out["by_dst"][team] = entry
        else:
            out["by_name"][_norm(r["name"])] = entry
    return out


def _load(path, showdown):
    key = str(path)
    hit = _cache.get(key)
    stamp = path.stat().st_mtime if path.exists() else 0
    if hit and hit[0] == stamp and time.time() - hit[1] < TTL:
        return hit[2]
    if not path.exists():
        return None
    try:
        df = load_showdown(path) if showdown else load_projections(path)
        data = _build(df, showdown)
    except Exception:
        return None
    _cache[key] = (stamp, time.time(), data)
    return data


def main_salaries():
    return _load(PROJ_PATH, showdown=False)


def showdown_salaries():
    return _load(SD_PATH, showdown=True)


def for_slate(slate_teams=None):
    """Pick the right salary table for the slate being viewed.

    A single game uses the showdown file when that file covers exactly those two teams;
    anything else falls back to the main-slate file.
    """
    want = {_team(t) for t in (slate_teams or [])}
    if len(want) == 2:
        sd = showdown_salaries()
        if sd and sd["teams"] == want:
            return sd
    table = main_salaries()
    if want and table and not (want & table["teams"]):
        return None            # this slate isn't in any uploaded file
    return table


def lookup(table, name, team, pos):
    if not table:
        return None
    if pos == "DST":
        return table["by_dst"].get(_team(team))
    return table["by_name"].get(_norm(name))
