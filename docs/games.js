// Games tab: tonight's NBA scoreboard → one page per game (score, linescore, live value, box score, lines, props),
// plus the multi-sport board. Live data comes from the same free ESPN feeds as the Sim tab (sim.js pollLive).
(() => {
const {coverHtml, lineBtns, editing, stop: simStop, pollLive, liveSimFromHere, liveValueHtml, liveBoxHtml, livePlaysHtml, showVs, prep, calibrate, am_, spS, pcS, lastName} = window.SL;
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
  if (D.tm === undefined) D.tm = await get("team_model.json", null);
  if (!D.dkday || D.dkday.date !== D.players.date) D.dkday = await get(`dk/${D.players.date}.json`, {date: D.players.date, games: {}});
  clearInterval(GV.timer);
  GV.timer = setInterval(() => { if (store.get("tab") !== "games" || document.hidden) return; GV.view ? pollGame() : refreshScores(); }, 30000);
  if (GV.view && gOf(GV.view)) renderGamePage(); else { GV.view = null; renderScoreboard(); }
}

// ------------------------------------------------------------------ scoreboard
function renderScoreboard() {
  const games = D.players.games || [];
  const day = D.players.date ? new Date(D.players.date + "T12:00:00").toLocaleDateString([], {weekday: "long", month: "short", day: "numeric"}) : "";
  const w = WATCH().games, shown = games.filter(g => passF({espn_id: g.espn_id})).sort((a, b) => (w.includes(b.espn_id) - w.includes(a.espn_id)));
  $("v-games").innerHTML = `<h2>NBA${day ? " · " + esc(day) : ""}</h2>
    <div class="games" id="gList">${shown.map(gCardHtml).join("") || '<div class="empty">No NBA games on the slate (or none match your filters).</div>'}</div>
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
  const w = WATCH().games, html = (D.players.games || []).filter(g => passF({espn_id: g.espn_id})).sort((a, b) => (w.includes(b.espn_id) - w.includes(a.espn_id))).map(gCardHtml).join("");
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
  return `<div class="gcard gs" data-g="${g.espn_id}"><div class="gs-top">${status}${isWatched("games", g.espn_id) ? '<span class="gold" style="margin-left:auto">★</span>' : ""}</div>${row("away")}${row("home")}
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
  const subs = [["game", "Overview"], ["match", "Matchup"], ["props", "Players"], ["lines", "Lines"], ["box", "Box"], ["sim", "Sim ▶"]];
  if (GV.sub === "sim") GV.sub = "game";
  $("v-games").innerHTML = `<div style="display:flex;align-items:center;justify-content:space-between"><button class="btn ghost" id="gBack" style="margin:0 0 8px;padding:6px 12px;font-size:13px">← All games</button>${starBtn("games", id)}</div>
    <div class="card" id="gHead"></div>
    <div class="seg" id="gSubs" style="display:flex;gap:4px;margin:10px 0">${subs.map(([k, l]) => `<button data-k="${k}" class="${k === GV.sub ? "on" : ""}" style="flex:1;padding:7px 2px;font-size:12.5px">${l}</button>`).join("")}</div>
    <div id="gBody"></div>
    <button class="btn ghost" id="gSim" style="margin-top:12px;width:100%">▶ Watch the sim of this game</button>`;
  $("gBack").onclick = () => { GV.view = null; renderScoreboard(); window.scrollTo(0, 0); };
  $("gSubs").querySelectorAll("button").forEach(b => b.onclick = () => { if (b.dataset.k === "sim") return go("sim"); GV.sub = b.dataset.k; $("gSubs").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); fillGame(true); });
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
  if (!force && editing() && b.contains(document.activeElement)) return;     // you're typing a line to price
  if (!force && (GV.sub === "props" || GV.sub === "match" || GV.sub === "lines" && !(n && n.state === "in"))) return;   // nothing live to redraw there
  b.innerHTML = GV.sub === "match" ? matchSub(g) : GV.sub === "box" ? boxSub(g, n) : GV.sub === "lines" ? linesSub(g, n) : GV.sub === "props" ? propsSub(g) : gameSub(g, n);
  if (GV.sub === "lines") showVs("gVs", id);
  const bd = $("gBoxD"); if (bd) bd.ontoggle = () => { SIMV.boxOpen = bd.open; };
  const pd = $("gPlayD"); if (pd) pd.ontoggle = () => { SIMV.playsOpen = pd.open; };
  bindPlayerLinks(b);
  b.querySelectorAll("[data-mw]").forEach(x => x.onclick = () => { GV.mw = x.dataset.mw; fillGame(true); });
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
  // pre-game preview: one summary card first, the detail below
  const T = sim && sim.total, rng = T ? `${T[1]}–${T[9]}` : "";
  const summary = (() => {
    const rows = [], tn = D.tm && D.tm.tonight && D.tm.tonight[id];
    if (sim) rows.push(["Projected", `<b>${esc(A)} ${f1(sim.avg.away)} – ${f1(sim.avg.home)} ${esc(H)}</b> · ${esc(sim.win_home >= 0.5 ? H : A)} ${Math.round(Math.max(sim.win_home, 1 - sim.win_home) * 100)}%`]);
    if (tn && c && c.sp != null) { const d = tn.margin + c.sp; rows.push(["Vegas vs our model", Math.abs(d) < 1.5 ? "agree on the spread" : `our model has ${esc(d > 0 ? H : A)} ${f1(Math.abs(d))} better than DraftKings`]); }
    if (tn && tn.out && tn.out.length) rows.push(["Who's out", tn.out.map(o => `${esc(o.team)}: ${esc(o.names)} (${f1(o.pts)})`).join(" · ")]);
    const day = (window.normPicks ? normPicks(((D.best || {}).days || {})[D.players.date]) : ((D.best || {}).days || {})[D.players.date]) || {}, picks = [...(day.props || []), ...(day.games || [])].filter(x => x.espn_id === id).slice(0, 2);
    if (picks.length) rows.push(["Best bets here", picks.map(x => `${x.kind === "prop" ? esc(lastName(x.name)) + " " + x.side.toLowerCase() + " " + x.point + " " + (STATN[x.stat] || x.stat) : x.kind === "spread" ? esc(x.team) + " " + (x.point > 0 ? "+" : "") + x.point : x.side + " " + x.point} <span class="cvb ${x.tier}">${x.tier || ""}</span>`).join(" · ")]);
    const risks = [];
    if (sim && sim.mh) { const n = Object.values(sim.mh).reduce((t, v) => t + v, 0); let b = 0; for (const [k, v] of Object.entries(sim.mh)) if (Math.abs(+k) >= 15) b += v; if (b / n >= 0.3) risks.push(`blowout in ${Math.round(b / n * 100)}% of sims (starters sit late)`); }
    const sp = (g.vegas || {}).spots || {}; ["away", "home"].forEach(sd => { if (sp[sd] && sp[sd].rest === 0) risks.push(`${g[sd]} on a back-to-back`); });
    if (risks.length) rows.push(["Biggest risk", esc(risks.join("; "))]);
    return rows.length ? `<div class="card gsum">${rows.map(([k, v]) => `<div class="row2"><span>${k}</span><span style="text-align:right">${v}</span></div>`).join("")}</div>` : "";
  })();
  const mv = (x, y) => x == null || y == null || x === y ? "" : ` <span class="gold">(${y > x ? "+" : ""}${f1(y - x)})</span>`;
  return `${summary}<div class="card" style="margin-top:10px">
      ${sim ? `<div class="lbl" style="margin-top:0">Sim · 10,000 games</div>
        <div style="font-size:16px;font-weight:700">${esc(A)} ${f1(sim.avg.away)} – ${f1(sim.avg.home)} ${esc(H)}</div>
        <div class="desc">${esc(sim.win_home >= 0.5 ? H : A)} wins ${Math.round(Math.max(sim.win_home, 1 - sim.win_home) * 100)}% · total ${f1(T[0])} (80% of sims ${rng}) · ${esc(sim.margin >= 0 ? H : A)} by ${f1(Math.abs(sim.margin))}</div>` : '<div class="desc">The sim projection arrives with the next research run.</div>'}
      ${dk.open || dk.close ? `<div class="lbl">DraftKings</div><div class="scroll"><table class="tbl"><tr><th></th><th>Open</th><th>Now / close</th>${sim ? "<th>Sim</th>" : ""}</tr>
        <tr><td class="t">${esc(H)} spread</td><td>${spS(dk.open && dk.open.sp)}</td><td>${spS(c && c.sp)}${mv(dk.open && dk.open.sp, dk.close && dk.close.sp)}</td>${sim ? `<td>${spS(-sim.margin)}</td>` : ""}</tr>
        <tr><td class="t">Total</td><td>${f1(dk.open && dk.open.tot)}</td><td>${f1(c && c.tot)}${mv(dk.open && dk.open.tot, dk.close && dk.close.tot)}</td>${sim ? `<td>${f1(T[0])}</td>` : ""}</tr>
        <tr><td class="t">${esc(H)} / ${esc(A)} ML</td><td>${am_(dk.open && dk.open.ml_h)} / ${am_(dk.open && dk.open.ml_a)}</td><td>${am_(c && c.ml_h)} / ${am_(c && c.ml_a)}</td>${sim ? `<td>${Math.round(sim.win_home * 100)}%</td>` : ""}</tr></table></div>` : ""}
      ${c ? `<div class="lbl">Bet DraftKings' line</div>${lineBtns(c, false)}` : ""}
      ${vegasLine(g) ? `<div class="desc" style="margin-top:6px">${vegasLine(g).replace(/^<div class="mdl">|<\/div>$/g, "")}</div>` : ""}
      ${sim && sim.mh ? coverHtml(sim.mh, sim.th, c && c.sp != null ? c.sp : sim.vegas && sim.vegas.spread_home, c && c.tot != null ? c.tot : sim.vegas && sim.vegas.total, H, A, c) : ""}
      <div class="desc" style="margin-top:6px">Live tracking (score, box score, live value) starts at tip-off.</div></div>
    ${spotsHtml(g)}${topProps(g, 3, true)}`;
}
// schedule spots (rest, travel, time zones, altitude) and tonight's referee crew
function spotsHtml(g) {
  const v = g.vegas || {}, sp = v.spots, rf = v.refs; if (!sp && !rf) return "";
  const side = sd => { const x = sp[sd], ab = g[sd];
    return `<div class="ti"><span><b style="color:var(--text)">${esc(ab)}</b> · ${x.why && x.why.length ? esc(x.why.map(w => w[1]).join(", ")) : "normal spot"}
      <br><span class="dim" style="font-size:11.5px">${x.rest == null ? "" : x.rest === 0 ? "played last night · " : x.rest + " day" + (x.rest > 1 ? "s" : "") + " rest · "}${(x.miles7 || 0).toLocaleString()} miles in 7 days · ${x.games7 || 0} games in 7 days${x.road_trip >= 2 ? " · road game " + x.road_trip : ""}</span></span>
      <b class="${x.pts < -0.05 ? "neg" : x.pts > 0.05 ? "pos" : "dim"}">${x.pts ? (x.pts > 0 ? "+" : "") + f1(x.pts) : "0"}</b></div>`; };
  const net = sp ? sp.net_home : 0;
  return `<h2>Schedule spot and referees</h2><div class="card">
    ${sp ? `<div class="desc" style="margin-bottom:2px">Points of margin each team gains or loses from rest and travel (measured on 2,500 games: a back-to-back costs the most, flying east on short rest a little).</div>
      ${side("away")}${side("home")}
      <div class="desc" style="margin-top:6px">${Math.abs(net) >= 0.3 ? `Net: <b style="color:var(--text)">${esc(net > 0 ? g.home : g.away)} +${f1(Math.abs(net))}</b> from the schedule. It's already in the model's score for this game.` : "No real schedule edge either way."}</div>` : ""}
    ${rf ? `<div class="lbl">Referees</div><div class="desc"><b style="color:var(--text)">${esc(rf.crew.join(", "))}</b>${rf.n ? `. Their games have run ${rf.fta > 0 ? "+" : ""}${f1(rf.fta)} free throws and ${rf.tot > 0 ? "+" : ""}${f1(rf.tot)} points vs average (about ${rf.n} games each).` : "."} Context only: in our data a referee's tendencies one season didn't carry into the next, so they don't move the projections.</div>` : ""}</div>`;
}
function topProps(g, k, link) {
  const rows = onePerPlayer(propRows().filter(r => r.espn_id === g.espn_id && ALL_STATS.includes(r.stat)), pickScore).slice(0, k);
  if (!rows.length) return "";
  return `<h2>Top plays in this game</h2><div class="card" style="padding:2px 12px">${rows.map(cRow).join("")}</div>${link ? '<button class="more" data-gsub="props">Every player in this game →</button>' : ""}`;
}

// ------------------------------------------------------------------ Matchup Builder (team model, phase 1)
const PHI = x => { const t = 1 / (1 + 0.2316419 * Math.abs(x)), d = 0.3989423 * Math.exp(-x * x / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return x > 0 ? 1 - p : p; };
function teamPredict(T, h, a, b2bH, b2bA) {
  const e = T.eff, p = T.pace, P = T.params;
  if (e.O[h] == null || e.O[a] == null) return null;
  const pace = (2 * P.pace_mu + (p.O[h] || 0) + (p.D[a] || 0) + (p.O[a] || 0) + (p.D[h] || 0)) / 2 + P.pace_b2b * (b2bH + b2bA) / 2;
  const hp = P.mu + e.O[h] + (e.D[a] || 0) + P.home * 0.5 + P.b2b_off * b2bH + P.b2b_def * b2bA;
  const ap = P.mu + e.O[a] + (e.D[h] || 0) - P.home * 0.5 + P.b2b_off * b2bA + P.b2b_def * b2bH;
  const H = pace * hp / 100, A = pace * ap / 100;
  return {home: H, away: A, margin: H - A, total: H + A, pace, win: PHI((H - A) / P.sd_margin)};
}
const FF = [["efg", "Effective FG%", 1, true], ["tov", "Turnovers per poss", -1, true], ["orb", "Off. rebound %", 1, true],
  ["ftr", "Free throws per shot", 1, true], ["tpar", "3-point attempt rate", 0, true], ["tpp", "3-point %", 1, true]];
const ZN = [["rim", "Rim"], ["paint", "Paint"], ["mid", "Mid-range"], ["corner3", "Corner 3"], ["above3", "Above-break 3"]];
const pc1 = v => v == null ? "—" : (v * 100).toFixed(1) + "%";
function matchSub(g) {
  const T = D.tm, H = g.home, A = g.away;
  if (!T || !T.teams || !T.teams[H] || !T.teams[A]) return '<div class="empty">The team model arrives with the next research run.</div>';
  const sp = (g.vegas || {}).spots || {}, b2b = sd => sp[sd] && sp[sd].rest === 0 ? 1 : 0;
  const P = T.params, L = T.league, th = T.teams[H], ta = T.teams[A];
  let pr = teamPredict(T, H, A, b2b("home"), b2b("away"));
  const tn = (T.tonight || {})[g.espn_id];           // ratings plus who's playing tonight (agent/roster.py)
  if (tn) pr = {...pr, home: tn.home_pts, away: tn.away_pts, margin: tn.margin, total: tn.total, pace: tn.pace, win: PHI(tn.margin / P.sd_margin)};
  const who = sd => { const r = tn && tn.adj && tn.adj[sd]; if (!r || !(r.out.length + r.back.length)) return `<div class="ti"><span><b style="color:var(--text)">${esc(g[sd])}</b> · the usual rotation</span><b class="dim">0</b></div>`;
    return `<div class="ti"><span><b style="color:var(--text)">${esc(g[sd])}</b>${r.out.length ? " · out: " + r.out.map(x => `${esc(x[0])} (${f1(-x[1])})`).join(", ") : ""}${r.back.length ? " · back: " + r.back.map(x => `${esc(x[0])} (+${f1(x[1])})`).join(", ") : ""}</span><b class="${r.pts < -0.05 ? "neg" : r.pts > 0.05 ? "pos" : "dim"}">${r.pts > 0 ? "+" : ""}${f1(r.pts)}</b></div>`; };
  const LV = T.live || {}, btB = (T.backtest || {}).big_roster;
  const whoCard = tn && tn.roster ? `<h2>Who's playing</h2><div class="card"><div class="desc" style="margin-bottom:2px">Points of margin from tonight's lineup compared with the team the ratings have seen lately. A player who has already missed most recent games counts for less, because the ratings have adjusted.</div>
      ${who("away")}${who("home")}
      <div class="desc" style="margin-top:6px">Ratings alone: ${esc(tn.base_margin >= 0 ? H : A)} by ${f1(Math.abs(tn.base_margin))}. With lineups: <b style="color:var(--text)">${esc(tn.margin >= 0 ? H : A)} by ${f1(Math.abs(tn.margin))}</b>.${btB ? ` In past games with 3+ points of lineup news the model missed by ${f1(btB.margin_mae.model)} (ratings alone ${f1(btB.margin_mae.ratings_only)}, DraftKings ${f1(btB.margin_mae.line)}).` : ""}</div></div>` : "";
  const dk = dkOf(g.espn_id), c = dk.close || dk.open || (g.vegas && g.vegas.total ? {sp: g.vegas.spread_home, tot: g.vegas.total} : null);
  const z = 1.2816, bt = (T.backtest || {}).all;
  // Vegas vs our model: the game each one expects, where they split, which players that moves, and how past splits have done
  let vs = "";
  if (c && c.sp != null && c.tot != null) {
    const dS = pr.margin + c.sp, dT = pr.total - c.tot;
    const vh = c.tot / 2 - c.sp / 2, va = c.tot / 2 + c.sp / 2;           // Vegas' implied team points
    const bucket = (obj, d, cuts) => { const k = cuts.filter(x => Math.abs(d) >= x).pop(); const r = obj && obj[String(k)]; return r && r.w + r.l ? `${r.w}-${r.l} (${Math.round(r.pct * 100)}%) ${k ? `when it disagreed by ${k}+` : "on every game"}` : ""; };
    const gap = (v, cut, f) => Math.abs(v) < 0.5 ? '<span class="dim">agree</span>' : `<span class="${Math.abs(v) >= cut ? "gold" : "dim"}">${f(v)}</span>`;
    // players: the nightly sims are on the Vegas line; under our model, points follow the team's points and every counting stat follows the pace
    const sg = (D.sims && D.sims.games || {})[g.espn_id], movers = [];
    if (sg && sg.players) {
      const vPace = sg.pace || pr.pace, kP = pr.pace / vPace;
      for (const [pid, x] of Object.entries(sg.players)) {
        if (x.min < 20) continue;
        const kT = x.team === H ? pr.home / vh : pr.away / va;
        const pts = x.s.pts[0], d = pts * kT - pts, dr = x.s.reb[0] * (kP - 1), da = x.s.ast[0] * (kP - 1);
        movers.push({pid, name: x.name, team: x.team, pts, d, dra: d + dr + da});
      }
      movers.sort((a, b) => Math.abs(b.dra) - Math.abs(a.dra));
    }
    const LVs = T.live || {};
    vs = `<div class="lbl">Vegas vs our model</div>
      <div class="scroll"><table class="tbl"><tr><th></th><th>DraftKings</th><th>Our model</th><th>Gap</th></tr>
      <tr><td class="t">${esc(H)} spread</td><td>${spS(c.sp)}</td><td>${spS(-pr.margin)}</td><td>${gap(dS, 2, v => `${esc(v > 0 ? H : A)} +${f1(Math.abs(v))}`)}</td></tr>
      <tr><td class="t">Total</td><td>${f1(c.tot)}</td><td>${f1(pr.total)}</td><td>${gap(dT, 3, v => (v > 0 ? "Over " : "Under ") + f1(Math.abs(v)))}</td></tr>
      <tr><td class="t">${esc(A)} points</td><td>${f1(va)}</td><td>${f1(pr.away)}</td><td>${gap(pr.away - va, 2, v => (v > 0 ? "+" : "") + f1(v))}</td></tr>
      <tr><td class="t">${esc(H)} points</td><td>${f1(vh)}</td><td>${f1(pr.home)}</td><td>${gap(pr.home - vh, 2, v => (v > 0 ? "+" : "") + f1(v))}</td></tr></table></div>
      ${movers.length && movers[0] && Math.abs(movers[0].dra) >= 0.3 ? `<div class="desc" style="margin-top:6px"><b style="color:var(--text)">Players this moves</b> (PRA under our model vs the Vegas-line sims): ${movers.slice(0, 5).map(m => `<a class="plink" data-pid="${m.pid}" data-stat="pra">${esc(lastName(m.name))}</a> <span class="${m.dra > 0 ? "pos" : "neg"}">${m.dra > 0 ? "+" : ""}${f1(m.dra)}</span>`).join(" · ")}. Our model thinks ${Math.abs(pr.home - vh) >= Math.abs(pr.away - va) ? esc(H) : esc(A)}'s players are ${(Math.abs(pr.home - vh) >= Math.abs(pr.away - va) ? pr.home - vh : pr.away - va) > 0 ? "underpriced" : "overpriced"} by the game script, before any player matchup.</div>` : ""}
      ${bt ? `<div class="desc">How past gaps did, in the backtest: sides ${bucket(bt.ats, dS, [0, 2, 4, 6]) || "—"} · totals ${bucket(bt.ou, dT, [0, 3, 6, 9]) || "—"}. Break-even at -110 is 52.4%.${LVs.n ? ` Since tracking started: ${LVs.ats["2"].w}-${LVs.ats["2"].l} on sides at 2+ points${LVs.clv && LVs.clv.n ? `, and the line moved toward our model in ${LVs.clv.toward} of ${LVs.clv.n} splits` : ""}.` : ""} Tracked only: the agent doesn't bet game lines from this until the scoreboard shows an edge.</div>` : ""}`;
  }
  // filters: model (opponent-adjusted) or raw splits
  const W = GV.mw || "model", spl = T.splits || {};
  const side = (t, k) => W === "model" ? {off: T.teams[t].off, def: T.teams[t].def} : spl[t] && spl[t][k] ? spl[t][k] : null;
  const wkA = W === "home" ? "road" : W, wkH = W === "road" ? "home" : W;    // "Home/Road" shows each team in tonight's venue
  const kA = W === "venue" ? "road" : wkA, kH = W === "venue" ? "home" : wkH;
  const sA = side(A, kA), sH = side(H, kH);
  const chips = [["model", "Model"], ["l5", "Last 5"], ["l10", "Last 10"], ["venue", "Tonight's venue"]]
    .map(([k, l]) => `<button data-mw="${k}" class="${W === k ? "on" : ""}">${l}</button>`).join("");
  // Each factor's edge in points per game for the offense (≈, from how many possessions it touches tonight), and who it favors.
  const PPP = (P.mu || 111) / 100;
  const ptsOf = (k, o, d, lg) => { if (o == null || d == null || lg == null) return null; const x = o + d - 2 * lg, pc = pr.pace;
    return k === "efg" ? pc * 0.88 * 2 * x : k === "tov" ? -pc * x * PPP : k === "orb" ? pc * 0.47 * x * PPP : k === "ftr" ? pc * 0.88 * x * 0.78 * 0.55 : null; };
  const ink = t => (window.teamInk ? window.teamInk(t) : "var(--text)");
  const chip = t => `<b class="tchip" style="color:${ink(t)};border-color:${ink(t)}55">${esc(t)}</b>`;
  const bar = (k, o, d, lg, dir, ot, dt) => {   // offense value vs the defense's allowed value, both vs league
    if (o == null || d == null || lg == null) return "<td></td>";
    const pts0 = ptsOf(k, o, d, lg), pts = pts0 == null ? null : pts0 * KF, edge = dir === 0 ? 0 : dir * ((o - lg) + (d - lg)) / Math.max(1e-6, Math.abs(lg));
    const fav = Math.abs(edge) < 0.005 ? null : edge > 0 ? ot : dt, w = Math.min(46, Math.abs(edge) * 380);
    return `<td><div class="mbar2">${fav ? `<span style="${edge > 0 ? "left:50%" : "right:50%"};width:${w}%;background:${ink(fav)}"></span>` : ""}</div>
      <div class="mfav">${fav ? chip(fav) + (pts != null && Math.abs(pts) >= 0.1 ? ` <span class="dim">+${f1(Math.abs(pts))}</span>` : "") : '<span class="dim">even</span>'}</div></td>`;
  };
  const ffRaw = (off, def) => { if (!off || !def) return null; let t = 0; for (const [k] of FF) { const v = ptsOf(k, off[k], def[k], L[k]); if (v != null) t += v; } return t; };
  const ffSum = (off, def) => { const t = ffRaw(off, def); return t == null ? null : t * KF; };
  const ffT = (off, def, ot, dt) => !off || !def ? '<div class="desc">No games in this window yet.</div>' : `<table class="tbl mff"><tr><th>${esc(ot)} off. vs ${esc(dt)} def.</th><th>${esc(ot)}</th><th>${esc(dt)} allows</th><th style="width:28%">Edge to</th></tr>
    ${FF.filter(([k, , dir]) => !FLT().signal || dir === 0 ? !FLT().signal : Math.abs(dir * ((off[k] - L[k]) + (def[k] - L[k])) / Math.max(1e-6, Math.abs(L[k]))) >= 0.005).map(([k, lab, dir, pct]) => `<tr><td class="t">${lab}<div class="dim" style="font-size:10.5px">league ${pct ? pc1(L[k]) : f1(L[k])}</div></td><td>${pct ? pc1(off[k]) : f1(off[k])}</td><td>${pct ? pc1(def[k]) : f1(def[k])}</td>${bar(k, off[k], def[k], L[k], dir, ot, dt)}</tr>`).join("")}</table>
    ${(t => t == null ? "" : `<div class="desc" style="margin-top:4px">${esc(ot)}'s offense vs ${esc(dt)}'s defense: ${Math.abs(t) < 0.3 ? "about even" : `${chip(t > 0 ? ot : dt)} <b style="color:var(--text)">≈ +${f1(Math.abs(t))} points</b> from shooting, turnovers, rebounding and free throws`}.</div>`)(ffSum(off, def, ot, dt))}`;
  const ffNum = v => v == null ? null : v;
  const fx = x => x && {efg: x.efg, tov: x.tov, orb: x.orb, ftr: ffNum(x.ftr), tpar: x.tpar, tpp: x.tpp};
  const awayOff = sA && fx(sA.off), homeDef = sH && fx(sH.def), homeOff = sH && fx(sH.off), awayDef = sA && fx(sA.def);
  // the factor ratings are fitted one at a time, so their raw sum overshoots; scale the points so both ends add up to the
  // team model's margin before home court (shown as "≈")
  const rawNet = W === "model" && homeOff && awayDef && awayOff && homeDef ? ffRaw(homeOff, awayDef) - ffRaw(awayOff, homeDef) : null;
  const target = pr.margin - (P.home || 0) * pr.pace / 100;
  const KF = rawNet && Math.abs(rawNet) >= 2 && Math.sign(rawNet) === Math.sign(target) ? Math.max(0.3, Math.min(1.5, target / rawNet)) : 1;
  // shooting luck: what opponents have hit from three vs the league
  const luck = t => { const r = T.teams[t].raw; if (!r || r.opp3 == null) return "";
    const d = (r.opp3 - P.lg3) * 100; return Math.abs(d) < 1.2 ? "" : `<b style="color:var(--text)">${esc(t)}</b> opponents are hitting ${pc1(r.opp3)} from three (league ${pc1(P.lg3)}). Most of that is luck, so the model expects it to ${d > 0 ? "come down" : "go up"}${d > 0 ? ": their defense is better than its raw rating" : ": their defense is worse than its raw rating"}.`; };
  const lk = [luck(A), luck(H)].filter(Boolean);
  // shot zones
  const Z = T.zones || {}, zl = Z._league;
  const zT = (ot, dt) => !Z[ot] || !Z[dt] || !zl ? "" : `<div class="scroll"><table class="tbl"><tr><th>${esc(ot)} shots</th><th>${esc(ot)} takes</th><th>${esc(dt)} allows</th><th>Lg</th><th>${esc(dt)} FG% allowed</th></tr>
    ${ZN.map(([k, lab]) => { const o = Z[ot].off[k], d = Z[dt].def[k], l = zl[k]; const hot = o[0] > l[0] * 1.1 && d[0] > l[0] * 1.05;
      return `<tr><td class="t">${lab}</td><td class="${o[0] > l[0] * 1.1 ? "gold" : ""}">${pc1(o[0])}</td><td class="${d[0] > l[0] * 1.05 ? "gold" : ""}">${pc1(d[0])}</td><td class="dim">${pc1(l[0])}</td><td class="${d[1] != null && l[1] != null ? (d[1] > l[1] + 0.02 ? "pos" : d[1] < l[1] - 0.02 ? "neg" : "") : ""}">${pc1(d[1])}${hot ? " ★" : ""}</td></tr>`; }).join("")}</table></div>`;
  // what it means for props
  const blow = 1 - (PHI((15 - pr.margin) / P.sd_margin) - PHI((-15 - pr.margin) / P.sd_margin));
  const paceD = pr.pace - P.pace_mu;
  const rk = (t, k) => T.teams[t].rank ? T.teams[t].rank[k] : "";
  const row = (lab, k, fmt, rkey) => `<tr><td class="t">${lab}</td><td>${fmt(ta[k])} <span class="dim">#${rk(A, rkey || k)}</span></td><td>${fmt(th[k])} <span class="dim">#${rk(H, rkey || k)}</span></td></tr>`;
  return `<div class="card"><div class="lbl" style="margin-top:0">Team model · projected score</div>
      <div style="font-size:18px;font-weight:700">${esc(A)} ${f1(pr.away)} – ${f1(pr.home)} ${esc(H)}</div>
      <div class="desc">${esc(pr.margin >= 0 ? H : A)} by ${f1(Math.abs(pr.margin))} · wins ${Math.round(Math.max(pr.win, 1 - pr.win) * 100)}% · total ${f1(pr.total)} · ${f1(pr.pace)} possessions<br>
      80% range: total ${Math.round(pr.total - z * P.sd_total)}–${Math.round(pr.total + z * P.sd_total)}, margin ${esc(H)} ${f1(pr.margin - z * P.sd_margin)} to ${f1(pr.margin + z * P.sd_margin)}${b2b("home") || b2b("away") ? ` · back-to-back included (${[b2b("away") ? A : "", b2b("home") ? H : ""].filter(Boolean).join(", ")})` : ""}</div>
      ${vs}
      <div class="lbl">Ratings · per 100 possessions, opponent-adjusted</div>
      <table class="tbl"><tr><th></th><th>${esc(A)}</th><th>${esc(H)}</th></tr>
      ${row("Net rating", "net", v => (v > 0 ? "+" : "") + f1(v))}${row("Offense", "ortg", f1)}${row("Defense", "drtg", f1)}${row("Pace", "pace", f1)}
      <tr><td class="t">Last 10 net (raw)</td><td>${ta.net10 == null ? "—" : (ta.net10 > 0 ? "+" : "") + f1(ta.net10)}</td><td>${th.net10 == null ? "—" : (th.net10 > 0 ? "+" : "") + f1(th.net10)}</td></tr></table>
      <div class="desc">Points are luck-adjusted: each team's threes are re-scored at its long-run 3P%, so defenses are judged on the threes they allow, not on how many fell. Recent games count more.</div></div>
    ${whoCard}
    <h2>Four factors</h2><div class="card"><div class="chips" style="margin-bottom:8px">${chips}</div>
      ${ffT(awayOff, homeDef, A, H)}<div style="height:10px"></div>${ffT(homeOff, awayDef, H, A)}
      ${(() => { const a = ffSum(awayOff, homeDef, A, H), h = ffSum(homeOff, awayDef, H, A); if (a == null || h == null) return "";
        const net = h - a; return `<div class="ffnet">Four factors overall: ${Math.abs(net) < 0.5 ? "<b>even</b>" : `${chip(net > 0 ? H : A)} <b>≈ +${f1(Math.abs(net))} points</b>`} <span class="dim">(team model margin: ${esc(pr.margin >= 0 ? H : A)} by ${f1(Math.abs(pr.margin))}; the rest is home court${tn ? ", lineups" : ""} and schedule)</span></div>`; })()}
      <div class="desc">Each row's bar points toward the team that factor favors, in that team's color, with its value in points tonight (approximate, and on the Model view scaled so the factors add up to the team model's margin before home court). 3-point rate and 3-point % are already inside effective FG%, so they carry no points of their own. "Model" is opponent-adjusted; the other windows are raw this season${FLT().signal ? ". Signal only is on, so factors that are about even (and the 3-point rate row) are hidden" : ""}${W === "venue" ? `, with ${esc(A)} on the road and ${esc(H)} at home` : ""}.</div>
      ${lk.length ? `<div class="lbl">Three-point luck</div><div class="desc">${lk.join("<br>")}</div>` : ""}</div>
    ${zl ? `<h2>Shot zones</h2><div class="card">${zT(A, H)}<div style="height:10px"></div>${zT(H, A)}
      <div class="desc">Share of shots from each zone this season. Gold = above league; ★ = a zone the offense loves and the defense gives up. FG% allowed: green = defense is leaky there.</div></div>` : ""}
    <h2>What it means for props</h2><div class="card"><div class="desc" style="line-height:1.6">
      Pace: <b style="color:var(--text)">${f1(pr.pace)}</b> possessions, ${Math.abs(paceD) < 0.7 ? "about league average" : `${paceD > 0 ? "+" : ""}${f1(paceD)} vs average (${paceD > 0 ? "more" : "fewer"} chances for every counting stat, about ${Math.abs(Math.round(paceD / P.pace_mu * 100))}%)`}.<br>
      Blowout risk (15+ point margin): <b style="color:var(--text)">${Math.round(blow * 100)}%</b>${blow > 0.35 ? ". Starters' minutes are at risk" : ""}.<br>
      Team totals: ${esc(A)} ${f1(pr.away)}, ${esc(H)} ${f1(pr.home)}.</div></div>
    ${bt ? `<details class="sec"><summary>How the team model has done</summary><div style="padding:0 14px 12px"><table class="tbl">
      <tr><th></th><th>Model</th><th>DK line</th></tr>
      <tr><td class="t">Avg miss, margin</td><td>${bt.margin_mae.model.toFixed(2)}</td><td>${bt.margin_mae.line.toFixed(2)}</td></tr>
      ${bt.margin_mae.ratings_only ? `<tr><td class="t dim">  ratings without lineups</td><td class="dim">${bt.margin_mae.ratings_only.toFixed(2)}</td><td></td></tr>` : ""}
      <tr><td class="t">Avg miss, total</td><td>${f1(bt.total_mae.model)}</td><td>${f1(bt.total_mae.line)}</td></tr>
      ${bt.win_ll ? `<tr><td class="t">Win prob. log loss</td><td>${bt.win_ll.model}</td><td>${bt.win_ll.line}</td></tr>` : ""}</table>
      <div class="desc">Walk-forward over ${bt.n.toLocaleString()} games with ESPN's pregame DraftKings line (the model only ever saw games before each date). Best blend weight on the model: ${bt.margin_best_w} for spreads, ${bt.total_best_w} for totals (0 = the line alone is best). Lower is better for every row.</div>
      <div class="lbl">Live scoreboard · since Nov 11</div><div class="desc">${LV.n ? `${LV.n} games graded. Margin miss: model ${LV.margin_mae.model}, closing line ${LV.margin_mae.close}. Against the spread when it disagreed by 2+: ${LV.ats["2"].w}-${LV.ats["2"].l}.${LV.clv && LV.clv.n ? ` The line moved the model's way in ${LV.clv.toward} of ${LV.clv.n} disagreements (${LV.clv.avg_pts > 0 ? "+" : ""}${LV.clv.avg_pts} points on average).` : ""}` : "No games graded yet."} Status: <b style="color:var(--text)">${LV.status === "edge" ? "edge: may bet" : LV.status === "skip" ? "skip" : "tracked only"}</b> (needs ${LV.gate || 150} graded games and 55%+ against the spread before the agent may bet game lines from it).</div></div></details>` : ""}`;
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
  const cur = L || c || o;
  return `<div class="card"><div class="lbl" style="margin-top:0">DraftKings</div>${table}
    ${cur ? `<div class="lbl">Log a bet at DraftKings' ${L ? "live" : dk.close ? "current" : "opening"} line</div>${lineBtns(cur, !!L)}` : ""}${snapT}</div>
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
