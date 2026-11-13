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
const CASE = require(path.join(ROOT, "docs", "case.js"));
const read = (f, d) => { try { return JSON.parse(fs.readFileSync(path.join(DATA, f), "utf8")); } catch (e) { return d; } };
const STATS = ["pts", "reb", "ast", "tpm", "pra", "ra"];
const mean = a => { let t = 0; for (let i = 0; i < a.length; i++) t += a[i]; return t / a.length; };
const qs = a => { const s = Array.from(a).sort((x, y) => x - y); return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9].map(q => s[Math.min(s.length - 1, Math.floor(q * s.length))]); };
const r1 = v => Math.round(v * 10) / 10;

// ------------------------------------------------------------------ best bets of the day
// Five props and five game lines (spreads/totals) a night, priced at DraftKings from the 10,000-game sims,
// then corrected by what the agent has learned:
//  - sim bias: the graded sims (sims_log.json agg) say how far the sim has run high or low on totals and
//    on each player stat; that average miss is taken off, shrunk toward zero until the sample is large;
//  - model vs market: the scoreboard learns how much the model should count against DraftKings' no-vig
//    price, per stat. Probabilities blend toward the market by that weight (props), or by how the sim's
//    margin/total errors compare with Vegas' (game lines);
//  - conviction (1-5) breaks ties for props.
// Picks for games that have tipped are locked; later runs only re-price games still to play.
// Past days are graded from the DraftKings day files (final scores, players' actual stats).
function bestBetsSetup(pj, simlog) {
  const BEST = read("best.json", {days: {}});
  const sb = read("scoreboard.json", {}), st = read("state.json", {params: {}});
  const agg = simlog.agg || {};
  const wGlobal = (st.params && st.params.prop_model_weight != null) ? st.params.prop_model_weight : 0.4;
  const shrink = (n, k) => n / (n + k);
  const bias = k => { const a = agg[k]; return a && a[0] ? (a[2] / a[0]) * shrink(a[0], 60) : 0; };   // sim minus actual, per game
  const focusOf = s => { const f = sb.focus && sb.focus[s]; if (f) return f.status; const b = sb.by_stat && sb.by_stat[s];
    return !b || b.n < 150 ? "watch" : b.best_w >= 0.25 && b.market.ll - b.best_ll >= 0.001 ? "edge" : b.best_w <= 0.10 ? "skip" : "watch"; };
  const OBA = (read("ourbook_track.json", {}).accuracy) || {};
  const wStat = s => { const ob = OBA[`inf|${s}`]; if (ob && ob.blend && ob.blend.ready) return ob.blend.best_w;     // measured on frozen lines, leave-one-day-out
    const b = sb.by_stat && sb.by_stat[s]; if (!b || !b.n) return wGlobal; return (b.n * b.best_w + 100 * wGlobal) / (b.n + 100); };
  // game lines: weight the sim by how its errors compare with the market's (inverse squared error)
  const wGame = (() => {
    const s_ = agg.margin, v_ = agg.margin_vegas;
    if (!s_ || !v_ || v_[0] < 20 || s_[0] < 20) return 0.35;
    const es = s_[1] / s_[0], ev = v_[1] / v_[0];
    return Math.max(0.1, Math.min(0.6, (ev * ev) / (es * es + ev * ev)));
  })();
  const totBias = bias("total");
  const am = d => d >= 2 ? Math.round((d - 1) * 100) : -Math.round(100 / (d - 1));
  const frac = (n, f) => { let w = 0, l = 0; for (let i = 0; i < n; i++) { const r = f(i); if (r > 0) w++; else if (r < 0) l++; } return w + l ? w / (w + l) : null; };
  const novig = (a, b) => (1 / a) / (1 / a + 1 / b);
  const cands = {props: [], games: []};
  // every priced prop's case is logged (not just the five picks) so the agent can learn which reasons actually pay
  const CLOG = read("case_log.json", {days: {}}), clogDay = {};
  const gLearned = !!(agg.margin && agg.margin_vegas && agg.margin[0] >= 20 && agg.margin_vegas[0] >= 20);
  const learned = {focus: Object.fromEntries(["pts", "reb", "ast", "tpm", "pra", "ra"].map(k => [k, focusOf(k)])), w_props: {}, w_game: Math.round(wGame * 100) / 100, w_game_learned: gLearned, total_bias: r1(totBias), stat_bias: {},
    games_graded: (agg.total || [0])[0], props_scored: (sb.all && sb.all.n) || 0};
  ["pts", "reb", "ast", "tpm", "pra", "ra"].forEach(k => { learned.w_props[k] = Math.round(wStat(k) * 100) / 100; learned.stat_bias[k] = Math.round(bias(k) * 100) / 100; });
  const startOf = {}, NSIM = +(process.env.SIM_N || 10000); (pj.games || []).forEach(g => startOf[g.espn_id] = g);
  // conviction model (agent/convmodel.py), each player's record against his posted lines, and the league's under rate
  const CM = sb.conviction && sb.conviction.ready ? sb.conviction : null;
  const KD = read(`kalshi/${pj.date}.json`, {props: {}, games: {}}), KT = ((read("kalshi_track.json", {}).props || {}).all || {}).disagree || null;
  const kalshiP = (pid, stat, line, side) => { const k = (KD.props || {})[pid + "|" + stat]; return k && k.p != null && k.line === line ? Math.round((side === "Over" ? k.p : 1 - k.p) * 1000) / 1000 : null; };
  const OB = read(`ourbook/${pj.date}.json`, null), OBT = read("ourbook_track.json", {});
  const OB_ORDER = ["tip", "6pm", "3pm", "noon", "9am"];
  const obInd = (pid, stat) => { const pr = OB && OB.props && OB.props[pid + "|" + stat]; if (!pr) return null;
    for (const f of OB_ORDER) if (pr.f[f] && pr.f[f].ind) return {line: pr.f[f].ind[0], at: f}; return null; };
  const obStage = ["early", "late"].reduce((m, w) => Math.max(m, (((OBT.cuts || {})[`ind|props|${w}`]) || {}).stage || 0), 0);
  const obRate = (() => { const c = Object.entries(OBT.cuts || {}).filter(([k]) => k.startsWith("ind|props")).map(([, v]) => v); const n = c.reduce((t, x) => t + x.n, 0), w = c.reduce((t, x) => t + x.toward, 0); return n ? {n, rate: w / n} : null; })();
  const dkRecent = read("dk_recent.json", {}), linesAll = read("lines.json", {entries: {}}).entries || {};
  const tendOf = (pid, stat) => {
    const seen = {}; let o = 0, u = 0;
    for (const r of dkRecent[pid + "|" + stat] || []) if (r[6] === "over" || r[6] === "under") seen[r[0]] = r[6];
    for (const e of Object.values(linesAll)) if (e.pid === pid && e.stat === stat && (e.result === "over" || e.result === "under") && !seen[e.date]) seen[e.date] = e.result;
    for (const v of Object.values(seen)) v === "over" ? o++ : u++;
    return [o, u];
  };
  const TM = read("team_model.json", null);
  learned.conviction = CM ? {n: CM.n, days: CM.days, cv_ll: CM.cv_ll, tiers: CM.tiers, by_cv: CM.by_cv, unders: CM.unders} : null;

  function price(g, M, dkg, dkProps, gf) {
    const N = M.team.home.length, line = dkg && (dkg.close || dkg.open);
    const game = `${g.away} @ ${g.home}`, base = {espn_id: g.espn_id, game, start: g.start, home: g.home, away: g.away,
      home_name: g.home_name, away_name: g.away_name};
    const avgH = mean(M.team.home), avgA = mean(M.team.away);
    let blowN = 0; for (let i = 0; i < N; i++) if (Math.abs(M.team.home[i] - M.team.away[i]) >= 15) blowN++;
    const blow = blowN / N, tm = (TM && TM.tonight && TM.tonight[g.espn_id]) || null, opn = dkg && dkg.open, spots = (g.vegas && g.vegas.spots) || g.spots || null;
    const tmRec = TM && TM.backtest && TM.backtest.all;
    // the case for a spread or total: the sim, the team model (ratings + who is playing), the schedule spot, the line move
    const gameCase = (kind, side, pt, ps, pm, dec) => {
      const F = [], A = [], add = (arr, tag, t) => arr.push({tag, t});
      const home = side === "home", over = side === "Over";
      if (ps >= 0.55) add(F, "simulation", `${kind === "spread" ? "Covers" : "Goes " + side.toLowerCase()} in ${Math.round(ps * 100)}% of 10,000 simulated games.`);
      else if (ps < 0.5) add(A, "simulation", `The simulation leans the other way (${Math.round(ps * 100)}%).`);
      if (tm) {
        if (kind === "spread") { const d = (tm.margin + (home ? pt : -pt)) * (home ? 1 : -1);      // points of cover the team model sees
          if (Math.abs(d) >= 1.5) add(d > 0 ? F : A, "team model", `Team ratings${tm.roster ? " with tonight's lineups" : ""} make it ${tm.margin >= 0 ? g.home : g.away} by ${f1(Math.abs(tm.margin))}, ${f1(Math.abs(d))} ${d > 0 ? "better" : "worse"} than this number.`); }
        else { const d = (tm.total - pt) * (over ? 1 : -1);
          if (Math.abs(d) >= 2.5) add(d > 0 ? F : A, "team model", `Team ratings project ${f1(tm.total)} points, ${f1(Math.abs(d))} ${d > 0 ? "on your side of" : "the wrong side of"} ${pt}.`); }
        for (const o of tm.out || []) if (Math.abs(o.pts) >= 1) { const hurts = (o.team === g.home) !== home; if (kind === "spread") add(hurts ? F : A, "who's out", `${o.team} without ${o.names}: worth about ${f1(Math.abs(o.pts))} points.`); }
      }
      if (kind === "spread" && spots && Math.abs(spots.net_home || 0) >= 0.8) { const good = (spots.net_home > 0) === home;
        add(good ? F : A, "schedule", `Schedule spot favors ${spots.net_home > 0 ? g.home : g.away} by ${f1(Math.abs(spots.net_home))} (rest and travel).`); }
      if (opn) { const o = kind === "spread" ? opn.sp : opn.tot, c = kind === "spread" ? line.sp : line.tot;
        if (o != null && c != null && o !== c) { const toward = kind === "spread" ? ((c < o) === home) : ((c > o) === over);
          add(toward ? F : A, "line move", toward ? `The line has moved this way since it opened (${o} to ${c}): you're with the money, at a worse number.` : `The line has moved against this side since it opened (${o} to ${c}).`); } }
      if (pm <= 0.47) add(A, "market", `DraftKings prices this side at ${Math.round(pm * 100)}%.`);
      if (kind === "total" && over && blow >= 0.4) add(A, "blowout", `${Math.round(blow * 100)}% of sims are blowouts, and benches slow the scoring late.`);
      if (tmRec) { const r = kind === "spread" ? tmRec.ats["0"] : tmRec.ou["0"];
        add(A, "model record", `Game lines are the hardest market to beat: in the backtest our team ratings went ${r.w}-${r.l} against DraftKings' ${kind === "spread" ? "spreads" : "totals"}. Treat this as a lean.`); }
      return {for: F.slice(0, 4), against: A.slice(0, 4)};
    };
    const gFin = (c, kind, side, pt, ps, pm, dec, p) => { const cs = gameCase(kind, side, pt, ps, pm, dec), conv = Math.min(59, CASE.convScore(p, dec));   // game lines top out at Consider until the team scoreboard shows an edge
      Object.assign(c, {conv, tier: CASE.convTier(conv), case: cs, verdict: CASE.verdict(cs, conv)}); return c; };
    if (line && line.sp != null && line.sp_h && line.sp_a) {
      const pSim = frac(N, i => M.team.home[i] + line.sp - M.team.away[i]);         // home covers
      const pMkt = novig(line.sp_h, line.sp_a);
      if (pSim != null) for (const side of ["home", "away"]) {
        const ps = side === "home" ? pSim : 1 - pSim, pm = side === "home" ? pMkt : 1 - pMkt, dec = side === "home" ? line.sp_h : line.sp_a;
        const p = wGame * ps + (1 - wGame) * pm, pt = side === "home" ? line.sp : -line.sp;
        const team = side === "home" ? g.home : g.away;
        cands.games.push(gFin({...base, kind: "spread", side, team, team_name: side === "home" ? g.home_name : g.away_name, point: pt, dec, am: am(dec),
          p: r1(p * 1000) / 1000, p_sim: r1(ps * 1000) / 1000, p_mkt: r1(pm * 1000) / 1000, ev: r1((p * dec - 1) * 1000) / 1000,
          why: `Sim: ${Math.abs(avgH - avgA) < 0.5 ? "dead even" : `${(avgH - avgA) >= 0 ? g.home : g.away} by ${f1(Math.abs(avgH - avgA))}`} on average (${g.away} ${f1(avgA)}, ${g.home} ${f1(avgH)}) · ${team} ${pt > 0 ? "+" : ""}${pt} covers in ${Math.round(ps * 100)}% of sims vs ${Math.round(pm * 100)}% DraftKings`}, "spread", side, pt, ps, pm, dec, p));
      }
    }
    if (line && line.tot != null && line.ov && line.un) {
      const pSim = frac(N, i => M.team.home[i] + M.team.away[i] - totBias - line.tot);
      const pMkt = novig(line.ov, line.un);
      if (pSim != null) for (const side of ["Over", "Under"]) {
        const ps = side === "Over" ? pSim : 1 - pSim, pm = side === "Over" ? pMkt : 1 - pMkt, dec = side === "Over" ? line.ov : line.un;
        const p = wGame * ps + (1 - wGame) * pm;
        cands.games.push(gFin({...base, kind: "total", side, point: line.tot, dec, am: am(dec),
          p: r1(p * 1000) / 1000, p_sim: r1(ps * 1000) / 1000, p_mkt: r1(pm * 1000) / 1000, ev: r1((p * dec - 1) * 1000) / 1000,
          why: `Sim total ${f1(avgH + avgA - totBias)}${Math.abs(totBias) >= 0.5 ? ` (after a learned ${totBias > 0 ? "-" : "+"}${f1(Math.abs(totBias))} correction)` : ""} vs ${line.tot} · ${side.toLowerCase()} in ${Math.round(ps * 100)}% of sims vs ${Math.round(pm * 100)}% DraftKings`}, "total", side, line.tot, ps, pm, dec, p));
      }
    }
    for (const r of Object.values(dkProps)) {
      if (r.espn_id !== g.espn_id || !r.c || r.c[0] == null || !r.c[1] || !r.c[2]) continue;
      const a = M.out[r.pid] && M.out[r.pid][r.stat]; if (!a) continue;
      const L = r.c[0], b = bias(r.stat), w = wStat(r.stat);
      const foc = focusOf(r.stat);
      if (foc === "skip" && !(CM && (r.cv || 0) >= 3)) continue;   // the market beats the model in this stat: only strong conviction gets through
      const gp = gf && gf.players && gf.players[r.pid];
      const pSim = frac(N, i => a[i] - b - L); if (pSim == null) continue;
      const pMkt = novig(r.c[1], r.c[2]), simAvg = mean(a) - b;
      // guards: only players with a real role tonight, and no line the market has clearly re-priced
      // (a big gap between the sim and DraftKings usually means news the inputs don't have yet)
      const pp = (pj.players || {})[r.pid] || {}, simMin = mean(M.out[r.pid].min);
      if (simMin < 18 || pp.out || /quest|doubt|gtd|day.to.day/i.test(pp.status || "")) continue;
      const gap = Math.abs(simAvg - L);
      if (gap >= 2.5 && gap / Math.max(L, 1) > 0.3) continue;
      if (r.proj != null && Math.abs(simAvg - r.proj) > Math.max(2, 0.25 * r.proj)) continue;
      for (const side of ["Over", "Under"]) {
        const ps = side === "Over" ? pSim : 1 - pSim, pm = side === "Over" ? pMkt : 1 - pMkt, dec = side === "Over" ? r.c[1] : r.c[2];
        const pb = w * ps + (1 - w) * pm;
        // the case: form at this line and the next ones out, tonight's context, how the misses happen, matchup, sim, price
        let cs = null;
        if (gp && gp.logs) try {
          cs = CASE.buildCase({p: gp, players: gf.players, stat: r.stat, side, line: L, dec, pMkt: pm, pSim: ps, simAvg, open: r.o ? r.o[0] : null,
            focus: foc, blow, tend: tendOf(r.pid, r.stat), underRate: CM && CM.unders, convModel: CM, pKalshi: kalshiP(r.pid, r.stat, L, side), kalshiTrack: KT, pModel: r.pm == null ? null : (side === "Over" ? r.pm : 1 - r.pm)});
        } catch (e) { console.log("[best] case failed for " + r.name + ": " + e.message); }
        // two independent reads, averaged: the conviction model (what has actually paid) and the sim blended with the price
        const p = cs && cs.p_conv != null ? (cs.p_conv + pb) / 2 : pb;
        // Our Book (stage 1: tracked on every pick; stage 2, once the independent book passes its gates: a reason and a nudge)
        const ob = obInd(r.pid, r.stat), obGap = ob ? ob.line - L : null, obFor = obGap != null && Math.abs(obGap) >= 1 ? (obGap > 0) === (side === "Over") : null;
        if (cs && obStage >= 2 && obFor != null) (obFor ? cs.for : cs.against).unshift({tag: "our book", t: `Our independent book (no market input) posts ${ob.line}, ${f1(Math.abs(obGap))} ${obGap > 0 ? "above" : "below"} DraftKings${obRate ? `; when it disagrees, DraftKings' line has moved its way ${Math.round(obRate.rate * 100)}% of the time (${obRate.n} lines)` : ""}.`});
        const conv = Math.max(1, Math.min(99, CASE.convScore(p, dec) + (obStage >= 2 && obFor != null ? (obFor ? 3 : -3) : 0)));
        cands.props.push({...base, kind: "prop", conv, ob_ind: ob ? ob.line : null, ob_gap: obGap != null ? r1(obGap) : null, ob_stage: obStage, tier: CASE.convTier(conv), p_conv: cs && cs.p_conv != null ? r1(cs.p_conv * 1000) / 1000 : null,
          case: cs ? {for: cs.for, against: cs.against} : null, ladder: cs ? cs.ladder : null, verdict: cs ? CASE.verdict(cs, conv) : null, avg10: cs ? cs.avg10 : null, pid: r.pid, name: r.name, team: r.team, opp: r.opp, stat: r.stat, side, point: L, dec, am: am(dec),
          p: r1(p * 1000) / 1000, p_sim: r1(ps * 1000) / 1000, p_mkt: r1(pm * 1000) / 1000, p_kalshi: kalshiP(r.pid, r.stat, L, side), ev: r1((p * dec - 1) * 1000) / 1000,
          cv: r.cv || null, lean: r.lean || null, proj: r.proj != null ? r1(r.proj) : null, sim: r1(simAvg), open: r.o ? r.o[0] : null,
          why: `Sim avg ${f1(simAvg)} vs ${L} · ${side.toLowerCase()} in ${Math.round(ps * 100)}% of sims vs ${Math.round(pm * 100)}% DraftKings · model counts ${Math.round(w * 100)}% (learned)`});
      }
      const two = cands.props.slice(-2).filter(x => x.pid === r.pid && x.stat === r.stat && x.case);
      if (two.length === 2) { const b = two[0].conv >= two[1].conv ? two[0] : two[1];
        clogDay[r.pid + "|" + r.stat] = [g.espn_id, b.side, L, b.dec, b.conv, b.case.for.map(x => x.tag), b.case.against.map(x => x.tag), null]; }
    }
  }

  // ------------------------------------------------------------------ ranked board (ranked.json, ranked_log.json)
  // Every priced line, not just the five picks: one side per player-stat (and per game line), conviction first, the
  // raw sim win rate breaks ties. Prices shorter than -200 stay in the file but get no rank: they win often, so they
  // would crowd the top and flatter the board's record. Each night's order is logged and graded, so we can check
  // whether the top of the board actually beats the bottom.
  const FLOOR_DEC = 1.5;   // -200
  const RANK_BUCKETS = [[1, 10], [11, 30], [31, 1e9]];
  function rankedBoard(date, updated) {
    const prev = read("ranked.json", {}), LOG = read("ranked_log.json", {days: {}});
    const better = (x, b) => !b || x.conv > b.conv || (x.conv === b.conv && x.p_sim > b.p_sim);
    const bestOf = (list, keyF) => { const by = {}; for (const x of list) { const k = keyF(x); if (better(x, by[k])) by[k] = x; } return Object.values(by); };
    const r3 = v => v == null ? null : Math.round(v * 1000) / 1000;
    const slim = x => ({k: x.kind === "prop" ? x.pid + "|" + x.stat : "g|" + x.espn_id + "|" + x.kind, kind: x.kind, espn_id: x.espn_id,
      ...(x.kind === "prop" ? {pid: x.pid, name: x.name, team: x.team, opp: x.opp, stat: x.stat, sim: x.sim, ob_ind: x.ob_ind} : {game: x.game, team: x.team || null}),
      side: x.side, point: x.point, dec: x.dec, am: x.am, conv: x.conv, tier: x.tier, p: r3(x.p), p_sim: r3(x.p_sim), p_mkt: r3(x.p_mkt), p_kalshi: r3(x.p_kalshi), ev: r3(x.ev)});
    const tipped = id => { const g = startOf[id]; return g && g.state !== "pre"; };
    const build = (list, keyF, old) => {
      const rows = bestOf(list.filter(x => !tipped(x.espn_id)), keyF).map(slim), have = new Set(rows.map(r => r.k));
      if (prev.date === date) for (const r of old || []) if (tipped(r.espn_id) && !have.has(r.k)) rows.push(r);   // tipped games keep their last rank
      rows.sort((a, b) => (b.conv - a.conv) || (b.p_sim - a.p_sim));
      let i = 0; rows.forEach(r => { r.rank = r.dec >= FLOOR_DEC ? ++i : null; });
      return rows;
    };
    const props = build(cands.props, x => x.pid + "|" + x.stat, prev.props), games = build(cands.games, x => x.espn_id + "|" + x.kind, prev.games);
    // the night's order, for grading: [side, point, dec, conv, p_sim, p, rank, result]
    const today = {};
    for (const r of [...props, ...games]) today[r.k] = [r.side, r.point, r.dec, r.conv, r.p_sim, r.p, r.rank, null];
    LOG.days[date] = today;
    for (const [d, rows] of Object.entries(LOG.days)) {
      if (d >= date) continue;
      let dk = null;
      for (const [k, row] of Object.entries(rows)) {
        if (row[7]) continue;
        dk = dk || read(`dk/${d}.json`, {props: {}, games: {}});
        let diff = null;
        if (k.startsWith("g|")) {
          const [, gid, kind] = k.split("|"), g = (dk.games || {})[gid]; if (!g || !g.final) continue;
          const [h, a] = g.final;
          diff = kind === "total" ? (row[0] === "Over" ? h + a - row[1] : row[1] - h - a) : (row[0] === "home" ? h + row[1] - a : a + row[1] - h);
        } else {
          const r = (dk.props || {})[k]; if (!r) continue;
          if (r.res === "void") { row[7] = "v"; continue; }
          if (r.actual == null) continue;
          diff = row[0] === "Over" ? r.actual - row[1] : row[1] - r.actual;
        }
        row[7] = diff > 0 ? "w" : diff < 0 ? "l" : "p";
      }
    }
    LOG.days = Object.fromEntries(Object.keys(LOG.days).sort().slice(-45).map(d => [d, LOG.days[d]]));
    fs.writeFileSync(path.join(DATA, "ranked_log.json"), JSON.stringify(LOG));
    // how each slice of the board has done: wins, losses, units at 1u flat
    const perf = {props: {}, games: {}, days: 0, from: null, unranked: {w: 0, l: 0}};
    for (const [lo, hi] of RANK_BUCKETS) for (const k of ["props", "games"]) perf[k][hi > 1e8 ? `${lo}+` : `${lo}-${hi}`] = {w: 0, l: 0, u: 0};
    for (const [d, rows] of Object.entries(LOG.days)) {
      let any = false;
      for (const [k, row] of Object.entries(rows)) {
        if (row[7] !== "w" && row[7] !== "l") continue;
        any = true;
        if (row[6] == null) { perf.unranked[row[7]]++; continue; }
        const [lo, hi] = RANK_BUCKETS.find(([a, b]) => row[6] >= a && row[6] <= b);
        const s = perf[k.startsWith("g|") ? "games" : "props"][hi > 1e8 ? `${lo}+` : `${lo}-${hi}`];
        s[row[7]]++; s.u = Math.round((s.u + (row[7] === "w" ? row[2] - 1 : -1)) * 100) / 100;
      }
      if (any) { perf.days++; perf.from = perf.from && perf.from < d ? perf.from : d; }
    }
    fs.writeFileSync(path.join(DATA, "ranked.json"), JSON.stringify({date, updated, floor_am: -200, props, games, perf}));
    console.log(`[ranked] ${props.filter(r => r.rank).length} props + ${games.filter(r => r.rank).length} game lines ranked (${props.filter(r => !r.rank).length + games.filter(r => !r.rank).length} shorter than -200 left unranked)`);
  }

  function gradeDay(date, day) {
    const dk = read(`dk/${date}.json`, null); if (!dk) return;
    for (const b of [...(day.props || []), ...(day.games || []), ...(day.dropped || [])]) {
      if (b.res) continue;
      let d = null, actual = null;
      if (b.kind === "prop") {
        const r = (dk.props || {})[b.pid + "|" + b.stat]; if (!r || r.actual == null) { if (r && r.res === "void") b.res = "void"; continue; }
        actual = r.actual; d = b.side === "Over" ? actual - b.point : b.point - actual;
      } else {
        const g = (dk.games || {})[b.espn_id]; if (!g || !g.final) continue;
        const [h, a] = g.final; actual = b.kind === "total" ? h + a : h - a;
        d = b.kind === "total" ? (b.side === "Over" ? h + a - b.point : b.point - h - a) : (b.side === "home" ? h + b.point - a : a + b.point - h);
      }
      b.actual = actual; b.res = d > 0 ? "win" : d < 0 ? "loss" : "push";
      b.units = b.res === "win" ? r1((b.dec - 1) * 100) / 100 : b.res === "loss" ? -1 : 0;
    }
  }

  function finish() {
    const date = pj.date, now = Date.now();
    const old = BEST.days[date] || {props: [], games: []};
    const tipped = x => { const g = startOf[x.espn_id]; return g ? g.state !== "pre" : new Date(x.start).getTime() <= now; };
    const pick = (list, lockedOld, nMax, perGame, perPlayer) => {
      const locked = lockedOld.filter(tipped);
      const chosen = [...locked], usedGame = {}, usedPlayer = new Set();
      chosen.forEach(x => { usedGame[x.espn_id] = (usedGame[x.espn_id] || 0) + 1; if (x.pid) usedPlayer.add(x.pid); });
      const score = x => x.conv != null ? x.conv + x.ev : 100 * x.ev;          // conviction first; expected value breaks ties
      for (const x of list.filter(x => !tipped(x)).sort((a, b) => score(b) - score(a))) {
        if (chosen.length >= nMax) break;
        if ((usedGame[x.espn_id] || 0) >= perGame) continue;
        if (perPlayer && usedPlayer.has(x.pid)) continue;
        if (chosen.some(c => c.espn_id === x.espn_id && c.kind === x.kind && (c.pid || "") === (x.pid || "") && (c.stat || "") === (x.stat || ""))) continue;
        chosen.push(x); usedGame[x.espn_id] = (usedGame[x.espn_id] || 0) + 1; if (x.pid) usedPlayer.add(x.pid);
      }
      return chosen.sort((a, b) => score(b) - score(a)).map(x => ({...x, plus: x.ev > 0}));
    };
    const day = {updated: new Date().toISOString(), why: process.env.SIM_REASON || "scheduled refresh", learned,
      props: pick(cands.props, old.props || [], 5, 2, true),
      games: pick(cands.games, old.games || [], 5, 1, false)};
    // A pick is never erased silently: one that drops out before tip-off stays listed with the reason, and is graded
    // like the rest (record.dropped), so refreshing can't flatter the record.
    const keyOf = x => [x.kind, x.espn_id, x.pid || "", x.stat || "", x.side].join("|");
    day.dropped = (old.dropped || []).slice();
    const now_ = new Set([...day.props, ...day.games].map(keyOf)), seenD = new Set(day.dropped.map(keyOf));
    for (const x of [...(old.props || []), ...(old.games || [])]) {
      const k = keyOf(x); if (now_.has(k) || seenD.has(k)) continue;
      const c = [...cands.props, ...cands.games].find(y => keyOf(y) === k);
      const why = !c ? "no longer passes the checks (minutes, injury tag, or the sim and the line too far apart)"
        : c.point !== x.point ? `line moved ${x.point} → ${c.point}` : c.conv < x.conv ? `confidence fell ${x.conv} → ${c.conv}` : "other plays ranked higher";
      day.dropped.push({...x, dropped: day.updated, drop_why: why});
    }
    BEST.days[date] = day;
    // grade earlier days, keep 60 days, and keep a running record by type
    for (const [d, x] of Object.entries(BEST.days)) if (d < date) gradeDay(d, x);
    const keep = Object.keys(BEST.days).sort().slice(-60);
    BEST.days = Object.fromEntries(keep.map(d => [d, BEST.days[d]]));
    const z = () => ({w: 0, l: 0, p: 0, u: 0});
    const rec = {props: z(), games: z(), plus: z(), tiers: {Confident: z(), Consider: z(), Pass: z()}, dropped: z()};
    for (const x of Object.values(BEST.days)) for (const b of x.dropped || []) {
      if (!["win", "loss", "push"].includes(b.res)) continue;
      const r = rec.dropped; r[b.res[0] === "w" ? "w" : b.res[0] === "l" ? "l" : "p"]++; r.u = r1((r.u + (b.units || 0)) * 100) / 100;
    }
    for (const x of Object.values(BEST.days)) for (const k of ["props", "games"]) for (const b of x[k] || []) {
      if (!["win", "loss", "push"].includes(b.res)) continue;
      for (const r of [rec[k], ...(b.plus ? [rec.plus] : []), ...(b.conv != null ? [rec.tiers[CASE.convTier(b.conv)]] : [])]) { r[b.res[0] === "w" ? "w" : b.res[0] === "l" ? "l" : "p"]++; r.u = r1((r.u + (b.units || 0)) * 100) / 100; }
    }
    // case log: keep tipped games' rows, replace the rest, grade earlier days, then count how each reason has done
    const prevRows = CLOG.days[date] || {};
    for (const [k, row] of Object.entries(prevRows)) { const g = startOf[row[0]]; if (!(k in clogDay) && g && g.state !== "pre") clogDay[k] = row; }
    CLOG.days[date] = clogDay;
    for (const [d, rows] of Object.entries(CLOG.days)) {
      if (d >= date) continue;
      let dk = null;
      for (const [k, row] of Object.entries(rows)) {
        if (row[7]) continue;
        dk = dk || read(`dk/${d}.json`, {props: {}});
        const r = (dk.props || {})[k]; if (!r || r.actual == null) continue;
        row[7] = r.actual === row[2] ? "p" : (r.actual > row[2]) === (row[1] === "Over") ? "w" : "l";
      }
    }
    CLOG.days = Object.fromEntries(Object.keys(CLOG.days).sort().slice(-30).map(d => [d, CLOG.days[d]]));
    fs.writeFileSync(path.join(DATA, "case_log.json"), JSON.stringify(CLOG));
    const pat = {n: 0, tags: {}, conv: {Confident: [0, 0], Consider: [0, 0], Pass: [0, 0]}, from: null};
    for (const [d, rows] of Object.entries(CLOG.days)) for (const row of Object.values(rows)) {
      if (row[7] !== "w" && row[7] !== "l") continue;
      const win = row[7] === "w" ? 0 : 1; pat.n++; pat.from = pat.from || d;
      pat.conv[CASE.convTier(row[4])][win]++;
      for (const t of row[5]) { (pat.tags[t] = pat.tags[t] || {f: [0, 0], a: [0, 0]}).f[win]++; }
      for (const t of row[6]) { (pat.tags[t] = pat.tags[t] || {f: [0, 0], a: [0, 0]}).a[win]++; }
    }
    BEST.patterns = pat;
    try { rankedBoard(date, day.updated); } catch (e) { console.log("[ranked] failed: " + e.message); }
    BEST.record = rec; BEST.updated = day.updated; BEST.date = date;
    fs.writeFileSync(path.join(DATA, "best.json"), JSON.stringify(BEST));
    console.log(`[best] ${day.props.length} props + ${day.games.length} game lines (${[...day.props, ...day.games].filter(x => x.plus).length} priced +EV); weights: props ${JSON.stringify(learned.w_props)}, games ${learned.w_game}, total bias ${learned.total_bias}`);
  }
  return {price, finish};
}
const f1 = v => (Math.round(v * 10) / 10).toFixed(1);
// How tonight's outcomes move together, for pricing combos and same-game parlays later (Our Book step 4, not used yet):
// correlations between the game margin, the total, and points / rebounds / assists of each team's top 4 players by minutes.
// Saved as labels + the upper triangle (row by row), two decimals. ~3 KB a game.
function corrTable(M, mar, tot, n) {
  const cols = [["margin", mar], ["total", tot]];
  for (const side of ["home", "away"]) {
    const ids = Object.keys(M.out).filter(pid => M.who[pid].side === side).map(pid => [pid, mean(M.out[pid].min)]).sort((a, b) => b[1] - a[1]).slice(0, 4);
    for (const [pid] of ids) for (const k of ["pts", "reb", "ast"]) cols.push([pid + "|" + k, M.out[pid][k]]);
  }
  const st = cols.map(([, a]) => { const m = mean(a); let v = 0; for (let i = 0; i < n; i++) v += (a[i] - m) ** 2; return [m, Math.sqrt(v / n) || 1]; });
  const tri = [];
  for (let i = 0; i < cols.length; i++) for (let j = i + 1; j < cols.length; j++) {
    const a = cols[i][1], b = cols[j][1], [ma, sa] = st[i], [mb, sb] = st[j]; let c = 0;
    for (let k = 0; k < n; k++) c += (a[k] - ma) * (b[k] - mb);
    tri.push(Math.round(c / n / sa / sb * 100) / 100);
  }
  return {cols: cols.map(c => c[0]), tri};
}

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
  // why this run happened (set by the agent), and SIM_TEST=1 keeps test runs out of the day's log
  const TEST = !!process.env.SIM_TEST;
  const run = {ts: out.updated, why: process.env.SIM_REASON || "scheduled refresh", games: {}};
  const lastRun = (day.runs || []).slice(-1)[0] || null;
  const log = read("sims_log.json", {pending: {}, graded: {}, agg: {}});
  // today's market: game lines from the agent's last scan, prop lines it priced (line history)
  const board = read("board.json", {events: []}), linesDb = read("lines.json", {entries: {}});
  const nick = n => (n || "").split(" ").slice(-1)[0].toLowerCase();
  const evFor = g => (board.events || []).find(e => e.sport === "basketball_nba" && nick(e.home) === nick(g.home_name) && nick(e.away) === nick(g.away_name));
  const frac = (n, f) => { let w = 0, l = 0; for (let i = 0; i < n; i++) { const r = f(i); if (r > 0) w++; else if (r < 0) l++; } return w + l ? r1(w / (w + l) * 1000) / 1000 : null; };
  log.pending[pj.date] = log.pending[pj.date] || {};
  const dkDay = read(`dk/${pj.date}.json`, {games: {}, props: {}});
  const BB = bestBetsSetup(pj, log);
  const t0 = Date.now();
  for (const g of games) {
    const gf = read(`games/${g.espn_id}.json`, null);
    if (!gf) continue;
    // no paid-board line for this game: anchor to DraftKings' free line instead of the model alone
    const dkg = (dkDay.games || {})[g.espn_id], dkl = dkg && (dkg.close || dkg.open);
    if (dkl && dkl.sp != null && dkl.tot != null && !(g.vegas && g.vegas.spread_home != null && g.vegas.total)) {
      g.vegas = {...(g.vegas || {}), spread_home: dkl.sp, total: dkl.tot, src: "draftkings",
                 implied: {home: r1(dkl.tot / 2 - dkl.sp / 2), away: r1(dkl.tot / 2 + dkl.sp / 2)}};
      gf.game = {...(gf.game || {}), vegas: g.vegas};
    }
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
      // full count distributions for rotation players, so the phone can price any line exactly (Best Bets re-price)
      if (mean(o.min) >= 15) { const h = {}; STATS.forEach(k => { const d = {}; const a = o[k]; for (let i = 0; i < N; i++) d[a[i]] = (d[a[i]] || 0) + 1; h[k] = d; }); players[pid].h = h; }
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
    // full margin and total distributions, so the dashboard can show how often each side covers any line
    const mh = {}, th = {};
    for (let i = 0; i < N; i++) { mh[mar[i]] = (mh[mar[i]] || 0) + 1; th[tot[i]] = (th[tot[i]] || 0) + 1; }
    out.games[g.espn_id] = {mh, th, at: out.updated, pace: r1(P.pace), model_pace: r1(P.modelPace || P.pace), home: g.home, away: g.away, start: g.start, vegas: g.vegas || null, avg, win_home: r1(hw / N * 100) / 100,
      total: [r1(mean(tot)), ...qs(tot)], margin: r1(mean(mar)), cover_home: sp != null ? r1(cover / N * 100) / 100 : null,
      over: vt ? r1(over / N * 100) / 100 : null, players, typical, corr: corrTable(M, mar, tot, N)};
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
    BB.price(g, M, dkg, dkDay.props || {}, gf);
    run.games[g.espn_id] = {avg, win_home: out.games[g.espn_id].win_home, total: out.games[g.espn_id].total[0],
      vegas: g.vegas ? {sp: g.vegas.spread_home, tot: g.vegas.total} : null,
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
  try { BB.finish(); } catch (e) { console.log("[best] failed: " + e.message); }
  // what changed since the last run, per game: the line, who's playing (minutes), the projected score
  for (const [gid, gr] of Object.entries(run.games)) {
    const pg = lastRun && lastRun.games && lastRun.games[gid]; if (!pg) { gr.changes = ["first run of the day"]; continue; }
    const ch = [];
    if (gr.vegas && pg.vegas) {
      if (gr.vegas.sp != null && pg.vegas.sp != null && gr.vegas.sp !== pg.vegas.sp) ch.push(`spread ${pg.vegas.sp > 0 ? "+" : ""}${pg.vegas.sp} → ${gr.vegas.sp > 0 ? "+" : ""}${gr.vegas.sp}`);
      if (gr.vegas.tot != null && pg.vegas.tot != null && gr.vegas.tot !== pg.vegas.tot) ch.push(`total ${pg.vegas.tot} → ${gr.vegas.tot}`);
    }
    const mv = [];
    for (const pid of new Set([...Object.keys(gr.players), ...Object.keys(pg.players || {})])) {
      const a = (pg.players[pid] || [0])[0], b = (gr.players[pid] || [0])[0], nm = ((out.games[gid] || {}).players[pid] || {}).name || pid;
      if (a >= 10 && b < 3) mv.push([99, `${nm.split(" ").slice(-1)[0]} out`]);
      else if (b >= 10 && a < 3) mv.push([98, `${nm.split(" ").slice(-1)[0]} in`]);
      else if (Math.abs(b - a) >= 4) mv.push([Math.abs(b - a), `${nm.split(" ").slice(-1)[0]} ${b > a ? "+" : ""}${r1(b - a)} min`]);
    }
    mv.sort((x, y) => y[0] - x[0]).slice(0, 3).forEach(x => ch.push(x[1]));
    const dt = gr.total - pg.total; if (Math.abs(dt) >= 1) ch.push(`sim total ${dt > 0 ? "+" : ""}${r1(dt)}`);
    gr.changes = ch.length ? ch : ["no change"];
  }
  if (TEST) { console.log("[sims] test run: day log and grading queue left alone"); return; }
  day.runs = (day.runs || []).concat([run]).slice(-10);
  fs.writeFileSync(path.join(DATA, "sims_day.json"), JSON.stringify(day));
  fs.writeFileSync(path.join(DATA, "sims_log.json"), JSON.stringify(log));
  console.log(`[sims] ${Object.keys(out.games).length} games x ${N} sims in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
})();
