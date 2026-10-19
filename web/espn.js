// ESPN refuses requests from datacenter IPs, so the server can't fetch it once hosted.
// Browsers are fine (ESPN sends CORS headers), so the scoreboard, slates, live game
// situation and play-by-play are fetched here instead. Mirrors core/scoreboard.py.
const ESPN_SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard";
const ESPN_SUM = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary";
const TEAM_FIX = { WSH: "WAS" };
const STATE_ORDER = { in: 0, pre: 1, post: 2 };
const LOGO = (t) => `https://sleepercdn.com/images/team_logos/nfl/${t.toLowerCase()}.png`;

const fixTeam = (t) => { const a = String(t || "").toUpperCase(); return TEAM_FIX[a] || a; };

// mirrors core/scoreboard.py: CHANNEL_FIX, _channel, parse_spread
const CHANNEL_FIX = { "Fox": "FOX", "NFL Net": "NFL Network" };
function channelOf(comp) {
  const names = [...new Set((comp.broadcasts || []).flatMap((b) => b.names || []))];
  return names.map((n) => CHANNEL_FIX[n] || n).join("/");
}
function parseSpread(details, home, overUnder) {
  const d = String(details || "").trim();
  if (!d) return null;
  const total = overUnder === undefined || overUnder === null || overUnder === "" || Number.isNaN(Number(overUnder))
    ? null : Number(overUnder);
  if (["EVEN", "PK", "PICK"].includes(d.toUpperCase())) return { team: home, line: "PK", total };
  const cut = d.lastIndexOf(" ");
  const team = d.slice(0, cut), line = d.slice(cut + 1);
  if (cut < 1 || !line.startsWith("-")) return null;
  return { team: fixTeam(team), line, total };
}

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
      detail: st.state === "pre" && comp.date
        ? (comp.timeValid === false ? "TBD" : centralTime(comp.date)) + (channelOf(comp) ? ` - ${channelOf(comp)}` : "")
        : (st.shortDetail || ""),
      spread: parseSpread(((comp.odds || [{}])[0] || {}).details, h, ((comp.odds || [{}])[0] || {}).overUnder),
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

// Team colors for the play log. Several primaries (navy, black) vanish on the dark
// background, so a too-dark primary falls back to the team's alternate color, and
// whatever is chosen is lifted toward white until it reads.
function luminance(hex) {
  const n = parseInt(String(hex || "").replace("#", ""), 16);
  if (Number.isNaN(n)) return 0;
  return (0.2126 * (n >> 16 & 255) + 0.7152 * (n >> 8 & 255) + 0.0722 * (n & 255)) / 255;
}
function playColor(team) {
  const main = team?.color, alt = team?.alternateColor;
  const pick = luminance(main) < 0.22 && luminance(alt) > luminance(main) ? alt : main;
  if (!pick) return "";
  const lum = luminance(pick), MIN = 0.4;
  if (lum >= MIN) return `#${pick}`;
  const t = (MIN - lum) / (1 - lum), n = parseInt(pick, 16);
  const lift = (c) => Math.round(c + (255 - c) * t);
  return `rgb(${lift(n >> 16 & 255)},${lift(n >> 8 & 255)},${lift(n & 255)})`;
}

const BIG_PLAY_YDS = 20;
const NOT_FROM_SCRIMMAGE = /kickoff|punt|penalty|timeout|end |field goal|extra point/i;
// Interceptions are typed as such. A lost fumble is "RECOVERED by" in capitals — the
// play-by-play writes an own-team recovery in lower case. Text before a replay reversal
// describes the overturned call, so only what follows it counts.
function isTurnover(p) {
  const type = p.type?.text || "";
  if (/interception/i.test(type) || type === "Fumble Recovery (Opponent)") return true;
  const text = String(p.text || "").split("REVERSED.").pop();
  return /\bRECOVERED by\b/.test(text);
}

async function espnGameDetail(gameId, startIso) {
  const data = await espnJson(`${ESPN_SUM}?event=${gameId}`);
  const drives = data.drives || {};
  const all = [...(drives.previous || [])];
  if (drives.current) all.push(drives.current);

  const byId = {};
  all.forEach((d) => { if (d.team?.id && d.team?.abbreviation) byId[String(d.team.id)] = fixTeam(d.team.abbreviation); });
  const colors = {};
  ((data.header?.competitions || [{}])[0].competitors || []).forEach((c) => {
    if (c.team?.id && c.team?.abbreviation && !byId[String(c.team.id)]) byId[String(c.team.id)] = fixTeam(c.team.abbreviation);
    if (c.team?.id) colors[String(c.team.id)] = playColor(c.team);
  });

  const seen = new Set(), plays = [];
  all.forEach((d) => (d.plays || []).forEach((p) => {
    const id = p.id || JSON.stringify(p).slice(0, 40);
    if (seen.has(id)) return;
    seen.add(id); plays.push(p);
  }));
  const log = plays.map((p) => {
    const offense = String(p.start?.team?.id || "");
    const turnover = isTurnover(p);
    const big = !turnover && Number(p.statYardage) >= BIG_PLAY_YDS && !NOT_FROM_SCRIMMAGE.test(p.type?.text || "");
    // a turnover belongs to the team that took the ball away
    const colorOf = turnover ? String(p.end?.team?.id || "") : offense;
    return {
      clock: p.clock?.displayValue || "", period: p.period?.number,
      text: p.text || "", team: byId[offense] || "",
      down: p.start?.shortDownDistanceText || "", spot: p.start?.possessionText || "",
      scoring: !!p.scoringPlay, turnover, big,
      color: colors[colorOf] || colors[offense] || "",
    };
  }).reverse();

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

// ESPN drops the line from the scoreboard at kickoff; the game summary keeps the closing one
async function espnSpread(gameId, home) {
  const data = await espnJson(`${ESPN_SUM}?event=${gameId}`);
  const pick = (data.pickcenter || [])[0] || {};
  return parseSpread(pick.details, home, pick.overUnder);
}

// must match core/loader.py _norm so names line up with the server's rows
function normName(n) {
  return String(n).toLowerCase().replace(/[.'\-]/g, "")
    .replace(/\b(jr|sr|ii|iii|iv)\b/g, "").replace(/\s+/g, " ").trim();
}
