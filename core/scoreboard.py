"""Live NFL scoreboard plus fantasy-friendly box scores.

Scores and clock come from ESPN's public scoreboard feed; the per-player fantasy
numbers come from the same Sleeper stat line the Live tab uses, so a player's points
are identical in both places.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from datetime import datetime
from zoneinfo import ZoneInfo

from .loader import _norm
from .live import (CDN_TEAM, SCORING, DEFAULT_SCORING, TZ, dk_points, _get, _statline,
                   get_players, get_stats, get_state, now_str, normalize_dst,
                   week_games, team_game_status)

ESPN = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
        "?dates={season}&seasontype=2&week={week}")
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={gid}"

# ESPN agrees with Sleeper on all 32 abbreviations except Washington.
TEAM_FIX = {"WSH": "WAS"}

BOX_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]
STATE_ORDER = {"in": 0, "pre": 1, "post": 2}   # in progress first, finals last

_sb_cache = {}
_plays_cache = {}
_inj_cache = {}
SB_TTL = 20
PLAYS_TTL = 15
INJ_TTL = 120          # injury reports move slowly; one fetch per game every 2 min


def _team(abbr):
    a = (abbr or "").upper()
    return TEAM_FIX.get(a, a)


# ESPN's network names, tidied for display
CHANNEL_FIX = {"Fox": "FOX", "NFL Net": "NFL Network"}


def _channel(comp):
    names = [n for b in comp.get("broadcasts", []) for n in (b.get("names") or [])]
    return "/".join(CHANNEL_FIX.get(n, n) for n in dict.fromkeys(names))


def parse_spread(details, home, total=None):
    """'KC -7.5' -> {"team": "KC", "line": "-7.5", "total": 44.5}; a pick'em sits beside the
    home team. total is the game over/under, from which each side's implied points follow."""
    details = str(details or "").strip()
    if not details:
        return None
    try:
        total = float(total) if total not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    if details.upper() in ("EVEN", "PK", "PICK"):
        return {"team": home, "line": "PK", "total": total}
    team, _, line = details.rpartition(" ")
    if not team or not line.startswith("-"):
        return None
    return {"team": _team(team), "line": line, "total": total}


def get_scoreboard(season=None, week=None):
    """All games for one week: score, state, clock. In-progress games sort first."""
    state = get_state()
    season = str(season or state["season"])
    week = int(week or state["week"])

    key = (season, week)
    hit = _sb_cache.get(key)
    if hit and time.time() - hit[0] < SB_TTL:
        games = hit[1]
    else:
        try:
            raw = _get(ESPN.format(season=season, week=week), timeout=25)
        except Exception:
            # ESPN refuses datacenter IPs; the browser fetches it instead (see web/espn.js).
            return {"season": season, "week": week, "current_week": state["week"],
                    "scoring_options": [{"key": k, "label": SCORING[k]["label"]}
                                        for k in SCORING if not k.startswith("league:")],
                    "games": [], "live_games": 0, "upcoming_games": 0,
                    "unavailable": True, "updated_at": now_str()}
        games = []
        for e in raw.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            home, away = sides.get("home"), sides.get("away")
            if not home or not away:
                continue
            st = (comp.get("status") or {}).get("type", {})
            h, a = _team(home["team"].get("abbreviation")), _team(away["team"].get("abbreviation"))

            detail = st.get("shortDetail", "")
            if st.get("state") == "pre" and comp.get("date"):
                try:
                    kick = datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(TZ)
                    detail = kick.strftime("%-m/%-d - %-I:%M %p %Z")
                except Exception:
                    pass
                if _channel(comp):
                    detail += f" - {_channel(comp)}"

            situation = None
            sit = comp.get("situation") or {}
            if sit:
                by_id = {c["team"].get("id"): _team(c["team"].get("abbreviation"))
                         for c in comp.get("competitors", []) if c.get("team")}
                last = sit.get("lastPlay") or {}
                situation = {
                    "possession": by_id.get(str(sit.get("possession"))) or by_id.get(sit.get("possession")) or "",
                    "down_distance": sit.get("downDistanceText") or sit.get("shortDownDistanceText") or "",
                    "ball_on": sit.get("possessionText") or "",
                    "red_zone": bool(sit.get("isRedZone")),
                    "last_play": last.get("text") or "",
                }

            games.append({
                "situation": situation,
                "id": e.get("id"),
                "home": h, "away": a,
                "home_name": home["team"].get("shortDisplayName", h),
                "away_name": away["team"].get("shortDisplayName", a),
                "home_logo": CDN_TEAM.format(team=h.lower()),
                "away_logo": CDN_TEAM.format(team=a.lower()),
                "home_score": int(home.get("score") or 0),
                "away_score": int(away.get("score") or 0),
                "state": st.get("state", "pre"),          # pre | in | post
                "detail": detail,
                "spread": parse_spread(((comp.get("odds") or [{}])[0]).get("details"), h,
                                       ((comp.get("odds") or [{}])[0]).get("overUnder")),
                "start": comp.get("date", ""),
            })
        _sb_cache[key] = (time.time(), games)

    games = sorted(games, key=lambda g: (STATE_ORDER.get(g["state"], 3), g["start"]))
    return {
        "season": season, "week": week, "current_week": state["week"],
        "scoring_options": [{"key": k, "label": SCORING[k]["label"]}
                            for k in SCORING if not k.startswith("league:")],
        "games": games,
        "live_games": sum(1 for g in games if g["state"] == "in"),
        "upcoming_games": sum(1 for g in games if g["state"] == "pre"),
        "updated_at": now_str(),
    }


def box_score(season=None, week=None, away=None, home=None, scoring=DEFAULT_SCORING,
              owners=None, inj=None):
    """Fantasy box score for one game: skill players and DST for both teams."""
    state = get_state()
    season = str(season or state["season"])
    week = int(week or state["week"])
    away, home = _team(away), _team(home)

    players = get_players()
    stats = get_stats(season, week)
    status = team_game_status(week_games(season, week))
    sides = {away: [], home: []}

    for pid, s in stats.items():
        if pid.startswith("TEAM_"):
            continue
        meta = players.get(pid)
        if not meta:
            continue
        pos, team = meta["pos"], (meta["team"] or pid).upper()
        if team not in sides or pos not in BOX_POSITIONS:
            continue
        if pos == "DST":
            s = normalize_dst(s, status.get(team, "") in ("in_game", "complete"))
        pts = dk_points(s, pos, scoring)
        line = _statline(s, pos)
        if pts == 0 and not line:
            continue                       # never touched the ball
        own = (owners or {}).get(str(pid))
        hurt = (inj or {}).get(_norm(meta["name"]))
        sides[team].append({
            "owner": own["team"] if own else "", "mine": bool(own and own["mine"]),
            "injury": hurt["status"] if hurt else "", "injury_detail": hurt["detail"] if hurt else "",
            "name": meta["name"], "pos": pos, "team": team, "pts": pts, "line": line,
            "img": (CDN_TEAM.format(team=team.lower()) if pos == "DST"
                    else f"https://sleepercdn.com/content/nfl/players/{pid}.jpg"),
            "team_img": CDN_TEAM.format(team=team.lower()),
        })

    order = {p: i for i, p in enumerate(BOX_POSITIONS)}
    for t in sides:
        sides[t] = sorted(sides[t], key=lambda r: (order.get(r["pos"], 9), -r["pts"]))

    return {
        "season": season, "week": week, "away": away, "home": home,
        "scoring": scoring,
        "scoring_label": SCORING.get(scoring, SCORING[DEFAULT_SCORING])["label"],
        "teams": [{"team": away, "players": sides[away]}, {"team": home, "players": sides[home]}],
        "updated_at": now_str(),
    }


def last_plays(game_id, n=5):
    """The most recent n plays for a game, newest first. Empty on any failure."""
    if not game_id:
        return []
    hit = _plays_cache.get(str(game_id))
    if hit and time.time() - hit[0] < PLAYS_TTL:
        return hit[1]
    try:
        data = _get(ESPN_SUMMARY.format(gid=game_id), timeout=25)
        drives = data.get("drives") or {}
        all_drives = list(drives.get("previous") or [])
        cur = drives.get("current") or {}
        if cur:
            all_drives.append(cur)

        # plays only carry a numeric team id, so build id -> abbreviation first
        by_id = {}
        for d in all_drives:
            t = d.get("team") or {}
            if t.get("id") and t.get("abbreviation"):
                by_id[str(t["id"])] = _team(t["abbreviation"])
        for c in ((data.get("header") or {}).get("competitions") or [{}])[0].get("competitors", []):
            t = c.get("team") or {}
            if t.get("id") and t.get("abbreviation"):
                by_id.setdefault(str(t["id"]), _team(t["abbreviation"]))

        # the current drive is often repeated inside "previous" — dedupe by play id
        plays, seen = [], set()
        for d in all_drives:
            for pl in (d.get("plays") or []):
                pid = pl.get("id") or id(pl)
                if pid in seen:
                    continue
                seen.add(pid)
                plays.append(pl)

        out = []
        for p in plays[-n:]:
            start = p.get("start") or {}
            tid = str((start.get("team") or {}).get("id") or "")
            out.append({
                "clock": (p.get("clock") or {}).get("displayValue", ""),
                "period": ((p.get("period") or {}).get("number")),
                "text": p.get("text", ""),
                "team": by_id.get(tid, ""),
                "down": start.get("shortDownDistanceText") or "",
                "scoring": bool(p.get("scoringPlay")),
            })
        out.reverse()                     # newest first
    except Exception:
        out = []
    _plays_cache[str(game_id)] = (time.time(), out)
    return out


def game_detail(season=None, week=None, away=None, home=None, scoring=DEFAULT_SCORING, owners=None):
    """Box score plus, for a game in progress, the live situation and last five plays."""
    sb = get_scoreboard(season, week)
    game = next((g for g in sb["games"] if g["away"] == _team(away) and g["home"] == _team(home)), None)
    hurt = _game_injuries(game["id"], game.get("start")) if game and game.get("id") else {}
    box = box_score(season, week, away, home, scoring, owners, hurt)
    box["game"] = game
    box["plays"] = last_plays(game.get("id"), 5) if game and game["state"] == "in" else []
    return box


ET = ZoneInfo("America/New_York")      # NFL windows are defined in Eastern


def _slate_of(dt_et):
    """Which DFS slate a kickoff belongs to, from its Eastern start time."""
    if dt_et.weekday() == 6:                      # Sunday
        if 12 <= dt_et.hour <= 15:
            return "early"
        if 16 <= dt_et.hour <= 18:
            return "late"
    return "island"                                # Thu/Fri/Sat/Mon, SNF, international


def week_slates(season=None, week=None):
    """Slate options for the week: All / Main / Early / Late, then one per island game."""
    sb = get_scoreboard(season, week)
    early, late, islands = set(), set(), []

    for g in sb["games"]:
        try:
            dt = datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            continue
        kind = _slate_of(dt)
        pair = {g["away"], g["home"]}
        if kind == "early":
            early |= pair
        elif kind == "late":
            late |= pair
        else:
            label = dt.astimezone(TZ).strftime("%a %-I:%M %p")
            islands.append({"key": f"game:{g['away']}-{g['home']}",
                            "label": f"{label} · {g['away']} @ {g['home']}",
                            "teams": sorted(pair), "start": g["start"]})

    islands.sort(key=lambda x: x["start"])
    out = [{"key": "all", "label": "All games", "teams": None}]
    if early or late:
        out.append({"key": "main", "label": "Main slate", "teams": sorted(early | late)})
    if early:
        out.append({"key": "early", "label": "Early only", "teams": sorted(early)})
    if late:
        out.append({"key": "late", "label": "Late only", "teams": sorted(late)})
    out += [{k: v for k, v in i.items() if k != "start"} for i in islands]
    return out


# designations a player can pick up during a game; IR / suspension are roster moves
IN_GAME_STATUSES = {"questionable", "doubtful", "out"}


def _is_in_game(item, kickoff):
    """True only for a designation made after kickoff — i.e. someone hurt during the game.

    Pre-game scratches carry fantasyStatus INACTIVE and a timestamp from before kickoff;
    those are roster news, not what we want on a live board.
    """
    if (item.get("status") or "").strip().lower() not in IN_GAME_STATUSES:
        return False
    fantasy = (((item.get("details") or {}).get("fantasyStatus") or {})
               .get("description") or "").upper()
    if fantasy == "INACTIVE":
        return False
    stamp = item.get("date")
    if not stamp or not kickoff:
        return False                      # can't prove it happened in-game
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) >= kickoff
    except Exception:
        return False


def _game_injuries(game_id, start=None):
    """In-game injury designations for one game, keyed by normalized player name."""
    key = str(game_id)
    hit = _inj_cache.get(key)
    if hit and time.time() - hit[0] < INJ_TTL:
        return hit[1]

    kickoff = None
    if start:
        try:
            kickoff = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except Exception:
            pass

    out = {}
    try:
        data = _get(ESPN_SUMMARY.format(gid=game_id), timeout=25)
        for block in (data.get("injuries") or []):
            for item in (block.get("injuries") or []):
                if not _is_in_game(item, kickoff):
                    continue
                ath = item.get("athlete") or {}
                name, status = ath.get("displayName"), item.get("status")
                if not name or not status:
                    continue
                out[_norm(name)] = {"status": status,
                                    "detail": ((item.get("details") or {}).get("type") or "").strip()}
    except Exception:
        pass
    _inj_cache[key] = (time.time(), out)
    return out


def injuries(games):
    """Merged in-game injuries across several games, fetched in parallel.

    `games` is a list of scoreboard game dicts (needs "id" and "start").
    """
    pairs = [(str(g["id"]), g.get("start")) for g in games if g.get("id")]
    if not pairs:
        return {}
    merged = {}
    with ThreadPoolExecutor(max_workers=min(8, len(pairs))) as pool:
        for part in pool.map(lambda p: _game_injuries(*p), pairs):
            merged.update(part)
    return merged
