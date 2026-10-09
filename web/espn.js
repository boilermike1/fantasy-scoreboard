// ESPN refuses requests from datacenter IPs, so the server can't fetch it once hosted.
// Browsers are fine (ESPN sends CORS headers), so the scoreboard, slates, live game
// situation and play-by-play are fetched here instead. Mirrors core/scoreboard.py.
const ESPN_SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard";
const ESPN_SUM = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary";
const TEAM_FIX = { WSH: "WAS" };
const STATE_ORDER = { in: 0, pre: 1, post: 2 };
const LOGO = (t) => `https://sleepercdn.com/images/team_logos/nfl/${t.toLowerCase()}.png`;

const fixTeam = (t) => { const a = String(t || "").toUpperCase(); return TEAM_FIX[a] || a; };

const CENTRAL = { timeZone: "America/Chicago" };
function centralTime(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-US", { ...CENTRAL, month: "numeric", day: "numeric" }).split(",")[0] +
      " - " + d.toLocaleString("en-US", { ...CENTRAL, hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  } catch { return ""; }
}

async function espnJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`ESPN returned ${r.status}`);
  return r.json();
}

async function espnScoreboard(season, week) {
  const raw = await espnJson(`${ESPN_SB}?dates=${season}&seasontype=2&week=${week}`);
  const games = (raw.events || []).map((e) => {
    const comp = (e.competitions || [{}])[0];
    const sides = {};
    (comp.competitors || []).forEach((c) => { sides[c.homeAway] = c; });
    const home = sides.home, away = sides.away;
    if (!home || !away) return null;
    const st = (comp.status || {}).type || {};
    const h = fixTeam(home.team?.abbreviation), a = fixTeam(away.team?.abbreviation);

    let situation = null;
    const sit = comp.situation;
    if (sit) {
      const byId = {};
      (comp.competitors || []).forEach((c) => { if (c.team?.id) byId[String(c.team.id)] = fixTeam(c.team.abbreviation); });
      situation = {
        possession: byId[String(sit.possession)] || "",
        down_distance: sit.downDistanceText || sit.shortDownDistanceText || "",
        ball_on: sit.possessionText || "",
        red_zone: !!sit.isRedZone,
        last_play: sit.lastPlay?.text || "",
      };
    }
    return {
      id: e.id, home: h, away: a,
      home_logo: LOGO(h), away_logo: LOGO(a),
      home_score: Number(home.score || 0), away_score: Number(away.score || 0),
      state: st.state || "pre",
      detail: st.state === "pre" && comp.date ? centralTime(comp.date) : (st.shortDetail || ""),
      start: comp.date || "",
      situation,
    };
  }).filter(Boolean);

  games.sort((x, y) => (STATE_ORDER[x.state] ?? 3) - (STATE_ORDER[y.state] ?? 3) || x.start.localeCompare(y.start));
  return {
    season: String(season), week: Number(week), games,
    live_games: games.filter((g) => g.state === "in").length,
    upcoming_games: games.filter((g) => g.state === "pre").length,
  };
}

// Which DFS slate a kickoff belongs to, from its Eastern start time
function slateOf(iso) {
  const d = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "numeric", hour12: false,
  }).formatToParts(d);
  const day = parts.find((p) => p.type === "weekday")?.value;
  const hour = Number(parts.find((p) => p.type === "hour")?.value);
  if (day === "Sun") {
    if (hour >= 12 && hour <= 15) return "early";
    if (hour >= 16 && hour <= 18) return "late";
  }
  return "island";
}

function espnSlates(games) {
  const early = new Set(), late = new Set(), islands = [];
  games.forEach((g) => {
    if (!g.start) return;
    const kind = slateOf(g.start);
    if (kind === "early") { early.add(g.away); early.add(g.home); }
    else if (kind === "late") { late.add(g.away); late.add(g.home); }
    else {
      const when = new Date(g.start).toLocaleString("en-US", {
        ...CENTRAL, weekday: "short", hour: "numeric", minute: "2-digit" });
      islands.push({ key: `game:${g.away}-${g.home}`, label: `${when} · ${g.away} @ ${g.home}`,
                     teams: [g.away, g.home].sort(), start: g.start });
    }
  });
  islands.sort((a, b) => a.start.localeCompare(b.start));
  const out = [{ key: "all", label: "All games", teams: null }];
  const main = [...new Set([...early, ...late])].sort();
  if (main.length) out.push({ key: "main", label: "Main slate", teams: main });
  if (early.size) out.push({ key: "early", label: "Early only", teams: [...early].sort() });
  if (late.size) out.push({ key: "late", label: "Late only", teams: [...late].sort() });
  islands.forEach(({ start, ...rest }) => out.push(rest));
  return out;
}

const IN_GAME = new Set(["questionable", "doubtful", "out"]);

async function espnGameDetail(gameId, startIso) {
  const data = await espnJson(`${ESPN_SUM}?event=${gameId}`);
  const drives = data.drives || {};
  const all = [...(drives.previous || [])];
  if (drives.current) all.push(drives.current);

  const byId = {};
  all.forEach((d) => { if (d.team?.id && d.team?.abbreviation) byId[String(d.team.id)] = fixTeam(d.team.abbreviation); });
  ((data.header?.competitions || [{}])[0].competitors || []).forEach((c) => {
    if (c.team?.id && c.team?.abbreviation && !byId[String(c.team.id)]) byId[String(c.team.id)] = fixTeam(c.team.abbreviation);
  });

  const seen = new Set(), plays = [];
  all.forEach((d) => (d.plays || []).forEach((p) => {
    const id = p.id || JSON.stringify(p).slice(0, 40);
    if (seen.has(id)) return;
    seen.add(id); plays.push(p);
  }));
  const last = plays.slice(-5).map((p) => ({
    clock: p.clock?.displayValue || "", period: p.period?.number,
    text: p.text || "", team: byId[String(p.start?.team?.id || "")] || "",
    down: p.start?.shortDownDistanceText || "", scoring: !!p.scoringPlay,
  })).reverse();

  // only designations made after kickoff count as in-game injuries
  const kickoff = startIso ? new Date(startIso).getTime() : null;
  const injuries = {};
  (data.injuries || []).forEach((block) => (block.injuries || []).forEach((item) => {
    const status = String(item.status || "").toLowerCase();
    if (!IN_GAME.has(status)) return;
    if (String(item.details?.fantasyStatus?.description || "").toUpperCase() === "INACTIVE") return;
    if (!item.date || !kickoff || new Date(item.date).getTime() < kickoff) return;
    const name = item.athlete?.displayName;
    if (name) injuries[normName(name)] = { status: item.status, detail: (item.details?.type || "").trim() };
  }));
  return { plays: last, injuries };
}

// must match core/loader.py _norm so names line up with the server's rows
function normName(n) {
  return String(n).toLowerCase().replace(/[.'\-]/g, "")
    .replace(/\b(jr|sr|ii|iii|iv)\b/g, "").replace(/\s+/g, " ").trim();
}

// ---- DFS slates for the optimizer tabs ----
// Eastern calendar date (YYYY-MM-DD), weekday and hour of a moment
function etParts(d) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    weekday: "short", hour: "numeric", hour12: false,
  }).formatToParts(d).map((x) => [x.type, x.value]));
  return { ymd: `${p.year}-${p.month}-${p.day}`, day: p.weekday, hour: Number(p.hour) % 24 };
}
const addDay = (ymd) => new Date(Date.parse(ymd + "T12:00:00Z") + 864e5).toISOString().slice(0, 10);

// The week whose slates to show. Rolls to next week at 6am Eastern the morning after
// the last game is final (Tuesday after MNF), whatever ESPN's own week says.
async function dfsWeek() {
  let raw = await espnJson(ESPN_SB);
  const events = raw.events || [];
  const allFinal = events.length && events.every((e) => e.competitions?.[0]?.status?.type?.state === "post");
  if (allFinal) {
    const last = etParts(new Date(Math.max(...events.map((e) => Date.parse(e.date)))));
    const now = etParts(new Date());
    if (now.ymd > addDay(last.ymd) || (now.ymd > last.ymd && now.hour >= 6)) {
      let type = raw.season?.type, week = (raw.week?.number || 0) + 1;
      if (type === 2 && week > 18) { type = 3; week = 1; }
      try {
        const next = await espnJson(`${ESPN_SB}?dates=${raw.season.year}&seasontype=${type}&week=${week}`);
        if ((next.events || []).length) raw = next;
      } catch { /* keep the finished week */ }
    }
  }
  const games = (raw.events || []).map((e) => {
    const sides = {};
    ((e.competitions || [{}])[0].competitors || []).forEach((c) => { sides[c.homeAway] = c; });
    if (!sides.home || !sides.away) return null;
    return { away: fixTeam(sides.away.team?.abbreviation), home: fixTeam(sides.home.team?.abbreviation),
             start: e.date };
  }).filter((g) => g && g.start);
  return { season: raw.season?.year, type: raw.season?.type, week: raw.week?.number, games };
}

// Rules (Eastern time):
//   Sunday 12pm-6pm kickoffs        -> Main slate (classic)
//   other Sunday games              -> showdown each (international morning, SNF)
//   any other day with 1 game       -> showdown (TNF, MNF, Black Friday...)
//   any other day with 2+ games     -> one classic slate for the day (Thanksgiving, Saturdays)
//   ...and on Monday, a showdown for each game as well (MNF doubleheaders)
function dfsSlates(wk) {
  const weekLabel = wk.type === 3 ? `Playoffs wk ${wk.week}` : wk.type === 1 ? `Preseason wk ${wk.week}` : `Week ${wk.week}`;
  const base = { season: wk.season, type: wk.type, week: wk.week, week_label: weekLabel };
  const prefix = `${wk.season}-${wk.type}-${wk.week}`;
  const days = {};
  wk.games.forEach((g) => {
    const et = etParts(new Date(g.start));
    (days[et.ymd] ||= { day: et.day, games: [] }).games.push({ ...g, hour: et.hour });
  });
  const teamsOf = (gs) => [...new Set(gs.flatMap((g) => [g.away, g.home]))].sort();
  const when = (iso) => new Date(iso).toLocaleString("en-US", { ...CENTRAL, weekday: "short", month: "numeric",
                                                                 day: "numeric", hour: "numeric", minute: "2-digit" });
  const showdown = (g, tag) => ({ ...base, sid: `${prefix}-g-${g.away}-${g.home}`, kind: "showdown",
                                  label: `${tag} ${g.away}@${g.home}`, teams: [g.away, g.home].sort(),
                                  start: g.start, detail: when(g.start) });
  const out = [];
  Object.entries(days).forEach(([ymd, { day, games }]) => {
    games.sort((a, b) => a.start.localeCompare(b.start));
    if (day === "Sun") {
      const main = games.filter((g) => g.hour >= 12 && g.hour <= 18);
      if (main.length) out.push({ ...base, sid: `${prefix}-main`, kind: "classic", label: "Main slate",
                                  teams: teamsOf(main), start: main[0].start,
                                  detail: `${main.length} games · from ${when(main[0].start)}` });
      games.filter((g) => !main.includes(g)).forEach((g) => out.push(showdown(g, g.hour >= 19 ? "SNF" : "Sun")));
    } else if (games.length === 1) {
      out.push(showdown(games[0], { Thu: "TNF", Mon: "MNF" }[day] || day));
    } else {
      out.push({ ...base, sid: `${prefix}-day-${ymd}`, kind: "classic", label: `${day} slate`,
                 teams: teamsOf(games), start: games[0].start,
                 detail: `${games.length} games · from ${when(games[0].start)}` });
      if (day === "Mon") games.forEach((g) => out.push(showdown(g, "MNF")));
    }
  });
  out.sort((a, b) => a.start.localeCompare(b.start) || (a.kind === "classic" ? -1 : 1));
  out.forEach((s, i) => { s.order = i; });
  return out;
}
