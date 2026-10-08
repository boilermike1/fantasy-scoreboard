const $ = (id) => document.getElementById(id);
const PUBLIC_MODE = (window.DFS_MODE || "full") === "scoreboard";
const state = {
  players: [], include: [], exclude: [], lineups: [],
  sdPlayers: [], sdInclude: [], sdExclude: [], sdCaptainPool: [], sdCaptainExclude: [], sdLineups: [],
};

// ---- tabs ----
document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    b.classList.add("active");
    $(b.dataset.tab).classList.add("active");
  })
);

// ---- helpers ----
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401) { location.reload(); return; }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || r.statusText);
  return data;
}
const money = (n) => "$" + n.toLocaleString();
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function setStatus(el, msg, cls = "muted") { el.textContent = msg; el.className = "status " + cls; }
// fantasy-team tag, shown only when a league scoring mode is active
const ownerTag = (p) => p.owner
  ? `<span class="owner${p.mine ? " mine" : ""}" title="${esc(p.owner)}">${esc(p.owner)}</span>` : "";

// in-game injury designation
const INJ_SHORT = { out: "OUT", doubtful: "D", questionable: "Q", probable: "P" };
function injTag(p) {
  if (!p.injury) return "";
  const k = String(p.injury).toLowerCase();
  const short = INJ_SHORT[k] || String(p.injury).slice(0, 3).toUpperCase();
  const title = p.injury + (p.injury_detail ? ` — ${p.injury_detail}` : "");
  return `<span class="inj ${k === "out" ? "out" : "qd"}" title="${esc(title)}">${esc(short)}</span>`;
}

/**
 * Wire a search box to a set of chip lists.
 * actions: [{label, key, exclusiveWith}] — key names a property on `state`.
 */
function wireSearch({ searchId, resultsId, poolKey, actions, onChange }) {
  $(searchId).addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    const box = $(resultsId); box.innerHTML = "";
    if (q.length < 2) return;
    state[poolKey].filter((p) => p.name.toLowerCase().includes(q)).slice(0, 8).forEach((p) => {
      const d = document.createElement("div"); d.className = "item";
      d.innerHTML = `<span><b>${esc(p.name)}</b> <span class="muted">${p.pos} ${p.team} ${money(p.sal)} · ${p.proj}</span></span>
        <span class="acts">${actions.map((a, i) => `<button data-i="${i}">${a.label}</button>`).join("")}</span>`;
      d.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        const a = actions[Number(b.dataset.i)];
        if (!state[a.key].includes(p.name)) state[a.key].push(p.name);
        (a.exclusiveWith || []).forEach((k) => {
          const i = state[k].indexOf(p.name); if (i >= 0) state[k].splice(i, 1);
        });
        onChange(); $(searchId).value = ""; box.innerHTML = "";
      }));
      box.appendChild(d);
    });
  });
}

function renderChipList(key, cls, elId, onChange) {
  const el = $(elId); el.innerHTML = "";
  state[key].forEach((n, i) => {
    const c = document.createElement("span"); c.className = "chip " + cls;
    c.innerHTML = `${esc(n)}<button title="remove">×</button>`;
    c.querySelector("button").addEventListener("click", () => { state[key].splice(i, 1); onChange(); });
    el.appendChild(c);
  });
}

// ================================================================ main slate ====
async function loadState() {
  if (PUBLIC_MODE) return;
  const s = await api("/api/state");
  state.players = s.players || [];
  state.include = (s.picks && s.picks.include) || [];
  state.exclude = (s.picks && s.picks.exclude) || [];
  renderChips();

  if (s.projections) setStatus($("projStatus"), `${s.projections.filename} · ${s.projections.players} players · ${s.projections.uploaded_at}`, "ok");
  else setStatus($("projStatus"), "No projections loaded");
  if (s.top_plays) setStatus($("tpStatus"), `${s.top_plays.count} top plays loaded · ${s.top_plays.uploaded_at}`, "ok");
  else setStatus($("tpStatus"), "No top plays loaded");
  $("run").disabled = !s.projections;

  // showdown
  state.sdPlayers = s.sd_players || [];
  const sp = s.sd_picks || {};
  state.sdInclude = sp.include || []; state.sdExclude = sp.exclude || [];
  state.sdCaptainPool = sp.captain_pool || []; state.sdCaptainExclude = sp.captain_exclude || [];
  renderSdChips();
  if (s.showdown_error) setStatus($("sdStatus"), s.showdown_error, "err");
  else if (s.showdown && s.showdown.teams) {
    const skip = s.showdown.skipped ? ` · ${s.showdown.skipped} skipped (no projection)` : "";
    setStatus($("sdStatus"), `${s.showdown.teams.join(" vs ")} · ${s.showdown.players} players${skip} · ${s.showdown.uploaded_at}`, "ok");
  }
  else setStatus($("sdStatus"), "No showdown slate loaded");
  $("sdRun").disabled = !state.sdPlayers.length;
}

$("projFile").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  setStatus($("projStatus"), "Uploading…");
  try { await api("/api/upload/projections", { method: "POST", body: fd }); await loadState(); }
  catch (err) { setStatus($("projStatus"), err.message, "err"); }
  e.target.value = "";
});

async function saveTopPlays(fd) {
  setStatus($("tpStatus"), "Saving…");
  try {
    const r = await api("/api/upload/top_plays", { method: "POST", body: fd });
    await loadState();
    const rep = r.report;
    if (r.picks) setStatus($("tpStatus"), $("tpStatus").textContent + ` · ${r.picks.include.length} included, ${r.picks.exclude.length} excluded`, "ok");
    if (rep && (rep.unmatched.length || rep.ambiguous.length)) {
      const bits = [];
      if (rep.unmatched.length) bits.push("not found: " + rep.unmatched.join(", "));
      if (rep.ambiguous.length) bits.push("ambiguous: " + rep.ambiguous.join("; "));
      setStatus($("tpStatus"), `${rep.matched.length} matched — ${bits.join(" · ")}`, "err");
    }
  } catch (err) { setStatus($("tpStatus"), err.message, "err"); }
}
$("tpFile").addEventListener("change", (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f); saveTopPlays(fd); e.target.value = "";
});
$("tpSave").addEventListener("click", () => {
  const t = $("tpText").value.trim(); if (!t) return;
  const fd = new FormData(); fd.append("text", t); saveTopPlays(fd); $("tpText").value = "";
});
$("tpClear").addEventListener("click", async () => { await api("/api/top_plays", { method: "DELETE" }); await loadState(); });

function savePicks() {
  api("/api/picks", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ include: state.include, exclude: state.exclude }) }).catch(() => {});
}
function renderChips() {
  renderChipList("include", "inc", "includeChips", onPicksChange);
  renderChipList("exclude", "exc", "excludeChips", onPicksChange);
}
function onPicksChange() { renderChips(); savePicks(); }
wireSearch({
  searchId: "playerSearch", resultsId: "searchResults", poolKey: "players", onChange: onPicksChange,
  actions: [{ label: "+ Inc", key: "include", exclusiveWith: ["exclude"] },
            { label: "− Exc", key: "exclude", exclusiveWith: ["include"] }],
});

$("run").addEventListener("click", async () => {
  $("run").disabled = true; setStatus($("runStatus"), "Solving…"); $("error").innerHTML = "";
  try {
    const body = { n_lineups: 10, include: state.include, exclude: state.exclude };
    const r = await api("/api/optimize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    state.lineups = r.lineups;
    $("empty").classList.add("hidden"); $("toolbar").classList.remove("hidden");
    if (r.warnings?.length) $("error").innerHTML = r.warnings.map((x) => `<div class="warn">${esc(x)}</div>`).join("");
    $("lineups").innerHTML = r.lineups.map((L, i) => `
      <div class="card">
        <div class="head">
          <span class="title">Lineup ${i + 1}</span>
          <span class="stat pts"><b>${L.proj.toFixed(1)}</b> pts</span>
          <span class="stat">salary <b>${money(L.salary)}</b></span>
          ${L.top_plays ? `<span class="stat">top plays <b>${L.top_plays}</b></span>` : ""}
        </div>
        <table><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Opp</th><th class="num">Salary</th><th class="num">Proj</th></tr></thead>
        <tbody>${L.players.map((p) => `<tr><td class="slot">${p.slot}</td><td>${esc(p.name)}${p.top_play ? '<span class="tag">TOP PLAY</span>' : ""}</td>
          <td>${p.team}</td><td>${p.opp}</td><td class="num">${money(p.sal)}</td><td class="num">${p.proj.toFixed(1)}</td></tr>`).join("")}</tbody></table>
      </div>`).join("");
    setStatus($("runStatus"), "");
  } catch (err) {
    $("error").innerHTML = `<div class="warn err">${esc(err.message)}</div>`;
    setStatus($("runStatus"), "Failed", "err");
  }
  $("run").disabled = false;
});

async function download(url, lineups, filename) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lineups }) });
  const blob = await r.blob(); const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename; a.click(); URL.revokeObjectURL(a.href);
}
$("download").addEventListener("click", () => download("/api/export/dk_upload", state.lineups, "dk_upload.csv"));

// ================================================================ showdown ====
$("sdFile").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  setStatus($("sdStatus"), "Uploading…");
  try { await api("/api/showdown/upload", { method: "POST", body: fd }); await loadState(); }
  catch (err) { setStatus($("sdStatus"), err.message, "err"); }
  e.target.value = "";
});

function saveSdPicks() {
  api("/api/showdown/picks", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ include: state.sdInclude, exclude: state.sdExclude,
                           captain_pool: state.sdCaptainPool, captain_exclude: state.sdCaptainExclude }) }).catch(() => {});
}
function renderSdChips() {
  renderChipList("sdCaptainPool", "inc", "sdCaptainPoolChips", onSdPicksChange);
  renderChipList("sdCaptainExclude", "exc", "sdCaptainExcludeChips", onSdPicksChange);
  renderChipList("sdInclude", "inc", "sdIncludeChips", onSdPicksChange);
  renderChipList("sdExclude", "exc", "sdExcludeChips", onSdPicksChange);
}
function onSdPicksChange() { renderSdChips(); saveSdPicks(); }
wireSearch({
  searchId: "sdCaptSearch", resultsId: "sdCaptResults", poolKey: "sdPlayers", onChange: onSdPicksChange,
  actions: [{ label: "Must", key: "sdCaptainPool", exclusiveWith: ["sdCaptainExclude"] },
            { label: "Never", key: "sdCaptainExclude", exclusiveWith: ["sdCaptainPool"] }],
});
wireSearch({
  searchId: "sdSearch", resultsId: "sdResults", poolKey: "sdPlayers", onChange: onSdPicksChange,
  actions: [{ label: "+ Inc", key: "sdInclude", exclusiveWith: ["sdExclude"] },
            { label: "− Exc", key: "sdExclude", exclusiveWith: ["sdInclude"] }],
});

$("sdRun").addEventListener("click", async () => {
  $("sdRun").disabled = true; setStatus($("sdRunStatus"), "Solving…"); $("sdError").innerHTML = "";
  try {
    const body = { n_lineups: 10, include: state.sdInclude, exclude: state.sdExclude,
                   captain_pool: state.sdCaptainPool, captain_exclude: state.sdCaptainExclude };
    const r = await api("/api/showdown/optimize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    state.sdLineups = r.lineups;
    $("sdEmpty").classList.add("hidden"); $("sdToolbar").classList.remove("hidden");
    $("sdSummary").textContent = `${r.lineups.length} lineups`;
    if (r.warnings?.length) $("sdError").innerHTML = r.warnings.map((x) => `<div class="warn">${esc(x)}</div>`).join("");
    $("sdLineups").innerHTML = r.lineups.map((L, i) => `
      <div class="card">
        <div class="head">
          <span class="title">Lineup ${i + 1}</span>
          <span class="stat pts"><b>${L.proj.toFixed(1)}</b> pts</span>
          <span class="stat">salary <b>${money(L.salary)}</b></span>
          <span class="stat">${esc(L.split)}</span>
        </div>
        <table><thead><tr><th>Slot</th><th>Player</th><th>Pos</th><th>Team</th><th class="num">Salary</th><th class="num">Proj</th></tr></thead>
        <tbody>${L.players.map((p) => `<tr class="${p.slot === "CPT" ? "cpt" : ""}"><td class="slot">${p.slot}</td><td>${esc(p.name)}</td>
          <td>${p.pos}</td><td>${p.team}</td><td class="num">${money(p.sal)}</td><td class="num">${p.proj.toFixed(1)}</td></tr>`).join("")}</tbody></table>
      </div>`).join("");
    setStatus($("sdRunStatus"), "");
  } catch (err) {
    $("sdError").innerHTML = `<div class="warn err">${esc(err.message)}</div>`;
    setStatus($("sdRunStatus"), "Failed", "err");
  }
  $("sdRun").disabled = false;
});
$("sdDownload").addEventListener("click", () => download("/api/showdown/export", state.sdLineups, "dk_showdown_upload.csv"));

if (PUBLIC_MODE) {
  // public scoreboard: Live + Scoreboard only, nothing that needs a login
  ["optimizer", "showdown"].forEach((id) => {
    document.querySelector(`.tab[data-tab="${id}"]`)?.remove();
    $(id)?.remove();
  });
  document.querySelector(".logout")?.remove();   // nothing to log out of
} else {
  loadState().catch((e) => setStatus($("projStatus"), e.message, "err"));
}

// ================================================================ live ====
let liveTimer = null, liveLoading = false, livePending = false, liveData = null;

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
  const optCard = prefGet("live.optimal", false) ? optimalCard() : "";
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
    const scoring = $("liveScoring").value || prefGet("live.scoring", "dk");
    await syncTeamPickers(scoring);
    const qs = new URLSearchParams({ scoring });
    if (scoring === "sleeper" && myTeam()) qs.set("my_team", myTeam());
    if (wk) qs.set("week", wk);
    if ($("liveSlate").value) qs.set("slate", $("liveSlate").value);
    if (prefGet("live.optimal", false)) qs.set("optimal", "true");
    const r = await api(`/api/live?${qs}`);
    liveData = r;
    $("liveError").innerHTML = "";

    if (!$("liveScoring").options.length) {
      r.scoring_options.forEach((o) => {
        const el = document.createElement("option");
        el.value = o.key; el.textContent = o.label;
        $("liveScoring").appendChild(el);
      });
      $("liveScoring").value = r.scoring;
    }
    if (!$("liveWeek").options.length) {
      for (let w = r.current_week; w >= 1; w--) {
        const o = document.createElement("option");
        o.value = w; o.textContent = `Week ${w}${w === r.current_week ? " (current)" : ""}`;
        $("liveWeek").appendChild(o);
      }
      const season = document.createElement("option");
      season.value = "season"; season.textContent = "Season to date";
      $("liveWeek").appendChild(season);
      $("liveWeek").value = r.season_mode ? "season" : r.week;
    }
    const posSig = r.positions.join("|") + (r.salaries ? "+s" : "");
    if ($("posBar").dataset.sig !== posSig) {
      $("posBar").dataset.sig = posSig;
      renderPosBar(r.positions, !!r.salaries);
    }

    const slates = r.slates || [];
    $("liveSlateWrap").classList.toggle("hidden", !slates.length);
    const wantSlate = $("liveSlate").value || "all";
    const signature = slates.map((x) => x.key).join("|");
    if ($("liveSlate").dataset.sig !== signature) {
      $("liveSlate").dataset.sig = signature;
      $("liveSlate").innerHTML = slates.map((x) =>
        `<option value="${esc(x.key)}">${esc(x.label)}</option>`).join("");
      $("liveSlate").value = slates.some((x) => x.key === wantSlate) ? wantSlate : "all";
    }

    const isCurrent = r.season_mode || Number(r.week) === Number(r.current_week);
    const when = r.season_mode ? `${r.season} season to date` : `${r.season} Week ${r.week}`;
    const games = r.live_games ? ` · <b>${r.live_games}</b> game${r.live_games > 1 ? "s" : ""} in progress` : "";
    const slateLabel = (r.slates || []).find((x) => x.key === r.slate);
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
  const on = scoring === "sleeper";
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
  if (liveData) loadLive();
  if (sbOpen) openBox(sbOpen.away, sbOpen.home);
}
$("liveScoring").addEventListener("change", () => setScoringEverywhere($("liveScoring").value));
$("liveWeek").addEventListener("change", () => { $("liveSlate").value = "all"; loadLive(); });
$("liveSlate").addEventListener("change", loadLive);
$("liveRefresh").addEventListener("click", loadLive);
$("liveAuto").addEventListener("change", setLiveTimer);
document.querySelector('.tab[data-tab="live"]').addEventListener("click", () => {
  loadLive(); setLiveTimer();
});

// Live is the landing tab
$("liveScoring").value = prefGet("live.scoring", "dk");
loadLive();
setLiveTimer();

// ================================================================ scoreboard ====
let sbTimer = null, sbLoading = false, sbPending = false, sbData = null, sbOpen = null;

async function loadScoreboard(keepDetail = true) {
  if (sbLoading) { sbPending = true; return; }
  sbLoading = true;
  try {
    const wk = $("sbWeek").value;
    const r = await api(`/api/scoreboard${wk ? `?week=${wk}` : ""}`);
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
    if (!$("sbScoring").options.length && r.scoring_options) {
      r.scoring_options.forEach((o) => {
        const el = document.createElement("option");
        el.value = o.key; el.textContent = o.label;
        $("sbScoring").appendChild(el);
      });
      $("sbScoring").value = prefGet("live.scoring", "dk");
    }
    const games = r.live_games ? ` · <b>${r.live_games}</b> in progress` : "";
    $("sbMeta").innerHTML = `<span class="live-dot ${Number(r.week) === Number(r.current_week) ? "" : "stale"}"></span>` +
      `${r.season} Week ${r.week} · ${r.games.length} games${games} · updated ${esc(r.updated_at)}`;

    await syncTeamPickers(prefGet("live.scoring", "dk"));
    renderGames();
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
        g.situation && g.situation.possession === (side === "away" ? g.away : g.home)
          ? '<span class="poss" title="Has the ball"></span>' : ""}</span>
      <span class="sc">${g.state === "pre" ? "" : them}</span>
    </div>`;
  };
  $("sbGames").innerHTML = sbData.games.map((g) => `
    <div class="game ${g.state === "in" ? "inprog" : ""}" data-away="${esc(g.away)}" data-home="${esc(g.home)}">
      <div class="gstatus">${g.state === "in" ? '<span class="gdot in_game"></span>' : ""}${esc(g.detail)}</div>
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
  sbOpen = { away, home };
  const scoring = prefGet("live.scoring", "dk");
  const wk = $("sbWeek").value;
  try {
    await syncTeamPickers(scoring);
    const qs = new URLSearchParams({ away, home, scoring });
    if (scoring === "sleeper" && myTeam()) qs.set("my_team", myTeam());
    if (wk) qs.set("week", wk);
    const b = await api(`/api/boxscore?${qs}`);
    const g = (sbData?.games || []).find((x) => x.away === away && x.home === home) || {};

    const table = (side) => {
      let lastPos = null;
      const rows = side.players.map((p) => {
        const header = p.pos !== lastPos ? (lastPos = p.pos, `<tr class="posgroup"><td colspan="4">${p.pos}</td></tr>`) : "";
        return `${header}<tr class="${p.mine ? "mine" : ""}">
          <td class="av"><img class="avatar${p.pos === "DST" ? " logo" : ""}" loading="lazy" alt=""
            src="${esc(p.img)}" data-fallback="${esc(p.team_img)}"></td>
          <td>${esc(p.name)}${injTag(p)}${ownerTag(p)}${p.line ? `<div class="line">${esc(p.line)}</div>` : ""}</td>
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
    $("sbDetail").innerHTML = `
      <div class="boxhead">
        <button id="sbBack" class="small">← All games</button>
        <span class="score">${esc(away)} ${g.away_score ?? ""} @ ${esc(home)} ${g.home_score ?? ""}</span>
        <span class="status muted">${g.state === "in" ? '<span class="gdot in_game"></span>' : ""}${esc(g.detail || "")} · ${esc(b.scoring_label)} scoring</span>
      </div>
      ${sit ? `<div class="card" style="margin-bottom:14px"><div class="head">
          <span class="title">${esc(sit.possession || "")} ball</span>
          ${sit.down_distance ? `<span class="stat"><b>${esc(sit.down_distance)}</b></span>` : ""}
          ${sit.ball_on ? `<span class="stat">ball on <b>${esc(sit.ball_on)}</b></span>` : ""}
          ${sit.red_zone ? `<span class="stat" style="color:var(--red)"><b>RED ZONE</b></span>` : ""}
        </div></div>` : ""}
      ${plays.length ? `<div class="card plays" style="margin-bottom:14px">
          <div class="head"><span class="title">Last ${plays.length} plays</span></div>
          ${plays.map((p) => `<div class="play ${p.scoring ? "score" : ""}">
            <span class="pclock">${p.period ? `Q${p.period} ` : ""}${esc(p.clock || "")}</span>
            <span class="ptm">${esc(p.team || "")}</span>
            <span>${p.down ? `<span class="muted">${esc(p.down)}</span> ` : ""}${esc(p.text || "")}</span>
          </div>`).join("")}
        </div>` : ""}
      <div class="two">${b.teams.map(table).join("")}</div>`;
    $("sbBack").addEventListener("click", closeBox);
    wireAvatarFallback($("sbDetail"));
  } catch (err) {
    $("sbError").innerHTML = `<div class="warn err">${esc(err.message)}</div>`;
  }
}

function closeBox() {
  sbOpen = null;
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
document.querySelector('.tab[data-tab="scoreboard"]').addEventListener("click", () => {
  loadScoreboard(); setSbTimer();
});
