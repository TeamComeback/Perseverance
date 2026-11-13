// Our Book: freeze the lines we would post, at fixed times, so they can be graded honestly later.
//
//   FREEZE=9am|noon|3pm|6pm|tip  [GAMES=espn_id,espn_id]  node tools/ourbook.js
//
// Two books, saved side by side and never edited after the freeze:
//   ind  Independent book: the sim run in team-model mode (ratings + tonight's roster + player projections).
//        No market input of any kind: no Vegas spread/total, no implied team totals, no Kalshi. Its only learning
//        comes from its own graded results (ourbook_track.json -> ind_bias), never from same-day prices.
//   inf  Informed book: tonight's sims.json (anchored to DraftKings' line, with the agent's learned corrections).
//        This is the "Our line" the dashboard shows.
// Each freeze also snapshots what DraftKings and Kalshi showed at that moment, and the inputs behind our numbers
// (who was out, projected minutes), so later grading can tell skill from news.
//
// File: docs/data/ourbook/<date>.json   (schema v1, see FILE_DOC below). Pruned after KEEP_DAYS.
const fs = require("fs"), path = require("path");
const ROOT = path.resolve(__dirname, "..");
const DATA = process.env.PERSEVERANCE_DATA || path.join(ROOT, "docs", "data");
const N = +(process.env.OB_N || 4000);
const KEEP_DAYS = 45;
global.window = global; global.RENDER = {}; global.D = {};
eval(fs.readFileSync(path.join(ROOT, "docs", "sim.js"), "utf8"));
const E = window.simEngine;
const read = (f, d) => { try { return JSON.parse(fs.readFileSync(path.join(DATA, f), "utf8")); } catch (e) { return d; } };
const STATS = ["pts", "reb", "ast", "tpm", "pra", "ra"];
const r1 = v => Math.round(v * 10) / 10, pm = p => Math.round(p * 1000);
const FILE_DOC = "v1 · games[eid].f[freeze] = {ind, inf, dk, k, inp}; book game = {sp, pc, spl:[[sp,p‰]], tot, po, totl:[[tot,p‰]], win, mar, mt}; " +
  "props[pid|stat].f[freeze] = {ind:[line,p‰ over,[p‰ at line-2..line+2],mean], inf:same, dk:[line,p‰ over no-vig]|null, k:p‰|null}; p‰ = chance ×1000";

// ---- lines from a count distribution {value: count}
function dist(h, shift) {
  const a = Object.entries(h || {}).map(([k, v]) => [+k - (shift || 0), v]).sort((x, y) => x[0] - y[0]);
  const n = a.reduce((t, x) => t + x[1], 0);
  const over = L => n ? a.reduce((t, x) => t + (x[0] > L ? x[1] : 0), 0) / n : null;
  const mean = n ? a.reduce((t, x) => t + x[0] * x[1], 0) / n : null;
  let c = 0, med = a.length ? a[a.length - 1][0] : 0;
  for (const [x, v] of a) { c += v; if (c >= n / 2) { med = x; break; } }
  return {n, over, mean, med};
}
// the half-point line closest to 50/50
function halfLine(d, allowNeg) {
  let best = null;
  for (let L = Math.floor(d.med) - 1.5; L <= Math.floor(d.med) + 1.5; L += 1) {
    if (!allowNeg && L < 0) continue;
    const p = d.over(L); if (best == null || Math.abs(p - 0.5) < Math.abs(best[1] - 0.5)) best = [L, p];
  }
  return best;
}
function gameBook(mh, th, totShift) {
  if (!mh) return null;
  const m = dist(mh, 0), t = th ? dist(th, totShift || 0) : null, ml = halfLine(m, true);
  const sp = -ml[0], spl = [], totl = [];
  for (let j = -4; j <= 4; j++) spl.push([sp + j, pm(m.over(-(sp + j)))]);           // home covers sp' = margin > -sp'
  const tl = t ? halfLine(t) : null;
  if (tl) for (let j = -6; j <= 6; j++) totl.push([tl[0] + j, pm(t.over(tl[0] + j))]);
  const win = m.over(0) / Math.max(1e-9, m.over(0) + 1 - m.over(-0.5));        // regulation ties are settled in overtime by the sim
  return {sp, pc: pm(ml[1]), spl, tot: tl ? tl[0] : null, po: tl ? pm(tl[1]) : null, totl,
          win: pm(Math.min(1, Math.max(0, win))), mar: r1(m.mean), mt: t ? r1(t.mean) : null};
}
function propBook(h, shift, full) {
  if (!h) return null;
  const d = dist(h, shift); if (!d.n) return null;
  const hl = halfLine(d); if (!hl) return null;
  if (!full) return [hl[0], pm(hl[1]), 0, r1(d.mean)];           // midday freezes: line and chance only (keeps the repo small)
  const lad = []; for (let j = -2; j <= 2; j++) lad.push(hl[0] + j >= 0 ? pm(d.over(hl[0] + j)) : 1000);
  return [hl[0], pm(hl[1]), lad, r1(d.mean)];
}
const novig = (a, b) => a && b ? (1 / a) / (1 / a + 1 / b) : null;
const histOf = (arr, n) => { const d = {}; for (let i = 0; i < n; i++) d[arr[i]] = (d[arr[i]] || 0) + 1; return d; };

(async () => {
  const kind = process.env.FREEZE || "manual", FULL = kind === "9am" || kind === "tip" || kind === "manual";
  const only = (process.env.GAMES || "").split(",").filter(Boolean);
  const pj = read("players.json", null);
  if (!pj || !pj.date) { console.log("[ourbook] no players.json"); return; }
  const date = pj.date, file = path.join(DATA, "ourbook", `${date}.json`);
  const sims = read("sims.json", {}), simsToday = sims.date === date ? sims.games || {} : {};
  const TM = read("team_model.json", null);
  const dk = read(`dk/${date}.json`, {games: {}, props: {}}), K = read(`kalshi/${date}.json`, {games: {}, props: {}});
  const best = read("best.json", {days: {}}), learned = ((best.days || {})[date] || {}).learned || {};
  const track = read("ourbook_track.json", {}), indBias = (track.ind_bias || {});        // the independent book's own learning
  D.players = pj; D.tm = TM;
  const games = (pj.games || []).filter(g => (process.env.OB_ANY || (g.state === "pre" && new Date(g.start) > Date.now())) && (!only.length || only.includes(g.espn_id)));
  if (!games.length) { console.log(`[ourbook] ${kind}: no games still to tip`); return; }
  let db; try { db = JSON.parse(fs.readFileSync(file, "utf8")); } catch (e) { db = null; }
  if (!db || db.date !== date) db = {date, version: 1, doc: FILE_DOC, freezes: [], games: {}, props: {}};
  const at = new Date().toISOString(), fid = kind;
  const P_ = pj.players || {};
  let nInd = 0, nInf = 0, nProps = 0;
  for (const g of games) {
    const gf = read(`games/${g.espn_id}.json`, null);
    const G = db.games[g.espn_id] = db.games[g.espn_id] || {home: g.home, away: g.away, start: g.start, f: {}};
    if (G.f[fid]) continue;                                  // a freeze is never overwritten
    const rec = {at};
    // independent: team-model mode, no market anywhere
    if (gf && TM && TM.tonight && TM.tonight[g.espn_id]) {
      globalThis.PZ_SIM_MODE = "model";
      const gg = {...g, vegas: {...(g.vegas || {}), spread_home: null, total: null, implied: null, src: "none"}};
      E.setSeed((parseInt(g.espn_id, 10) * 7 + 11) % 2147483647);
      const P = E.calibrate(E.prep(gg, {...gf, game: {...(gf.game || {}), vegas: gg.vegas}}), 4, 400);
      const M = await E.monteCarlo(P, N);
      globalThis.PZ_SIM_MODE = null;
      const n = M.team.home.length, mar = new Int16Array(n), tot = new Int16Array(n);
      for (let i = 0; i < n; i++) { mar[i] = M.team.home[i] - M.team.away[i]; tot[i] = M.team.home[i] + M.team.away[i]; }
      rec.ind = gameBook(histOf(mar, n), histOf(tot, n), indBias.total || 0);
      rec.ind.src = {tm_margin: TM.tonight[g.espn_id].margin, tm_total: TM.tonight[g.espn_id].total};
      for (const pid in M.out) {
        const o = M.out[pid]; let mn = 0; for (let i = 0; i < n; i++) mn += o.min[i]; mn /= n;
        if (mn < 15) continue;
        for (const s of STATS) {
          const b = propBook(histOf(o[s], n), indBias[s] || 0, FULL); if (!b) continue;
          const pr = db.props[pid + "|" + s] = db.props[pid + "|" + s] || {name: M.who[pid].name, team: P.teams[M.who[pid].side].ab, eid: g.espn_id, f: {}};
          (pr.f[fid] = pr.f[fid] || {}).ind = b;
        }
      }
      nInd++;
    }
    // informed: tonight's sims (DraftKings-anchored, learned corrections)
    const sg = simsToday[g.espn_id];
    if (sg && sg.mh) {
      rec.inf = gameBook(sg.mh, sg.th, learned.total_bias || 0);
      rec.inf.sims_at = sg.at || sims.updated;
      for (const [pid, sp] of Object.entries(sg.players || {})) {
        if (!sp.h) continue;
        for (const s of STATS) {
          const b = propBook(sp.h[s], (learned.stat_bias || {})[s] || 0, FULL); if (!b) continue;
          const pr = db.props[pid + "|" + s] = db.props[pid + "|" + s] || {name: sp.name, team: sp.team, eid: g.espn_id, f: {}};
          (pr.f[fid] = pr.f[fid] || {}).inf = b;
        }
      }
      nInf++;
    }
    // the market at this moment
    const dg = (dk.games || {})[g.espn_id] || {}, dl = dg.close || dg.open;
    rec.dk = dl ? {sp: dl.sp, pc: pm(novig(dl.sp_h, dl.sp_a) || 0.5), tot: dl.tot, po: pm(novig(dl.ov, dl.un) || 0.5), win: pm(novig(dl.ml_h, dl.ml_a) || 0.5), at: dg.close_at || dk.updated} : null;
    const kg = ((K.games || {})[g.espn_id] || {}).pre;
    rec.k = kg ? {sp: kg.sp, tot: kg.tot, win: kg.win != null ? pm(kg.win) : null, at: kg.at} : null;
    // inputs behind the numbers: who is out, projected minutes of the rotation, the team model's number
    const tm = TM && TM.tonight && TM.tonight[g.espn_id];
    const outs = Object.values(P_).filter(p => p.game === g.espn_id && p.out).map(p => p.pid).sort();
    const mins = {}; Object.values(P_).filter(p => p.game === g.espn_id && !p.out && (p.minutes || 0) >= 12).forEach(p => mins[p.pid] = Math.round(p.minutes));
    const q = Object.values(P_).filter(p => p.game === g.espn_id && /quest|doubt|gtd|day.to.day/i.test(p.status || "")).map(p => p.pid).sort();
    rec.inp = {out: outs, q, min: mins, tm: tm ? {margin: tm.margin, total: tm.total} : null, research_at: pj.updated};
    G.f[fid] = rec;
  }
  // props: DraftKings and Kalshi at this moment, for every player we froze
  for (const [k, pr] of Object.entries(db.props)) {
    const fr = pr.f[fid]; if (!fr || fr.dk !== undefined) continue;
    const d = (dk.props || {})[k];
    fr.dk = d && d.c && d.c[0] != null ? [d.c[0], pm(novig(d.c[1], d.c[2]) || 0.5)] : null;
    const kp = (K.props || {})[k];
    fr.k = kp && kp.p != null && d && d.c && kp.line === d.c[0] ? pm(kp.p) : null;
    nProps++;
  }
  db.freezes.push({id: fid, at, games: games.map(g => g.espn_id), ind: nInd, inf: nInf});
  db.updated = at;
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(db));
  // prune old days
  const cut = new Date(Date.now() - KEEP_DAYS * 864e5).toISOString().slice(0, 10);
  for (const f of fs.readdirSync(path.dirname(file))) if (f.slice(0, 10) < cut) fs.unlinkSync(path.join(path.dirname(file), f));
  console.log(`[ourbook] ${date} ${kind}: ${games.length} games (independent ${nInd}, informed ${nInf}), ${nProps} props frozen`);
})().catch(e => { console.log("[ourbook] failed: " + e.message); process.exitCode = 1; });
