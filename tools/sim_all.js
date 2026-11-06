// Simulate every game on tonight's slate N times (default 10,000) with the dashboard's own engine
// (docs/sim.js), then save the averages and ranges to sims.json and queue them for grading against
// the real box scores (sims_log.json, graded by agent/simtrack.py after the games).
const fs = require("fs"), path = require("path");
const ROOT = path.resolve(__dirname, "..");
const DATA = process.env.PERSEVERANCE_DATA || path.join(ROOT, "docs", "data");
const N = +(process.env.SIM_N || 10000);
global.window = global; global.RENDER = {}; global.D = {};
eval(fs.readFileSync(path.join(ROOT, "docs", "sim.js"), "utf8"));
const E = window.simEngine;
const read = (f, d) => { try { return JSON.parse(fs.readFileSync(path.join(DATA, f), "utf8")); } catch (e) { return d; } };
const STATS = ["pts", "reb", "ast", "tpm", "pra", "ra"];
const mean = a => { let t = 0; for (let i = 0; i < a.length; i++) t += a[i]; return t / a.length; };
const qs = a => { const s = Array.from(a).sort((x, y) => x - y); return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].map(q => s[Math.min(s.length - 1, Math.floor(q * s.length))]); };
const r1 = v => Math.round(v * 10) / 10;

(async () => {
  const pj = read("players.json", null);
  if (!pj) { console.log("[sims] no players.json"); return; }
  D.players = pj;                                  // learned sim settings ride along in players.json
  const games = (pj.games || []).filter(g => g.state === "pre");
  const prev = read("sims.json", {});
  // keep games that have already tipped off from earlier runs today; re-sim the rest
  const out = {updated: new Date().toISOString(), date: pj.date, n: N, games: prev.date === pj.date ? (prev.games || {}) : {}};
  const day = read("sims_day.json", {});
  if (day.date !== pj.date) { day.date = pj.date; day.runs = []; }
  const run = {ts: out.updated, games: {}};
  const log = read("sims_log.json", {pending: {}, graded: {}, agg: {}});
  // today's market: game lines from the agent's last scan, prop lines it priced (line history)
  const board = read("board.json", {events: []}), linesDb = read("lines.json", {entries: {}});
  const nick = n => (n || "").split(" ").slice(-1)[0].toLowerCase();
  const evFor = g => (board.events || []).find(e => e.sport === "basketball_nba" && nick(e.home) === nick(g.home_name) && nick(e.away) === nick(g.away_name));
  const frac = (n, f) => { let w = 0, l = 0; for (let i = 0; i < n; i++) { const r = f(i); if (r > 0) w++; else if (r < 0) l++; } return w + l ? r1(w / (w + l) * 1000) / 1000 : null; };
  log.pending[pj.date] = log.pending[pj.date] || {};
  const t0 = Date.now();
  for (const g of games) {
    const gf = read(`games/${g.espn_id}.json`, null);
    if (!gf) continue;
    E.setSeed(parseInt(g.espn_id, 10) % 2147483647);    // same inputs -> same results; changes come from the inputs
    const P = E.calibrate(E.prep(g, gf), 4, 500);
    const M = await E.monteCarlo(P, N);
    let hw = 0, cover = 0, over = 0; const tot = new Int16Array(N), mar = new Int16Array(N);
    const vt = g.vegas && g.vegas.total, sp = P.spreadHome;
    for (let i = 0; i < N; i++) {
      const h = M.team.home[i], a = M.team.away[i];
      if (h > a) hw++; if (sp != null && h + sp > a) cover++; if (vt && h + a > vt) over++;
      tot[i] = h + a; mar[i] = h - a;
    }
    const players = {};
    for (const pid in M.out) {
      const o = M.out[pid], w = M.who[pid];
      const s = {}; STATS.forEach(k => s[k] = [r1(mean(o[k])), ...qs(o[k])]);
      players[pid] = {name: w.name, team: P.teams[w.side].ab, min: r1(mean(o.min)), s};
    }
    const avg = {home: r1(mean(M.team.home)), away: r1(mean(M.team.away))};
    // the single most typical simulated game: closest to the averages on the score and every player's line
    const sd = a => { const m = mean(a); let v = 0; for (let i = 0; i < a.length; i++) v += (a[i] - m) ** 2; return Math.sqrt(v / a.length) || 1; };
    const terms = [[M.team.home, 30], [M.team.away, 30]];
    for (const pid in M.out) { const o = M.out[pid], wt = Math.min(1, mean(o.min) / 36); if (wt < 0.2) continue; ["pts", "reb", "ast"].forEach(k => terms.push([o[k], wt])); }
    const stats_ = terms.map(([a, w]) => [a, w, mean(a), sd(a)]);
    let bi = 0, bd = Infinity;
    for (let i = 0; i < N; i++) {
      let d = 0; for (const [a, w, m, v] of stats_) { const z = (a[i] - m) / v; d += w * z * z; if (d >= bd) break; }
      if (d < bd) { bd = d; bi = i; }
    }
    const typical = {score: {home: M.team.home[bi], away: M.team.away[bi]},
      players: Object.fromEntries(Object.keys(M.out).map(pid => { const o = M.out[pid]; return [pid, [r1(o.min[bi]), o.pts[bi], o.reb[bi], o.ast[bi], o.tpm[bi]]]; }))};
    out.games[g.espn_id] = {at: out.updated, pace: r1(P.pace), model_pace: r1(P.modelPace || P.pace), home: g.home, away: g.away, start: g.start, vegas: g.vegas || null, avg, win_home: r1(hw / N * 100) / 100,
      total: [r1(mean(tot)), ...qs(tot)], margin: r1(mean(mar)), cover_home: sp != null ? r1(cover / N * 100) / 100 : null,
      over: vt ? r1(over / N * 100) / 100 : null, players, typical};
    // the sim's own probability at DraftKings' lines (pushes excluded), next to DraftKings' no-vig price
    const ev = evFor(g), vs = {book: "draftkings", game: [], props: []};
    const dk = (ev && ev.dk) || {};
    for (const [mk, outs] of Object.entries(dk)) {
      const tot = outs.reduce((t, o) => t + 1 / o.price, 0);
      for (const o of outs) {
        let p = null;
        const isHome = nick(o.name) === nick(g.home_name);
        if (mk === "totals" && o.point != null) p = frac(N, i => (M.team.home[i] + M.team.away[i] - o.point) * (o.name === "Over" ? 1 : -1));
        else if (mk === "spreads" && o.point != null) p = frac(N, i => isHome ? M.team.home[i] + o.point - M.team.away[i] : M.team.away[i] + o.point - M.team.home[i]);
        else if (mk === "h2h") p = frac(N, i => isHome ? M.team.home[i] - M.team.away[i] : M.team.away[i] - M.team.home[i]);
        if (p != null) vs.game.push({market: mk, name: o.name, point: o.point, p_sim: p, p_dk: r1(1 / o.price / tot * 1000) / 1000, price: o.price, am: o.am});
      }
    }
    for (const e of Object.values(linesDb.entries || {})) {
      if (e.espn_id !== g.espn_id || e.date !== pj.date || !e.dk || !M.out[e.pid] || !M.out[e.pid][e.stat]) continue;
      const a = M.out[e.pid][e.stat];
      vs.props.push({pid: e.pid, name: e.name, stat: e.stat, line: e.dk.line, mean: r1(mean(a)), p_over_sim: frac(N, i => a[i] - e.dk.line),
                     p_over_dk: e.dk.p, over: e.dk.over, under: e.dk.under});
    }
    out.games[g.espn_id].vs = vs;
    run.games[g.espn_id] = {avg, win_home: out.games[g.espn_id].win_home, total: out.games[g.espn_id].total[0],
      players: Object.fromEntries(Object.entries(players).map(([pid, x]) => [pid, [x.min, ...STATS.map(k => x.s[k][0])]]))};
    // what gets graded tomorrow: averages plus the model's own projections for comparison
    const idx = pj.players || {};
    log.pending[pj.date][g.espn_id] = {start: g.start, home: g.home, away: g.away, avg, win_home: out.games[g.espn_id].win_home,
      vegas: g.vegas ? {spread_home: g.vegas.spread_home, total: g.vegas.total} : null,
      p: Object.fromEntries(Object.entries(players).map(([pid, x]) => [pid, {nm: x.name, min: x.min, st: idx[pid] ? idx[pid].st || 0 : 0, ...Object.fromEntries(STATS.map(k => [k, x.s[k][0]])),
        proj: idx[pid] ? Object.fromEntries(STATS.map(k => [k, idx[pid].proj[k] ? r1(idx[pid].proj[k][0]) : null])) : null}]))};
    console.log(`[sims] ${g.away} @ ${g.home}: ${avg.away}-${avg.home}, home win ${Math.round(hw / N * 100)}%`);
  }
  if (!Object.keys(run.games).length) { console.log("[sims] no upcoming games to simulate"); return; }
  fs.writeFileSync(path.join(DATA, "sims.json"), JSON.stringify(out));
  day.runs = (day.runs || []).concat([run]).slice(-10);
  fs.writeFileSync(path.join(DATA, "sims_day.json"), JSON.stringify(day));
  fs.writeFileSync(path.join(DATA, "sims_log.json"), JSON.stringify(log));
  console.log(`[sims] ${Object.keys(out.games).length} games x ${N} sims in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
})();
