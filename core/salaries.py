"""DraftKings salaries.

DraftKings has no public API for salaries, so they come from DK's own salary export,
uploaded on the Settings tab and kept per NFL week (see dk_for_week).

Matching: offensive players by normalized name, defenses by team (DK and Sleeper name
defenses differently).
"""
import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .loader import _norm

UPLOADS = Path(__file__).resolve().parent.parent / "data" / "uploads"
DK_PATH = UPLOADS / "dk_salaries.csv"
TTL = 60

# DK's abbreviations differ from Sleeper's in a few places
TEAM_ALIASES = {"LA": "LAR", "WSH": "WAS", "JAC": "JAX"}

_cache = {}


def _team(t):
    t = (t or "").upper()
    return TEAM_ALIASES.get(t, t)


# ---- DraftKings exports, kept per NFL week ----
# Every upload is filed under data/salaries/<season>/w<week>/ by the week its games fall in,
# so old weeks keep their salaries. A file is identified by its set of games: re-uploading
# the same slate (DK re-exports after a price change) replaces it; a different slate the
# same week (main, showdown, Thu-Mon) sits beside it.
SAL_DIR = UPLOADS.parent / "salaries"


def _slate_id(parsed):
    games = ",".join(f"{g['away']}@{g['home']}" for g in parsed["games"]) or ",".join(sorted(parsed["teams"]))
    kind = "sd" if parsed["showdown"] else "cl"
    return f"{kind}-{hashlib.sha1(games.encode()).hexdigest()[:10]}"


def week_of(parsed):
    """(season, week) the file's games belong to, from Sleeper's schedule."""
    from . import live
    starts = [g["start"] for g in parsed["games"] if g.get("start")]
    if starts:
        first = datetime.fromisoformat(min(starts))
        season = first.year if first.month >= 3 else first.year - 1
        try:
            sched = live.get_schedule(season)
            by_pair = {(_team(g["away"]), _team(g["home"])): int(g["week"]) for g in sched}
            votes = Counter(by_pair[(g["away"], g["home"])] for g in parsed["games"]
                            if (g["away"], g["home"]) in by_pair)
            if votes:
                return str(season), votes.most_common(1)[0][0]
        except Exception:
            pass
    st = live.get_state()
    return str(st["season"]), int(st["week"])


def save_dk(raw, filename):
    """Validate and store an upload. Returns its description (see _describe)."""
    from .dksalaries import load_dk_salaries
    parsed = load_dk_salaries(raw)
    season, week = week_of(parsed)
    sid = _slate_id(parsed)
    folder = SAL_DIR / season / f"w{week}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.csv").write_bytes(raw)
    meta = {"filename": filename, "uploaded_at": time.strftime("%Y-%m-%d %H:%M")}
    (folder / f"{sid}.json").write_text(json.dumps(meta))
    return _describe(folder / f"{sid}.csv")


def _parse_dk(path):
    key = str(path)
    stamp = path.stat().st_mtime if path.exists() else 0
    hit = _cache.get(key)
    if hit and hit[0] == stamp:
        return hit[2]
    from .dksalaries import load_dk_salaries
    try:
        data = load_dk_salaries(path)
    except Exception:
        return None
    data["source"] = "DraftKings"
    _cache[key] = (stamp, time.time(), data)
    return data


def _describe(path):
    data = _parse_dk(path)
    if not data:
        return None
    season, week = path.parent.parent.name, int(path.parent.name[1:])
    side = path.with_suffix(".json")
    meta = json.loads(side.read_text()) if side.exists() else {}
    return {"id": f"{season}/w{week}/{path.stem}", "season": season, "week": week,
            "showdown": data["showdown"], "players": data["count"],
            "games": data["games"], "teams": sorted(data["teams"]),
            "filename": meta.get("filename"), "uploaded_at": meta.get("uploaded_at")}


def _migrate_legacy():
    """The single shared file from before salaries were kept per week."""
    if DK_PATH.exists():
        try:
            save_dk(DK_PATH.read_bytes(), "dk_salaries.csv")
            DK_PATH.unlink()
        except Exception:
            pass


def saved_dk():
    """Every stored upload, newest week first."""
    _migrate_legacy()
    out = [d for d in (_describe(p) for p in SAL_DIR.glob("*/w*/*.csv")) if d]
    out.sort(key=lambda d: (d["season"], d["week"], not d["showdown"], len(d["games"])), reverse=True)
    return out


def delete_dk(sid):
    """Remove one stored upload by the id saved_dk() reports."""
    parts = sid.split("/")
    if len(parts) != 3 or not re.fullmatch(r"\d{4}", parts[0]) or not re.fullmatch(r"w\d{1,2}", parts[1]) \
            or not re.fullmatch(r"(cl|sd)-[0-9a-f]{10}", parts[2]):
        raise ValueError("Unknown salary file.")
    folder = SAL_DIR / parts[0] / parts[1]
    for ext in (".csv", ".json"):
        (folder / f"{parts[2]}{ext}").unlink(missing_ok=True)


def dk_for_week(season, week, teams=None, prefer_showdown=False):
    """The best stored DK table for one week and (optionally) a set of teams.

    Classic files win unless prefer_showdown (a single-game view, where a showdown file
    for exactly that game is the right contest). Among candidates, the one covering the
    most of the wanted teams wins, then the bigger slate.
    """
    _migrate_legacy()
    folder = SAL_DIR / str(season) / f"w{int(week)}"
    tables = [t for t in (_parse_dk(p) for p in folder.glob("*.csv")) if t]
    if not tables:
        return None
    want = {_team(x) for x in (teams or [])}
    if prefer_showdown and len(want) == 2:
        exact = [t for t in tables if t["showdown"] and t["teams"] == want]
        if exact:
            return exact[0]

    def rank(t):
        return (len(t["teams"] & want) if want else 0, not t["showdown"], len(t["teams"]))
    best = max(tables, key=rank)
    if want and not best["teams"] & want:
        return None
    return best


def for_slate(slate_teams=None, season=None, week=None, prefer_showdown=False):
    """The stored DK salary table for the slate being viewed, or None."""
    if not (season and week):
        return None
    return dk_for_week(season, week, {_team(t) for t in (slate_teams or [])}, prefer_showdown)


def lookup(table, name, team, pos):
    if not table:
        return None
    if pos == "DST":
        return table["by_dst"].get(_team(team))
    return table["by_name"].get(_norm(name))
