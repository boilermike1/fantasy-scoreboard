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
  const log = plays.map((p) => ({
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
  return { plays: log, injuries };
}

// must match core/loader.py _norm so names line up with the server's rows
function normName(n) {
  return String(n).toLowerCase().replace(/[.'\-]/g, "")
    .replace(/\b(jr|sr|ii|iii|iv)\b/g, "").replace(/\s+/g, " ").trim();
}
