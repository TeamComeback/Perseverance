// For every game in progress with a new DraftKings live snapshot, simulate the rest of the game from that
// exact score and clock and save the sim's chance of each live bet (home cover, over, home win).
const fs = require("fs"), path = require("path");
const ROOT = path.resolve(__dirname, "..");
const DATA = process.env.PERSEVERANCE_DATA || path.join(ROOT, "docs", "data");
const N = +(process.env.LIVE_N || 3000);
global.window = global; global.RENDER = {}; global.D = {};
eval(fs.readFileSync(path.join(ROOT, "docs", "sim.js"), "utf8"));
const E = window.simEngine;
const read = (f, d) => { try { return JSON.parse(fs.readFileSync(path.join(DATA, f), "utf8")); } catch (e) { return d; } };
const etDay = t => new Intl.DateTimeFormat("en-CA", {timeZone: "America/New_York"}).format(t);
(async () => {
  const pj = read("players.json", null); if (!pj) return;
  D.players = pj;
  const now = new Date();
  for (const day of new Set([etDay(now), etDay(new Date(now - 8 * 3600e3))])) {
    const f = `dk/${day}.json`, db = read(f, null);
    if (!db) continue;
    let changed = false;
    for (const [eid, G] of Object.entries(db.games || {})) {
      const todo = (G.live || []).filter(s => !s.sim && s.line);
      if (!todo.length || G.final) continue;
      const g = (pj.games || []).find(x => x.espn_id === eid), gf = read(`games/${eid}.json`, null);
      if (!g || !gf) continue;
      E.setSeed(parseInt(eid, 10) % 2147483647);
      const P = E.calibrate(E.prep(g, gf), 3, 300);
      for (const s of todo) {
        const st = {period: s.period, clock: s.clock, away: s.score[0], home: s.score[1]}, L = s.line;
        let cw = 0, cl = 0, ow = 0, ol = 0, hw = 0;
        E.setSeed((parseInt(eid, 10) + s.period * 1000 + Math.round(s.clock)) % 2147483647);
        for (let i = 0; i < N; i++) {
          const r = E.simGame(P, false, st), h = r.score.home, a = r.score.away;
          if (L.sp != null) { const m = h + L.sp - a; if (m > 0) cw++; else if (m < 0) cl++; }
          if (L.tot != null) { const d = h + a - L.tot; if (d > 0) ow++; else if (d < 0) ol++; }
          if (h > a) hw++;
        }
        s.sim = {p_home_cover: cw + cl ? +(cw / (cw + cl)).toFixed(3) : null, p_over: ow + ol ? +(ow / (ow + ol)).toFixed(3) : null,
                 p_home_win: +(hw / N).toFixed(3), n: N};
        // live books keep spreads and totals near 50/50: a line the sim makes 78/22 or worse means ESPN's copy is stale
        const lop = p => p != null && (p < 0.22 || p > 0.78);
        if (lop(s.sim.p_home_cover) || lop(s.sim.p_over)) s.sim.stale = true;
        changed = true;
      }
      console.log(`[live] ${G.away} @ ${G.home}: simmed ${todo.length} snapshot(s)`);
    }
    if (changed) fs.writeFileSync(path.join(DATA, f), JSON.stringify(db));
  }
})();
