"""Sleeper leagues: scoring systems and roster ownership, for any number of leagues.

Nothing here is tied to one account. A visitor supplies their own Sleeper username (or
user id); the leagues they pick are passed in per request, so two people using the site
at once see their own leagues and their own team highlighted.

SLEEPER_LEAGUE_ID / SLEEPER_USER_ID in the environment still work as a default for a
visitor who hasn't chosen anything.
"""
import os
import time

from . import live
from .live import _get, BASE

KEY_PREFIX = "league:"
LEAGUE_TTL = 3600          # scoring settings barely change
ROSTER_TTL = 300           # waiver moves land during the week
USER_TTL = 600

_league_cache = {}
_roster_cache = {}
_user_cache = {}

PA_KEYS = [("pts_allow_0", 0), ("pts_allow_1_6", 6), ("pts_allow_7_13", 13),
           ("pts_allow_14_20", 20), ("pts_allow_21_27", 27), ("pts_allow_28_34", 34),
           ("pts_allow_35p", 10 ** 6)]


def scoring_key(league_id):
    return f"{KEY_PREFIX}{league_id}"


def league_id_from_key(key):
    return key[len(KEY_PREFIX):] if str(key).startswith(KEY_PREFIX) else None


def default_ids():
    """Fallback league/user from the environment, for a visitor with no settings yet."""
    return (os.environ.get("SLEEPER_LEAGUE_ID", "").strip(),
            os.environ.get("SLEEPER_USER_ID", "").strip())


# ------------------------------------------------------------------ lookups ----
def _cached(store, key, ttl, build):
    hit = store.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        value = build()
    except Exception:
        return hit[1] if hit else None
    store[key] = (time.time(), value)
    return value


def resolve_user(who):
    """Username or numeric id -> {user_id, username, display_name}. None if unknown."""
    who = str(who or "").strip()
    if not who:
        return None
    return _cached(_user_cache, who.lower(), USER_TTL,
                   lambda: _get(f"{BASE}/user/{who}", timeout=20))


def user_leagues(who, season=None):
    """Every NFL league that user is in, newest season by default."""
    user = resolve_user(who)
    if not user or not user.get("user_id"):
        return []
    season = str(season or live.get_state()["season"])
    uid = user["user_id"]
    out = _cached(_user_cache, f"lg:{uid}:{season}", USER_TTL,
                  lambda: _get(f"{BASE}/user/{uid}/leagues/nfl/{season}", timeout=25)) or []
    return [{"league_id": str(l.get("league_id")), "name": l.get("name") or "League",
             "teams": l.get("total_rosters"), "season": l.get("season"),
             "scoring_key": scoring_key(l.get("league_id"))} for l in out]


def league(league_id):
    return _cached(_league_cache, str(league_id), LEAGUE_TTL,
                   lambda: _get(f"{BASE}/league/{league_id}", timeout=25))


def _rosters(league_id):
    def build():
        users = _get(f"{BASE}/league/{league_id}/users", timeout=25)
        rosters = _get(f"{BASE}/league/{league_id}/rosters", timeout=25)
        label = {u["user_id"]: ((u.get("metadata") or {}).get("team_name")
                                or u.get("display_name") or "?") for u in users}
        data = {"names": {}, "players": {}}
        for r in rosters:
            owner = str(r.get("owner_id") or f"roster{r.get('roster_id')}")
            data["names"][owner] = label.get(r.get("owner_id"), "?")
            data["players"][owner] = [str(x) for x in (r.get("players") or [])]
        return data
    return _cached(_roster_cache, str(league_id), ROSTER_TTL, build) or {"names": {}, "players": {}}


# ------------------------------------------------------------------ scoring ----
def _system_from(settings, label):
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


def register(league_ids):
    """Add each league to live.SCORING as its own system. Returns the ones that worked."""
    ok = []
    for lid in league_ids:
        lid = str(lid).strip()
        if not lid:
            continue
        lg = league(lid)
        if not lg:
            continue
        live.SCORING[scoring_key(lid)] = _system_from(lg.get("scoring_settings") or {},
                                                      lg.get("name") or "Sleeper league")
        ok.append(lid)
    return ok


def teams(league_id):
    """Every team in a league, eliminated ones last."""
    d = _rosters(league_id)
    out = [{"owner_id": oid, "team": d["names"].get(oid, "?"),
            "eliminated": not d["players"].get(oid)} for oid in d["names"]]
    out.sort(key=lambda t: (t["eliminated"], t["team"].lower()))
    return out


def ownership(league_id, my_team=None):
    """player_id -> {"team": name, "mine": bool} for one league."""
    if not league_id:
        return {}
    d = _rosters(league_id)
    me = str(my_team or "")
    out = {}
    for oid, pids in d["players"].items():
        name, mine = d["names"].get(oid, "?"), (oid == me)
        for pid in pids:
            out[pid] = {"team": name, "mine": mine}
    return out
