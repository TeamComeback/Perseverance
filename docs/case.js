// The case for and against one prop: everything the dashboard knows about a player at a line, boiled down to
// the few points that matter tonight. Shared by the nightly best-bets run (tools/sim_all.js) and the player card.
// Pure function: buildCase(ctx) -> {score, tier, verdict, for[], against[], ladder, tend, lean}
(() => {
const SV = {pts: r => r[4], reb: r => r[5], ast: r => r[6], tpm: r => r[7], pra: r => r[4] + r[5] + r[6], ra: r => r[5] + r[6]};
const SN = {pts: "points", reb: "rebounds", ast: "assists", tpm: "threes", pra: "PRA", ra: "reb+ast"};
const STEP = {pts: 2, pra: 2, ra: 1, reb: 1, ast: 1, tpm: 1};
const f1 = v => (Math.round(v * 10) / 10).toFixed(1).replace(/\.0$/, "");
const pc = v => Math.round(v * 100) + "%";
const mean = a => a.length ? a.reduce((t, v) => t + v, 0) / a.length : null;
const sig = x => 1 / (1 + Math.exp(-x)), logit = p => { p = Math.min(0.97, Math.max(0.03, p)); return Math.log(p / (1 - p)); };

// conviction model (agent/convmodel.py): the market's price for the lean side, moved by what has actually paid
function convProb(M, pMktSide, cv, under, agree, edge) {
  if (!M || !M.ready || !M.coef || pMktSide == null) return null;
  const c = M.coef, a = Math.abs(cv || 0);
  return sig(logit(pMktSide) + c.b0 + (a >= 2 ? c.cv2 : 0) + (a >= 3 ? c.cv3 : 0) + (a >= 4 ? c.cv4 : 0) + (under ? c.under : 0)
    + (agree ? c.agree : 0) + c.edge * Math.max(-0.25, Math.min(0.25, edge || 0)));
}

function ladder(logs, stat, line, season, opp, home) {
  const v = SV[stat], played = logs.filter(r => r[3] > 0), st = STEP[stat] || 1;
  const lines = [line - st, line, line + st].filter(x => x > 0);
  const win = {L5: played.slice(-5), L10: played.slice(-10), L20: played.slice(-20), Season: played.filter(r => r[12] === season),
    ["vs " + opp]: played.filter(r => r[1] === opp), [home ? "Home" : "Road"]: played.filter(r => r[12] === season && !!r[2] === !!home)};
  const rows = {};
  for (const L of lines) rows[L] = Object.fromEntries(Object.entries(win).map(([k, rs]) => [k, [rs.filter(r => v(r) > L).length, rs.filter(r => v(r) !== L).length]]));
  return {lines, cols: Object.keys(win), rows};
}

function buildCase(x) {
  const {p, stat, side, line} = x, over = side === "Over", sgn = over ? 1 : -1, v = SV[stat], nm = SN[stat] || stat;
  const season = x.season || Math.max(...p.logs.map(r => r[12]), 0), logs = p.logs.filter(r => r[3] > 0);
  const F = [], A = [];
  const add = (arr, w, tag, t) => arr.push({w, tag, t});
  const rate = rs => { const n = rs.filter(r => v(r) !== line).length; return n ? [rs.filter(r => over ? v(r) > line : v(r) < line).length, n] : [0, 0]; };
  const word = over ? "over" : "under", cleared = over ? "Cleared" : "Stayed under";
  const lad = ladder(p.logs, stat, line, season, p.opp, p.home);

  // ---- form at this line
  const l5 = rate(logs.slice(-5)), l10 = rate(logs.slice(-10)), l20 = rate(logs.slice(-20)), ssn = rate(logs.filter(r => r[12] === season));
  const r10 = l10[1] ? l10[0] / l10[1] : null, r5 = l5[1] ? l5[0] / l5[1] : null, rS = ssn[1] >= 4 ? ssn[0] / ssn[1] : null;
  if (r10 != null && l10[1] >= 6) {
    if (r10 >= 0.6) add(F, 2 + (r10 - 0.6) * 5, "recent form", `${cleared} ${line} in ${l10[0]} of his last ${l10[1]}${r5 != null && l5[1] >= 4 ? ` (${l5[0]} of the last ${l5[1]})` : ""}.`);
    else if (r10 <= 0.4) add(A, 2 + (0.4 - r10) * 5, "recent form", `Only ${l10[0]} of his last ${l10[1]} went ${word} ${line}${r5 != null && l5[1] >= 4 ? ` (${l5[0]} of the last ${l5[1]})` : ""}.`);
  }
  if (r5 != null && l5[1] >= 4 && r10 != null && Math.abs(r5 - r10) >= 0.3)
    add(r5 > r10 ? F : A, 1.2, "trend", r5 > r10 ? `Trending the right way: ${l5[0]} of his last ${l5[1]}, better than his last 10.` : `Cooling off: only ${l5[0]} of his last ${l5[1]} after a better run before that.`);
  if (rS != null && ssn[1] >= 8 && r10 != null && (rS >= 0.6) !== (r10 >= 0.6) && Math.abs(rS - r10) >= 0.2)
    add(rS >= 0.6 ? F : A, 1, "season", `Season: ${ssn[0]} of ${ssn[1]} ${word} this number.`);
  // cushion: the next line out
  const st = STEP[stat] || 1, far = over ? line + st : line - st;
  if (far > 0 && logs.length >= 8) {
    const rs = logs.slice(-10), n = rs.length, h = rs.filter(r => over ? v(r) > far : v(r) < far).length;
    if (h / n >= 0.6) add(F, 1.6, "cushion", `Room to spare: he ${over ? "also cleared" : "stayed under even"} ${far} in ${h} of his last ${n}.`);
    const near = rs.filter(r => Math.abs(v(r) - line) <= (stat === "pts" || stat === "pra" ? 2 : 1)).length;
    if (near / n >= 0.5) add(A, 1.5, "thin margin", `Thin margin: ${near} of his last ${n} finished within ${stat === "pts" || stat === "pra" ? 2 : 1} of this number, so it often comes down to one play.`);
  }
  const avg10 = mean(logs.slice(-10).map(v));
  if (avg10 != null && Math.abs(avg10 - line) / Math.max(line, 1) >= 0.12)
    add((avg10 > line) === over ? F : A, 1.3, "average", `Averaging ${f1(avg10)} ${nm} over his last ${Math.min(10, logs.length)}; the line is ${line}.`);

  // ---- tonight's context: only the splits that apply tonight and actually differ
  const base = l20[1] ? l20[0] / l20[1] : null;
  const split = (rs, label, minN) => {
    const r = rate(rs); if (r[1] < minN || base == null) return;
    const d = r[0] / r[1] - base;
    if (Math.abs(d) >= 0.2) add(d > 0 ? F : A, 1 + Math.min(1, r[1] / 10), "tonight's spot", `${label}: ${r[0]} of ${r[1]} ${word} ${line} (${pc(r[0] / r[1])} vs ${pc(base)} lately).`);
  };
  const cur = logs.filter(r => r[12] >= season - 1);
  split(cur.filter(r => !!r[2] === !!p.home).slice(-20), p.home ? "At home" : "On the road", 6);
  split(logs.filter(r => r[1] === p.opp), `Against ${p.opp}`, 3);
  const fl = p.flags || {};
  if (fl.b2b) { const days = new Set(logs.map(r => r[0])); const prev = d => { const t = new Date(d + "T12:00:00Z"); t.setUTCDate(t.getUTCDate() - 1); return t.toISOString().slice(0, 10); };
    split(cur.filter(r => days.has(prev(r[0]))), "On zero days' rest", 4); }
  const outs = (p.mates || []).map((m, i) => ({...m, i})).filter(m => x.players && x.players[m.pid] && x.players[m.pid].out);
  for (const m of outs.slice(0, 2)) split(cur.filter(r => r[15] === p.team && !(r[11] >> m.i & 1)), `Without ${m.name}`, 3);
  const spr = Math.abs(fl.spread || 0);
  if (spr >= 9) split(cur.filter(r => Math.abs(r[13] - r[14]) >= 15), "In blowouts (15+ margin)", 5);

  // ---- how the misses happen
  const rs20 = logs.slice(-20).filter(r => v(r) !== line), miss = rs20.filter(r => over ? v(r) < line : v(r) > line), hits = rs20.filter(r => !miss.includes(r));
  if (miss.length >= 3 && hits.length >= 3) {
    const mm = mean(miss.map(r => r[3])), mh = mean(hits.map(r => r[3]));
    if (over && mh - mm >= 3) add(A, 1.4, "minutes", `It misses when the minutes dip: ${f1(mm)} minutes in his misses vs ${f1(mh)} when he cleared it. He's projected for ${f1(p.minutes)} tonight.`);
    if (!over && mm - mh >= 3) add(A, 1.4, "minutes", `It misses when he plays long: ${f1(mm)} minutes in the games he went over vs ${f1(mh)} otherwise. He's projected for ${f1(p.minutes)} tonight.`);
    if (stat !== "reb" && stat !== "ast" && stat !== "ra") {
      const sm = mean(miss.map(r => r[9])), sh = mean(hits.map(r => r[9]));
      if (Math.abs(sm - sh) >= 3 && (sm < sh) === over) add(A, 0.9, "shot volume", `Depends on shot volume: ${f1(sm)} shots in the misses vs ${f1(sh)} in the hits.`);
    }
  }

  // ---- matchup signals and conviction
  const pj = (p.proj || {})[stat] || {}, lean = pj.proj != null ? (pj.proj > line ? "Over" : "Under") : null;
  if (pj.proj != null && Math.abs(pj.proj - line) / Math.max(line, 1) >= 0.05)
    add((pj.proj > line) === over ? F : A, 1.5, "projection", `Model projects ${f1(pj.proj)} ${nm} against a line of ${line}.`);
  for (const s of pj.signals || []) {
    if (s.v == null || Math.abs(s.v - 1) < 0.04 || !s.txt) continue;
    if (s.k === "chances" && !["reb", "ra", "pra"].includes(stat)) continue;
    add((s.v > 1) === over ? F : A, 1 + Math.min(1.2, Math.abs(s.v - 1) * 8), s.k === "dvp" ? "matchup" : s.k === "sim" ? "similar players" : s.k === "h2h" ? "history" : "matchup", s.txt + ".");
  }
  const cv = (p.cv || {})[stat];
  if (cv && cv[1]) for (const part of cv[1].split(" · ")) {
    const ag = /^against: /.test(part), t = part.replace(/^against: /, "");
    if (/matchup effect|signals agree|priced edge|no edge/.test(t)) continue;
    const forLean = !ag, same = lean === side;
    const m = /Vegas team total/.test(t) ? "Vegas" : /without/.test(t) ? "usage" : /minutes/.test(t) ? "role" : /back-to-back|3rd game/.test(t) ? "rest" : /blowout/.test(t) ? "blowout" : /injury/.test(t) ? "injury" : "context";
    if (m === "role" && !over) continue;                       // secure minutes argue for overs only
    if (m === "rest" || m === "blowout" || m === "injury") { add(over ? A : F, 1.3, m, t[0].toUpperCase() + t.slice(1) + "."); continue; }
    add(forLean === same ? F : A, 1.1, m, t[0].toUpperCase() + t.slice(1) + ".");
  }
  if (cv && cv[2] >= 4 && lean === side) add(F, 1.8, "conviction", `Matchup conviction ${cv[2]} of 5: the checks that have to line up do.`);

  // ---- sim, price and market
  if (x.pSim != null) {
    if (x.pSim >= 0.55) add(F, 1.5 + (x.pSim - 0.55) * 6, "simulation", `Goes ${word} in ${pc(x.pSim)} of 10,000 simulated games${x.simAvg != null ? ` (sim average ${f1(x.simAvg)})` : ""}.`);
    else if (x.pSim < 0.48) add(A, 1.5 + (0.48 - x.pSim) * 6, "simulation", `The simulation disagrees: only ${pc(x.pSim)} of 10,000 games go ${word}.`);
  }
  if (x.pMkt != null) {
    if (x.pMkt <= 0.44) add(A, 1.4, "market", `You're betting against the market: DraftKings' price says this happens ${pc(x.pMkt)} of the time.`);
    if (x.dec && x.dec < 1.62) add(A, 1.2, "price", `Heavy price: it has to win ${pc(1 / x.dec)} of the time just to break even.`);
  }
  if (x.open != null && x.open !== line) {
    const toward = (line > x.open) === over;     // line rising = money on the over
    add(toward ? F : A, 1, "line move", toward ? `The line moved his way (${x.open} to ${line}): money has been on the ${word}.` : `The line moved against it (${x.open} to ${line}), though you now get a ${over ? "lower" : "higher"} number than the opener's would have been.`);
  }
  if (x.tend && x.tend[0] + x.tend[1] >= 3) {
    const [o, u] = x.tend, mine = over ? o : u, n = o + u;
    if (mine / n >= 0.67) add(F, 1.2, "vs his lines", `Has gone ${word} his posted line in ${mine} of ${n} tracked games.`);
    else if (mine / n <= 0.33) add(A, 1.2, "vs his lines", `Has gone ${word} his posted line in only ${mine} of ${n} tracked games.`);
  }
  if (x.underRate && x.underRate.n >= 300 && Math.abs(x.underRate.r - 0.5) >= 0.03)
    add(over === (x.underRate.r < 0.5) ? F : A, 1, "league pattern", `Across the league, unders have won ${pc(x.underRate.r)} of ${x.underRate.n.toLocaleString()} graded props this season${over ? ", so overs start behind" : ""}.`);
  if (x.blow != null && p.minutes >= 26) {
    if (x.blow >= 0.33) add(over ? A : F, 1.3, "blowout", `Blowout risk: ${pc(x.blow)} of sims end 15+ points apart, when starters sit late.`);
  }
  if (x.focus === "skip") add(A, 1.1, "model record", `The market has out-predicted the model on ${nm} this season, so lean on the price here.`);
  if (x.focus === "edge") add(F, 0.9, "model record", `${nm[0].toUpperCase() + nm.slice(1)} is a stat where the model has beaten the market so far.`);
  if (p.pplay != null && p.pplay < 0.93) add(A, 1.6, "availability", `Not certain to play (${pc(p.pplay)}); a late scratch voids it.`);
  for (const w of (p.min_why || []).slice(0, 1)) { const m = /^([+-])\d/.exec(w); if (!m || /learned|added up/.test(w)) continue;
    add((m[1] === "+") === over ? F : A, 0.8, "role tonight", `Minutes ${m[1] === "+" ? "up" : "down"} tonight: ${w}.`); }

  F.sort((a, b) => b.w - a.w); A.sort((a, b) => b.w - a.w);
  // one bullet per tag, strongest first
  const uniq = arr => { const seen = new Set(); return arr.filter(b => !seen.has(b.tag) && seen.add(b.tag)); };
  const Fu = uniq(F).slice(0, 5), Au = uniq(A).slice(0, 4);
  // conviction: the conviction model's probability when there is a price, otherwise the weight of the case
  const agree = r10 != null && l10[1] >= 5 && r10 > 0.5;
  let p_conv = null;
  if (x.pMkt != null && cv && lean) {
    const pl = convProb(x.convModel, lean === side ? x.pMkt : 1 - x.pMkt, cv[2], lean === "Under",
      lean === side ? agree : (r10 != null && r10 < 0.5), lean === side ? (x.pModel != null ? x.pModel - x.pMkt : 0) : (x.pModel != null ? x.pMkt - x.pModel : 0));
    if (pl != null) p_conv = lean === side ? pl : 1 - pl;
  }
  const wf = Fu.reduce((t, b) => t + b.w, 0), wa = Au.reduce((t, b) => t + b.w, 0);
  return {for: Fu.map(b => ({tag: b.tag, t: b.t})), against: Au.map(b => ({tag: b.tag, t: b.t})), ladder: lad, lean, p_conv,
    balance: Math.round((wf - wa) * 10) / 10, form: {l5, l10, l20, season: ssn}, avg10: avg10 == null ? null : Math.round(avg10 * 10) / 10};
}

// conviction 0-100 from the probability edge over the price (0.44 break-even gap = 0, +10 points of edge = 100)
// A play that wins less than half the time is never "strong", whatever the price.
function convScore(p, dec) { const e = p - 1 / dec; return Math.max(5, Math.min(p < 0.5 ? 54 : p < 0.54 ? 74 : 99, Math.round(40 + e * 600))); }
const convTier = s => s >= 75 ? "Strong" : s >= 55 ? "Solid" : s >= 40 ? "Lean" : "Pass";
function verdict(c, score) {
  const f = c.for.slice(0, 2).map(b => b.tag), a = c.against[0];
  return `${convTier(score)}: ${f.length ? f.join(" and ") + (f.length > 1 ? " agree" : " carries it") : "the price is the case"}${a ? `; the main risk is ${a.tag}` : ""}.`;
}
const api = {buildCase, convProb, convScore, convTier, verdict, ladder, SV};
if (typeof window !== "undefined") window.CASE = api;
if (typeof module !== "undefined") module.exports = api;
})();
