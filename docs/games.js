// Games tab: tonight's NBA scoreboard → one page per game (score, linescore, live value, box score, lines, props),
// plus the multi-sport board. Live data comes from the same free ESPN feeds as the Sim tab (sim.js pollLive).
(() => {
const {stop: simStop, pollLive, liveSimFromHere, liveValueHtml, liveBoxHtml, livePlaysHtml, showVs, prep, calibrate, am_, spS, pcS, lastName} = window.SL;
const GV = window.GAMESV = {view: null, sub: "game", timer: null, sb: null};
const gOf = id => (D.players.games || []).find(x => x.espn_id === id);
const sbOf = id => (GV.sb || []).find(x => x.id === id);
const nowOf = id => SIMV.gameId === id && SIMV.now && SIMV.now.gid === id ? SIMV.now : null;
const dkOf = id => ((D.dkday && D.dkday.games) || {})[id] || {};
function gState(g) {
  const n = nowOf(g.espn_id), s = sbOf(g.espn_id);
  if (n) return n.state; if (s) return s.state;
  return g.state === "pre" && new Date(g.start) < Date.now() ? "in" : g.state;
}

async function renderGames() {
  if (!D.sims) D.sims = await get("sims.json", {games: {}});
  if (!D.dkday || D.dkday.date !== D.players.date) D.dkday = await get(`dk/${D.players.date}.json`, {date: D.players.date, games: {}});
  clearInterval(GV.timer);
  GV.timer = setInterval(() => { if (store.get("tab") !== "games" || document.hidden) return; GV.view ? pollGame() : refreshScores(); }, 30000);
  if (GV.view && gOf(GV.view)) renderGamePage(); else { GV.view = null; renderScoreboard(); }
}

// ------------------------------------------------------------------ scoreboard
function renderScoreboard() {
  const games = D.players.games || [];
  const day = D.players.date ? new Date(D.players.date + "T12:00:00").toLocaleDateString([], {weekday: "long", month: "short", day: "numeric"}) : "";
  $("v-games").innerHTML = `<h2>NBA${day ? " · " + esc(day) : ""}</h2>
    <div class="games" id="gList">${games.map(gCardHtml).join("") || '<div class="empty">No NBA games on the slate.</div>'}</div>
    <div class="desc" style="margin:6px 2px 4px">Scores refresh every 30 seconds while this tab is open. Tap a game for its box score, lines, live value, props and sim.</div>
    ${sec("b-board", "Board · bet it yourself", "", "", false)}`;
  bindCards();
  board(D.bd);
  refreshScores();
}
function bindCards() { document.querySelectorAll("#gList .gcard").forEach(el => el.onclick = () => openGame(el.dataset.g)); }
async function refreshScores() {
  if (!D.players.date) return;
  const sb = await scoreboard("basketball_nba", D.players.date.replaceAll("-", ""));
  if (sb) GV.sb = sb;
  const L = $("gList"); if (!L || GV.view) return;
  const html = (D.players.games || []).map(gCardHtml).join("");
  if (html && L.innerHTML !== html) { L.innerHTML = html; bindCards(); }
}
function gCardHtml(g) {
  const s = sbOf(g.espn_id), n = nowOf(g.espn_id), st = gState(g);
  const sc = n ? {away: n.score[0], home: n.score[1]} : s && s.state !== "pre" ? {away: s.away.score, home: s.home.score} : null;
  const dk = dkOf(g.espn_id), ln = dk.close || dk.open, sim = (D.sims.games || {})[g.espn_id];
  const lead = sc && st === "post" ? (sc.home > sc.away ? "home" : "away") : null;
  const row = side => `<div class="gs-r${lead === side ? " w" : ""}${lead && lead !== side ? " l" : ""}"><span class="ab">${esc(g[side])}</span><span class="nm">${esc((g[side + "_name"] || "").split(" ").slice(-1)[0])}</span><span class="sc">${sc ? sc[side] : ""}</span></div>`;
  const status = st === "in" ? `<span class="pill live">${esc(n ? n.detail : s ? s.detail : "Live")}</span>` : st === "post" ? '<span class="pill fin">Final</span>' : `<span class="t">${tod(g.start)}</span>`;
  const lineTxt = ln ? `DK ${esc(g.home)} ${spS(ln.sp)} · O/U ${f1(ln.tot)}` : g.vegas && g.vegas.total ? `${esc(g.home)} ${spS(g.vegas.spread_home)} · O/U ${f1(g.vegas.total)}` : "";
  const simTxt = sim ? `Sim ${Math.round(sim.avg.away)}–${Math.round(sim.avg.home)} · ${esc(sim.win_home >= 0.5 ? g.home : g.away)} ${Math.round(Math.max(sim.win_home, 1 - sim.win_home) * 100)}%` : "";
  return `<div class="gcard gs" data-g="${g.espn_id}"><div class="gs-top">${status}</div>${row("away")}${row("home")}
    ${lineTxt || simTxt ? `<div class="mdl">${[lineTxt, simTxt].filter(Boolean).join(" · ")}</div>` : ""}</div>`;
}

// ------------------------------------------------------------------ game page
function openGame(id, sub) {
  GV.view = id; if (sub) GV.sub = sub;
  if (store.get("tab") !== "games") return go("games");
  renderGamePage(); window.scrollTo(0, 0);
}
function renderGamePage() {
  const id = GV.view, g = gOf(id);
  if (!g) { GV.view = null; return renderScoreboard(); }
  if (SIMV.gameId !== id) {   // the sim engine's live state follows the game you're looking at (the Sim tab follows too)
    simStop();
    Object.assign(SIMV, {gameId: id, P: null, game: null, mc: null, now: null, nowSim: null}); clearInterval(SIMV.liveTimer);
  }
  const subs = [["game", "Game"], ["box", "Box score"], ["lines", "Lines"], ["props", "Props"]];
  $("v-games").innerHTML = `<button class="btn ghost" id="gBack" style="margin:0 0 8px;padding:6px 12px;font-size:13px">← All games</button>
    <div class="card" id="gHead"></div>
    <div class="seg" id="gSubs" style="display:flex;gap:4px;margin:10px 0">${subs.map(([k, l]) => `<button data-k="${k}" class="${k === GV.sub ? "on" : ""}" style="flex:1;padding:7px 2px;font-size:12.5px">${l}</button>`).join("")}</div>
    <div id="gBody"></div>
    <button class="btn ghost" id="gSim" style="margin-top:12px;width:100%">▶ Watch the sim of this game</button>`;
  $("gBack").onclick = () => { GV.view = null; renderScoreboard(); window.scrollTo(0, 0); };
  $("gSubs").querySelectorAll("button").forEach(b => b.onclick = () => { GV.sub = b.dataset.k; $("gSubs").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); fillGame(true); });
  $("gSim").onclick = () => go("sim");
  fillGame(true);
  pollGame();
}
async function pollGame() {
  const g = gOf(GV.view); if (!g) return;
  const st = gState(g);
  if (!GV.sb) refreshScores();
  if (st === "pre" && new Date(g.start) - Date.now() > 15 * 60e3) return;
  if (st === "post" && nowOf(g.espn_id) && nowOf(g.espn_id).box) return;
  if (st === "in" && !SIMV.P) ensureP(g);
  await pollLive();   // sim.js: score, clock, DraftKings live line, box, plays; calls gamesLiveHook, then re-sims when the score moves
}
async function ensureP(g) {
  if (GV.calib === g.espn_id) return; GV.calib = g.espn_id;
  const gf = await gameFile(g.espn_id);
  if (gf && SIMV.gameId === g.espn_id && !SIMV.P) { await new Promise(r => setTimeout(r, 20)); SIMV.P = calibrate(prep(g, gf)); }
  GV.calib = null;
  if (SIMV.now && SIMV.now.state === "in" && SIMV.gameId === g.espn_id) liveSimFromHere();
}
window.gamesLiveHook = () => { if (store.get("tab") === "games" && GV.view && GV.view === SIMV.gameId) fillGame(false); };

function fillGame(force) {
  const id = GV.view, g = gOf(id); if (!g || !$("gHead")) return;
  const n = nowOf(id);
  $("gHead").innerHTML = headHtml(g, n);
  const b = $("gBody"); if (!b) return;
  if (!force && (GV.sub === "props" || GV.sub === "lines" && !(n && n.state === "in"))) return;   // nothing live to redraw there
  b.innerHTML = GV.sub === "box" ? boxSub(g, n) : GV.sub === "lines" ? linesSub(g, n) : GV.sub === "props" ? propsSub(g) : gameSub(g, n);
  if (GV.sub === "lines") showVs("gVs", id);
  const bd = $("gBoxD"); if (bd) bd.ontoggle = () => { SIMV.boxOpen = bd.open; };
  const pd = $("gPlayD"); if (pd) pd.ontoggle = () => { SIMV.playsOpen = pd.open; };
  bindPlayerLinks(b);
  b.querySelectorAll("[data-gsub]").forEach(x => x.onclick = () => { GV.sub = x.dataset.gsub; renderGamePage(); });
}
const uid = h => h.replace('id="liveBoxD"', 'id="gBoxD"').replace('id="livePlayD"', 'id="gPlayD"');

function headHtml(g, n) {
  const s = sbOf(g.espn_id), st = gState(g), A = g.away, H = g.home;
  const sc = n ? {away: n.score[0], home: n.score[1]} : s && s.state !== "pre" ? {away: s.away.score, home: s.home.score} : null;
  const detail = st === "in" ? (n ? n.detail : s ? s.detail : "Live") : st === "post" ? "Final" : new Date(g.start).toLocaleString([], {weekday: "short", hour: "numeric", minute: "2-digit"});
  const rec = side => n && n.teams && n.teams[side] && n.teams[side].rec ? n.teams[side].rec : "";
  const team = side => `<div class="gh-t"><div class="ab">${esc(g[side])}</div><div class="nm">${esc(g[side + "_name"] || "")}</div><div class="rec">${esc(rec(side))}</div></div>`;
  const win = side => sc && st === "post" && sc[side] > sc[side === "home" ? "away" : "home"];
  let ls = "";
  if (n && n.ls && (n.ls.home || []).length) {
    const q = Math.max(n.ls.home.length, n.ls.away.length, 4), lab = i => i < 4 ? "Q" + (i + 1) : i === 4 ? "OT" : "OT" + (i - 3);
    ls = `<div class="scroll" style="margin-top:8px"><table class="tbl"><tr><th></th>${Array.from({length: q}, (_, i) => `<th>${lab(i)}</th>`).join("")}<th>T</th></tr>
      ${["away", "home"].map(sd => `<tr><td class="t">${esc(g[sd])}</td>${Array.from({length: q}, (_, i) => `<td class="dim">${n.ls[sd][i] ?? ""}</td>`).join("")}<td><b>${sc ? sc[sd] : ""}</b></td></tr>`).join("")}</table></div>`;
  }
  const dk = dkOf(g.espn_id), c = dk.close || dk.open, L = n && n.state === "in" ? n.line : null;
  const lineTxt = [L && L.sp != null ? `Live <b style="color:var(--text)">${esc(H)} ${spS(L.sp)} · O/U ${f1(L.tot)}</b>` : "",
    c ? `${dk.close ? "Close" : "Open"} ${esc(H)} ${spS(c.sp)} · O/U ${f1(c.tot)}` : ""].filter(Boolean).join(" · ");
  return `<div class="gh">${team("away")}<div class="gh-s"><div class="sc">${sc ? `<span class="${win("away") ? "" : st === "post" ? "dim" : ""}">${sc.away}</span> – <span class="${win("home") ? "" : st === "post" ? "dim" : ""}">${sc.home}</span>` : "@"}</div>
      <div class="st">${st === "in" ? `<span class="pill live">${esc(detail)}</span>` : esc(detail)}</div></div>${team("home")}</div>
    ${ls}${lineTxt ? `<div class="desc" style="margin-top:8px;text-align:center">DraftKings · ${lineTxt}</div>` : ""}`;
}

function gameSub(g, n) {
  const st = gState(g), id = g.espn_id, A = g.away, H = g.home, dk = dkOf(id), c = dk.close || dk.open;
  const sim = (D.sims.games || {})[id];
  if (st === "in") {
    if (!n) return '<div class="empty">Loading the live game…</div>';
    return `<div class="card">${liveValueHtml(n, c, A, H)}<div class="desc">Updated ${esc(n.at ? n.at.toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"}).toLowerCase() : "")} · refreshes every 30s</div>
      ${uid(livePlaysHtml(n, A, H))}</div>
      ${topProps(g, 3, true)}`;
  }
  if (st === "post") {
    const sc = n ? {away: n.score[0], home: n.score[1]} : sbOf(id) ? {away: sbOf(id).away.score, home: sbOf(id).home.score} : null;
    if (!sc) return '<div class="empty">Loading the final…</div>';
    const tot = sc.away + sc.home, mar = sc.home - sc.away, rows = [];
    if (c && c.tot != null) rows.push([`Total ${f1(c.tot)}`, `<b>${tot > c.tot ? "Over" : tot < c.tot ? "Under" : "Push"}</b> · ${tot} scored`]);
    if (c && c.sp != null) { const m = mar + c.sp; rows.push([`${esc(H)} ${spS(c.sp)}`, `<b>${m > 0 ? esc(H) + " covered" : m < 0 ? esc(A) + " covered" : "Push"}</b> · ${esc(H)} ${mar >= 0 ? "+" : ""}${mar}`]); }
    if (sim) rows.push(["Sim", `${Math.round(sim.avg.away)}–${Math.round(sim.avg.home)}, total ${f1(sim.total[0])} (off by ${f1(Math.abs(tot - sim.total[0]))})`]);
    return `<div class="card"><div class="lbl" style="margin-top:0">How it closed out</div><table class="tbl">
      ${rows.map(r => `<tr><td class="t">${r[0]}</td><td class="t" style="text-align:right;white-space:normal">${r[1]}</td></tr>`).join("")}</table>
      ${n ? uid(livePlaysHtml(n, A, H)) : ""}</div>
      <button class="more" data-gsub="box">Full box score →</button>`;
  }
  // pre-game preview
  const T = sim && sim.total, rng = T ? `${T[1]}–${T[9]}` : "";
  const mv = (x, y) => x == null || y == null || x === y ? "" : ` <span class="gold">(${y > x ? "+" : ""}${f1(y - x)})</span>`;
  return `<div class="card">
      ${sim ? `<div class="lbl" style="margin-top:0">Sim · 10,000 games</div>
        <div style="font-size:16px;font-weight:700">${esc(A)} ${f1(sim.avg.away)} – ${f1(sim.avg.home)} ${esc(H)}</div>
        <div class="desc">${esc(sim.win_home >= 0.5 ? H : A)} wins ${Math.round(Math.max(sim.win_home, 1 - sim.win_home) * 100)}% · total ${f1(T[0])} (80% of sims ${rng}) · ${esc(sim.margin >= 0 ? H : A)} by ${f1(Math.abs(sim.margin))}</div>` : '<div class="desc">The sim projection arrives with the next research run.</div>'}
      ${dk.open || dk.close ? `<div class="lbl">DraftKings</div><div class="scroll"><table class="tbl"><tr><th></th><th>Open</th><th>Now / close</th>${sim ? "<th>Sim</th>" : ""}</tr>
        <tr><td class="t">${esc(H)} spread</td><td>${spS(dk.open && dk.open.sp)}</td><td>${spS(c && c.sp)}${mv(dk.open && dk.open.sp, dk.close && dk.close.sp)}</td>${sim ? `<td>${spS(-sim.margin)}</td>` : ""}</tr>
        <tr><td class="t">Total</td><td>${f1(dk.open && dk.open.tot)}</td><td>${f1(c && c.tot)}${mv(dk.open && dk.open.tot, dk.close && dk.close.tot)}</td>${sim ? `<td>${f1(T[0])}</td>` : ""}</tr>
        <tr><td class="t">${esc(H)} / ${esc(A)} ML</td><td>${am_(dk.open && dk.open.ml_h)} / ${am_(dk.open && dk.open.ml_a)}</td><td>${am_(c && c.ml_h)} / ${am_(c && c.ml_a)}</td>${sim ? `<td>${Math.round(sim.win_home * 100)}%</td>` : ""}</tr></table></div>` : ""}
      ${vegasLine(g) ? `<div class="desc" style="margin-top:6px">${vegasLine(g).replace(/^<div class="mdl">|<\/div>$/g, "")}</div>` : ""}
      <div class="desc" style="margin-top:6px">Live tracking (score, box score, live value) starts at tip-off.</div></div>
    ${topProps(g, 3, true)}`;
}
function topProps(g, k, link) {
  const rows = onePerPlayer(propRows().filter(r => r.espn_id === g.espn_id && ALL_STATS.includes(r.stat)), pickScore).slice(0, k);
  if (!rows.length) return "";
  return `<h2>Top plays in this game</h2><div class="card" style="padding:2px 12px">${rows.map(cRow).join("")}</div>${link ? '<button class="more" data-gsub="props">Every player in this game →</button>' : ""}`;
}

function boxSub(g, n) {
  if (n && n.box && ((n.box.home || []).length || (n.box.away || []).length)) return `<div class="card">${uid(liveBoxHtml(n, g.away, g.home))}</div>`;
  const sim = (D.sims.games || {})[g.espn_id];
  if (gState(g) !== "pre") return '<div class="empty">Loading the box score…</div>';
  if (!sim) return '<div class="empty">The projected box score arrives with the next sim run.</div>';
  const tb = side => {
    const rs = Object.entries(sim.players).map(([pid, p]) => ({pid, ...p})).filter(p => p.team === g[side] && p.min >= 4).sort((a, b) => b.min - a.min);
    return `<div class="scroll"><table class="tbl"><tr><th>${esc(g[side])}</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>3PM</th><th>PRA</th></tr>
      ${rs.map(p => `<tr data-pid="${p.pid}" data-stat="pts" style="cursor:pointer"><td class="t">${esc(lastName(p.name))}</td><td class="dim">${f1(p.min)}</td><td><b>${f1(p.s.pts[0])}</b></td><td>${f1(p.s.reb[0])}</td><td>${f1(p.s.ast[0])}</td><td>${f1(p.s.tpm[0])}</td><td class="dim">${f1(p.s.pra[0])}</td></tr>`).join("")}
      <tr><td class="t"><b>Team</b></td><td></td><td><b>${f1(sim.avg[side])}</b></td><td></td><td></td><td></td><td></td></tr></table></div>`;
  };
  return `<div class="card"><div class="lbl" style="margin-top:0">Projected box · average of 10,000 sims</div>${tb("away")}${tb("home")}
    <div class="desc">Becomes the live box score at tip-off. Tap a player for his card.</div></div>`;
}

function linesSub(g, n) {
  const id = g.espn_id, A = g.away, H = g.home, dk = dkOf(id), o = dk.open, c = dk.close, L = n && n.state === "in" ? n.line : null;
  const qc = s => s.detail && /half/i.test(s.detail) ? "Half" : `Q${Math.min(s.period, 4)}${s.period > 4 ? " OT" : ""} ${Math.floor(s.clock / 60)}:${String(Math.floor(s.clock % 60)).padStart(2, "0")}`;
  const table = o || c || L ? `<div class="scroll"><table class="tbl"><tr><th></th><th>Open</th><th>Close</th>${L ? "<th>Live</th>" : ""}</tr>
      <tr><td class="t">${esc(H)} spread</td><td>${spS(o && o.sp)}</td><td>${spS(c && c.sp)}</td>${L ? `<td><b>${spS(L.sp)}</b></td>` : ""}</tr>
      <tr><td class="t">Total</td><td>${f1(o && o.tot)}</td><td>${f1(c && c.tot)}</td>${L ? `<td><b>${f1(L.tot)}</b></td>` : ""}</tr>
      <tr><td class="t">${esc(H)} ML</td><td>${am_(o && o.ml_h)}</td><td>${am_(c && c.ml_h)}</td>${L ? `<td><b>${am_(L.ml_h)}</b></td>` : ""}</tr>
      <tr><td class="t">${esc(A)} ML</td><td>${am_(o && o.ml_a)}</td><td>${am_(c && c.ml_a)}</td>${L ? `<td><b>${am_(L.ml_a)}</b></td>` : ""}</tr></table></div>`
    : '<div class="empty">No DraftKings lines saved for this game yet (they arrive with the morning research run).</div>';
  const snaps = dk.live || [];
  const snapT = snaps.length ? `<div class="lbl">Saved live snapshots</div><div class="scroll"><table class="tbl"><tr><th>When</th><th>${esc(A)}–${esc(H)}</th><th>${esc(H)} sprd</th><th>Total</th><th>Sim cover · over</th></tr>
      ${snaps.map(s => { const l = s.line || {}, m = s.sim || {}; return `<tr><td class="t">${qc(s)}</td><td>${s.score[0]}–${s.score[1]}</td><td>${spS(l.sp)}</td><td>${f1(l.tot)}</td><td class="dim">${pcS(m.p_home_cover)} · ${pcS(m.p_over)}</td></tr>`; }).join("")}</table></div>` : "";
  const evs = ((D.bd && D.bd.events) || []).filter(e => e.sport === "basketball_nba" && nk(e.home) === nk(g.home_name) && nk(e.away) === nk(g.away_name));
  return `<div class="card"><div class="lbl" style="margin-top:0">DraftKings</div>${table}${snapT}</div>
    <h2>Best price at your books</h2><div class="card">${evs.length ? evs.map(evHtml).join("") + '<div class="desc" style="margin-top:6px">From the agent\'s latest scan. Tap a price to log the bet.</div>' : '<div class="empty">The agent hasn\'t scanned this game\'s lines yet (scans run around 10am, 3pm and 6:35pm ET).</div>'}</div>
    <h2>Sim vs DraftKings</h2><div class="card" id="gVs"></div>`;
}

function propsSub(g) {
  const rows = onePerPlayer(propRows().filter(r => r.espn_id === g.espn_id && ALL_STATS.includes(r.stat)), pickScore);
  if (!rows.length) return '<div class="empty">Projections for this game arrive with the morning research run.</div>';
  const side = ab => rows.filter(r => r.team === ab);
  return [g.away, g.home].map(ab => `<h2>${esc(ab)}</h2><div class="card" style="padding:2px 12px">${side(ab).map(cRow).join("") || '<div class="empty">No projected players.</div>'}</div>`).join("")
    + '<div class="desc" style="margin:6px 2px">Each player\'s strongest stat by conviction. Tap a player for every stat, hit rates and line history.</div>';
}

RENDER.games = renderGames;
window.openGame = openGame;
})();
