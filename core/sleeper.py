"""Optional Sleeper league mode: the league's own scoring, plus who owns each player.

Configured in data/config.json:
    {"sleeper": {"league_id": "...", "user_id": "..."}}
Everything here degrades to "off" if that block is missing or Sleeper is unreachable.
"""
import json
import time
from pathlib import Path

from . import live
from .live import _get, BASE

CONFIG = Path(__file__).resolve().parent.parent / "data" / "config.json"
KEY = "sleeper"                       # the scoring-system key this registers
LEAGUE_TTL = 3600                     # scoring settings change almost never
ROSTER_TTL = 300                      # waiver moves land during the week

_cache = {"league": (0, None), "owners": (0, None)}

# Sleeper's points-allowed buckets -> (ceiling, award)
PA_KEYS = [("pts_allow_0", 0), ("pts_allow_1_6", 6), ("pts_allow_7_13", 13),
           ("pts_allow_14_20", 20), ("pts_allow_21_27", 27), ("pts_allow_28_34", 34),
           ("pts_allow_35p", 10 ** 6)]


def config():
    """League settings from the environment first, then data/config.json.

    Environment wins so a hosted copy needs no config file on disk:
        SLEEPER_LEAGUE_ID, SLEEPER_USER_ID
    """
    import os
    league = os.environ.get("SLEEPER_LEAGUE_ID", "").strip()
    if league:
        return {"league_id": league, "user_id": os.environ.get("SLEEPER_USER_ID", "").strip()}
    try:
        return (json.loads(CONFIG.read_text()) or {}).get(KEY) or {}
    except Exception:
        return {}


def enabled():
    c = config()
    return bool(c.get("league_id"))


def _league():
    hit = _cache["league"]
    if hit[1] and time.time() - hit[0] < LEAGUE_TTL:
        return hit[1]
    lg = _get(f"{BASE}/league/{config()['league_id']}", timeout=25)
    _cache["league"] = (time.time(), lg)
    return lg


def _system_from(settings, label):
    """Turn Sleeper scoring_settings into one of our scoring systems."""
    pick = lambda keys: {k: float(v) for k, v in settings.items() if k in keys}
    kicker_keys = {k for k in settings if k.startswith(("fgm", "xpm")) or k in ("fgmiss", "xpmiss")}

    tiers = [(ceil, float(settings[k])) for k, ceil in PA_KEYS if k in settings]
    bonus = any(k.startswith("bonus_") and float(v) for k, v in settings.items())

    return {
        "label": label,
        "offense": {**live.OFFENSE, **pick(live.OFFENSE.keys())},
        "dst": {**live.DST, **pick(live.DST.keys())},
        "kicker": {**live.KICKER, **pick(kicker_keys)},
        "bonuses": bonus,
        "pts_allow": tiers or live.PTS_ALLOW,
    }


def ensure_registered():
    """Add (or refresh) the league's scoring system in live.SCORING. Safe to call often."""
    if not enabled():
        live.SCORING.pop(KEY, None)
        return False
    try:
        lg = _league()
        live.SCORING[KEY] = _system_from(lg.get("scoring_settings") or {},
                                         lg.get("name") or "Sleeper league")
        return True
    except Exception:
        return KEY in live.SCORING       # keep a previously loaded copy on a blip


def _rosters():
    """Cached (owner_id -> team name, owner_id -> [player ids]). Empty when unreachable."""
    hit = _cache["owners"]
    if hit[1] is not None and time.time() - hit[0] < ROSTER_TTL:
        return hit[1]
    try:
        lid = config()["league_id"]
        users = _get(f"{BASE}/league/{lid}/users", timeout=25)
        rosters = _get(f"{BASE}/league/{lid}/rosters", timeout=25)
        label = {u["user_id"]: ((u.get("metadata") or {}).get("team_name")
                                or u.get("display_name") or "?") for u in users}
        data = {"names": {}, "players": {}}
        for r in rosters:
            owner = str(r.get("owner_id") or f"roster{r.get('roster_id')}")
            data["names"][owner] = label.get(r.get("owner_id"), "?")
            data["players"][owner] = [str(x) for x in (r.get("players") or [])]
        _cache["owners"] = (time.time(), data)
        return data
    except Exception:
        return hit[1] or {"names": {}, "players": {}}


def teams():
    """Every team in the league, eliminated ones last. Empty when not configured."""
    if not enabled():
        return {"teams": [], "default": ""}
    d = _rosters()
    out = [{"owner_id": oid, "team": d["names"].get(oid, "?"),
            "eliminated": not d["players"].get(oid)} for oid in d["names"]]
    out.sort(key=lambda t: (t["eliminated"], t["team"].lower()))
    return {"teams": out, "default": str(config().get("user_id") or "")}


def ownership(my_team=None):
    """player_id -> {"team": name, "mine": bool}. my_team is an owner_id; falls back to config."""
    if not enabled():
        return {}
    d = _rosters()
    me = str(my_team or config().get("user_id") or "")
    out = {}
    for oid, pids in d["players"].items():
        name, mine = d["names"].get(oid, "?"), (oid == me)
        for pid in pids:
            out[pid] = {"team": name, "mine": mine}
    return out
