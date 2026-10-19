"""DraftKings' own salary export — the file you download from a contest's player list.

This is public DraftKings data, so it is safe to use on the
public site. Handles both classic exports and showdown exports (which list each player
twice, once as CPT and once as FLEX).

Columns: Position, Name + ID, Name, ID, Roster Position, Salary, Game Info,
         TeamAbbrev, AvgPointsPerGame, Status
"""
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .loader import _norm

ET = ZoneInfo("America/New_York")
TEAM_ALIASES = {"LA": "LAR", "WSH": "WAS", "JAC": "JAX"}

GAME_RE = re.compile(r"^\s*([A-Z]{2,4})@([A-Z]{2,4})\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}[AP]M)", re.I)


def looks_like_dk(df):
    cols = {c.strip().lower() for c in df.columns}
    return "teamabbrev" in cols and "salary" in cols and ("name + id" in cols or "roster position" in cols)


def _team(t):
    t = str(t or "").strip().upper()
    return TEAM_ALIASES.get(t, t)


def _parse_game(info):
    """'DET@ARI 10/11/2026 04:25PM ET' -> (away, home, kickoff in Eastern)."""
    m = GAME_RE.match(str(info or ""))
    if not m:
        return None
    away, home, date, clock = m.groups()
    try:
        when = datetime.strptime(f"{date} {clock}", "%m/%d/%Y %I:%M%p").replace(tzinfo=ET)
    except ValueError:
        when = None
    return _team(away), _team(home), when


def load_dk_salaries(source):
    """Parse a DK salary export into the lookup shape the rest of the app expects."""
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    df = pd.read_csv(source)
    df.columns = [c.strip() for c in df.columns]
    if not looks_like_dk(df):
        raise ValueError("This doesn't look like a DraftKings salary export "
                         "(expected columns like TeamAbbrev, Salary, Roster Position).")

    out = {"by_name": {}, "by_dst": {}, "teams": set(), "games": [], "showdown": False,
           "ids": {}, "count": 0}
    seen_games = {}

    for _, r in df.iterrows():
        pos = str(r.get("Position", "")).strip().upper()
        roster = str(r.get("Roster Position", "")).strip().upper()
        team = _team(r.get("TeamAbbrev"))
        name = str(r.get("Name", "")).strip()
        try:
            sal = int(float(r.get("Salary")))
        except (TypeError, ValueError):
            continue
        if not name or not team or sal <= 0:
            continue

        is_cpt = "CPT" in roster or "CAPTAIN" in roster
        if is_cpt:
            out["showdown"] = True

        out["teams"].add(team)
        key = _norm(name)
        slot = "cpt_sal" if is_cpt else "sal"
        bucket = out["by_dst"] if pos in ("DST", "DEF") else out["by_name"]
        entry = bucket.setdefault(team if pos in ("DST", "DEF") else key, {})
        entry[slot] = sal
        if not is_cpt and r.get("ID"):
            out["ids"][key if pos not in ("DST", "DEF") else team] = str(r.get("ID")).strip()

        g = _parse_game(r.get("Game Info"))
        if g and g[0] and g[1]:
            seen_games[(g[0], g[1])] = g[2]

    out["games"] = [{"away": a, "home": h,
                     "start": (w.isoformat() if w else None)} for (a, h), w in sorted(seen_games.items())]
    out["count"] = len(out["by_name"]) + len(out["by_dst"])
    if not out["count"]:
        raise ValueError("No priced players found in that file.")
    return out
