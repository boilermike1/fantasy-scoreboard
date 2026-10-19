const $ = (id) => document.getElementById(id);

// ---- tabs ----
document.querySelector("nav").addEventListener("click", (e) => {
  const b = e.target.closest(".tab"); if (!b) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  b.classList.add("active");
  $(b.dataset.tab).classList.add("active");
});

// ---- helpers ----
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401) { location.reload(); return; }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || r.statusText);
  return data;
}
const money = (n) => "$" + n.toLocaleString();
const nowCentral = () => new Date().toLocaleTimeString("en-US",
  { timeZone: "America/Chicago", hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function setStatus(el, msg, cls = "muted") { el.textContent = msg; el.className = "status " + cls; }
// fantasy-team tag, shown only when a league scoring mode is active
const ownerTag = (p) => p.owner
  ? `<span class="owner${p.mine ? " mine" : ""}" title="${esc(p.owner)}">${esc(p.owner)}</span>` : "";

// who has the ball: a small football beside the team
const BALL = `<svg class="poss" viewBox="0 0 24 14" aria-hidden="true"><title>Has the ball</title>
  <ellipse cx="12" cy="7" rx="11" ry="6.2" fill="#B8642C" stroke="#F0C9A0" stroke-width="1.2"/>
  <path d="M4.5 7h15" stroke="#fff" stroke-width="1.1" stroke-linecap="round"/>
  <path d="M9 5.2v3.6M11 5v4M13 5v4M15 5.2v3.6" stroke="#fff" stroke-width="1" stroke-linecap="round"/>
</svg>`;
const hasBall = (g, team) => !!(g.situation && g.situation.possession === team);

// Betting lines (spreads, totals, results) can be hidden on the Scoreboard; shown by default.
const showBetting = () => prefGet("sb.betting", true) !== false;

// Each team's implied points: half the over/under, plus or minus half the spread. When a
// game has a spread but no total, the spread sits beside the favorite instead.
function implied(g, team) {
  if (!showBetting()) return null;
  const s = g.spread;
  if (!s || s.total === null || s.total === undefined) return null;
  const half = s.line === "PK" ? 0 : Math.abs(parseFloat(s.line)) / 2;
  return s.total / 2 + (s.team === team ? half : -half);
}
const lineTitle = (g) => g.spread
  ? `${g.spread.team} ${g.spread.line}${g.spread.total != null ? ` · O/U ${g.spread.total}` : ""}` : "";
const spreadTag = (g, team) => showBetting() && g.spread && g.spread.team === team && implied(g, team) === null
  ? `<span class="spread" title="Point spread">${esc(g.spread.line)}</span>` : "";
// The card's top line: kickoff, channel and spread before the game; for a final, how the
// spread and total came out ("CLE covered +3 · Under 39.5").
function bettingResult(g) {
  const s = g.spread;
  if (!s) return "";
  const out = [];
  const favScore = s.team === g.home ? g.home_score : g.away_score;
  const dog = s.team === g.home ? g.away : g.home;
  const dogScore = s.team === g.home ? g.away_score : g.home_score;
  if (s.line === "PK") {
    if (favScore !== dogScore) out.push(`${favScore > dogScore ? s.team : dog} covered PK`);
  } else {
    const pts = Math.abs(parseFloat(s.line)), margin = favScore - dogScore;
    out.push(margin > pts ? `${s.team} covered ${s.line}`
      : margin < pts ? `${dog} covered +${pts}` : `Push ${s.line}`);
  }
  if (s.total != null) {
    const sum = g.home_score + g.away_score;
    out.push(`${sum > s.total ? "Over" : sum < s.total ? "Under" : "Push"} ${s.total}`);
  }
  return out.join(" · ");
}
function statusText(g) {
  const base = g.detail || "";
  if (!showBetting()) return base;
  if (g.state === "pre" && g.spread) return `${base} - ${g.spread.team} ${g.spread.line}`;
  if (g.state === "post") { const r = bettingResult(g); return r ? `${base} · ${r}` : base; }
  return base;
}

// implied total in the score slot before kickoff, small beside the score after
function impTag(g, team) {
  const v = implied(g, team);
  if (v === null) return "";
  return `<span class="imp${g.state === "pre" ? "" : " sm"}" title="Implied points · ${esc(lineTitle(g))}">${Number(v.toFixed(2))}</span>`;
}

// ESPN only lists the line before kickoff. Lines seen then are remembered in this browser,
// and a started game's closing line is fetched from its summary once and kept.
const SPREADS = "scoreboard.lines";       // game id -> {team, line, total, closed}
const spreadTried = new Set();
function applySpreads(games) {
  const all = prefGet(SPREADS, {}) || {};
  let changed = false;
  games.forEach((g) => {
    if (!g.id) return;
    if (g.spread && g.state === "pre") {
      if (JSON.stringify(all[g.id]) !== JSON.stringify(g.spread)) { all[g.id] = g.spread; changed = true; }
    } else if (!g.spread && all[g.id]) {
      g.spread = { team: all[g.id].team, line: all[g.id].line, total: all[g.id].total ?? null };
    }
  });
  if (changed) prefSet(SPREADS, all);
}
async function fetchClosingSpreads(games) {
  const all = prefGet(SPREADS, {}) || {};
  const todo = games.filter((g) => g.id && g.state !== "pre" && !(all[g.id] && all[g.id].closed)
                                   && !spreadTried.has(g.id));
  if (!todo.length) return false;
  todo.forEach((g) => spreadTried.add(g.id));
  const got = await Promise.all(todo.map((g) => espnSpread(g.id, g.home).catch(() => null)));
  let changed = false;
  todo.forEach((g, i) => {
    if (!got[i]) return;
    all[g.id] = { ...got[i], closed: true };
    g.spread = got[i]; changed = true;
  });
  if (changed) prefSet(SPREADS, all);
  return changed;
}

// in-game injury designation
const INJ_SHORT = { out: "OUT", doubtful: "D", questionable: "Q", probable: "P" };
function injTag(p) {
  if (!p.injury) return "";
  const k = String(p.injury).toLowerCase();
  const short = INJ_SHORT[k] || String(p.injury).slice(0, 3).toUpperCase();
  const title = p.injury + (p.injury_detail ? ` — ${p.injury_detail}` : "");
  return `<span class="inj ${k === "out" ? "out" : "qd"}" title="${esc(title)}">${esc(short)}</span>`;
}

// ================================================================ live ====
let liveTimer = null, liveLoading = false, livePending = false, liveData = null;
let liveSeason = null, liveWeekNow = null, liveEspn = null;

// Pull in-game injuries from ESPN (browser-side) and attach them to the server's rows.
async function applyEspnInjuries(payload, games) {
  const live = (games || []).filter((g) => g.state === "in");
  if (!live.length) return;
  try {
    const parts = await Promise.all(live.map((g) => espnGameDetail(g.id, g.start).catch(() => null)));
    const merged = {};
    parts.forEach((p) => p && Object.assign(merged, p.injuries));
    if (!Object.keys(merged).length) return;
    payload.positions.forEach((pos) => (payload.players[pos] || []).forEach((row) => {
      const hit = merged[normName(row.name)];
      if (hit) { row.injury = hit.status; row.injury_detail = hit.detail; }
    }));
  } catch { /* injuries are a nicety */ }
}

// Polling cadence follows the slate: fast while games are being played, a slow check
// while waiting for kickoff, and nothing at all once every game is final.
const FAST_MS = 30000, SLOW_MS = 300000;
function refreshPlan(d) {
  if (!d) return { ms: FAST_MS, note: "" };
  if ((d.live_games || 0) > 0) return { ms: FAST_MS, note: "" };
  if ((d.upcoming_games || 0) > 0) return { ms: SLOW_MS, note: "no games live — checking every 5 min" };
  return { ms: 0, note: "paused — no games live" };
}
const LIVE_DEFAULT_OFF = ["K", "FLEX"];  // hidden unless asked for

function prefGet(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
  catch { return fallback; }
}
function prefSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

// Scoring systems the visitor has hidden in Settings. Hidden, not shown, so a system
// added later (a new league, a new base system) appears by default.
let allScoringOpts = [];
const hiddenScoring = () => prefGet("settings.hidden_scoring", []) || [];
function visibleScoring(opts) {
  const off = hiddenScoring();
  const on = opts.filter((o) => !off.includes(o.key));
  return on.length ? on : opts;
}
// fill a Scoring <select> with the visible systems, keeping the saved choice when it's still there
function fillScoring(sel, opts) {
  const vis = visibleScoring(opts);
  const sig = vis.map((o) => o.key).join("|");
  if (sel.dataset.sig === sig) return;
  sel.dataset.sig = sig;
  const want = prefGet("live.scoring", "dk");
  sel.innerHTML = vis.map((o) => `<option value="${esc(o.key)}">${esc(o.label)}</option>`).join("");
  sel.value = vis.some((o) => o.key === want) ? want : vis[0].key;
}
const currentScoring = () => {
  const want = prefGet("live.scoring", "dk");
  const vis = visibleScoring(allScoringOpts);
  return !vis.length || vis.some((o) => o.key === want) ? want : vis[0].key;
};

// Store which positions are HIDDEN, not which are shown: a position added later
// (ALL, when a single game is picked) is then visible by default instead of filtered out.
function visiblePositions(all) {
  const off = prefGet("live.hidden", null) || LIVE_DEFAULT_OFF;
  return all.filter((p) => !off.includes(p));
}

function renderPosBar(all, showOptimal) {
  const on = visiblePositions(all);
  const wantOpt = prefGet("live.optimal", false);
  const pills = all.map((p) => `
    <label class="${on.includes(p) ? "" : "off"}">
      <input type="checkbox" data-pos value="${p}" ${on.includes(p) ? "checked" : ""}> ${p}
    </label>`).join("");
  const optPill = showOptimal ? `
    <label class="opt ${wantOpt ? "" : "off"}" title="Best lineup possible from points already scored">
      <input type="checkbox" id="optToggle" ${wantOpt ? "checked" : ""}> Optimal
    </label>` : "";
  $("posBar").innerHTML = pills + optPill;

  $("posBar").querySelectorAll("input[data-pos]").forEach((cb) => cb.addEventListener("change", () => {
    const hidden = [...$("posBar").querySelectorAll("input[data-pos]")].filter((x) => !x.checked).map((x) => x.value);
    const keep = (prefGet("live.hidden", null) || LIVE_DEFAULT_OFF).filter((p) => !all.includes(p));
    prefSet("live.hidden", [...new Set([...keep, ...hidden])]);
    renderPosBar(all, showOptimal);
    renderBoard();
  }));
  const opt = $("optToggle");
  if (opt) opt.addEventListener("change", () => { prefSet("live.optimal", opt.checked); loadLive(); });
}

// the Optimal lineup needs DK salaries, so it only exists under DraftKings scoring;
// the preference is kept and the card returns when DraftKings is picked again
const wantOptimal = () => prefGet("live.optimal", false) && ($("liveScoring").value || currentScoring()) === "dk";

function optimalCard() {
  const o = liveData && liveData.optimal;
  if (!o) return "";
  if (!o.available) {
    return `<div class="card"><div class="head"><span class="title">Optimal lineup</span></div>
      <div class="empty" style="padding:18px">${esc(o.reason || "Unavailable")}</div></div>`;
  }
  const pct = Math.round((o.salary / o.cap) * 100);
  return `<div class="card optimal">
    <div class="head">
      <span class="title">Optimal lineup${o.showdown ? " · showdown" : ""}</span>
      <span class="stat pts"><b>${o.points.toFixed(2)}</b> pts</span>
      <span class="stat">salary <b>${money(o.salary)}</b> / ${money(o.cap)}</span>
      <span class="stat">left <b>${money(o.remaining)}</b></span>
    </div>
    <div class="salbar"><span style="width:${pct}%"></span></div>
    <div class="cardbody"><table><tbody>${o.players.map((p) => `<tr>
      <td class="slot">${p.slot}</td>
      <td>${esc(p.name)} <span class="muted">${esc(p.team)}${o.showdown ? " · " + esc(p.pos) : ""}</span></td>
      <td class="num muted">${money(p.sal)}</td>
      <td class="num pts">${p.pts.toFixed(2)}</td>
    </tr>`).join("")}</tbody></table></div>
  </div>`;
}

function renderBoard() {
  if (!liveData) return;
  const show = visiblePositions(liveData.positions);
  const optCard = wantOptimal() && liveData.scoring === "dk" ? optimalCard() : "";
  if (!show.length) {
    $("liveBoard").innerHTML = optCard || `<div class="empty">No positions selected.</div>`;
    return;
  }
  $("liveBoard").innerHTML = optCard + show.map((pos) => {
    const rows = liveData.players[pos] || [];
    return `<div class="card">
      <div class="head"><span class="title">${pos}</span></div>
      ${rows.length ? `<div class="cardbody"><table><tbody>${rows.map((p, i) => `<tr class="${p.mine ? "mine" : ""}">
          <td class="rank">${i + 1}</td>
          <td class="av"><img class="avatar${p.pos === "DST" ? " logo" : ""}" loading="lazy" alt=""
            src="${esc(p.img)}" data-fallback="${esc(p.team_img)}"></td>
          <td>${pos === "FLEX" ? `<span class="posbadge">${esc(p.pos)}</span>` : ""}${esc(p.name)}${injTag(p)} <span class="muted">${esc(p.team)}</span>${ownerTag(p)}${p.line ? `<div class="line">${esc(p.line)}</div>` : ""}</td>
          ${liveData.salaries ? `<td class="num sal">${p.sal ? money(p.sal) : ""}</td>` : ""}
          <td class="num pts">${p.status ? `<span class="gdot ${esc(p.status)}" title="${p.status === "in_game" ? "In progress" : "Final"}"></span>` : ""}${p.pts.toFixed(2)}</td>
        </tr>`).join("")}</tbody></table></div>`
        : `<div class="empty" style="padding:20px">No scores yet.</div>`}
    </div>`;
  }).join("");

  wireAvatarFallback($("liveBoard"));
}

// Image loads can be aborted when many fire at once, which looks identical to a 404.
// Retry the original once, then fall back to the team logo, then give up quietly.
function wireAvatarFallback(root) {
  root.querySelectorAll("img.avatar").forEach((img) => {
    const original = img.src;
    img.addEventListener("error", () => {
      const stage = Number(img.dataset.stage || 0);
      if (stage === 0) {
        img.dataset.stage = "1";
        setTimeout(() => { img.src = `${original}?r=1`; }, 500 + Math.random() * 500);
      } else if (stage === 1 && img.dataset.fallback) {
        img.dataset.stage = "2";
        img.src = img.dataset.fallback;
        img.classList.add("logo");
      } else {
        img.style.visibility = "hidden";
      }
    });
  });
}

async function loadLive() {
  // a change made while a fetch is in flight must not be dropped — queue it
  if (liveLoading) { livePending = true; return; }
  liveLoading = true;
  try {
    const wk = $("liveWeek").value;
    const scoring = $("liveScoring").value || currentScoring();
    await syncTeamPickers(scoring);

    // ESPN needs a concrete season/week, so learn those before asking it
    if (!liveWeekNow) {
      try {
        const st = await api("/api/live/state");
        liveSeason = st.season; liveWeekNow = st.week;
      } catch { /* fall through; slates just stay empty this pass */ }
    }

    // ESPN is fetched here, not on the server (it refuses datacenter IPs)
    const seasonMode = !!wk && !/^\d+$/.test(wk);   // a multi-week view, not one week
    let slates = [], espnGames = [];
    if (!seasonMode) {
      try {
        const sb = await espnScoreboard(liveSeason, wk || liveWeekNow);
        espnGames = sb.games;
        slates = espnSlates(sb.games);
        liveEspn = sb;
      } catch { slates = []; }
    }

    const qs = new URLSearchParams({ scoring });
    qs.set("leagues", leagueParam());
    const lid = scoring.startsWith("league:") ? scoring.slice(7) : "";
    if (lid && myTeamFor(lid)) qs.set("my_team", myTeamFor(lid));
    if (wk) qs.set("week", wk);
    const slateKey = scoring === "dk" ? ($("liveSlate").value || "all") : "all";
    const chosenSlate = slates.find((x) => x.key === slateKey);
    if (chosenSlate && chosenSlate.teams) {
      qs.set("slate", slateKey);
      qs.set("slate_teams", chosenSlate.teams.join(","));
      if (slateKey.startsWith("game:")) qs.set("single_game", "true");
    }
    if (wantOptimal()) qs.set("optimal", "true");
    const r = await api(`/api/live?${qs}`);
    if (!seasonMode) {
      r.slates = slates;
      r.slate = slateKey;
      r.live_games = liveEspn ? liveEspn.live_games : r.live_games;
      r.upcoming_games = liveEspn ? liveEspn.upcoming_games : r.upcoming_games;
      await applyEspnInjuries(r, espnGames);
    }
    liveData = r;
    $("liveError").innerHTML = "";

    // rebuild when the set of systems changes (leagues added or removed in Settings)
    allScoringOpts = r.scoring_options;
    fillScoring($("liveScoring"), allScoringOpts);
    if ($("liveScoring").value !== r.scoring) { livePending = true; }   // choice was hidden; reload
    if (!$("liveWeek").options.length) {
      for (let w = r.current_week; w >= 1; w--) {
        const o = document.createElement("option");
        o.value = w; o.textContent = `Week ${w}${w === r.current_week ? " (current)" : ""}`;
        $("liveWeek").appendChild(o);
      }
      [["season", "Season to date"], ["last3", "Last 3 weeks (average)"],
       ["last5", "Last 5 weeks (average)"], ["season_avg", "Season to date (average)"]].forEach(([v, label]) => {
        const o = document.createElement("option");
        o.value = v; o.textContent = label;
        $("liveWeek").appendChild(o);
      });
      $("liveWeek").value = r.week;
    }
    liveSeason = r.season; liveWeekNow = r.current_week;
    const posSig = r.positions.join("|") + (r.salaries ? "+s" : "");
    if ($("posBar").dataset.sig !== posSig) {
      $("posBar").dataset.sig = posSig;
      renderPosBar(r.positions, !!r.salaries);
    }

    const slateOpts = r.slates || [];
    $("liveSlateWrap").classList.toggle("hidden", !slateOpts.length || r.scoring !== "dk");
    const wantSlate = $("liveSlate").value || "all";
    const signature = slateOpts.map((x) => x.key).join("|");
    if ($("liveSlate").dataset.sig !== signature) {
      $("liveSlate").dataset.sig = signature;
      $("liveSlate").innerHTML = slateOpts.map((x) =>
        `<option value="${esc(x.key)}">${esc(x.label)}</option>`).join("");
      $("liveSlate").value = slateOpts.some((x) => x.key === wantSlate) ? wantSlate : "all";
    }

    const isCurrent = r.season_mode || Number(r.week) === Number(r.current_week);
    const when = r.season_mode ? `${r.season} ${r.mode_label}` : `${r.season} Week ${r.week}`;
    const games = r.live_games ? ` · <b>${r.live_games}</b> game${r.live_games > 1 ? "s" : ""} in progress` : "";
    const slateLabel = slateOpts.find((x) => x.key === r.slate);
    const slateTxt = slateLabel && r.slate !== "all" ? ` · ${esc(slateLabel.label)}` : "";
    $("liveMeta").innerHTML = `<span class="live-dot ${isCurrent ? "" : "stale"}"></span>` +
      `${when}${slateTxt} · ${esc(r.scoring_label)}${games} · updated ${esc(r.updated_at)}`;
    renderBoard();
  } catch (err) {
    $("liveError").innerHTML = `<div class="warn err">${esc(err.message)}</div>`;
  }
  liveLoading = false;
  if (livePending) { livePending = false; loadLive(); } else { setLiveTimer(); }
}

function setLiveTimer() {
  if (liveTimer) { clearTimeout(liveTimer); liveTimer = null; }
  if (!$("liveAuto").checked) { $("liveAutoNote").textContent = ""; return; }
  const { ms, note } = refreshPlan(liveData);
  $("liveAutoNote").textContent = note;
  if (!ms) return;
  liveTimer = setTimeout(() => {
    if ($("live").classList.contains("active")) loadLive();
    else setLiveTimer();
  }, ms);
}

let sleeperTeams = null;

async function ensureTeams() {
  if (sleeperTeams) return sleeperTeams;
  try { sleeperTeams = await api("/api/sleeper/teams"); } catch { sleeperTeams = { teams: [], default: "" }; }
  [["liveTeam", "liveTeamWrap"], ["sbTeam", "sbTeamWrap"]].forEach(([sel]) => {
    if ($(sel).options.length) return;
    sleeperTeams.teams.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.owner_id;
      o.textContent = t.team + (t.eliminated ? " (out)" : "");
      $(sel).appendChild(o);
    });
  });
  const chosen = prefGet("league.myteam", null) || sleeperTeams.default;
  ["liveTeam", "sbTeam"].forEach((id) => { if ($(id).options.length) $(id).value = chosen; });
  return sleeperTeams;
}

function myTeam() { return prefGet("league.myteam", null) || (sleeperTeams && sleeperTeams.default) || ""; }

async function syncTeamPickers(scoring) {
  const on = false;   // team is chosen per league on the Settings tab
  if (on) await ensureTeams();
  $("liveTeamWrap").classList.toggle("hidden", !on || !sleeperTeams?.teams.length);
  $("sbTeamWrap").classList.toggle("hidden", !on || !sleeperTeams?.teams.length);
}

function setTeamEverywhere(val) {
  prefSet("league.myteam", val);
  if ($("liveTeam").options.length) $("liveTeam").value = val;
  if ($("sbTeam").options.length) $("sbTeam").value = val;
  if (liveData) loadLive();
  if (sbOpen) openBox(sbOpen.away, sbOpen.home);
}
$("liveTeam").addEventListener("change", () => setTeamEverywhere($("liveTeam").value));
$("sbTeam").addEventListener("change", () => setTeamEverywhere($("sbTeam").value));

function setScoringEverywhere(val) {
  prefSet("live.scoring", val);
  if ($("liveScoring").options.length) $("liveScoring").value = val;
  if ($("sbScoring").options.length) $("sbScoring").value = val;
  renderBoard();              // drops the Optimal card right away when leaving DraftKings
  if (liveData) loadLive();
  if (sbOpen) openBox(sbOpen.away, sbOpen.home);
}
$("liveScoring").addEventListener("change", () => {
  if ($("liveScoring").value !== "dk") $("liveSlate").value = "all";
  setScoringEverywhere($("liveScoring").value);
});
$("liveWeek").addEventListener("change", () => { $("liveSlate").value = "all"; loadLive(); });
$("liveSlate").addEventListener("change", loadLive);
$("liveRefresh").addEventListener("click", loadLive);
$("liveAuto").addEventListener("change", setLiveTimer);
document.querySelector('.tab[data-tab="live"]').addEventListener("click", () => {
  loadLive(); setLiveTimer();
});

// Live is the landing tab
loadLive();
setLiveTimer();

// ================================================================ scoreboard ====
let sbTimer = null, sbLoading = false, sbPending = false, sbData = null, sbOpen = null;
let sbBox = null, sbPlaysAll = false, sbTicket = 0;
const finalPlays = {};        // ESPN game id -> detail, for games already final

async function loadScoreboard(keepDetail = true) {
  if (sbLoading) { sbPending = true; return; }
  sbLoading = true;
  try {
    const wk = $("sbWeek").value;
    let r = await api(`/api/scoreboard${wk ? `?week=${wk}` : ""}`);
    if (r.unavailable || !r.games.length) {
      // ESPN blocks the server's IP; fetch it here instead
      const sb = await espnScoreboard(r.season || liveSeason || new Date().getFullYear(),
                                      wk || r.current_week || liveWeekNow);
      r = { ...r, ...sb, unavailable: false, updated_at: nowCentral() };
    }
    sbData = r;
    $("sbError").innerHTML = "";

    if (!$("sbWeek").options.length) {
      for (let w = r.current_week; w >= 1; w--) {
        const o = document.createElement("option");
        o.value = w; o.textContent = `Week ${w}${w === r.current_week ? " (current)" : ""}`;
        $("sbWeek").appendChild(o);
      }
      $("sbWeek").value = r.week;
    }
    const liveOpts = allScoringOpts.length ? allScoringOpts : (r.scoring_options || []);
    if (liveOpts.length) fillScoring($("sbScoring"), liveOpts);
    const games = r.live_games ? ` · <b>${r.live_games}</b> in progress` : "";
    $("sbMeta").innerHTML = `<span class="live-dot ${Number(r.week) === Number(r.current_week) ? "" : "stale"}"></span>` +
      `${r.season} Week ${r.week} · ${r.games.length} games${games} · updated ${esc(r.updated_at)}`;

    await syncTeamPickers(currentScoring());
    applySpreads(r.games);
    renderGames();
    fetchClosingSpreads(r.games).then((changed) => {
      if (!changed || sbData !== r) return;
      renderGames();
      if (sbOpen) renderBox();
    });
    if (sbOpen && keepDetail) await openBox(sbOpen.away, sbOpen.home);
  } catch (err) {
    $("sbError").innerHTML = `<div class="warn err">${esc(err.message)}</div>`;
  }
  sbLoading = false;
  if (sbPending) { sbPending = false; loadScoreboard(keepDetail); } else { setSbTimer(); }
}

function renderGames() {
  if (!sbData) return;
  const row = (side, g) => {
    const them = side === "away" ? g.away_score : g.home_score;
    const other = side === "away" ? g.home_score : g.away_score;
    const lost = g.state === "post" && them < other;
    return `<div class="grow ${lost ? "lost" : ""}">
      <img src="${esc(side === "away" ? g.away_logo : g.home_logo)}" alt="" loading="lazy">
      <span class="tm">${esc(side === "away" ? g.away : g.home)}${
        hasBall(g, side === "away" ? g.away : g.home) ? BALL : ""}${spreadTag(g, side === "away" ? g.away : g.home)}</span>
      ${impTag(g, side === "away" ? g.away : g.home)}
      ${g.state === "pre" ? "" : `<span class="sc">${them}</span>`}
    </div>`;
  };
  $("sbGames").innerHTML = sbData.games.map((g) => `
    <div class="game ${g.state === "in" ? "inprog" : ""}" data-away="${esc(g.away)}" data-home="${esc(g.home)}">
      <div class="gstatus">${g.state === "in" ? '<span class="gdot in_game"></span>' : ""}${esc(statusText(g))}</div>
      ${row("away", g)}${row("home", g)}
      ${g.situation && (g.situation.down_distance || g.situation.last_play) ? `
        <div class="sit ${g.situation.red_zone ? "rz" : ""}">
          ${g.situation.down_distance ? `<b>${esc(g.situation.down_distance)}</b>` : ""}
          ${g.situation.possession ? ` · ${esc(g.situation.possession)} ball` : ""}
          ${g.situation.last_play ? `<div>${esc(g.situation.last_play)}</div>` : ""}
        </div>` : ""}
    </div>`).join("");
  $("sbGames").querySelectorAll(".game").forEach((el) =>
    el.addEventListener("click", () => openBox(el.dataset.away, el.dataset.home)));
}

async function openBox(away, home) {
  if (!sbOpen || sbOpen.away !== away || sbOpen.home !== home) sbPlaysAll = false;
  sbOpen = { away, home };
  const ticket = ++sbTicket;   // a slower, older request must not overwrite this one
  const scoring = currentScoring();
  const wk = $("sbWeek").value;
  try {
    await syncTeamPickers(scoring);
    const qs = new URLSearchParams({ away, home, scoring });
    qs.set("leagues", leagueParam());
    const lid2 = scoring.startsWith("league:") ? scoring.slice(7) : "";
    if (lid2 && myTeamFor(lid2)) qs.set("my_team", myTeamFor(lid2));
    if (wk) qs.set("week", wk);
    const b = await api(`/api/boxscore?${qs}`);
    const g = (sbData?.games || []).find((x) => x.away === away && x.home === home) || {};
    b.game = { ...(b.game || {}), ...g };
    if ((g.state === "in" || g.state === "post") && g.id) {
      try {
        const extra = finalPlays[g.id] || await espnGameDetail(g.id, g.start);
        if (g.state === "post") finalPlays[g.id] = extra;   // a final game's log won't change
        b.plays = extra.plays;
        if (g.state === "in") b.teams.forEach((side) => side.players.forEach((p) => {
          const hit = extra.injuries[normName(p.name)];
          if (hit) { p.injury = hit.status; p.injury_detail = hit.detail; }
        }));
      } catch { /* plays are a nicety */ }
    }
    if (ticket !== sbTicket) return;
    sbBox = b;
    renderBox();
  } catch (err) {
    $("sbError").innerHTML = `<div class="warn err">${esc(err.message)}</div>`;
  }
}

// Live games show the last few plays with a toggle for the rest; finished games keep
// the whole log behind a button.
const RECENT_PLAYS = 5;
function playsCard(plays, state) {
  if (!plays.length) return "";
  const final = state === "post";
  // plays arrive newest first; a finished game reads better from kickoff
  const shown = sbPlaysAll ? (final ? [...plays].reverse() : plays) : final ? [] : plays.slice(0, RECENT_PLAYS);
  const more = plays.length > shown.length;
  const title = final ? "Play log" : sbPlaysAll ? `All ${plays.length} plays` : `Last ${shown.length} plays`;
  const btn = final
    ? `<button class="small ghost" data-plays>${sbPlaysAll ? "Hide play log" : `Show play log (${plays.length})`}</button>`
    : more || sbPlaysAll
      ? `<button class="small ghost" data-plays>${sbPlaysAll ? "Show last 5" : `Show all plays (${plays.length})`}</button>` : "";
  let lastQ = null;
  const rows = shown.map((p) => {
    const qhdr = sbPlaysAll && p.period !== lastQ
      ? (lastQ = p.period, `<div class="pq">${p.period > 4 ? "OT" : `Q${p.period}`}</div>`) : "";
    const hl = p.scoring || p.turnover || p.big;
    return `${qhdr}<div class="play${p.color ? " tc" : ""}${hl ? " hl" : ""}"${p.color ? ` style="--tc:${esc(p.color)}"` : ""}>
      <span class="pclock">${p.period ? `${p.period > 4 ? "OT" : `Q${p.period}`} ` : ""}${esc(p.clock || "")}</span>
      <span class="ptm">${esc(p.team || "")}</span>
      <span>${p.down || p.spot ? `<span class="muted">${esc([p.down, p.spot].filter(Boolean).join(" at "))}</span> ` : ""}${esc(p.text || "")}</span>
    </div>`;
  }).join("");
  return `<div class="card plays" style="margin-bottom:14px">
    <div class="head"><span class="title">${title}</span>${btn}</div>
    ${rows ? `<div class="playlist${sbPlaysAll ? " all" : ""}">${rows}</div>` : ""}
  </div>`;
}

// the other games this week, down the right side while a box score is open
function sideGames() {
  const others = (sbData?.games || []).filter((x) => !(sbOpen && x.away === sbOpen.away && x.home === sbOpen.home));
  if (!others.length) return "";
  const line = (t, s, g, lost) => `<div class="sgrow ${lost ? "lost" : ""}">
    <img src="${esc(LOGO(t))}" alt="" loading="lazy"><span>${esc(t)}${hasBall(g, t) ? BALL : ""}${spreadTag(g, t)}</span>
    ${impTag(g, t)}${g.state === "pre" ? "" : `<b>${s}</b>`}</div>`;
  return `<aside class="sidegames">${others.map((g) => `
    <div class="sgame ${g.state === "in" ? "inprog" : ""}" data-away="${esc(g.away)}" data-home="${esc(g.home)}">
      <div class="sgstatus" title="${esc(statusText(g))}">${g.state === "in" ? '<span class="gdot in_game"></span>' : ""}${esc(statusText(g))}</div>
      ${line(g.away, g.away_score, g, g.state === "post" && g.away_score < g.home_score)}
      ${line(g.home, g.home_score, g, g.state === "post" && g.home_score < g.away_score)}
    </div>`).join("")}</aside>`;
}

function renderBox() {
  const b = sbBox;
  if (!b || !sbOpen) return;
  const { away, home } = sbOpen;
  const g = b.game || {};

  const table = (side) => {
    let lastPos = null;
    const rows = side.players.map((p) => {
      const header = p.pos !== lastPos ? (lastPos = p.pos, `<tr class="posgroup"><td colspan="4">${p.pos}</td></tr>`) : "";
      return `${header}<tr class="${p.mine ? "mine" : ""}">
        <td class="av"><img class="avatar${p.pos === "DST" ? " logo" : ""}" loading="lazy" alt=""
          src="${esc(p.img)}" data-fallback="${esc(p.team_img)}"></td>
        <td>${esc(p.name)}${injTag(p)}${ownerTag(p)}${p.line ? `<div class="line">${esc(p.line)}</div>` : ""}</td>
        ${b.salaries ? `<td class="num sal">${p.sal ? money(p.sal) : ""}</td>` : ""}
        <td class="num pts">${p.pts.toFixed(2)}</td>
      </tr>`;
    }).join("");
    const logo = side.team === g.away ? g.away_logo : g.home_logo;
    const score = side.team === g.away ? g.away_score : g.home_score;
    return `<div class="card">
      <div class="head"><span class="boxtitle"><img src="${esc(logo || "")}" alt="">
        <span class="title" style="font-size:14px">${esc(side.team)}</span></span>
        ${g.state && g.state !== "pre" ? `<span class="stat pts"><b>${score}</b></span>` : ""}</div>
      ${side.players.length ? `<div class="cardbody"><table><tbody>${rows}</tbody></table></div>`
        : `<div class="empty" style="padding:20px">No stats yet.</div>`}
    </div>`;
  };

  $("sbGames").classList.add("hidden");
  $("sbDetail").classList.remove("hidden");
  const sit = (b.game && b.game.situation) || null;
  const plays = b.plays || [];
  $("sbDetail").innerHTML = `<div class="boxwrap"><div class="boxmain">
    <div class="boxhead">
      <button id="sbBack" class="small">← All games</button>
      <span class="score">${esc(away)} ${g.away_score ?? ""} @ ${esc(home)} ${g.home_score ?? ""}</span>
      <span class="status muted">${g.state === "in" ? '<span class="gdot in_game"></span>' : ""}${esc(statusText(g))} · ${esc(b.scoring_label)} scoring</span>
    </div>
    ${sit ? `<div class="card" style="margin-bottom:14px"><div class="head">
        <span class="title">${sit.possession ? BALL : ""}${esc(sit.possession || "")} ball</span>
        ${sit.down_distance ? `<span class="stat"><b>${esc(sit.down_distance)}</b></span>` : ""}
        ${sit.ball_on ? `<span class="stat">ball on <b>${esc(sit.ball_on)}</b></span>` : ""}
        ${sit.red_zone ? `<span class="stat" style="color:var(--red)"><b>RED ZONE</b></span>` : ""}
      </div></div>` : ""}
    ${playsCard(plays, g.state)}
    <div class="two">${b.teams.map(table).join("")}</div></div>${sideGames()}</div>`;
  $("sbBack").addEventListener("click", closeBox);
  $("sbDetail").querySelector("[data-plays]")?.addEventListener("click", () => {
    sbPlaysAll = !sbPlaysAll; renderBox();
  });
  $("sbDetail").querySelectorAll(".sgame").forEach((el) => el.addEventListener("click", () => {
    openBox(el.dataset.away, el.dataset.home); window.scrollTo({ top: 0 });
  }));
  wireAvatarFallback($("sbDetail"));
}

function closeBox() {
  sbOpen = null; sbBox = null; sbTicket++;
  $("sbDetail").classList.add("hidden");
  $("sbDetail").innerHTML = "";
  $("sbGames").classList.remove("hidden");
}

function setSbTimer() {
  if (sbTimer) { clearTimeout(sbTimer); sbTimer = null; }
  if (!$("sbAuto").checked) { $("sbAutoNote").textContent = ""; return; }
  const { ms, note } = refreshPlan(sbData);
  $("sbAutoNote").textContent = note;
  if (!ms) return;
  sbTimer = setTimeout(() => {
    if ($("scoreboard").classList.contains("active")) loadScoreboard();
    else setSbTimer();
  }, ms);
}

$("sbScoring").addEventListener("change", () => setScoringEverywhere($("sbScoring").value));
$("sbWeek").addEventListener("change", () => { closeBox(); loadScoreboard(false); });
$("sbRefresh").addEventListener("click", () => loadScoreboard());
$("sbAuto").addEventListener("change", setSbTimer);
$("sbBetting").checked = showBetting();
$("sbBetting").addEventListener("change", () => {
  prefSet("sb.betting", $("sbBetting").checked);
  renderGames();
  if (sbOpen) renderBox();
});
document.querySelector('.tab[data-tab="scoreboard"]').addEventListener("click", () => {
  loadScoreboard(); setSbTimer();
});

// ================================================================ settings ====
// Leagues and the chosen team live in this browser only, so two people using the
// site at once each see their own. The salary file is server-side and shared.
const myLeagues = () => prefGet("settings.leagues", []);          // [{league_id,name,teams}]
const leagueParam = () => myLeagues().map((l) => l.league_id).join(",");
const myTeamFor = (leagueId) => (prefGet("settings.myteams", {}) || {})[leagueId] || "";

function setMyTeamFor(leagueId, ownerId) {
  const all = prefGet("settings.myteams", {}) || {};
  all[leagueId] = ownerId;
  prefSet("settings.myteams", all);
}

async function loadSleeperLeagues(who) {
  setStatus($("setSleeperStatus"), "Looking up…");
  try {
    const r = await api(`/api/sleeper/user?who=${encodeURIComponent(who)}`);
    prefSet("settings.sleeper_user", { id: r.user_id, name: r.username || r.display_name });
    prefSet("settings.available", r.leagues);
    setStatus($("setSleeperStatus"),
      `${r.username || r.display_name} · ${r.leagues.length} league${r.leagues.length === 1 ? "" : "s"}`, "ok");
    renderLeagues();
  } catch (e) {
    setStatus($("setSleeperStatus"), e.message, "err");
  }
}

async function renderLeagues() {
  const available = prefGet("settings.available", []);
  const chosen = myLeagues().map((l) => l.league_id);
  if (!available.length) { $("setLeagues").innerHTML = ""; return; }

  $("setLeagues").innerHTML = available.map((l) => `
    <div class="leaguerow" data-id="${esc(l.league_id)}">
      <input type="checkbox" ${chosen.includes(l.league_id) ? "checked" : ""}>
      <span class="lname">${esc(l.name)}</span>
      <span class="lmeta">${l.teams || "?"} teams</span>
      <select class="teamsel" ${chosen.includes(l.league_id) ? "" : "disabled"}>
        <option value="">— my team —</option>
      </select>
    </div>`).join("");

  $("setLeagues").querySelectorAll(".leaguerow").forEach(async (row) => {
    const id = row.dataset.id;
    const box = row.querySelector("input");
    const sel = row.querySelector(".teamsel");

    box.addEventListener("change", () => {
      const picked = [...$("setLeagues").querySelectorAll(".leaguerow")]
        .filter((r) => r.querySelector("input").checked)
        .map((r) => available.find((a) => a.league_id === r.dataset.id))
        .filter(Boolean);
      prefSet("settings.leagues", picked);
      renderLeagues();
      liveData = null; sbData = null;
      if ($("live").classList.contains("active")) loadLive();
    });

    if (!box.checked) return;
    try {
      const t = await api(`/api/sleeper/teams?league_id=${encodeURIComponent(id)}`);
      sel.innerHTML = `<option value="">— my team —</option>` + t.teams.map((x) =>
        `<option value="${esc(x.owner_id)}">${esc(x.team)}${x.eliminated ? " (out)" : ""}</option>`).join("");
      sel.value = myTeamFor(id) || t.default || "";
      if (sel.value && !myTeamFor(id)) setMyTeamFor(id, sel.value);
      sel.addEventListener("change", () => {
        setMyTeamFor(id, sel.value);
        if (liveData) loadLive();
      });
    } catch { /* league unreachable; leave the picker empty */ }
  });
}

function renderScoringSettings() {
  if (!allScoringOpts.length) {
    $("setScoring").innerHTML = `<div class="status muted">Loading…</div>`;
    return;
  }
  const off = hiddenScoring();
  $("setScoring").innerHTML = allScoringOpts.map((o) => `
    <label class="check"><input type="checkbox" value="${esc(o.key)}" ${off.includes(o.key) ? "" : "checked"}> ${esc(o.label)}</label>`).join("");
  $("setScoring").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => {
    const boxes = [...$("setScoring").querySelectorAll("input")];
    if (!boxes.some((x) => x.checked)) { cb.checked = true; return; }   // keep at least one
    const hidden = boxes.filter((x) => !x.checked).map((x) => x.value);
    const keep = hiddenScoring().filter((k) => !allScoringOpts.some((o) => o.key === k));
    prefSet("settings.hidden_scoring", [...keep, ...hidden]);
    const pick = currentScoring();
    prefSet("live.scoring", pick);
    fillScoring($("liveScoring"), allScoringOpts);
    if ($("sbScoring").options.length) fillScoring($("sbScoring"), allScoringOpts);
    liveData = null;
  }));
}

async function loadSettings() {
  renderScoringSettings();
  if (!allScoringOpts.length) loadLive().then(renderScoringSettings);
  const saved = prefGet("settings.sleeper_user", null);
  if (saved) {
    $("setSleeperUser").value = saved.name || "";
    setStatus($("setSleeperStatus"), `${saved.name} · saved in this browser`, "ok");
  }
  renderLeagues();

  try {
    const s = await api("/api/settings");
    renderSalaryFiles(s.salary_files || []);
  } catch (e) { setStatus($("setSalStatus"), e.message, "err"); }
}

// one row per stored DK file, newest week first
function renderSalaryFiles(files) {
  if (!files.length) {
    setStatus($("setSalStatus"), "No salary files saved");
    $("setSalGames").innerHTML = "";
    return;
  }
  const weeks = new Set(files.map((f) => `${f.season}-${f.week}`)).size;
  setStatus($("setSalStatus"), `${files.length} file${files.length === 1 ? "" : "s"} across ` +
    `${weeks} week${weeks === 1 ? "" : "s"}`, "ok");
  const what = (f) => f.showdown
    ? `Showdown · ${f.games.map((g) => `${esc(g.away)} @ ${esc(g.home)}`).join(", ")}`
    : `Classic · ${f.games.length} game${f.games.length === 1 ? "" : "s"}`;
  $("setSalGames").innerHTML = `<div class="salfiles">${files.map((f) => `
    <div class="salfile">
      <span class="salwk">Week ${f.week}</span>
      <span class="salwhat" title="${esc(f.games.map((g) => `${g.away} @ ${g.home}`).join(", "))}">${what(f)}</span>
      <span class="lmeta">${f.players} players${f.uploaded_at ? ` · ${esc(f.uploaded_at)}` : ""}</span>
      <button class="small ghost" data-salid="${esc(f.id)}">Remove</button>
    </div>`).join("")}</div>`;
  $("setSalGames").querySelectorAll("[data-salid]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Remove this salary file for everyone?")) return;
    try {
      await api(`/api/settings/salaries?id=${encodeURIComponent(b.dataset.salid)}`, { method: "DELETE" });
    } catch (err) { setStatus($("setSalStatus"), err.message, "err"); return; }
    await loadSettings();
    liveData = null; if ($("live").classList.contains("active")) loadLive();
  }));
}

$("setSleeperLoad").addEventListener("click", () => {
  const who = $("setSleeperUser").value.trim();
  if (who) loadSleeperLeagues(who);
});
$("setSleeperUser").addEventListener("keydown", (e) => { if (e.key === "Enter") $("setSleeperLoad").click(); });
$("setSleeperClear").addEventListener("click", () => {
  ["settings.sleeper_user", "settings.available", "settings.leagues", "settings.myteams"]
    .forEach((k) => { try { localStorage.removeItem(k); } catch {} });
  $("setSleeperUser").value = "";
  setStatus($("setSleeperStatus"), "Cleared");
  $("setLeagues").innerHTML = "";
  liveData = null; loadLive();
});

$("setSalFile").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  setStatus($("setSalStatus"), "Uploading…");
  try {
    const r = await api("/api/settings/salaries", { method: "POST", body: fd });
    await loadSettings();
    setStatus($("setSalStatus"), `Saved to Week ${r.week} · ${r.players} players` +
      (r.showdown ? " · showdown" : ""), "ok");
    liveData = null; if ($("live").classList.contains("active")) loadLive();
  } catch (err) { setStatus($("setSalStatus"), err.message, "err"); }
  e.target.value = "";
});

document.querySelector('.tab[data-tab="settings"]')?.addEventListener("click", loadSettings);
