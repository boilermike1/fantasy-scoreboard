"""Live NFL scoring leaderboard, DraftKings scoring, via the Sleeper API.

Sleeper's read API is public: no key, no auth. We stay well under its rate limits by
caching the player dictionary on disk (it changes rarely) and the weekly stat line in
memory for a few seconds.
"""
import json
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from .loader import _norm

BASE = "https://api.sleeper.app/v1"
TZ = ZoneInfo("America/Chicago")      # the app reports Central regardless of where it runs


def now_str():
    return datetime.now(TZ).strftime("%-I:%M:%S %p %Z")
CDN_PLAYER = "https://sleepercdn.com/content/nfl/players/{pid}.jpg"
CDN_TEAM = "https://sleepercdn.com/images/team_logos/nfl/{team}.png"
CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
PLAYERS_CACHE = CACHE / "players.json"
PLAYERS_TTL = 24 * 3600      # the player dictionary is ~15MB; refresh daily
STATS_TTL = 20               # seconds; protects the API when both users watch at once

FLEX_OF = ("RB", "WR", "TE")          # what the FLEX board pools together
POSITIONS = ["QB", "RB", "WR", "TE", "FLEX", "DST", "K"]

# Scoring systems are rule tables applied to Sleeper's stat keys, scoped by position so a
# defensive stat never scores for a running back. "bonuses" is DK's 100/100/300 yardage set.
OFFENSE = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
    "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
    "rec": 1.0, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
    "fum_lost": -1, "fum_rec_td": 6,
}
RETURN_TD = 6                         # kick/punt return TDs; see dk_points
# fum_rec leaves out special teams recoveries (muffed punts etc.), which DK counts. pts_allow
# already nets out TDs and safeties the opponent's defense scored against this team's offense,
# as DK does. Both hold from 2020; Sleeper's 2018-19 stats folded the special teams recoveries
# into fum_rec and (mostly) gave the raw score as pts_allow.
DST = {
    "sack": 1, "int": 2, "fum_rec": 2, "def_st_fum_rec": 2, "safe": 2, "blk_kick": 2,
    "def_td": 6, "st_td": 6, "def_st_td": 6, "def_2pt": 2,
}
KICKER = {
    "xpm": 1, "fgm_0_19": 3, "fgm_20_29": 3, "fgm_30_39": 3,
    "fgm_40_49": 4, "fgm_50_59": 5, "fgm_50p": 5, "fgm_60p": 6,
}
# DraftKings points-allowed tiers: (max points allowed, award)
PTS_ALLOW = [(0, 10), (6, 7), (13, 4), (20, 1), (27, 0), (34, -1), (10 ** 6, -4)]


def _system(label, offense=None, dst=None, kicker=None, bonuses=True, pts_allow=None):
    return {"label": label, "offense": {**OFFENSE, **(offense or {})},
            "dst": {**DST, **(dst or {})}, "kicker": {**KICKER, **(kicker or {})},
            "bonuses": bonuses, "pts_allow": pts_allow or PTS_ALLOW, "return_td": RETURN_TD}


TURNOVERS_2 = {"pass_int": -2, "fum_lost": -2}

SCORING = {
    "dk":  _system("DraftKings"),
    "ppr": _system("PPR", offense=TURNOVERS_2, bonuses=False),
    "half": _system("Half PPR", offense={"rec": 0.5, **TURNOVERS_2}, bonuses=False),
    "std": _system("Standard", offense={"rec": 0, **TURNOVERS_2}, bonuses=False),
    "ffz": _system("Fan Free Zone", offense={"pass_yd": 0.05, "pass_td": 6, **TURNOVERS_2},
                   bonuses=False),
}
DEFAULT_SCORING = "dk"
BASE_KEYS = ("dk", "ppr", "half", "std", "ffz")
_stats_cache = {}            # (season, week) -> (fetched_at, payload)
_sched_cache = {}            # season -> (fetched_at, payload)
SCHED_TTL = 60               # game status changes during the day


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "fantasy-scoreboard/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_state():
    """Current NFL season / week per Sleeper."""
    s = _get(f"{BASE}/state/nfl")
    return {"season": str(s.get("season")), "week": int(s.get("week") or 1),
            "display_week": int(s.get("display_week") or s.get("week") or 1),
            "season_type": s.get("season_type", "regular")}


def get_players(force=False):
    """id -> {name, pos, team}. Cached on disk; the upstream file is ~15MB."""
    CACHE.mkdir(parents=True, exist_ok=True)
    if not force and PLAYERS_CACHE.exists() and time.time() - PLAYERS_CACHE.stat().st_mtime < PLAYERS_TTL:
        return json.loads(PLAYERS_CACHE.read_text())

    raw = _get(f"{BASE}/players/nfl", timeout=90)
    slim = {}
    for pid, p in raw.items():
        pos = p.get("position")
        if pos not in ("QB", "RB", "WR", "TE", "K", "DEF"):
            continue
        name = p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid
        slim[pid] = {"name": name, "pos": "DST" if pos == "DEF" else pos, "team": p.get("team") or ""}
    PLAYERS_CACHE.write_text(json.dumps(slim))
    return slim


def get_stats(season, week, ttl=STATS_TTL):
    """Stats for one week, or season-to-date totals when week is None."""
    key = (str(season), week)
    hit = _stats_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    url = f"{BASE}/stats/nfl/regular/{season}" + (f"/{int(week)}" if week else "")
    data = _get(url, timeout=45)
    _stats_cache[key] = (time.time(), data)
    return data


def get_schedule(season):
    hit = _sched_cache.get(str(season))
    if hit and time.time() - hit[0] < SCHED_TTL:
        return hit[1]
    data = _get(f"https://api.sleeper.app/schedule/nfl/regular/{season}")
    _sched_cache[str(season)] = (time.time(), data)
    return data


def week_games(season, week):
    """The schedule rows for one week. Empty on any failure — status is a nicety."""
    try:
        return [g for g in get_schedule(season) if int(g.get("week", 0)) == int(week)]
    except Exception:
        return []


def team_game_status(games):
    """team abbreviation -> 'pre_game' | 'in_game' | 'complete' | 'canceled'."""
    out = {}
    for g in games:
        for side in ("home", "away"):
            if g.get(side):
                out[g[side].upper()] = g.get("status", "")
    return out


# ---------------------------------------------------------------- DK scoring ----
def normalize_dst(stats, started):
    """Sleeper omits pts_allow entirely for a shutout, which silently costs a defense its
    +10. Once the game is under way, a missing value means zero allowed, not no data."""
    if started and "pts_allow" not in stats:
        return {**stats, "pts_allow": 0}
    return stats


def _points_allowed(pa, tiers):
    if pa is None:
        return 0.0
    for ceiling, award in tiers:
        if pa <= ceiling:
            return float(award)
    return 0.0


def dk_points(s, pos, scoring=DEFAULT_SCORING):
    """Fantasy points from a Sleeper stat line under the named scoring system."""
    sysr = SCORING.get(scoring) or SCORING[DEFAULT_SCORING]
    g = lambda k: float(s.get(k, 0) or 0)

    if pos == "DST":
        pts = sum(mult * g(k) for k, mult in sysr["dst"].items())
        return round(pts + _points_allowed(s.get("pts_allow"), sysr["pts_allow"]), 2)

    if pos == "K":
        rules = sysr["kicker"]
        pts = sum(mult * g(k) for k, mult in rules.items())
        made = any(s.get(k) for k in rules if k.startswith("fgm_"))
        if not made:                       # some feeds report only a total
            pts = rules.get("xpm", 1) * g("xpm") + 3 * g("fgm")
        if "fgmiss" in rules:
            pts += rules["fgmiss"] * g("fgmiss")
        if "xpmiss" in rules:
            pts += rules["xpmiss"] * g("xpmiss")
        return round(pts, 2)

    pts = sum(mult * g(k) for k, mult in sysr["offense"].items())
    # Kick/punt return TDs: kr_td/pr_td until 2019, st_td since. st_td also counts recovering
    # a blocked kick or a kickoff for a TD, which DK doesn't credit; from 2023 those are
    # flagged as misc_td (a kickoff recovery isn't, and costs 6 points, about once a season).
    pts += sysr["return_td"] * max(g("kr_td") + g("pr_td"), g("st_td") - g("misc_td"), 0)
    if sysr["bonuses"]:
        if g("pass_yd") >= 300: pts += 3
        if g("rush_yd") >= 100: pts += 3
        if g("rec_yd") >= 100: pts += 3
    return round(pts, 2)


def _statline(s, pos, avg=False):
    """Readable stat line. Primary stats lead: passing for QBs, rushing for RBs, receiving
    for WRs and TEs. With avg=True the stats are per-game averages, shown to one decimal."""
    g = lambda k: float(s.get(k, 0) or 0)
    n = lambda v: f"{v:.1f}" if avg else f"{v:.0f}"
    bits = []
    if pos == "DST":
        counts = {"sk": g("sack"), "int": g("int"),
                  "fr": g("fum_rec") + g("def_st_fum_rec"),     # both score as recoveries
                  "TD": g("def_td"), "saf": g("safe")}
        for label, v in counts.items():
            if v: bits.append(f"{n(v)} {label}")
        if s.get("pts_allow") is not None: bits.append(f"{n(g('pts_allow'))} PA")
        return ", ".join(bits)
    if pos == "K":
        if g("fgm"): bits.append(f"{n(g('fgm'))}/{n(g('fga'))} FG")
        if g("xpm"): bits.append(f"{n(g('xpm'))} XP")
        return ", ".join(bits)

    parts = {}
    if g("pass_att") or g("pass_yd") or g("pass_td"):
        att = f"{n(g('pass_cmp'))}/{n(g('pass_att'))}, " if g("pass_att") else ""
        parts["pass"] = (f"{att}{n(g('pass_yd'))} pass yd, {n(g('pass_td'))} TD"
                         + (f", {n(g('pass_int'))} INT" if g("pass_int") else ""))
    if g("rush_att") or g("rush_yd") or g("rush_td"):
        car = f"{n(g('rush_att'))} car, " if g("rush_att") else ""
        parts["rush"] = f"{car}{n(g('rush_yd'))} rush yd" + (f", {n(g('rush_td'))} TD" if g("rush_td") else "")
    if g("rec") or g("rec_yd") or g("rec_tgt"):
        tgt = f"/{n(g('rec_tgt'))}" if g("rec_tgt") else ""
        parts["rec"] = f"{n(g('rec'))}{tgt} rec, {n(g('rec_yd'))} yd" + (f", {n(g('rec_td'))} TD" if g("rec_td") else "")
    order = STAT_ORDER.get(pos, STAT_ORDER["QB"])
    bits = [parts[k] for k in order if k in parts]
    if g("fum_lost"):
        bits.append(f"{n(g('fum_lost'))} fum lost")
    return " · ".join(bits)


STAT_ORDER = {"QB": ("pass", "rush", "rec"), "RB": ("rush", "rec", "pass"),
              "WR": ("rec", "rush", "pass"), "TE": ("rec", "rush", "pass")}

# Multi-week views on the Live tab: key -> (label, how many recent weeks or None for all,
# average per completed game?). Each game is scored on its own and then added up, so per-game
# rules (DK's yardage bonuses, points-allowed tiers) apply per game, not to season totals.
RANGE_MODES = {
    "season": ("season to date", None, False),
    "season_avg": ("season to date, average per game", None, True),
    "last3": ("last 3 weeks, average per game", 3, True),
    "last5": ("last 5 weeks, average per game", 5, True),
}
PAST_WEEK_TTL = 3600         # a finished week's stats only change with late corrections


def _range_stats(season, cur_week, last_n, average, scoring, players):
    """pid -> {"pts", "gp", "stats"} summed over a span of weeks.

    Averages count only games that are final. "Last N weeks" means the N most recent weeks
    whose games are all final, so a week in progress joins the window once it's done.
    """
    statuses = {w: team_game_status(week_games(season, w)) for w in range(1, cur_week + 1)}
    if last_n:
        done = [w for w in statuses if statuses[w] and all(v == "complete" for v in statuses[w].values())]
        weeks = done[-last_n:]
    else:
        weeks = list(statuses)

    out = {}
    for w in weeks:
        try:
            stats = get_stats(season, w, ttl=STATS_TTL if w == cur_week else PAST_WEEK_TTL)
        except Exception:
            continue
        status = statuses[w]
        for pid, s in stats.items():
            meta = players.get(pid)
            if not meta or not s.get("gp"):
                continue
            team_state = status.get((meta["team"] or pid).upper(), "")
            if average and team_state != "complete":
                continue                  # only finished games count toward an average
            if meta["pos"] == "DST":
                s = normalize_dst(s, team_state in ("in_game", "complete"))
            row = out.setdefault(pid, {"pts": 0.0, "gp": 0, "stats": {}})
            row["pts"] += dk_points(s, meta["pos"], scoring)
            row["gp"] += 1
            for k, v in s.items():
                if isinstance(v, (int, float)):
                    row["stats"][k] = row["stats"].get(k, 0) + v
    return out


def leaderboard(season=None, week=None, top=50, scoring=DEFAULT_SCORING, owners=None,
                teams=None, injuries=None, salaries=None, scoring_keys=None):
    """Top scorers by position for one NFL week (Thu-Mon), or season to date.

    week may also be a RANGE_MODES key: season totals, or per-game averages over the
    season or the last 3 / 5 weeks.
    """
    state = get_state()
    season = str(season or state["season"])
    mode = str(week).lower() if str(week).lower() in RANGE_MODES else None
    season_mode = mode is not None
    week = None if season_mode else int(week or state["week"])

    players = get_players()
    ranged = None
    if season_mode:
        _, last_n, average = RANGE_MODES[mode]
        ranged = _range_stats(season, int(state["week"]), last_n, average, scoring, players)
        stats = {pid: r["stats"] for pid, r in ranged.items()}
    else:
        stats = get_stats(season, week)
    games = [] if season_mode else week_games(season, week)
    status_by_team = team_game_status(games)

    by_pos = {p: [] for p in POSITIONS}
    for pid, s in stats.items():
        if pid.startswith("TEAM_"):
            continue                      # team offensive totals, not a rosterable player
        meta = players.get(pid)
        if not meta:
            continue
        pos = meta["pos"]
        if pos not in by_pos or pos == "FLEX":
            continue
        if teams is not None and (meta["team"] or pid).upper() not in teams:
            continue
        started = status_by_team.get((meta["team"] or pid).upper(), "") in ("in_game", "complete")
        line_avg = False
        if ranged:
            r = ranged[pid]
            avg = RANGE_MODES[mode][2]
            pts = round(r["pts"] / r["gp"], 2) if avg else round(r["pts"], 2)
            if avg:
                s = {k: v / r["gp"] for k, v in s.items()}
                line_avg = r["gp"] > 1
        else:
            if pos == "DST":
                s = normalize_dst(s, started)
            pts = dk_points(s, pos, scoring)
        # a board of top scorers, not everyone who played — but a defense that is on the
        # field belongs there even while it is scoreless
        if pts <= 0 and not (pos == "DST" and started):
            continue
        team = meta["team"] or pid
        own = (owners or {}).get(str(pid))
        inj = (injuries or {}).get(_norm(meta["name"]))
        by_pos[pos].append({
            "owner": own["team"] if own else "", "mine": bool(own and own["mine"]),
            "injury": inj["status"] if inj else "", "injury_detail": inj["detail"] if inj else "",
            "sal": None, "cpt_sal": None,
            "name": meta["name"], "team": team, "pos": pos,
            "pts": pts, "line": ((f"{ranged[pid]['gp']} GP · " if ranged else "")
                                 + _statline(s, pos, line_avg)).rstrip(" ·"),
            "img": (CDN_TEAM.format(team=team.lower()) if pos == "DST"
                    else CDN_PLAYER.format(pid=pid)),
            "team_img": CDN_TEAM.format(team=team.lower()),
            "status": status_by_team.get(team.upper(), ""),
        })

    if salaries:
        from .salaries import lookup as _sal_lookup
        for pos_rows in by_pos.values():
            for row in pos_rows:
                hit = _sal_lookup(salaries, row["name"], row["team"], row["pos"])
                if hit:
                    row["sal"] = hit["sal"]
                    row["cpt_sal"] = hit.get("cpt_sal")

    # FLEX pools RB/WR/TE — built from the full lists, before each is truncated
    by_pos["FLEX"] = [dict(r) for p in FLEX_OF for r in by_pos[p]]

    for pos in by_pos:
        by_pos[pos] = sorted(by_pos[pos], key=lambda r: -r["pts"])[:top]

    return {
        "season": season,
        "week": mode if season_mode else week,
        "season_mode": season_mode,
        "mode_label": RANGE_MODES[mode][0] if season_mode else "",
        "current_week": state["week"],
        "scoring": scoring,
        "scoring_label": (SCORING.get(scoring) or SCORING[DEFAULT_SCORING])["label"],
        "scoring_options": [{"key": k, "label": SCORING[k]["label"]}
                            for k in (scoring_keys or list(SCORING)) if k in SCORING],
        "live_games": sum(1 for g in games if g.get("status") == "in_game"),
        "upcoming_games": sum(1 for g in games if g.get("status") == "pre_game"),
        "positions": POSITIONS,
        "players": by_pos,
        "updated_at": now_str(),
    }
