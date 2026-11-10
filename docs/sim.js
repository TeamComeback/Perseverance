// =====================================================================================
// Perseverance · Game simulator
// A possession-by-possession NBA game simulator calibrated to the matchup model's projections.
//  - "Watch" plays one simulated game on a tilted court with a live box score.
//  - "Run 5,000 sims" plays the game thousands of times for each player's full range of outcomes.
// Everything runs on the phone; inputs come from players.json + the game's detail file.
// =====================================================================================
(function () {
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// random numbers: Math.random by default; the nightly job seeds it per game so identical inputs give identical
// results, and any change between runs comes from the inputs (injuries, lineups, minutes), not luck.
let rnd = Math.random;
function setSeed(seed) {
  if (seed == null) { rnd = Math.random; return; }
  let a = seed >>> 0;
  rnd = () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function gauss() { let u = 0, v = 0; while (!u) u = rnd(); while (!v) v = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
function pick(items, w) {
  let t = 0; for (let i = 0; i < items.length; i++) t += w(items[i]);
  let r = rnd() * t;
  for (let i = 0; i < items.length; i++) { r -= w(items[i]); if (r <= 0) return items[i]; }
  return items[items.length - 1];
}
const Phi = z => { const t = 1 / (1 + 0.2316419 * Math.abs(z)), d = 0.3989423 * Math.exp(-z * z / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return z > 0 ? 1 - p : p; };
const SUF = /\s+(Jr\.?|Sr\.?|II|III|IV)(?=\s|$)/g;
const initials = n => { const w = n.replace(SUF, "").trim().split(/\s+/); return (w[0][0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); };
const lastName = n => { const w = n.replace(SUF, "").trim().split(/\s+/); return w.length > 1 ? w.slice(1).join(" ") : w[0]; };

// ------------------------------------------------------------------ inputs
// Per player: projected minutes and stat targets from the matchup model, shooting shape from his logs.
function prep(g, gf) {
  const P = {pace: (g.model && g.model.pace) || 99, spreadHome: g.vegas && g.vegas.spread_home != null ? g.vegas.spread_home : (g.model ? g.model.spread_home : 0),
             total: g.vegas && g.vegas.total, g, teams: {}};
  // A higher Vegas total than the model expects mostly means a faster game: about 60% of the gap goes to
  // pace (more possessions, so more shots, rebounds and assists for everyone), the rest to efficiency.
  const mt = g.model && g.model.total, vt = g.vegas && g.vegas.total;
  const share = (typeof D !== "undefined" && D.players && D.players.tuning && D.players.tuning.pace_share) || 0.6;
  P.paceK = mt && vt ? clamp(Math.pow(vt / mt, share), 0.92, 1.08) : 1;
  P.modelPace = P.pace; P.pace = P.pace * P.paceK;
  for (const side of ["home", "away"]) {
    const ab = g[side];
    // your lineup (Sim tab → Lineup) wins over the last refresh: players you rule out are dropped, players you clear
    // come back at their usual minutes, and the five you mark as starters start
    const ov = (typeof window !== "undefined" && typeof window.simOverride === "function") ? window.simOverride(g.espn_id) : null;
    const isIn = p => ov && ov.in && ov.in.includes(p.pid), isOut = p => ov && ov.out && ov.out.includes(p.pid);
    const stSet = ov && ov.st && ov.st[side] && ov.st[side].length ? ov.st[side] : null;
    const minsOf = p => p.minutes > 0 ? p.minutes : isIn(p) ? Math.max(p.min_base || 0, 16) : 0;
    const isSt = p => stSet ? stSet.includes(p.pid) : !!(p.rw && p.rw.starter);
    let ps = Object.values(gf.players || {}).filter(p => p.team === ab && !isOut(p) && (!p.out || isIn(p)) && minsOf(p) > 0);
    ps.sort((a, b) => (isSt(b) ? 100 : 0) + minsOf(b) - ((isSt(a) ? 100 : 0) + minsOf(a)));
    ps = ps.slice(0, 13);
    const rows = ps.map((p, i) => {
      // recent games count more (0.96 per game back, the same decay the projections use) and last season
      // counts less, so early in a season the rates lean on last year without ignoring the hot start
      const L = (p.logs || []).filter(r => r[3] > 0).slice(-80);
      const cur = L.reduce((m, r) => Math.max(m, r[12] || 0), 0);
      const wt = L.map((r, k) => Math.pow(0.96, L.length - 1 - k) * ((r[12] || cur) === cur ? 1 : 0.45));
      const sum = k => L.reduce((t, r, j) => t + wt[j] * (r[k] || 0), 0);
      const hasFt = L.some(r => r.length > 16);
      const mins = Math.max(1, sum(3)), pts = sum(4), reb = sum(5), ast = sum(6), tpm = sum(7), tpa = sum(8), fga = sum(9);
      const fta = hasFt ? sum(16) : 0.2 * pts / 0.78;                  // real free-throw attempts (old files: ~20% of points)
      const tov = hasFt ? sum(17) : 0;
      const p3 = (tpm + 0.36 * 15) / (tpa + 15);                     // shrunk toward league 36%
      const twoA = Math.max(1, fga - tpa), twoM = Math.max(0, (pts - 0.78 * fta - 3 * tpm) / 2);
      const p2 = (twoM + 0.53 * 20) / (twoA + 20);                   // shrunk toward league 53% on twos
      const pr = s => (p.proj[s] && p.proj[s].proj) || 0;
      return {pid: p.pid, name: p.name, ini: initials(p.name), last: lastName(p.name), starter: isSt(p), idx: i,
        min: minsOf(p), tgt: {pts: pr("pts"), reb: pr("reb"), ast: pr("ast"), tpm: pr("tpm")},
        u: Math.max(0.05, (fga + 0.44 * fta) / mins), r3: fga > 0 ? clamp(tpa / fga, 0.01, 0.9) : 0.25, p3: clamp(p3, 0.2, 0.47),
        p2: clamp(p2, 0.38, 0.7), ft: clamp((fta / 2) / Math.max(1, fga + fta / 2), 0.02, 0.35),
        tw: tov > 0 ? tov / mins : 0, rw: Math.max(0.02, reb / mins), aw: Math.max(0.01, ast / mins)};
    });
    if (ov) rows.forEach(r => {
      const p0 = ps.find(p => p.pid === r.pid), was = !!(p0.rw && p0.rw.starter) || (p0.logs || []).slice(-10).filter(x => x[10]).length >= 6;
      let m = r.min;
      if (r.starter && !was && m < 26) m = Math.min(30, Math.max(26, m + 6));        // moved into the starting five
      else if (!r.starter && was && stSet) m = m * 0.8;                              // moved to the bench
      if (m !== r.min) { const k = m / r.min; r.min = m; for (const st in r.tgt) r.tgt[st] *= k; }
    });
    // starters: the five flagged (or top five by minutes)
    const flagged = rows.filter(r => r.starter);
    const starters = (flagged.length >= 5 ? flagged : rows).slice(0, 5);
    rows.forEach(r => r.starter = starters.includes(r));
    // minutes add up to 240. Short (deep bench left out, early-season minutes): everyone grows in
    // proportion, starters capped at 38 and bench at 34, any remainder to the bench. Long: everyone shrinks.
    let tot = rows.reduce((t, r) => t + r.min, 0) || 1;
    const grow = (r, m) => { const k = m / r.min; r.min = m; for (const s in r.tgt) r.tgt[s] *= k; };
    if (tot > 240) {
      // too many minutes (deep roster all projected): the end of the bench gives them up first,
      // then the rest of the bench, and starters only shrink if the bench is already empty
      let extra = tot - 240;
      for (const r of rows.filter(r => !r.starter).sort((a, b) => a.min - b.min)) {
        if (extra <= 0) break;
        const cut = Math.min(extra, r.min * 0.85); grow(r, r.min - cut); extra -= cut;
      }
      if (extra > 0) { const t2 = rows.reduce((t, r) => t + r.min, 0); rows.forEach(r => grow(r, r.min * 240 / t2)); }
    }
    else {
      for (let pass = 0; pass < 4 && tot < 239.5; pass++) {
        const open = rows.filter(r => r.min < (r.starter ? 38 : 34)), ot = open.reduce((t, r) => t + r.min, 0) || 1, need = 240 - tot;
        open.forEach(r => grow(r, Math.min(r.starter ? 38 : 34, r.min + need * r.min / ot)));
        tot = rows.reduce((t, r) => t + r.min, 0);
      }
    }
    // anchor the team total most of the way to the market's implied points when Vegas has a line
    // (player shares still come from the projections)
    const imp = g.vegas && g.vegas.implied && g.vegas.implied[side];
    const sumPts = rows.reduce((t, r) => t + r.tgt.pts, 0);
    const ak = (typeof D !== "undefined" && D.players && D.players.tuning && D.players.tuning.sim_anchor) || 0.75;   // learned nightly
    if (imp && sumPts > 0) { const f = clamp(Math.pow(imp / sumPts, ak), 0.8, 1.2); rows.forEach(r => r.tgt.pts *= f); }
    // counting stats follow the pace (points are already anchored to the implied team total)
    if (P.paceK !== 1) rows.forEach(r => { r.tgt.reb *= P.paceK; r.tgt.ast *= P.paceK; r.tgt.tpm *= P.paceK; });
    // team habits vs this opponent (research.matchup_rates): offensive rebound rate, turnovers per possession,
    // and how much more or less often they get to the line than usual. Defaults are league-typical.
    const rt = (g.rates && g.rates[side]) || {};
    P.teams[side] = {ab, name: g[side + "_name"] || ab, players: rows, eff: 1,
      orb: clamp(rt.orb || 0.24, 0.15, 0.4), tovp: clamp(rt.tov || 0.132, 0.08, 0.22), ftk: clamp(rt.ftk || 1, 0.7, 1.4)};
  }
  return P;
}

// ------------------------------------------------------------------ one game
function simGame(P, log, start) {   // start: {period, clock (sec), home, away} to simulate the rest of a game in progress
  const sides = ["home", "away"], T = P.teams;
  const box = {}, secs = {};
  for (const s of sides) T[s].players.forEach(p => { box[p.pid] = {pts: 0, reb: 0, ast: 0, tpm: 0, tpa: 0, fgm: 0, fga: 0, ftm: 0, fta: 0, tov: 0}; secs[p.pid] = 0; });
  const score = {home: start ? start.home : 0, away: start ? start.away : 0};
  const possLen = 2880 / (2 * P.pace) * (P.lenK || 1);
  const poss = {home: 0, away: 0};
  const on = {home: [], away: []};
  let period = start ? Math.max(1, start.period) : 1, clock = start ? Math.max(0, start.clock) : 720,
      team = rnd() < 0.5 ? "home" : "away", lastSub = clock, events = log ? [] : null;
  const firstPoss = team;
  const elapsed = () => (Math.min(period, 4) - 1) * 720 + (720 - clock) + Math.max(0, period - 4) * 0;
  function lineup(s, force) {
    const ps = T[s].players, el = Math.max(1, elapsed());
    const margin = Math.abs(score.home - score.away);
    const garbage = period >= 4 && clock < 420 && margin >= 18;
    let order;
    if (force === "start") order = ps.filter(p => p.starter);
    else if (period > 4) order = ps.slice().sort((a, b) => b.min - a.min);
    else order = ps.slice().sort((a, b) => {
      const d = p => (p.min * (p.mk || 1) * 60) * (el / 2880) - secs[p.pid] + (garbage ? (p.starter ? -1e6 : 1e3) : 0) + (on[s].includes(p) ? 40 : 0);
      return d(b) - d(a);
    });
    on[s] = order.slice(0, 5);
    if (on[s].length < 5) on[s] = ps.slice(0, 5);
  }
  const ev = (o) => { if (events) events.push({period, clock, score: {...score}, on: {home: on.home.map(p => p.pid), away: on.away.map(p => p.pid)}, ...o}); };
  if (start && (period > 1 || clock < 720)) { lineup("home"); lineup("away"); }
  else { lineup("home", "start"); lineup("away", "start"); }
  ev({kind: "tip", team, text: `${on.home[4] ? on.home[4].last : ""} and ${on.away[4] ? on.away[4].last : ""} go up for the tip`});
  while (true) {
    // one possession
    const off = team, def = team === "home" ? "away" : "home", O = T[off];
    poss[off]++;
    let second = false, ended = false;
    while (true) {
      const dt = Math.max(3, (second ? 6 : possLen) + gauss() * (second ? 2.5 : 4));
      const used = Math.min(dt, clock);
      clock -= used;
      for (const s of sides) on[s].forEach(p => secs[p.pid] += used);
      if (clock <= 0) { ended = true; break; }
      if (rnd() < (second ? O.tovp * 0.55 : O.tovp * 0.95)) {
        const p = pick(on[off], x => x.tw > 0 ? x.tw : 0.12 * x.u); box[p.pid].tov++;
        ev({kind: "tov", team: off, a: p.pid, text: `${p.last} turnover`}); break;
      }
      const sh = pick(on[off], x => x.u);
      if (rnd() < sh.ft * O.ftk) {
        let m = 0; for (let k = 0; k < 2; k++) if (rnd() < 0.78) m++;
        box[sh.pid].fta += 2; box[sh.pid].ftm += m; box[sh.pid].pts += m; score[off] += m;
        const fouler = pick(on[def], () => 1);
        ev({kind: "ft", team: off, a: sh.pid, pts: m, text: `${fouler.last} fouls ${sh.last} · ${m} of 2 at the line`}); break;
      }
      const three = rnd() < sh.r3;
      const pm = clamp((three ? sh.p3 : sh.p2) * O.eff, 0.05, 0.85);
      box[sh.pid].fga++; if (three) box[sh.pid].tpa++;
      if (rnd() < pm) {
        const v = three ? 3 : 2;
        box[sh.pid].fgm++; box[sh.pid].pts += v; if (three) box[sh.pid].tpm++; score[off] += v;
        let as = null;
        if (rnd() < (three ? 0.82 : 0.55)) { as = pick(on[off].filter(p => p !== sh), x => x.aw); box[as.pid].ast++; }
        const kind = three ? "3pm" : "2pm";
        const shot = three ? ["pull-up three", "catch-and-shoot three", "corner three", "step-back three"][Math.floor(rnd() * 4)]
                           : ["driving layup", "dunk", "floater", "mid-range jumper", "hook shot", "putback"][Math.floor(rnd() * (second ? 6 : 5))];
        ev({kind, team: off, a: sh.pid, b: as && as.pid, pts: v, text: `${sh.last} ${shot}${as ? ` (${as.last} assist)` : ""}`}); break;
      }
      const oreb = rnd() < O.orb;
      const teamReb = rnd() < 0.06;                      // some misses are team rebounds (no player credit)
      const rb = oreb ? pick(on[off], x => x.rw * 0.7) : pick(on[def], x => x.rw);
      if (!teamReb) box[rb.pid].reb++;
      ev({kind: three ? "3miss" : "2miss", team: off, a: sh.pid, b: teamReb ? null : rb.pid, oreb,
          text: `${sh.last} misses ${three ? "a three" : "a jumper"}, ` + (teamReb ? `${oreb ? T[off].ab : T[def].ab} team rebound` : `${rb.last} ${oreb ? "offensive" : "defensive"} rebound`)});
      if (!oreb) break;
      second = true;
    }
    if (ended || clock <= 0) {
      // end of period
      if (period >= 4 && score.home !== score.away) { ev({kind: "final", team: off, text: `Final`}); break; }
      period++; clock = period > 4 ? 300 : 720; lastSub = clock;
      team = period > 4 ? (rnd() < 0.5 ? "home" : "away") : ((period % 2 === 0) === (firstPoss === "home") ? "away" : "home");
      lineup("home", period === 3 ? "start" : null); lineup("away", period === 3 ? "start" : null);
      ev({kind: "period", team, text: period > 4 ? `Overtime ${period - 4 > 1 ? period - 4 : ""}`.trim() : `Start of the ${["", "1st", "2nd", "3rd", "4th"][period]} quarter`});
      continue;
    }
    team = def;
    if (lastSub - clock >= 150) { lineup("home"); lineup("away"); lastSub = clock; }
  }
  return {score, box, secs, events, periods: period, poss};
}

// ------------------------------------------------------------------ calibration
// Nudge usage, three-point share, rebounding and passing weights until the average simulated
// box score matches the matchup model's projections (so the sim adds a range, not a new opinion).
function calibrate(P, rounds = 3, n = 220) {
  for (let it = 0; it < rounds; it++) {
    const acc = {};
    for (const s of ["home", "away"]) P.teams[s].players.forEach(p => acc[p.pid] = {pts: 0, reb: 0, ast: 0, tpm: 0, sec: 0});
    let np = 0;
    for (let i = 0; i < n; i++) {
      const r = simGame(P, false);
      np += (r.poss.home + r.poss.away) / 2 * (2880 / (2880 + Math.max(0, r.periods - 4) * 300));
      for (const pid in acc) { const b = r.box[pid]; acc[pid].pts += b.pts; acc[pid].reb += b.reb; acc[pid].ast += b.ast; acc[pid].tpm += b.tpm; acc[pid].sec += r.secs[pid]; }
    }
    P.lenK = clamp((P.lenK || 1) * Math.pow(np / n / P.pace, 1), 0.6, 1.6);     // possessions per team match the pace
    for (const s of ["home", "away"]) {
      const Tm = P.teams[s];
      let tg = 0, mn = 0;
      Tm.players.forEach(p => {
        const m = {pts: acc[p.pid].pts / n, reb: acc[p.pid].reb / n, ast: acc[p.pid].ast / n, tpm: acc[p.pid].tpm / n};
        const ratio = (t, v, pow) => t > 0 && v > 0 ? Math.pow(t / v, pow) : 1;
        p.mk = clamp((p.mk || 1) * ratio(p.min, acc[p.pid].sec / 60 / n, 1.0), 0.7, 1.5);   // rotation hits projected minutes
        p.u = clamp(p.u * ratio(p.tgt.pts, m.pts, 0.7), 0.01, 3);
        p.r3 = clamp(p.r3 * ratio(p.tgt.tpm, m.tpm, 0.6), 0.005, 0.92);
        p.rw = clamp(p.rw * ratio(p.tgt.reb, m.reb, 0.8), 0.005, 3);
        p.aw = clamp(p.aw * ratio(p.tgt.ast, m.ast, 0.8), 0.005, 3);
        tg += p.tgt.pts; mn += m.pts;
      });
      if (mn > 0) Tm.eff = clamp(Tm.eff * Math.pow(tg / mn, 0.9), 0.7, 1.35);
    }
  }
  return P;
}

// ------------------------------------------------------------------ Monte Carlo
const STATS = ["pts", "reb", "ast", "tpm", "pra", "ra"];
function monteCarlo(P, N, onProgress, seedBase) {
  return new Promise(resolve => {
    const pids = [], who = {};
    for (const s of ["home", "away"]) P.teams[s].players.forEach(p => { pids.push(p.pid); who[p.pid] = {...p, side: s}; });
    const out = {}; pids.forEach(pid => { out[pid] = {}; STATS.forEach(k => out[pid][k] = new Int16Array(N)); out[pid].min = new Float32Array(N); });
    const team = {home: new Int16Array(N), away: new Int16Array(N)};
    let i = 0;
    const chunk = () => {
      const end = Math.min(N, i + 250);
      for (; i < end; i++) {
        if (seedBase != null) setSeed(seedBase + i);
        const r = simGame(P, false);
        team.home[i] = r.score.home; team.away[i] = r.score.away;
        for (const pid of pids) {
          const b = r.box[pid], o = out[pid];
          o.pts[i] = b.pts; o.reb[i] = b.reb; o.ast[i] = b.ast; o.tpm[i] = b.tpm; o.pra[i] = b.pts + b.reb + b.ast; o.ra[i] = b.reb + b.ast;
          o.min[i] = r.secs[pid] / 60;
        }
      }
      onProgress && onProgress(i / N);
      if (i < N) setTimeout(chunk, 0); else { if (seedBase != null) setSeed(null); resolve({N, out, team, who}); }
    };
    chunk();
  });
}
const mean = a => { let t = 0; for (let i = 0; i < a.length; i++) t += a[i]; return t / a.length; };
const quant = (a, q) => { const s = Array.from(a).sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(q * s.length))]; };
const pOver = (a, line) => { let c = 0; for (let i = 0; i < a.length; i++) if (a[i] > line) c++; return c / a.length; };

// ------------------------------------------------------------------ court drawing
// Court in feet: 94 x 50, drawn at 10 px per foot. Baskets at x = 5.25 and 88.75.
const W = 940, H = 500, BX = [52.5, 887.5];
function courtSvg() {
  const line = 'stroke="#f4ead8" stroke-opacity=".82" stroke-width="2.2" fill="none"';
  const half = side => {
    const b = BX[side], s = side ? -1 : 1, x0 = side ? W : 0;
    return `<rect x="${side ? W - 190 : 0}" y="170" width="190" height="160" ${line} fill="#000" fill-opacity=".2"/>
      <path d="M${x0} 30 L${x0 + s * 140} 30 A237.5 237.5 0 0 ${side ? 0 : 1} ${x0 + s * 140} 470 L${x0} 470" ${line}/>
      <circle cx="${x0 + s * 190}" cy="250" r="60" ${line}/>
      <path d="M${b - s * 0} 210 A40 40 0 0 ${side ? 0 : 1} ${b} 290" ${line}/>
      <line x1="${x0 + s * 40}" x2="${x0 + s * 40}" y1="220" y2="280" stroke="#f4ead8" stroke-width="3"/>
      <g class="hoop" id="hoop${side}" style="transform-origin:${b}px 250px">
        <circle class="burst" cx="${b}" cy="250" r="9"/>
        <circle class="net" cx="${b}" cy="250" r="5.6"/>
        <circle class="rim" cx="${b}" cy="250" r="7.5"/>
      </g>`;
  };
  let planks = "";
  for (let y = 0; y < H; y += 25) planks += `<line x1="0" x2="${W}" y1="${y}" y2="${y}" stroke="#000" stroke-opacity=".08"/>`;
  return `<svg viewBox="-20 -20 ${W + 40} ${H + 40}" class="cv" id="simCourt">
    <defs><radialGradient id="ballGrad" cx=".35" cy=".35" r=".75"><stop offset="0" stop-color="#ffc07a"/><stop offset=".55" stop-color="#f08a2c"/><stop offset="1" stop-color="#b9561a"/></radialGradient><linearGradient id="wood" x1="0" x2="1"><stop offset="0" stop-color="#3e2a1a"/><stop offset=".5" stop-color="#5c3f27"/><stop offset="1" stop-color="#3e2a1a"/></linearGradient><radialGradient id="spot" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff" stop-opacity=".09"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>
    <rect x="-20" y="-20" width="${W + 40}" height="${H + 40}" rx="6" fill="#2a1d13"/>
    <rect x="0" y="0" width="${W}" height="${H}" fill="url(#wood)"/>${planks}<ellipse cx="${W / 2}" cy="250" rx="330" ry="210" fill="url(#spot)"/>
    <rect x="0" y="0" width="${W}" height="${H}" ${line}/>
    <line x1="${W / 2}" x2="${W / 2}" y1="0" y2="${H}" ${line}/><circle cx="${W / 2}" cy="250" r="60" ${line}/>
    <circle cx="${W / 2}" cy="250" r="20" ${line}/><circle cx="${W / 2}" cy="250" r="60" fill="#fff" fill-opacity=".04"/>
    ${half(0)}${half(1)}
    <g id="simDots"></g>
    <g id="simBallG" style="pointer-events:none">
      <ellipse id="simShadow" rx="8" ry="5" fill="#000" opacity=".38" cx="0" cy="0"/>
      <g id="simTrail">${[0, 1, 2, 3, 4].map(k => `<circle r="${6 - k}" opacity="0"/>`).join("")}</g>
      <g id="simBall"><circle r="9" fill="url(#ballGrad)" stroke="#3a1d00" stroke-width="1.4"/>
        <g id="simSeams" stroke="#3a1d00" stroke-width="1.1" fill="none"><line x1="-8" x2="8" y1="0" y2="0"/><line x1="0" x2="0" y1="-8" y2="8"/><path d="M-5.5-5.8Q-1.5 0-5.5 5.8M5.5-5.8Q1.5 0 5.5 5.8"/></g></g>
    </g>
    <g id="simPop"></g>
  </svg>`;
}
// spots relative to the basket the offense attacks (feet: dx toward half court, dy from center)
const OFF = [[24, 0], [20, -14], [20, 14], [3, -21], [8, 6]];      // PG top, wings, corner, post
const SHOT3 = [[23.5, 0], [21, -11], [21, 11], [2, -22], [2, 22], [17, -17], [17, 17]];
const SHOT2 = [[3, 0], [5, -6], [5, 6], [12, -9], [12, 9], [14, 0], [8, 0]];
function spot(attackRight, dx, dy) {
  const bx = attackRight ? BX[1] : BX[0];
  return [bx + (attackRight ? -1 : 1) * dx * 10, 250 + dy * 10 * (attackRight ? 1 : -1)];
}

// ------------------------------------------------------------------ the Sim view
const S = window.SIMV = {gameId: null, P: null, game: null, i: 0, timer: null, speed: 1, playing: false, mc: null, stat: "pts", busy: false};

async function renderSim() {
  if (!D.sims) D.sims = await get("sims.json", {games: {}});
  const games = (D.players.games || []);
  if (!S.gameId || !games.some(g => g.espn_id === S.gameId)) S.gameId = (games.find(g => g.state === "pre") || games[0] || {}).espn_id;
  const chips = games.map(g => `<button data-g="${g.espn_id}" class="${g.espn_id === S.gameId ? "on" : ""}">${esc(g.away)} @ ${esc(g.home)} ${g.state === "pre" ? tod(g.start) : g.state === "in" ? "· live" : "· final"}</button>`).join("");
  const gSel = games.find(g => g.espn_id === S.gameId) || {};
  const liveFirst = (S.now && S.now.state === "in") || gSel.state === "in" || (gSel.state === "pre" && gSel.start && new Date(gSel.start) < Date.now());
  $("v-sim").innerHTML = `
    <div class="chips" id="simg">${chips || '<span class="desc">No NBA games on the slate.</span>'}</div>
    ${liveFirst ? '<h2>Live · DraftKings</h2><div class="card" id="simLive"><div class="empty">Loading the live game…</div></div>' : ""}
    <div id="simStage"></div>
    <h2>Monte Carlo</h2>
    <div class="card" id="simMC"><div class="desc">Plays this game thousands of times with the same inputs, so every player gets a full range of outcomes and a hit rate for any line.</div>
      <button class="btn" id="simRun" style="margin-top:8px">Run 5,000 sims</button><div class="meter" id="simBar" style="display:none"><i style="width:0%"></i></div><div id="simRes"></div></div>
    ${liveFirst ? "" : '<h2>Live lines · DraftKings</h2><div class="card" id="simLive"><div class="empty">Loading…</div></div>'}
    <h2>Sim vs DraftKings</h2>
    <div class="card" id="simVs"><div class="empty">Loading…</div></div>
    <h2>Today's simulations</h2>
    <div class="card" id="simDay"><div class="empty">Loading…</div></div>
    <div class="desc" style="margin:10px 2px">How it works: each possession picks who shoots (usage), what kind of shot, makes and misses, assists, rebounds and turnovers from each player's own rates. Rotations follow projected minutes, starters sit in blowouts, and the box score is calibrated so the average sim matches the matchup projections. Pace and the spread come from the model and Vegas.</div>`;
  $("simg") && $("simg").querySelectorAll("button").forEach(b => b.onclick = () => { stop(); S.gameId = b.dataset.g; S.P = null; S.game = null; S.mc = null; S.now = null; S.nowSim = null; clearInterval(S.liveTimer); renderSim(); });
  if (!S.gameId) return;
  if (!S.P || S.P.g.espn_id !== S.gameId) {
    $("simStage").innerHTML = '<div class="empty">Loading the matchup and calibrating to the projections…</div>';
    const g = games.find(x => x.espn_id === S.gameId), gf = await gameFile(S.gameId);
    if (!gf) { $("simStage").innerHTML = '<div class="empty">No detail file for this game yet.</div>'; return; }
    await new Promise(r => setTimeout(r, 20));
    S.P = calibrate(prep(g, gf));
  }
  stage();
  $("simRun").onclick = runMC;
  if (S.mc) showMC(); else showStored();
  if (!D.simday) D.simday = await get("sims_day.json", {runs: []});
  showDay(); showVs();
  if (!D.dkday || D.dkday.date !== D.players.date) D.dkday = await get(`dk/${D.players.date}.json`, {date: D.players.date, games: {}});
  showLive();
}

// Team colors (ESPN's primary / alternate). Panel color is the darker one; the accent is the other, or white/black for contrast.
const TEAMC = {ATL: ["c8102e", "fdb927"], BOS: ["008348", "ffffff"], BKN: ["000000", "ffffff"], CHA: ["008ca8", "1d1060"], CHI: ["ce1141", "000000"], CLE: ["860038", "bc945c"], DAL: ["0064b1", "bbc4ca"], DEN: ["0e2240", "fec524"], DET: ["1d428a", "c8102e"], GS: ["fdb927", "1d428a"], HOU: ["ce0e2d", "000000"], IND: ["0c2340", "ffd520"], LAC: ["12173f", "c8102e"], LAL: ["552583", "fdb927"], MEM: ["5d76a9", "12173f"], MIA: ["98002e", "000000"], MIL: ["00471b", "eee1c6"], MIN: ["266092", "79bc43"], NO: ["0a2240", "b4975a"], NY: ["1d428a", "f58426"], OKC: ["007ac1", "ef3b24"], ORL: ["0150b5", "9ca0a3"], PHI: ["1d428a", "e01234"], PHX: ["29127a", "e56020"], POR: ["e03a3e", "000000"], SAC: ["5a2d81", "6a7a82"], SA: ["000000", "c4ced4"], TOR: ["d91244", "000000"], UTAH: ["4e008e", "79a3dc"], WSH: ["e31837", "002b5c"]};
const hexRgb = h => [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
const lum = h => { const [r, g, b] = hexRgb(h); return (0.299 * r + 0.587 * g + 0.114 * b) / 255; };
const cdist = (a, b) => { const x = hexRgb(a), y = hexRgb(b); return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); };
const ink = h => lum(h) > 0.6 ? "#0b0f17" : "#fff";
function teamCols(P) {
  const one = ab => {
    let [c, a] = TEAMC[ab] || ["3a4766", "ADD8E6"];
    if (lum(c) > 0.55 && lum(a) < 0.55) [c, a] = [a, c];
    if (lum(a) < 0.12 || cdist(a, c) < 70) a = lum(c) > 0.45 ? "0b0f17" : "ffffff";
    if (lum(c) < 0.06) c = "26292f";
    return {c, a};
  };
  const h = one(P.teams.home.ab), w = one(P.teams.away.ab);
  if (cdist(h.c, w.c) < 90) {                       // similar colors: the away team switches to its alternate (or white)
    const alt = (TEAMC[P.teams.away.ab] || [])[1];
    if (alt && lum(alt) > 0.12 && cdist(alt, h.c) > 110) { w.c = alt; w.a = lum(alt) > 0.5 ? "0b0f17" : "ffffff"; }
    else { w.c = "e7ecf5"; w.a = "0b0f17"; }
  }
  return {home: h, away: w};
}
const nickOf = s => { const g = S.P.g, n = g[s + "_name"] || S.P.teams[s].name || S.P.teams[s].ab; return n.split(" ").slice(-1)[0]; };
// jersey numbers from ESPN's team rosters (free, cached for a day on this device)
async function loadJerseys(P) {
  P.jersey = P.jersey || {};
  await Promise.all(["home", "away"].map(async s => {
    const ab = P.teams[s].ab, key = "jer-" + ab;
    let m = null;
    try { const c = JSON.parse(localStorage.getItem(key) || "null"); if (c && Date.now() - c.t < 864e5) m = c.m; } catch (e) {}
    if (!m) {
      try {
        const r = await fetch(`https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/${ab.toLowerCase()}/roster`).then(x => x.json());
        m = {}; (r.athletes || []).forEach(a => { if (a.jersey != null) m[a.id] = String(a.jersey); });
        try { localStorage.setItem(key, JSON.stringify({t: Date.now(), m})); } catch (e) {}
      } catch (e) { m = {}; }
    }
    Object.assign(P.jersey, m);
  }));
  if (S.P === P) { document.querySelectorAll("#simDots .pdot").forEach(g => { const t = g.querySelector("text"), n = P.jersey[g.getAttribute("data-p")]; if (t && n) t.textContent = n; }); boxes(S.i); }
}
const jer = pid => (S.P && S.P.jersey && S.P.jersey[pid]) || "";

function stage() {
  const P = S.P, g = P.g, C = S.col = teamCols(P);
  const vars = `--hc:#${C.home.c};--ha:#${C.home.a};--ht:${ink(C.home.c)};--ac:#${C.away.c};--aa:#${C.away.a};--at:${ink(C.away.c)}`;
  S.boxSide = S.boxSide || "away";
  $("simStage").innerHTML = `
    <div class="bc${S.theater ? " theater" : ""} side-${S.boxSide}" id="bcWrap" style="${vars}">
      <div class="bc-sb">
        <div class="bc-tm a"><div class="tx"><div class="nm">${esc(nickOf("away"))}</div><div class="sub" id="fgA">${esc(P.teams.away.ab)}</div></div><div class="sc" id="scA">0</div></div>
        <div class="bc-clk"><div class="c" id="simClk">Q1 12:00</div><div class="shot">SHOT <b id="shotClk">24</b></div></div>
        <div class="bc-tm h"><div class="sc" id="scH">0</div><div class="tx"><div class="nm">${esc(nickOf("home"))}</div><div class="sub" id="fgH">${esc(P.teams.home.ab)}</div></div></div>
        <div class="bc-wp"><span class="a" id="wpA">50%</span><div class="trk"><svg viewBox="0 0 300 24" preserveAspectRatio="none"><line x1="0" x2="300" y1="12" y2="12"/><polyline id="wpPoly" points=""/></svg><i id="wpBar" style="left:0%;top:50%"></i><em>WIN PROBABILITY</em></div><span class="h" id="wpH">50%</span></div>
      </div>
      <div class="bc-main">
        <div class="bc-box a" id="bxA"></div>
        <div class="bc-court"><div class="court3d"><div class="tilt">${courtSvg()}</div></div></div>
        <div class="bc-box h" id="bxH"></div>
      </div>
      <div class="bc-tick" id="simCap"><div class="poss" id="capT"><b id="capP">TIP-OFF</b><span id="capS"></span></div><div class="txt" id="capX">Press play to simulate ${esc(g.away)} @ ${esc(g.home)}</div><span class="bdg" id="capB"></span></div>
      <div class="bc-ctl">
        <button class="bc-btn pri" id="simPlay">▶ Watch the average game</button>
        <button class="bc-btn" id="simRand" title="Random game">🎲 Random</button>
        <div class="seg bc-spd" id="simSpd">${[1, 4, 16].map(x => `<button data-s="${x}" class="${S.speed === x ? "on" : ""}">${x}×</button>`).join("")}</div>
        <button class="bc-btn ic" id="simSkip" title="Skip to final" aria-label="Skip to final">⏭</button>
        <button class="bc-btn ic" id="simSnd" title="Sound" aria-pressed="${SND.on}" aria-label="Sound">${SND.on ? "🔊" : "🔈"}</button>
        <button class="bc-btn ic" id="simFull" title="Full screen" aria-label="Full screen">${S.theater ? "✕" : "⤢"}</button>
      </div>
      <div class="bc-under">
        <div class="seg" id="simView">${[["live", "This game"], ["avg", "Average"], ["mode", "Most common"]].map(([k, l]) => `<button data-v="${k}" class="${(S.view || "live") === k ? "on" : ""}">${l}</button>`).join("")}</div>
        <div class="seg bc-side" id="simSide">${["away", "home"].map(x => `<button data-s="${x}" class="${S.boxSide === x ? "on" : ""}">${esc(P.teams[x].ab)}</button>`).join("")}</div>
      </div>
      <details class="lu-d" id="simLUd"${S.luOpen ? " open" : ""}><summary><span>👥 Lineup${hasOverride(S.gameId) ? ' <span class="fchip edge">your lineup</span>' : ""}</span><span class="dim" style="font-size:12px">${luSummary()}</span></summary><div id="simLU"><div class="empty">Loading…</div></div></details>
      <div class="bc-note">Simulated game, not a prediction · tap the court to replay a play · ⤢ for full screen (turn your phone sideways)</div>
    </div>`;
  $("simPlay").onclick = () => { if (S.playing) stop(); else play("avg"); };
  $("simRand").onclick = () => { stop(); S.kind = "random"; newGame("random"); S.view = "live"; syncView(); play("random", true); };
  $("simView").querySelectorAll("button").forEach(b => b.onclick = async () => { S.view = b.dataset.v; syncView(); if (S.view !== "live") await ensureAvg(); boxes(S.i); if (S.view === "live" && S.game) show(S.game.events[S.i], false); });
  $("simSide").querySelectorAll("button").forEach(b => b.onclick = () => { S.boxSide = b.dataset.s; const w = $("bcWrap"); w.classList.remove("side-away", "side-home"); w.classList.add("side-" + S.boxSide); $("simSide").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); });
  $("simSpd").querySelectorAll("button").forEach(b => b.onclick = () => { S.speed = +b.dataset.s; $("simSpd").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); });
  $("simSkip").onclick = async () => { if (!S.game || S.kind === "init") await loadAvgGame(); stop(); S.i = S.game.events.length - 1; replayTo(S.i); };
  $("simSnd").onclick = () => { SND.on = !SND.on; try { localStorage.setItem("sim_sound", SND.on ? "1" : "0"); } catch (e) {}
    if (SND.on) { actx(); sfx("swish"); } $("simSnd").textContent = SND.on ? "🔊" : "🔈"; $("simSnd").setAttribute("aria-pressed", SND.on); };
  $("simFull").onclick = () => theater(!S.theater);
  $("simCourt").addEventListener("click", replayCurrent);
  S.view = S.view || "live";
  newGame("init"); ballSnap(W / 2, 250); drawDots(S.game.events[0], false); boxes(0);
  if (!P.jersey) loadJerseys(P);
  const d = $("simLUd");
  if (d) { const fill = () => lineupHtml().then(h => { const x = $("simLU"); if (x) x.innerHTML = h; });
    if (d.open) fill(); d.ontoggle = () => { S.luOpen = d.open; if (d.open) fill(); }; }
}
function luSummary() {
  const o = LU(S.gameId); if (!o || !hasOverride(S.gameId)) return "set who plays and starts";
  const nm = pid => { for (const sd of ["home", "away"]) { const p = S.P.teams[sd].players.find(x => x.pid === pid); if (p) return p.last; } return null; };
  const parts = [];
  if ((o.out || []).length) parts.push((o.out.length) + " ruled out");
  if ((o.in || []).length) parts.push(o.in.map(nm).filter(Boolean).join(", ") + " back");
  if (Object.values(o.st || {}).some(a => a && a.length)) parts.push("starters set");
  return parts.join(" · ");
}
// ------------------------------------------------------------------ your lineup: rule players out or in, pick starters
// Saved on this device per game, so a late scratch you hear about before the agent's next refresh is simulated right away.
const LU = gid => { try { return JSON.parse(localStorage.getItem("pz-lu-" + gid) || "null"); } catch (e) { return null; } };
const LUset = (gid, v) => { try { v ? localStorage.setItem("pz-lu-" + gid, JSON.stringify(v)) : localStorage.removeItem("pz-lu-" + gid); } catch (e) {} };
function hasOverride(gid) { const o = LU(gid); return !!(o && ((o.out || []).length || (o.in || []).length || Object.values(o.st || {}).some(a => a && a.length))); }
if (typeof window !== "undefined") window.simOverride = gid => hasOverride(gid) ? LU(gid) : null;
async function lineupHtml() {
  const g = S.P.g, gf = await gameFile(S.gameId); if (!gf) return "";
  const o = LU(S.gameId) || {out: [], in: [], st: {}};
  const side = sd => {
    const ab = g[sd], inP = new Set(S.P.teams[sd].players.map(p => p.pid)), stP = new Set(S.P.teams[sd].players.filter(p => p.starter).map(p => p.pid));
    const ps = Object.values(gf.players).filter(p => p.team === ab).sort((a, b) => (inP.has(b.pid) - inP.has(a.pid)) || (stP.has(b.pid) - stP.has(a.pid)) || (b.minutes || b.min_base || 0) - (a.minutes || a.min_base || 0));
    const nSt = ps.filter(p => stP.has(p.pid)).length;
    const oSt = (o.st && o.st[sd]) || [], picked = oSt.length;
    return `<div class="lu-t"><div class="lu-h">${esc(nickOf(sd))} <span class="${picked && picked !== 5 ? "neg" : "dim"}">· ${picked ? picked + " starters picked" + (picked !== 5 ? " (pick 5; the sim starts the five with the most minutes)" : "") : nSt + " starters"}</span></div>
      ${ps.map(p => { const on = inP.has(p.pid), st = stP.has(p.pid);
        const sim = on ? S.P.teams[sd].players.find(x => x.pid === p.pid) : null;
        return `<div class="lu-r${on ? "" : " off"}"><span class="lu-n">${esc(jer(p.pid) ? "#" + jer(p.pid) + " " : "")}${esc(p.name)}
          <span class="dim">${on ? f1(sim.min) + " min" + ((o.in || []).includes(p.pid) ? ' · <span class="gold">cleared by you</span>' : "") : (o.out || []).includes(p.pid) ? '<span class="gold">ruled out by you</span>' : p.out ? esc((p.status || "out").toLowerCase()) : "not in the rotation"}</span></span>
          <button class="lu-b${st ? " on" : ""}" data-lu="st|${sd}|${p.pid}" ${on ? "" : "disabled"}>Starter</button>
          <button class="lu-b ${on ? "in" : "out"}" data-lu="io|${sd}|${p.pid}">${on ? "Playing" : "Out"}</button></div>`; }).join("")}</div>`;
  };
  return `<div class="desc">Heard about a scratch or a starting-lineup change before the agent's next refresh? Set it here and the sim re-runs with your lineup right away (minutes are re-shared to 240, and the team's scoring is redistributed to who's left). Saved on this device for this game only.</div>
    <div class="lu">${side("away")}${side("home")}</div>
    ${hasOverride(S.gameId) ? '<button class="btn ghost" data-lu="reset" style="margin-top:8px">Reset to the official lineup</button>' : ""}`;
}
async function applyLineup(act) {
  const gid = S.gameId, o = LU(gid) || {out: [], in: [], st: {}};
  o.out = o.out || []; o.in = o.in || []; o.st = o.st || {};
  if (act === "reset") LUset(gid, null);
  else {
    const [kind, sd, pid] = act.split("|"), gf = await gameFile(gid), p = gf.players[pid];
    if (kind === "io") {
      const playing = S.P.teams[sd].players.some(x => x.pid === pid);
      o.out = o.out.filter(x => x !== pid); o.in = o.in.filter(x => x !== pid);
      if (playing) o.out.push(pid); else if (p.out || !(p.minutes > 0)) o.in.push(pid);
      if (playing && o.st[sd]) o.st[sd] = o.st[sd].filter(x => x !== pid);
    } else {
      const cur = o.st[sd] && o.st[sd].length ? o.st[sd].slice() : S.P.teams[sd].players.filter(x => x.starter).map(x => x.pid);
      o.st[sd] = cur.includes(pid) ? cur.filter(x => x !== pid) : cur.concat(pid);
    }
    LUset(gid, o);
  }
  const box = $("simLU"); if (box) box.innerHTML = '<div class="empty">Re-simulating with your lineup…</div>';
  stop(); await new Promise(r => setTimeout(r, 30));
  const g = (D.players.games || []).find(x => x.espn_id === gid), gf = await gameFile(gid);
  const jersey = S.P && S.P.jersey;
  S.P = calibrate(prep(g, gf)); S.P.jersey = jersey; S.avgRun = null; S.mc = null; S.game = null; S.nowSim = null;
  S.luOpen = true; stage(); showStored(); if (S.now && S.now.state === "in") liveSimFromHere();
}
if (typeof document !== "undefined") document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("[data-lu]"); if (!b || b.disabled) return;
  e.preventDefault(); applyLineup(b.dataset.lu);
});
// Full-screen "theater": the sim covers the page (no tabs), and turning the phone sideways gives the broadcast layout.
function theater(on) {
  S.theater = on; const w = $("bcWrap"); if (w) w.classList.toggle("theater", on);
  document.body.classList.toggle("theater", on);
  const b = $("simFull"); if (b) { b.textContent = on ? "✕" : "⤢"; b.title = on ? "Exit full screen" : "Full screen"; }
  try { if (on && screen.orientation && screen.orientation.lock) screen.orientation.lock("landscape").catch(() => {}); if (!on && screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); } catch (e) {}
}
function syncView() { const v = $("simView"); if (v) v.querySelectorAll("button").forEach(x => x.classList.toggle("on", x.dataset.v === S.view)); }

// The "average game": run 2,000 seeded simulations, find the one closest to the averages (score and every
// player's line), then replay exactly that simulation on the court. Its box score ends near the averages.
async function ensureAvg() {
  if (S.avgRun && S.avgRun.gid === S.gameId) return S.avgRun;
  const P = S.P, N = 2000, seedBase = (parseInt(S.gameId, 10) % 1000003) * 7919;
  const cap = $("capX"); if (cap) cap.textContent = "Running 2,000 simulations to find the average game…";
  const M = await monteCarlo(P, N, f => { if (cap) cap.textContent = `Running 2,000 simulations to find the average game… ${Math.round(f * 100)}%`; }, seedBase);
  const mean = a => { let t = 0; for (let i = 0; i < a.length; i++) t += a[i]; return t / a.length; };
  const sd = a => { const m = mean(a); let v = 0; for (let i = 0; i < a.length; i++) v += (a[i] - m) ** 2; return Math.sqrt(v / a.length) || 1; };
  const mode = a => { const c = new Map(); let best = 0, bv = 0; for (let i = 0; i < a.length; i++) { const v = Math.round(a[i]); const n = (c.get(v) || 0) + 1; c.set(v, n); if (n > best) { best = n; bv = v; } } return [bv, best / a.length]; };
  const terms = [[M.team.home, 30], [M.team.away, 30]];
  for (const pid in M.out) { const o = M.out[pid], wt = Math.min(1, mean(o.min) / 36); if (wt >= 0.2) ["pts", "reb", "ast"].forEach(k => terms.push([o[k], wt])); }
  const tz = terms.map(([a, w]) => [a, w, mean(a), sd(a)]);
  let bi = 0, bd = Infinity;
  for (let i = 0; i < N; i++) { let d = 0; for (const [a, w, m, v] of tz) { const z = (a[i] - m) / v; d += w * z * z; if (d >= bd) break; } if (d < bd) { bd = d; bi = i; } }
  const players = {};
  for (const pid in M.out) {
    const o = M.out[pid];
    players[pid] = {avg: {min: mean(o.min), pts: mean(o.pts), reb: mean(o.reb), ast: mean(o.ast), tpm: mean(o.tpm)},
                    mode: {min: mode(o.min)[0], pts: mode(o.pts), reb: mode(o.reb), ast: mode(o.ast), tpm: mode(o.tpm)}};
  }
  const tot = new Int16Array(N), mar = new Int16Array(N);
  for (let i = 0; i < N; i++) { tot[i] = M.team.home[i] + M.team.away[i]; mar[i] = M.team.home[i] - M.team.away[i]; }
  // prefer the overnight 10,000-sim averages when they exist for this game
  // the stored 10,000-sim averages only when they were made from today's current inputs
  const sg = D.sims && D.sims.games && D.sims.games[S.gameId];
  const stored = !hasOverride(S.gameId) && sg && sg.at && D.players && sg.at >= (D.players.updated || "") ? sg : null;
  if (stored) for (const pid in players) { const x = stored.players[pid]; if (x) Object.assign(players[pid].avg, {min: x.min, pts: x.s.pts[0], reb: x.s.reb[0], ast: x.s.ast[0], tpm: x.s.tpm[0]}); }
  S.avgRun = {gid: S.gameId, N: stored ? D.sims.n : N, bi, seedBase, players, biScore: {home: M.team.home[bi], away: M.team.away[bi]},
    score: stored ? stored.avg : {home: mean(M.team.home), away: mean(M.team.away)},
    modeTot: mode(tot), modeMar: mode(mar), homeWin: Array.from(M.team.home).filter((h, i) => h > M.team.away[i]).length / N};
  return S.avgRun;
}
async function loadAvgGame() {
  const A = await ensureAvg();
  setSeed(A.seedBase + A.bi); S.game = simGame(S.P, true); setSeed(null);
  S.i = 0; S.kind = "avg-ready"; S.streak = {};
}

function newGame(kind) { setSeed(null); S.game = simGame(S.P, true); S.i = 0; S.cum = null; S.kind = kind || "random"; S.streak = {}; }
function trackStreak(e) {
  S.streak = S.streak || {};
  if (!e || !e.a) return;
  if (e.kind === "2pm" || e.kind === "3pm") S.streak[e.a] = (S.streak[e.a] || 0) + 1;
  else if (e.kind === "2miss" || e.kind === "3miss") S.streak[e.a] = 0;
}
function stop() { S.playing = false; clearTimeout(S.timer); BA.timers.forEach(clearTimeout); const b = $("simPlay"); if (b) b.textContent = S.game && S.i >= S.game.events.length - 1 ? "▶ Watch the average game" : "▶ Resume"; }
async function play(kind, fresh) {
  if (kind === "avg" && (S.kind !== "avg-ready" || S.i >= S.game.events.length - 1)) { $("simPlay").textContent = "Finding…"; await loadAvgGame(); drawDots(S.game.events[0], false); boxes(0); }
  else if (!S.game || (!fresh && S.i >= S.game.events.length - 1)) { newGame("random"); boxes(0); }
  S.playing = true; $("simPlay").textContent = "❚❚ Pause";
  const step = () => {
    if (!S.playing) return;
    if (S.i >= S.game.events.length - 1) { stop(); return; }
    S.i++; const ev = S.game.events[S.i]; trackStreak(ev); show(ev, true);
    S.timer = setTimeout(step, (S.speed >= 16 ? 1150 : playMs(ev)) / S.speed);
  };
  step();
}

function fmtClock(e) { const m = Math.floor(e.clock / 60), s = Math.floor(e.clock % 60); return (e.period > 4 ? "OT" + (e.period - 4 > 1 ? e.period - 4 : "") : "Q" + e.period) + " " + m + ":" + String(s).padStart(2, "0"); }
function winProb(e) {
  const P = S.P;
  const elapsedSec = (Math.min(e.period, 4) - 1) * 720 + (e.period > 4 ? 2880 : 720 - e.clock);
  const rem = e.kind === "final" ? 0 : Math.max(0, (2880 - elapsedSec) / 2880) + (e.period > 4 ? e.clock / 2880 : 0);
  const m = e.score.home - e.score.away;
  if (rem <= 0.0005) return m > 0 ? 1 : m < 0 ? 0 : 0.5;
  return Phi((m + (-(P.spreadHome || 0)) * rem) / (12.5 * Math.sqrt(rem)));
}

function setWP(wp, i) {
  $("wpA").textContent = Math.round((1 - wp) * 100) + "%"; $("wpH").textContent = Math.round(wp * 100) + "%";
  const G = S.game, dot = $("wpBar"), poly = $("wpPoly"); if (!dot || !poly) return;
  const x = ev => Math.min(1, ((Math.min(ev.period, 4) - 1) * 720 + (ev.period > 4 ? 720 : 720 - ev.clock)) / 2880);
  if (i == null || !G) { poly.setAttribute("points", ""); dot.style.left = "100%"; dot.style.top = (100 - wp * 100) + "%"; return; }
  if (!G.wp) G.wp = G.events.map(winProb);
  const pts = []; for (let k = 0; k <= i; k += Math.max(1, Math.floor(i / 120))) pts.push(`${(x(G.events[k]) * 300).toFixed(1)},${(24 - G.wp[k] * 24).toFixed(1)}`);
  pts.push(`${(x(G.events[i]) * 300).toFixed(1)},${(24 - G.wp[i] * 24).toFixed(1)}`);
  poly.setAttribute("points", pts.join(" "));
  dot.style.left = (x(G.events[i]) * 100) + "%"; dot.style.top = (100 - wp * 100) + "%";
  dot.className = wp >= 0.5 ? "h" : "a";
}
// shot clock when the play happened: 24 from the start of the possession, 14 after an offensive rebound
function shotClock(i) {
  const G = S.game.events, e = G[i], p = G[i - 1];
  if (!e || !p || ["tip", "period", "final"].includes(e.kind) || p.period !== e.period) return 24;
  const base = (p.kind === "2miss" || p.kind === "3miss") && p.oreb ? 14 : 24;
  return Math.max(1, Math.round(base - (p.clock - e.clock)));
}
function bump(id, v) { const el = $(id); if (!el) return; if (el.textContent !== String(v)) { el.textContent = v; el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop"); } }
function show(e, animate) {
  const P = S.P;
  if (S.view && S.view !== "live" && S.avgRun && S.avgRun.gid === S.gameId) { drawDots(e, animate); return aggBoxes(S.view); }
  bump("scA", e.score.away); bump("scH", e.score.home);
  $("simClk").textContent = e.kind === "final" ? "FINAL" + (e.period > 4 ? "/OT" : "") : fmtClock(e);
  $("shotClk").textContent = e.kind === "final" ? "–" : shotClock(S.i);
  setWP(winProb(e), S.i);
  const nx = S.game.events[S.i + 1], next = nx ? nx.team : null;
  const made = e.kind === "2pm" || e.kind === "3pm", miss = e.kind === "2miss" || e.kind === "3miss";
  $("capP").textContent = e.kind === "final" ? "FINAL" : e.kind === "period" ? (e.period > 4 ? "OVERTIME" : ["", "1ST", "2ND", "3RD", "4TH"][e.period] + " QTR") : e.kind === "tip" ? "TIP-OFF" : next ? nickOf(next).toUpperCase() + " BALL" : "";
  $("capS").textContent = e.kind === "final" || e.kind === "period" || e.kind === "tip" ? "" : made || e.kind === "ft" ? "inbound" : miss && e.oreb ? "second chance" : miss ? "transition" : e.kind === "tov" ? "live ball" : "half court";
  $("capT").className = "poss " + ((e.kind === "final" || e.kind === "period" || e.kind === "tip" ? e.team : next) === "home" ? "h" : "a");
  $("capX").textContent = e.text;
  const bd = $("capB"), btxt = made ? "+" + e.pts : e.kind === "ft" ? (e.pts ? "+" + e.pts : "0/2") : e.kind === "tov" ? "TURNOVER" : miss && e.oreb ? "OFF REB" : "";
  bd.textContent = btxt; bd.className = "bdg" + (btxt ? " on " + (e.team === "home" ? "h" : "a") : "") + (e.kind === "tov" ? " out" : "");
  drawDots(e, animate);
  boxes(S.i);
}

// box score up to event i (cumulative)
function boxAt(i) {
  const G = S.game, P = S.P, b = {};
  for (const s of ["home", "away"]) P.teams[s].players.forEach(p => b[p.pid] = {pts: 0, reb: 0, ast: 0, tpm: 0, tpa: 0, fgm: 0, fga: 0, sec: 0});
  for (let k = 1; k <= i; k++) {
    const e = G.events[k], prev = G.events[k - 1];
    const dt = (prev.period === e.period ? prev.clock - e.clock : 0);
    if (dt > 0) for (const s of ["home", "away"]) prev.on[s].forEach(pid => b[pid].sec += dt);
    if (e.kind === "2pm" || e.kind === "3pm") { b[e.a].pts += e.pts; b[e.a].fgm++; b[e.a].fga++; if (e.kind === "3pm") { b[e.a].tpm++; b[e.a].tpa++; } if (e.b) b[e.b].ast++; }
    else if (e.kind === "2miss" || e.kind === "3miss") { b[e.a].fga++; if (e.kind === "3miss") b[e.a].tpa++; if (e.b) b[e.b].reb++; }
    else if (e.kind === "ft") b[e.a].pts += e.pts;
  }
  return b;
}
// one team's box: starters, then bench; players on the floor are lit with the team color
function boxTable(s, cols, rows, on, hot) {
  const st = rows.filter(r => r.starter), bn = rows.filter(r => !r.starter);
  const tr = r => `<tr class="${on.has(r.pid) ? "on" : ""} ${hot.has(r.pid) ? "hot" : ""}" data-pid="${r.pid}"><td class="t"><span class="no">${esc(jer(r.pid))}</span>${esc(r.last)}</td>${r.cells.map((c, k) => `<td${k === 1 ? ' class="b"' : ""}>${c}</td>`).join("")}</tr>`;
  return `<div class="bh">${esc(S.P.teams[s].name || nickOf(s))}</div><table class="bt"><tr><th></th>${cols.map(c => `<th>${c}</th>`).join("")}</tr>
    <tr class="sec"><td colspan="${cols.length + 1}">Starters</td></tr>${st.map(tr).join("")}
    ${bn.length ? `<tr class="sec"><td colspan="${cols.length + 1}">Bench</td></tr>${bn.map(tr).join("")}` : ""}</table>`;
}
function boxes(i) {
  if (S.view && S.view !== "live" && S.avgRun && S.avgRun.gid === S.gameId) return aggBoxes(S.view);
  const P = S.P, b = boxAt(i), e = S.game.events[i];
  const hot = new Set([e.a, e.b].filter(Boolean));
  for (const s of ["away", "home"]) {
    const ps = P.teams[s].players, anyS = ps.some(p => p.starter);
    const rows = ps.map((p, k) => { const x = b[p.pid]; return {pid: p.pid, last: p.last, starter: anyS ? p.starter : k < 5, cells: [Math.floor(x.sec / 60), x.pts, `${x.fgm}-${x.fga}`, x.reb, x.ast, x.tpm]}; });
    $(s === "away" ? "bxA" : "bxH").innerHTML = boxTable(s, ["MIN", "PTS", "FG", "REB", "AST", "3PM"], rows, new Set(e.on[s]), hot);
    const t = ps.reduce((a, p) => { const x = b[p.pid]; a[0] += x.fgm; a[1] += x.fga; a[2] += x.tpm; a[3] += x.tpa; return a; }, [0, 0, 0, 0]);
    const fg = $(s === "away" ? "fgA" : "fgH"); if (fg) fg.textContent = i ? `FG ${t[0]}-${t[1]} · 3P ${t[2]}-${t[3]}` : P.teams[s].ab;
  }
}

function replayTo(i) { show(S.game.events[i], false); const b = $("simPlay"); if (b) b.textContent = "▶ Watch the average game"; }
function aggBoxes(view) {
  const P = S.P, A = S.avgRun, avg = view === "avg";
  const sc = avg ? A.score : {home: (A.modeTot[0] + A.modeMar[0]) / 2, away: (A.modeTot[0] - A.modeMar[0]) / 2};
  $("scA").textContent = avg ? f1(sc.away) : Math.round(sc.away); $("scH").textContent = avg ? f1(sc.home) : Math.round(sc.home);
  $("simClk").textContent = avg ? `AVG OF ${A.N.toLocaleString()}` : "MOST COMMON"; $("shotClk").textContent = "–";
  setWP(A.homeWin, null);
  $("capP").textContent = avg ? "AVERAGE" : "MOST COMMON"; $("capS").textContent = ""; $("capT").className = "poss h"; $("capB").className = "bdg";
  $("capX").textContent = avg ? `Average box score across ${A.N.toLocaleString()} simulations.`
    : `Most frequent result for each stat. Final margin ${A.modeMar[0] > 0 ? P.teams.home.ab + " by " + A.modeMar[0] : A.modeMar[0] < 0 ? P.teams.away.ab + " by " + (-A.modeMar[0]) : "tie"} (${Math.round(A.modeMar[1] * 100)}% of sims), total ${A.modeTot[0]} (${Math.round(A.modeTot[1] * 100)}%).`;
  const cell = (x, k) => avg ? f1(x.avg[k]) : `${x.mode[k][0]}<span class="dim" style="font-size:8.5px"> ${Math.round(x.mode[k][1] * 100)}%</span>`;
  for (const s of ["away", "home"]) {
    const ps = P.teams[s].players.filter(p => A.players[p.pid]).sort((a, b) => A.players[b.pid].avg.min - A.players[a.pid].avg.min);
    const rows = ps.map((p, k) => { const x = A.players[p.pid]; return {pid: p.pid, last: p.last, starter: k < 5, cells: [Math.round(x.avg.min), cell(x, "pts"), cell(x, "reb"), cell(x, "ast"), cell(x, "tpm")]}; });
    $(s === "away" ? "bxA" : "bxH").innerHTML = boxTable(s, ["MIN", "PTS", "REB", "AST", "3PM"], rows, new Set(ps.map(p => p.pid)), new Set());
    const fg = $(s === "away" ? "fgA" : "fgH"); if (fg) fg.textContent = P.teams[s].ab;
  }
}

// where a shot is taken from, by the kind of shot in the play text (feet from the basket: toward half court, sideways)
function shotSpot(e) {
  const t = e.text || "", R = Math.random, sg = R() < .5 ? -1 : 1;
  if (e.kind === "ft") return [15, 0];
  if (e.kind[0] === "3") return /corner/.test(t) ? [2, 22 * sg] : /step-back|pull-up/.test(t) ? (R() < .5 ? [24.5, (R() - .5) * 6] : [22, 12 * sg]) : SHOT3[Math.floor(R() * SHOT3.length)];
  if (/dunk|layup|putback/.test(t)) return [2.5, (R() - .5) * 4];
  if (/floater/.test(t)) return [9, (R() - .5) * 8];
  if (/hook/.test(t)) return [6, 5 * sg];
  if (/mid-range/.test(t)) return [15, 9 * sg];
  return SHOT2[Math.floor(R() * SHOT2.length)];
}
function actLabel(e) {
  const t = e.text || "";
  if (e.kind === "ft") return `FREE THROWS ${e.pts}/2`;
  if (e.kind === "tov") return /foul/.test(t) ? "OFFENSIVE FOUL" : "TURNOVER";
  if (e.kind === "3miss") return "MISSES THE 3";
  if (e.kind === "2miss") return /dunk|layup/.test(t) ? "NO GOOD" : "MISSES";
  const m = [[/pull-up three/, "PULL-UP 3"], [/catch-and-shoot/, "CATCH & SHOOT 3"], [/corner three/, "CORNER 3"], [/step-back/, "STEP-BACK 3"], [/dunk/, "SLAM DUNK"],
             [/driving layup/, "DRIVING LAYUP"], [/floater/, "FLOATER"], [/mid-range/, "MID-RANGE"], [/hook/, "HOOK SHOT"], [/putback/, "PUTBACK"]].find(([r]) => r.test(t));
  return (m ? m[1] : e.kind === "3pm" ? "THREE" : "BUCKET") + (streakOf(e.a) >= 3 ? " · ON FIRE" : "");
}
function drawDots(e, animate) {
  const P = S.P, layer = $("simDots"); if (!layer) return;
  // home attacks right in the 1st half, left in the 2nd (and overtime)
  const homeRight = e.period <= 2;
  const offRight = e.team === "home" ? homeRight : !homeRight;
  const off = e.team || "home", def = off === "home" ? "away" : "home";
  const pos = {};
  const offIds = e.on[off], defIds = e.on[def];
  const R = Math.random;                                         // visuals never touch the seeded sim stream
  offIds.forEach((pid, k) => pos[pid] = spot(offRight, OFF[k][0] + (R() - .5) * 4, OFF[k][1] + (R() - .5) * 4));
  // the shooter moves to his shot
  let shotAt = null;
  if (e.a && ["2pm", "3pm", "2miss", "3miss", "ft"].includes(e.kind)) {
    const s0 = shotSpot(e);
    shotAt = spot(offRight, s0[0], s0[1]);
    if (e.kind === "ft") { offIds.forEach((pid, k) => { if (pid !== e.a) pos[pid] = spot(offRight, [10, 10, 8, 8, 22][k], [-7, 7, -7, 7, 0][k]); }); }
    pos[e.a] = shotAt;
  }
  defIds.forEach((pid, k) => {
    const o = pos[offIds[k]] || spot(offRight, 10, 0), bx = offRight ? BX[1] : BX[0];
    pos[pid] = [o[0] + (bx - o[0]) * 0.2 + (R() - .5) * 8, o[1] + (250 - o[1]) * 0.18 + (R() - .5) * 8];
  });
  if (e.kind === "tip" || e.kind === "period") {
    const all = [...e.on.home, ...e.on.away];
    all.forEach((pid, k) => { const hm = k < 5, j = k % 5; pos[pid] = [W / 2 + (hm ? -1 : 1) * (j === 4 ? 12 : 60 + j * 35), 250 + [-120, 120, -60, 60, 0][j]]; });
  }
  const dur = animate ? Math.round(1000 / S.speed) : 0;
  const tmOf = pid => P.teams.home.players.some(p => p.pid === pid) ? "home" : "away";
  const ply = pid => P.teams[tmOf(pid)].players.find(p => p.pid === pid);
  const have = new Set();
  for (const pid of [...e.on.home, ...e.on.away]) {
    have.add(pid);
    let g = layer.querySelector(`[data-p="${pid}"]`);
    const p = ply(pid), side = tmOf(pid);
    if (!g) {
      g = document.createElementNS("http://www.w3.org/2000/svg", "g"); g.setAttribute("data-p", pid); g.setAttribute("class", "pdot " + side);
      g.innerHTML = `<g class="bob" style="animation-duration:${(2.4 + Math.random() * 1.8).toFixed(2)}s;animation-delay:-${(Math.random() * 3).toFixed(2)}s"><ellipse class="sh" cx="3" cy="10" rx="19" ry="9"/><circle class="rg" r="21"/><circle class="fl" r="17.5"/><text dy="6.5" text-anchor="middle">${esc(jer(pid) || (p ? p.ini : ""))}</text></g>`;
      g.style.transform = `translate(${W / 2}px,250px)`;
      layer.appendChild(g);
    }
    const isShooter = pid === e.a && shotAt;
    g.style.transition = `transform ${isShooter && dur ? Math.round(480 / Math.max(1, S.speed)) : dur}ms cubic-bezier(.45,.05,.3,1)`;
    const [x, y] = pos[pid] || [W / 2, 250];
    g.style.transform = `translate(${x}px,${y}px)`;
    g.classList.toggle("act", pid === e.a || pid === e.b);
  }
  layer.querySelectorAll(".pdot").forEach(g => { if (!have.has(g.getAttribute("data-p"))) g.remove(); });
  animateEvent(e, pos, offRight, shotAt, animate, ply, tmOf);
}

// ------------------------------------------------------------------ ball animation
// The ball lives on the floor plane (x, y in court pixels) plus a height h in feet. Height lifts the ball
// up the screen and grows it a little; the shadow stays on the floor. Visual randomness uses Math.random so
// the seeded simulation stream is never touched.
const HPX = 9;                                   // screen pixels per foot of height
const RIM_H = 10, REL_H = 7.5;                   // rim height, typical release height
const reduceMotion = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
const BA = {raf: 0, q: [], x: W / 2, y: 250, h: 0, spin: 0, trail: [], fire: false, timers: []};
const lerp = (a, b, t) => a + (b - a) * t;
const EASE = {lin: t => t, out: t => 1 - (1 - t) * (1 - t), inout: t => t < .5 ? 2 * t * t : 1 - 2 * (1 - t) * (1 - t)};
function ballDraw() {
  const sh = $("simShadow"), bl = $("simBall"); if (!sh || !bl) return;
  const sc = 1 + BA.h / 22;
  sh.setAttribute("transform", `translate(${BA.x},${BA.y}) scale(${Math.max(.45, 1 - BA.h / 26)})`);
  sh.setAttribute("opacity", (0.4 * Math.max(.3, 1 - BA.h / 20)).toFixed(2));
  bl.setAttribute("transform", `translate(${BA.x},${BA.y - BA.h * HPX}) scale(${sc}) rotate(${BA.spin})`);
  const tr = $("simTrail"); if (!tr) return;
  const dots = tr.children;
  for (let k = 0; k < dots.length; k++) {
    const p = BA.trail[BA.trail.length - 1 - (k + 1) * 2];
    if (!p || BA.h < 1.5) { dots[k].setAttribute("opacity", 0); continue; }
    dots[k].setAttribute("cx", p[0]); dots[k].setAttribute("cy", p[1]);
    dots[k].setAttribute("fill", BA.fire ? ["#ffef8a", "#ffb43c", "#ff7a1a", "#ff4d1a", "#c8200f"][k] : "#ffd9b0");
    dots[k].setAttribute("r", (BA.fire ? 9 : 6) - k * (BA.fire ? 1.3 : 1));
    dots[k].setAttribute("opacity", ((BA.fire ? .8 : .35) * (1 - k / 5)).toFixed(2));
  }
}
function ballStop() { cancelAnimationFrame(BA.raf); BA.raf = 0; BA.q = []; BA.timers.forEach(clearTimeout); BA.timers = []; BA.trail = []; }
function ballSnap(x, y, h = 0) { ballStop(); BA.x = x; BA.y = y; BA.h = h; ballDraw(); }
function later(fn, ms) { BA.timers.push(setTimeout(fn, ms)); }
// segments: {to:[x,y], dur, h: t => feet, ease, spin, start(), end()}
function ballRun(segs) {
  ballStop(); BA.q = segs.filter(Boolean);
  const tick = ts => {
    const g = BA.q[0]; if (!g) { BA.raf = 0; return; }
    if (g.t0 == null) { g.t0 = ts; g.from = [BA.x, BA.y]; if (g.start) g.start(); }
    const t = g.dur > 0 ? Math.min(1, (ts - g.t0) / g.dur) : 1, k = (g.ease || EASE.lin)(t);
    const to = g.to || g.from;
    BA.x = lerp(g.from[0], to[0], k); BA.y = lerp(g.from[1], to[1], k); BA.h = g.h ? g.h(t) : 0;
    BA.spin += (g.spin || 0);
    BA.trail.push([BA.x, BA.y - BA.h * HPX]); if (BA.trail.length > 14) BA.trail.shift();
    ballDraw();
    if (t >= 1) { BA.q.shift(); if (g.end) g.end(); }
    BA.raf = BA.q.length ? requestAnimationFrame(tick) : 0;
  };
  BA.raf = requestAnimationFrame(tick);
}
// height curves
const dribbleH = n => t => 3.1 * Math.abs(Math.sin(Math.PI * n * t)) ** 0.8;
const arcH = (h0, h1, apex) => t => lerp(h0, h1, t) + 4 * apex * t * (1 - t);
const bounceH = peak => t => 4 * peak * t * (1 - t);

// effects at the hoop
function hoopFx(right, cls) {
  const hp = $("hoop" + (right ? 1 : 0)); if (!hp) return;
  hp.classList.remove("swish", "clank", "slam"); void hp.getBoundingClientRect(); hp.classList.add(cls);
  later(() => hp.classList.remove(cls), 700);
}
// Broadcast-style callout over the player: [number][NAME][ACTION]. Stretched vertically to undo the court tilt.
function popTag(x, y, c, side, big) {
  const pop = $("simPop"); if (!pop) return;
  const cw = 10.4, nW = c.num ? 34 : 0, mW = c.name.length * cw + 22, aW = c.act.length * (cw - .6) + 22, tot = nW + mW + aW;
  let x0 = Math.max(-10, Math.min(W + 10 - tot, x - tot / 2));
  pop.innerHTML = `<g class="ptag ${side} ${big ? "big" : ""}" style="transform:translate(${x0}px,${Math.max(30, y - 50)}px) scale(1,1.25)"><g class="in">
    ${c.num ? `<rect class="n" x="0" y="-17" width="${nW}" height="30"/><text class="tn" x="${nW / 2}" dy="5" text-anchor="middle">${esc(c.num)}</text>` : ""}
    <rect class="m" x="${nW}" y="-17" width="${mW}" height="30"/><text class="tm" x="${nW + mW / 2}" dy="5" text-anchor="middle">${esc(c.name)}</text>
    <path class="a" d="M${nW + mW} -17 h${aW} l-8 30 h-${aW} z"/><text class="ta" x="${nW + mW + aW / 2 - 4}" dy="5" text-anchor="middle">${esc(c.act)}</text></g></g>`;
}

// ------------------------------------------------------------------ sound (Web Audio, off until switched on)
const SND = {on: false, ctx: null};
try { SND.on = localStorage.getItem("sim_sound") === "1"; } catch (e) {}
function actx() { if (!SND.ctx) { try { SND.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { SND.on = false; } } return SND.ctx; }
function noise(c, dur) { const b = c.createBuffer(1, Math.floor(c.sampleRate * dur), c.sampleRate), d = b.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; const n = c.createBufferSource(); n.buffer = b; return n; }
function sfx(kind) {
  if (!SND.on) return; const c = actx(); if (!c) return; if (c.state === "suspended") c.resume();
  const t = c.currentTime, g = c.createGain(); g.connect(c.destination);
  if (kind === "bounce") {
    const o = c.createOscillator(); o.type = "sine"; o.frequency.setValueAtTime(140, t); o.frequency.exponentialRampToValueAtTime(55, t + .12);
    g.gain.setValueAtTime(.55, t); g.gain.exponentialRampToValueAtTime(.001, t + .16); o.connect(g); o.start(t); o.stop(t + .17);
  } else if (kind === "swish") {
    const n = noise(c, .35), f = c.createBiquadFilter(); f.type = "bandpass"; f.frequency.setValueAtTime(5200, t); f.frequency.exponentialRampToValueAtTime(1800, t + .3); f.Q.value = .8;
    g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.35, t + .04); g.gain.exponentialRampToValueAtTime(.001, t + .32); n.connect(f); f.connect(g); n.start(t);
  } else if (kind === "clank") {
    [520, 787, 1210].forEach((fq, i) => { const o = c.createOscillator(), gg = c.createGain(); o.type = i ? "triangle" : "square"; o.frequency.value = fq;
      gg.gain.setValueAtTime(i ? .12 : .08, t); gg.gain.exponentialRampToValueAtTime(.001, t + .35 - i * .06); o.connect(gg); gg.connect(c.destination); o.start(t); o.stop(t + .36); });
  } else if (kind === "cheer") {
    const n = noise(c, 1.4), f = c.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = 1100; f.Q.value = .5;
    g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.22, t + .25); g.gain.exponentialRampToValueAtTime(.001, t + 1.35); n.connect(f); f.connect(g); n.start(t);
  } else if (kind === "horn") {
    [233, 277, 349].forEach(fq => { const o = c.createOscillator(), gg = c.createGain(); o.type = "sawtooth"; o.frequency.value = fq;
      gg.gain.setValueAtTime(.0001, t); gg.gain.exponentialRampToValueAtTime(.07, t + .05); gg.gain.setValueAtTime(.07, t + .7); gg.gain.exponentialRampToValueAtTime(.001, t + .9); o.connect(gg); gg.connect(c.destination); o.start(t); o.stop(t + .95); });
  } else if (kind === "whistle") {
    const o = c.createOscillator(), m = c.createOscillator(), mg = c.createGain(); o.frequency.value = 2900; m.frequency.value = 38; mg.gain.value = 120; m.connect(mg); mg.connect(o.frequency);
    g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.09, t + .03); g.gain.setValueAtTime(.09, t + .3); g.gain.exponentialRampToValueAtTime(.001, t + .38); o.connect(g); o.start(t); m.start(t); o.stop(t + .4); m.stop(t + .4);
  }
}

// ------------------------------------------------------------------ one play on the court
// streaks: three straight makes puts a player "on fire" (flame trail on his shots)
function streakOf(pid) { return (S.streak && S.streak[pid]) || 0; }
function animateEvent(e, pos, offRight, shotAt, animate, ply, tmOf) {
  const bx = offRight ? BX[1] : BX[0], dir = offRight ? 1 : -1;     // dir: +1 when attacking the right basket
  const side = e.team || "home", p = e.a ? ply(e.a) : null, nm = p ? p.last.toUpperCase() : "";
  const pop = $("simPop"); if (pop) pop.innerHTML = "";
  const isShot = ["2pm", "3pm", "2miss", "3miss"].includes(e.kind);
  const fast = !animate || S.speed >= 16 || reduceMotion;
  const k = 1 / Math.max(1, S.speed);                               // time scale
  BA.fire = !!(e.a && streakOf(e.a) >= 3 && isShot);
  const rimFront = [bx - dir * 9, 250 + (Math.random() - .5) * 8];   // where a miss hits the rim
  const floorBy = [bx - dir * 18, 250 + (Math.random() - .5) * 14];  // where a make lands under the net
  const tag = () => {
    if (!e.a || !pos[e.a]) return;
    const [x, y] = pos[e.a], made = e.kind === "2pm" || e.kind === "3pm";
    popTag(x, y, {num: jer(e.a), name: nm, act: actLabel(e)}, side, made && (e.kind === "3pm" || BA.fire));
  };

  // instant mode: put the ball where the play ends
  if (fast) {
    ballStop();
    if (isShot && (e.kind === "2miss" || e.kind === "3miss") && e.b && pos[e.b]) ballSnap(pos[e.b][0] + 12, pos[e.b][1] + 6);
    else if (isShot || e.kind === "ft") ballSnap(floorBy[0], floorBy[1]);
    else if (e.a && pos[e.a]) ballSnap(pos[e.a][0] + 12, pos[e.a][1] + 6);
    else ballSnap(W / 2, 250);
    if (animate) { if (e.kind === "2pm" || e.kind === "3pm") hoopFx(offRight, /dunk/.test(e.text) ? "slam" : "swish"); tag(); }
    return;
  }

  const start = [BA.x, BA.y];
  const carry = (to, dur) => { const d = Math.hypot(to[0] - BA.x, to[1] - BA.y); const n = Math.max(1, Math.round(d / 55));
    for (let i = 1; i <= n; i++) later(() => sfx("bounce"), dur * (i - .5) / n);
    return {to, dur, h: dribbleH(n), ease: EASE.inout, spin: 6}; };

  if (e.kind === "tip" || e.kind === "period") {
    sfx(e.kind === "tip" ? "whistle" : "horn");
    const recv = pos[(e.on[side] || [])[0]] || [W / 2, 250];
    ballRun([{to: [W / 2, 250], dur: 1, h: () => 0}, {to: [W / 2, 250], dur: 520 * k, h: arcH(4, 7, 9), ease: EASE.lin},
             {to: [recv[0] + 12, recv[1] + 6], dur: 380 * k, h: arcH(8, 3, 1), ease: EASE.out}]);
    return;
  }
  if (e.kind === "final") { sfx("horn"); ballRun([{to: [W / 2, 250], dur: 700 * k, h: dribbleH(3), ease: EASE.out}]); return; }

  if (e.kind === "tov") {
    const d = pos[(e.on[side === "home" ? "away" : "home"] || [])[Math.floor(Math.random() * 5)]] || [W / 2, 250];
    const h = pos[e.a] || start;
    ballRun([carry([h[0] + 12, h[1] + 6], 420 * k), {to: [d[0] + 12, d[1] + 6], dur: 320 * k, h: bounceH(1.2), ease: EASE.out, spin: -14, end: tag}]);
    return;
  }

  if (e.kind === "ft") {
    const line = shotAt || pos[e.a];
    const one = (made, last) => [
      {to: [line[0] + 4, line[1]], dur: 260 * k, h: dribbleH(2), start: () => { sfx("bounce"); later(() => sfx("bounce"), 130 * k); }},
      {to: [bx, 250], dur: 560 * k, h: arcH(REL_H, RIM_H, 4.5), ease: EASE.lin, spin: -9},
      made ? {to: [bx, 250], dur: 140 * k, h: t => lerp(RIM_H, 6, t), start: () => { hoopFx(offRight, "swish"); sfx("swish"); }}
           : {to: rimFront, dur: 90 * k, h: () => RIM_H, start: () => { hoopFx(offRight, "clank"); sfx("clank"); }},
      {to: made ? floorBy : [rimFront[0] - dir * 22, rimFront[1] + (Math.random() - .5) * 60], dur: 300 * k, h: made ? t => lerp(6, 0, t * t) : arcH(RIM_H, 0, 2), ease: EASE.lin,
       end: () => { sfx("bounce"); if (last) tag(); }},
      {to: last ? [line[0] + 12, line[1] + 6] : [line[0] + 4, line[1]], dur: 280 * k, h: bounceH(1.6), ease: EASE.out}];
    const m = e.pts || 0;
    ballRun([carry([line[0] + 12, line[1] + 6], 300 * k), ...one(m >= 1, false), ...one(m >= 2, true)]);
    return;
  }

  if (isShot) {
    const made = e.kind === "2pm" || e.kind === "3pm", three = e.kind[0] === "3";
    const dunk = /dunk/.test(e.text), layup = /layup|putback|floater|hook/.test(e.text);
    const from = shotAt || pos[e.a] || start;
    const dist = Math.hypot(from[0] - bx, from[1] - 250) / 10;    // feet
    const flight = (dunk ? 260 : layup ? 380 : 420 + dist * 9) * k;
    const apex = dunk ? 1 : layup ? 3 : three ? 8.5 : 6;
    const segs = [carry([from[0] + 12, from[1] + 6], 460 * k),
      {to: from, dur: 120 * k, h: t => lerp(1, dunk ? 9.5 : REL_H, t), ease: EASE.out}];
    if (made) {
      segs.push({to: [bx, 250], dur: flight, h: arcH(dunk ? 9.5 : REL_H, RIM_H + .4, apex), ease: EASE.lin, spin: -10});
      segs.push({to: [bx, 250], dur: 170 * k, h: t => lerp(RIM_H + .4, 6.5, t), start: () => {
        hoopFx(offRight, dunk ? "slam" : "swish"); sfx("swish"); if (three || BA.fire) later(() => sfx("cheer"), 60); tag(); }});
      segs.push({to: floorBy, dur: 260 * k, h: t => lerp(6.5, 0, t * t), ease: EASE.lin, end: () => sfx("bounce")});
      segs.push({to: [floorBy[0] - dir * 16, floorBy[1] + (Math.random() - .5) * 20], dur: 300 * k, h: bounceH(2.2), ease: EASE.out, end: () => sfx("bounce")});
      segs.push({to: [floorBy[0] - dir * 26, floorBy[1] + (Math.random() - .5) * 20], dur: 200 * k, h: bounceH(.7), ease: EASE.out});
    } else {
      segs.push({to: rimFront, dur: flight, h: arcH(dunk ? 9.5 : REL_H, RIM_H + .3, apex), ease: EASE.lin, spin: -10});
      const rb = e.b && pos[e.b] ? [pos[e.b][0] + 12, pos[e.b][1] + 6] : [bx - dir * 60, 250 + (Math.random() - .5) * 200];
      const mid = [lerp(rimFront[0], rb[0], .35), lerp(rimFront[1], rb[1], .35)];
      segs.push({to: [rimFront[0] + dir * 6, rimFront[1] + (Math.random() - .5) * 10], dur: 110 * k, h: t => RIM_H + .3 + 1.6 * Math.sin(Math.PI * t), start: () => {
        hoopFx(offRight, "clank"); sfx("clank"); tag(); }});
      segs.push({to: mid, dur: 260 * k, h: arcH(RIM_H + .3, 5, 3), ease: EASE.out, spin: 14});
      segs.push({to: rb, dur: 260 * k, h: e.b ? arcH(5, 6.5, 1.5) : arcH(5, 0, 1), ease: EASE.lin, spin: 8,
                 end: () => { if (!e.b) sfx("bounce"); }});
      if (e.b) segs.push({to: rb, dur: 160 * k, h: t => lerp(6.5, 3, t), ease: EASE.out});
    }
    ballRun(segs);
    return;
  }
  // anything else: carry the ball to the player
  if (e.a && pos[e.a]) ballRun([carry([pos[e.a][0] + 12, pos[e.a][1] + 6], 500 * k)]);
}

// how long a play takes on screen at 1× (ms)
function playMs(e) {
  if (e.kind === "ft") return 3300;
  if (["2pm", "3pm", "2miss", "3miss"].includes(e.kind)) return 2050;
  if (e.kind === "tip" || e.kind === "period") return 1500;
  return 1150;
}
// replay the current play by tapping the court
function replayCurrent() {
  if (!S.game || S.playing) return;
  const e = S.game.events[S.i]; if (!e) return;
  const prev = S.game.events[S.i - 1];
  if (prev) drawDots(prev, false);
  setTimeout(() => drawDots(e, true), 60);
}

// ------------------------------------------------------------------ Monte Carlo view
async function runMC() {
  if (S.busy || !S.P) return;
  S.busy = true;
  const bar = $("simBar"); bar.style.display = "block"; $("simRun").disabled = true; $("simRun").textContent = "Simulating…";
  S.mc = await monteCarlo(S.P, 5000, f => { const i = bar.querySelector("i"); if (i) i.style.width = (f * 100).toFixed(0) + "%"; });
  S.busy = false; bar.style.display = "none"; $("simRun").disabled = false; $("simRun").textContent = "Run 5,000 more";
  showMC();
}
// the overnight 10,000-sim run saved by the agent (tools/sim_all.js): shown instantly, no waiting
function pOverQ(q, line) {               // q = [mean, p10..p90]; rough P(X > line) from the deciles
  const v = q.slice(1);
  if (line < v[0]) return Math.min(0.97, 0.9 + 0.1 * (v[0] - line) / Math.max(1, v[0]));
  if (line >= v[8]) return Math.max(0.03, 0.1 * (1 - (line - v[8]) / Math.max(1, v[8])));
  for (let i = 0; i < 8; i++) if (line >= v[i] && line < v[i + 1]) return 1 - (0.1 * (i + 1) + 0.1 * (line - v[i]) / Math.max(1e-9, v[i + 1] - v[i]));
  return 0.5;
}
// the single simulated game closest to the averages (score and every player's line), as a real box score
function typicalHtml(G) {
  const T = G.typical, ps = Object.entries(G.players);
  const tbl = team => `<div class="bxh ${team === G.home ? "h" : "a"}">${esc(team)} ${team === G.home ? T.score.home : T.score.away}</div>
    <table class="bxt"><tr><th></th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>3PM</th></tr>
    ${ps.filter(([, x]) => x.team === team).sort((a, b) => T.players[b[0]][0] - T.players[a[0]][0]).map(([pid, x]) => { const b = T.players[pid];
      return `<tr class="on"><td class="t">${esc(lastName(x.name))}</td><td>${Math.round(b[0])}</td><td><b>${b[1]}</b></td><td>${b[2]}</td><td>${b[3]}</td><td>${b[4]}</td></tr>`; }).join("")}</table>`;
  return `<div class="desc" style="margin:8px 0 4px">Of all ${D.sims.n.toLocaleString()} simulations, this one landed closest to the averages overall: one realistic version of the game, not a forecast of every line.</div>${tbl(G.away)}${tbl(G.home)}`;
}
// every run of the day side by side: how the averages moved as injuries, lineups and minutes changed
function showDay() {
  const el = $("simDay"); if (!el) return;
  const runs = ((D.simday && D.simday.runs) || []).filter(r => r.games[S.gameId]).sort((a, b) => a.ts.localeCompare(b.ts));
  if (!runs.length) { el.innerHTML = '<div class="empty">Each run today (about 8:40am, 2pm, 3:45pm, 6:15pm and 8:45pm ET) is saved here so you can see which numbers hold steady and which move.</div>'; return; }
  const st = S.stat, si = {min: 0, pts: 1, reb: 2, ast: 3, tpm: 4, pra: 5, ra: 6}[st];
  const tm = ts => new Date(ts).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}).replace(" ", "").toLowerCase();
  const G0 = runs[runs.length - 1].games[S.gameId], P = S.P;
  const avgOf = xs => xs.reduce((t, x) => t + x, 0) / xs.length;
  const scores = runs.map(r => r.games[S.gameId]);
  const pids = Object.keys(G0.players);
  const rows = pids.map(pid => {
    const vals = runs.map(r => (r.games[S.gameId].players[pid] || [])[si]).filter(v => v != null);
    const name = (D.sims && D.sims.games[S.gameId] && D.sims.games[S.gameId].players[pid] || {}).name || pid;
    return {pid, name, vals, mean: avgOf(vals), range: Math.max(...vals) - Math.min(...vals), moved: vals.length > 1 ? vals[vals.length - 1] - vals[0] : 0};
  }).filter(r => r.mean >= (st === "tpm" ? 0.3 : 1)).sort((a, b) => b.mean - a.mean);
  el.innerHTML = `<div class="desc">${runs.length} run${runs.length > 1 ? "s" : ""} today, 10,000 simulations each. The sim is seeded, so the same inputs always give the same answer: any movement below comes from new information (injury report, lineups, minutes), not luck. <b>Avg of runs</b> is the steadiest single number; <b>moved</b> is the change since the first run.</div>
    <div class="scroll"><table class="tbl"><tr><th>Run</th><th>Score</th><th>${esc(G0 && P ? P.teams.home.ab : "Home")} win</th><th>Total</th></tr>
      ${runs.map((r, i) => { const g = r.games[S.gameId]; return `<tr><td class="t">${tm(r.ts)}</td><td>${f1(g.avg.away)}–${f1(g.avg.home)}</td><td>${Math.round(g.win_home * 100)}%</td><td>${f1(g.total)}</td></tr>`; }).join("")}
      ${runs.length > 1 ? `<tr><td class="t"><b>Avg of runs</b></td><td><b>${f1(avgOf(scores.map(g => g.avg.away)))}–${f1(avgOf(scores.map(g => g.avg.home)))}</b></td><td><b>${Math.round(avgOf(scores.map(g => g.win_home)) * 100)}%</b></td><td><b>${f1(avgOf(scores.map(g => g.total)))}</b></td></tr>` : ""}</table></div>
    <div class="chips" id="dayS" style="margin-top:8px">${["min", "pts", "reb", "ast", "tpm", "pra", "ra"].map(k => `<button data-s="${k}" class="${k === st ? "on" : ""}">${k === "min" ? "MIN" : STATN[k]}</button>`).join("")}</div>
    <div class="scroll"><table class="tbl"><tr><th>Player</th>${runs.map(r => `<th>${tm(r.ts)}</th>`).join("")}<th>Avg of runs</th><th>Moved</th></tr>
      ${rows.map(r => `<tr data-pid="${r.pid}" data-stat="${st === "min" ? "pts" : st}" style="cursor:pointer"><td class="t">${esc(lastName(r.name))}</td>${r.vals.map(v => `<td class="dim">${f1(v)}</td>`).join("")}
        <td><b>${f1(r.mean)}</b></td><td class="${Math.abs(r.moved) >= (st === "tpm" ? 0.4 : 1.5) ? (r.moved > 0 ? "h" : "m") : "dim"}">${r.moved > 0 ? "+" : ""}${f1(r.moved)}</td></tr>`).join("")}</table></div>`;
  $("dayS").querySelectorAll("button").forEach(b => b.onclick = () => { if (b.dataset.s !== "min") S.stat = b.dataset.s; S.dayStat = b.dataset.s; showDay2(b.dataset.s); });
  bindPlayerLinks(el);
}
function showDay2(k) { const keep = S.stat; S.stat = k; showDay(); if (k === "min") S.stat = keep; }
// ---------------- live DraftKings lines: opening, closing, live snapshots, and "sim from here" ----------------
const CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba";
function dkSnap(item, st) {
  const t = item[st] || {}, h = (item.homeTeamOdds || {})[st] || {}, a = (item.awayTeamOdds || {})[st] || {};
  const n = v => v == null || v === "" ? null : parseFloat(String(v).replace("+", ""));
  return {sp: h.pointSpread ? n(h.pointSpread.american) : null, sp_h: h.spread ? n(h.spread.decimal) : null, sp_a: a.spread ? n(a.spread.decimal) : null,
          tot: t.total ? n(t.total.american) : null, ov: t.over ? n(t.over.decimal) : null, un: t.under ? n(t.under.decimal) : null,
          ml_h: h.moneyLine ? n(h.moneyLine.decimal) : null, ml_a: a.moneyLine ? n(a.moneyLine.decimal) : null};
}
async function pollLive() {
  const gid = S.gameId;
  try {
    const [od, o200, sb] = await Promise.all([fetch(`${CORE}/events/${gid}/competitions/${gid}/odds`).then(r => r.json()).catch(() => ({})),
      fetch(`${CORE}/events/${gid}/competitions/${gid}/odds/200`).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(`https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=${gid}`).then(r => r.json())]);
    // provider 200 ("DraftKings - Live Odds") carries the in-game line; provider 100's "current" stays frozen at the close.
    // ESPN refreshes it irregularly, so it can lag the real DraftKings line (see staleWhy).
    const item = (o200 && o200.current ? o200 : null) || (od.items || []).find(i => String((i.provider || {}).id) === "200") || (od.items || []).find(i => String((i.provider || {}).id) === "100");
    const comp = sb.header.competitions[0], st = comp.status, t = {}, side = {};
    const ls = {}, tm = {};
    comp.competitors.forEach(c => { t[c.homeAway] = +c.score || 0; side[String(c.team ? c.team.id : c.id)] = c.homeAway;
      ls[c.homeAway] = (c.linescores || []).map(x => +(x.displayValue ?? x.value ?? 0));
      tm[c.homeAway] = {ab: (c.team || {}).abbreviation, rec: ((c.record || []).find(r => r.type === "total") || (c.record || [])[0] || {}).summary || ""}; });
    const clk = String(st.displayClock || "0"), sec = clk.includes(":") ? +clk.split(":")[0] * 60 + +clk.split(":")[1] : +clk;
    S.now = {state: st.type.state, period: st.period, clock: sec, detail: st.type.shortDetail, score: [t.away || 0, t.home || 0],
             line: item ? dkSnap(item, "current") : null, box: liveBox(sb, side), plays: livePlays(sb, side), ls, teams: tm, gid, at: new Date()};
  } catch (e) { S.now = S.now || null; }
  if (S.gameId !== gid) return;
  const n0 = S.now;
  if (n0 && n0.line) {                               // when did DraftKings' line on ESPN last change, and at what margin?
    S.lineSeen = S.lineSeen || {}; const k = lineSig(n0.line), seen = S.lineSeen[gid];
    if (!seen || seen.key !== k) S.lineSeen[gid] = {key: k, mar: n0.score[1] - n0.score[0], tot: n0.score[0] + n0.score[1], at: n0.detail};
  }
  showLive();
  const n = S.now; if (n && n.state === "in" && (!S.nowSim || S.nowSim.key !== liveKey(n))) liveSimFromHere();
}
const lineSig = L => `${L.sp}|${L.tot}|${L.ml_h}|${L.ml_a}`;
// Live sportsbooks keep spreads and totals close to 50/50. If the sim makes one side of DraftKings' live spread or total
// a heavy favorite, or the line hasn't moved while the score swung, ESPN's copy of the line has almost surely fallen behind.
function staleWhy(n, m, H) {
  const L = n && n.line; if (!L) return null;
  const why = [];
  if (m && m.p_home_cover != null && (m.p_home_cover < 0.22 || m.p_home_cover > 0.78))
    why.push(`the sim makes one side of the ${H} ${spS(L.sp)} spread ${pcS(Math.max(m.p_home_cover, 1 - m.p_home_cover))}`);
  if (m && m.p_over != null && (m.p_over < 0.22 || m.p_over > 0.78))
    why.push(`one side of the ${f1(L.tot)} total ${pcS(Math.max(m.p_over, 1 - m.p_over))}`);
  const seen = S.lineSeen && S.lineSeen[S.gameId];
  if (seen && seen.key === lineSig(L)) {
    const dm = Math.abs((n.score[1] - n.score[0]) - seen.mar), dt = n.score[0] + n.score[1] - seen.tot;
    if (dm >= 8 || dt >= 25) why.push(`the line hasn't changed since ${seen.at} while the margin moved ${dm} and ${dt} points were scored`);
  }
  return why.length ? why : null;
}
// Probability of a bet from the latest live sims, at any line (so you can price the number you see in your app).
function pAt(m, kind, side, point) {
  if (!m || !m.mg) return null;
  const arr = kind === "tot" ? m.tt : m.mg; let w = 0, l = 0;
  for (let i = 0; i < arr.length; i++) {
    const v = kind === "sp" ? (side === "home" ? arr[i] : -arr[i]) + point : kind === "tot" ? (side === "over" ? arr[i] - point : point - arr[i]) : (side === "home" ? arr[i] : -arr[i]);
    if (v > 0) w++; else if (v < 0) l++;
  }
  return w + l ? w / (w + l) : null;
}
const decOf = am => { am = parseFloat(String(am).replace("−", "-").replace("+", "")); return !am || isNaN(am) ? null : am > 0 ? 1 + am / 100 : 1 + 100 / -am; };
const amOf = d => d >= 2 ? Math.round((d - 1) * 100) : -Math.round(100 / (d - 1));
// One-tap logging for game lines (spread, total, moneyline), live or pregame, into your book.
S.lbets = S.lbets || {};
function linePref(kind, side, point, dec, live) {
  const g = (D.players.games || []).find(x => x.espn_id === S.gameId) || {}, Hn = g.home_name || g.home, An = g.away_name || g.away;
  const am = amOf(dec), ab = side === "home" ? g.home : g.away;
  const title = kind === "tot" ? `${side === "over" ? "Over" : "Under"} ${f1(point)} · Total` : kind === "ml" ? `${ab} · Moneyline` : `${ab} ${spS(point)} · Spread`;
  const n = S.now;
  return {title: title + (live ? " · Live" : ""), sub: `${g.away} @ ${g.home}` + (live && n ? ` · bet at ${n.detail}, ${g.away} ${n.score[0]}–${n.score[1]} ${g.home}` : ` · ${tod(g.start)}`),
    odds: (am > 0 ? "+" : "") + am, book: "DraftKings",
    leg: {sport: "basketball_nba", espn_id: g.espn_id, commence: g.start, home: Hn, away: An,
          market: kind === "tot" ? "totals" : kind === "ml" ? "h2h" : "spreads", outcome: kind === "tot" ? (side === "over" ? "Over" : "Under") : (side === "home" ? Hn : An),
          point: kind === "ml" ? null : point}};
}
function lbetKey(kind, side, point, dec, live) { const k = [kind, side, point, dec, live ? 1 : 0].join("|"); S.lbets[k] = () => linePref(kind, side, point, dec, live); return k; }
// buttons for every DraftKings game line (L = a dkSnap: sp, sp_h, sp_a, tot, ov, un, ml_h, ml_a)
function lineBtns(L, live) {
  const g = (D.players.games || []).find(x => x.espn_id === S.gameId) || {}; if (!L) return "";
  const b = (kind, side, point, dec, label) => dec ? `<button class="lbtn" data-lbet="${esc(lbetKey(kind, side, point, dec, live))}">${esc(label)} <b>${am_(dec)}</b></button>` : "";
  return `<div class="lbtns">${[
    L.sp != null ? b("sp", "away", -L.sp, L.sp_a, `${g.away} ${spS(-L.sp)}`) + b("sp", "home", L.sp, L.sp_h, `${g.home} ${spS(L.sp)}`) : "",
    L.tot != null ? b("tot", "over", L.tot, L.ov, `O ${f1(L.tot)}`) + b("tot", "under", L.tot, L.un, `U ${f1(L.tot)}`) : "",
    b("ml", "away", null, L.ml_a, `${g.away} ML`) + b("ml", "home", null, L.ml_h, `${g.home} ML`)].join("")}</div>`;
}
if (typeof document !== "undefined") document.addEventListener("click", e => {
  const lb = e.target.closest && e.target.closest("[data-lbet]");
  if (lb) { e.preventDefault(); e.stopPropagation(); const f = S.lbets[lb.dataset.lbet]; if (f && typeof openLogBet === "function") openLogBet(f()); return; }
  const go = e.target.closest && e.target.closest("[data-plgo]");
  if (go) { const f = go.closest(".plf"); S.pl = {k: f.querySelector(".plk").value, p: f.querySelector(".plp").value, o: f.querySelector(".plo").value}; priceIt(f); }
}, true);
// "Price the line you see": the sim's chance at your number and price
function priceIt(f) { const out = f.querySelector(".plout"); if (out) out.innerHTML = priceHtml(); }
function priceHtml() {
  const m = S.nowSim, pl = S.pl || {}; if (!pl.k) return "";
  const [kind, side] = pl.k.split("_"), point = parseFloat(pl.p), dec = decOf(pl.o);
  if (!m || !m.mg) return '<div class="desc">Waiting for the live sim.</div>';
  if (kind !== "ml" && isNaN(point)) return '<div class="desc">Enter the line, e.g. 5.5 or -5.5.</div>';
  if (!dec) return '<div class="desc">Enter the price, e.g. -110 or +150.</div>';
  const p = pAt(m, kind, side, point), ev = p * dec - 1, need = 1 / dec;
  const g = (D.players.games || []).find(x => x.espn_id === S.gameId) || {};
  const label = kind === "tot" ? `${side === "over" ? "Over" : "Under"} ${f1(point)}` : kind === "ml" ? `${side === "home" ? g.home : g.away} ML` : `${side === "home" ? g.home : g.away} ${spS(point)}`;
  return `<div style="margin-top:6px;font-size:14px">${esc(label)} ${am_(dec)}: sim <b>${pcS(p)}</b> vs ${pcS(need)} needed · <b class="${ev >= 0.05 ? "gold" : ev > 0 ? "h" : "dim"}">${ev > 0 ? "+" : ""}${Math.round(ev * 100)}% EV</b> ${ev >= 0.05 ? '<b class="gold">Value</b>' : ev >= 0.02 ? '<span class="h">Lean</span>' : '<span class="dim">Pass</span>'}
    <button class="lbtn" data-lbet="${esc(lbetKey(kind, side, kind === "ml" ? null : point, dec, true))}">+ Log it</button></div>`;
}
function priceForm(H, A) {
  const pl = S.pl || {k: "sp_away", p: "", o: ""};
  const opt = (v, l) => `<option value="${v}" ${pl.k === v ? "selected" : ""}>${esc(l)}</option>`;
  return `<div class="plf"><div class="lbl">Price the line you see in your app</div>
    <div class="plrow"><select class="plk">${opt("sp_away", A + " spread")}${opt("sp_home", H + " spread")}${opt("tot_over", "Over")}${opt("tot_under", "Under")}${opt("ml_away", A + " ML")}${opt("ml_home", H + " ML")}</select>
      <input class="plp" type="number" step="0.5" inputmode="decimal" placeholder="Line" value="${esc(pl.p || "")}"><input class="plo" inputmode="text" placeholder="Odds" value="${esc(pl.o || "")}">
      <button class="btn ghost" data-plgo="1" style="padding:6px 10px;font-size:13px">Price</button></div><div class="plout">${S.pl && S.pl.o ? priceHtml() : ""}</div>
    <div class="desc">Uses the same live sims as the table, so the score and clock are the ones shown above. Re-prices on its own as the game moves.</div></div>`;
}
// don't redraw a panel while you're typing in it
const editing = () => typeof document !== "undefined" && document.activeElement && !!document.activeElement.closest && !!document.activeElement.closest(".plf");
const liveKey = n => `${n.period}|${n.clock}|${n.score}|${n.line ? n.line.sp + "/" + n.line.tot : ""}`;
// ESPN's live box score, same feed the scoreboard uses (updates as plays are logged, usually within a minute of the play).
function liveBox(sb, side) {
  const out = {home: [], away: []};
  for (const t of ((sb.boxscore || {}).players || [])) {
    const s = side[String(t.team && t.team.id)]; if (!s) continue;
    for (const grp of (t.statistics || [])) {
      const L = grp.labels || [];
      for (const a of (grp.athletes || [])) {
        if (a.didNotPlay || !a.stats || !a.stats.length) continue;
        const v = k => a.stats[L.indexOf(k)];
        out[s].push({id: a.athlete.id, name: a.athlete.shortName || a.athlete.displayName, starter: !!a.starter, min: +v("MIN") || 0, pts: +v("PTS") || 0,
                     reb: +v("REB") || 0, ast: +v("AST") || 0, fg: v("FG") || "0-0", tp: v("3PT") || "0-0", pf: +v("PF") || 0, pm: v("+/-") || ""});
      }
    }
  }
  for (const s of ["home", "away"]) out[s].sort((a, b) => (b.starter - a.starter) || b.min - a.min);
  return out;
}
function livePlays(sb, side) {
  return (sb.plays || []).filter(p => p.text && !/^(End|Start) of/.test(p.text)).slice(-8).reverse()
    .map(p => ({q: (p.period || {}).number, clk: (p.clock || {}).displayValue, text: p.text, sc: !!p.scoringPlay, s: side[String((p.team || {}).id)], score: [p.awayScore, p.homeScore]}));
}
// Simulates the rest of the game from the live score and clock. Runs on its own after every refresh where the score, clock or line changed.
async function liveSimFromHere() {
  const n = S.now, P = S.P, gid = S.gameId; if (!n || !P || S.simBusy) return;
  S.simBusy = true; showLive();
  const L = n.line || {}, N = 1500; let cw = 0, cl = 0, ow = 0, ol = 0, hw = 0, tot = 0, mar = 0;
  const mg = new Int16Array(N), tt = new Int16Array(N);
  setSeed(null);
  for (let i = 0; i < N; i++) {
    const r = simGame(P, false, {period: n.period, clock: n.clock, away: n.score[0], home: n.score[1]}), h = r.score.home, a = r.score.away;
    if (L.sp != null) { const m = h + L.sp - a; if (m > 0) cw++; else if (m < 0) cl++; }
    if (L.tot != null) { const d = h + a - L.tot; if (d > 0) ow++; else if (d < 0) ol++; }
    if (h > a) hw++; tot += h + a; mar += h - a; mg[i] = h - a; tt[i] = h + a;
    if (i % 300 === 299) await new Promise(r => setTimeout(r, 0));
  }
  S.nowSim = {key: liveKey(n), p_home_cover: cw + cl ? cw / (cw + cl) : null, p_over: ow + ol ? ow / (ow + ol) : null, p_home_win: hw / N, avg_tot: tot / N, avg_mar: mar / N, n: N, mg, tt};
  S.simBusy = false;
  if (S.gameId === gid) showLive();
}
const am_ = d => d == null ? "—" : d >= 2 ? "+" + Math.round((d - 1) * 100) : "−" + Math.round(100 / (d - 1));
const spS = v => v == null ? "—" : (v > 0 ? "+" : "") + f1(v);
const pcS = p => p == null ? "—" : Math.round(p * 100) + "%";
// The "is this live number worth it?" box: close vs live, pace, the sim's chance vs what the price needs.
function liveValueHtml(n, c, A, H) {
  const L = n.line || {}, m = S.nowSim && S.nowSim.key === liveKey(n) ? S.nowSim : null;
  const reg = n.period <= 4, el = reg ? (n.period - 1) * 720 + (720 - n.clock) : 2880;
  const pts = n.score[0] + n.score[1], pace = reg && el >= 180 ? pts / el * 2880 : null;
  const rows = [], add = (label, vs, p, price, kind, side, point) => { const need = price ? 1 / price : null, ev = p != null && price ? p * price - 1 : null; rows.push({label, vs, p, price, need, ev, kind, side, point}); };
  const has = v => v != null && c && c[v] != null && L[v] != null;
  add(`Over ${f1(L.tot)}`, has("tot") ? c.tot - L.tot : null, m && m.p_over, L.ov, "tot", "over", L.tot);
  add(`Under ${f1(L.tot)}`, has("tot") ? L.tot - c.tot : null, m && m.p_over != null ? 1 - m.p_over : null, L.un, "tot", "under", L.tot);
  add(`${H} ${spS(L.sp)}`, has("sp") ? L.sp - c.sp : null, m && m.p_home_cover, L.sp_h, "sp", "home", L.sp);
  add(`${A} ${spS(L.sp == null ? null : -L.sp)}`, has("sp") ? c.sp - L.sp : null, m && m.p_home_cover != null ? 1 - m.p_home_cover : null, L.sp_a, "sp", "away", L.sp == null ? null : -L.sp);
  add(`${H} ML`, null, m && m.p_home_win, L.ml_h, "ml", "home", null);
  add(`${A} ML`, null, m && m.p_home_win != null ? 1 - m.p_home_win : null, L.ml_a, "ml", "away", null);
  const stale = staleWhy(n, m, H);
  if (stale) rows.forEach(r => { r.ev = null; });
  const verdict = ev => ev == null ? `<span class="dim">${S.simBusy ? "…" : "—"}</span>` : ev >= 0.05 ? '<b class="gold">Value</b>' : ev >= 0.02 ? '<span class="h">Lean</span>' : '<span class="dim">Pass</span>';
  const best = rows.filter(r => r.ev != null && r.price).sort((a, b) => b.ev - a.ev)[0];
  const head = stale ? `<div class="stale">⚠ <b>DraftKings' live line on ESPN looks out of date</b>: ${esc(stale.join("; "))}. Live books keep spreads and totals near 50/50, so this almost always means ESPN's feed hasn't caught up. EV is hidden. Check the line in the DraftKings app and price it below.</div>`
    : !L.tot && !L.sp ? '<div class="desc">DraftKings has no live line posted right now (often suspended during reviews and free throws).</div>'
    : !m ? `<div class="desc">${S.simBusy ? "Simulating the rest of the game from this score and clock…" : "Waiting for the sim."}</div>`
    : best && best.ev >= 0.02 ? `<div style="font-size:14px;margin:2px 0 4px">Best live number: <b class="gold">${esc(best.label)} ${am_(best.price)}</b>. Sim ${pcS(best.p)} vs ${pcS(best.need)} needed to break even (<b>${best.ev > 0 ? "+" : ""}${Math.round(best.ev * 100)}% EV</b>).</div>`
    : '<div style="font-size:14px;margin:2px 0 4px">Nothing clears +2% EV right now: <b>pass</b> and check back.</div>';
  const lineRow = (lab, cv, lv, fmt) => cv == null || lv == null ? "" : `<span>${lab} ${fmt(cv)} → <b style="color:var(--text)">${fmt(lv)}</b>${lv !== cv ? ` <span class="gold">(${lv > cv ? "+" : ""}${f1(lv - cv)})</span>` : ""}</span>`;
  const proj = [pace != null ? `on pace for <b style="color:var(--text)">${Math.round(pace)}</b>` : "", m ? `sim projects <b style="color:var(--text)">${f1(m.avg_tot)}</b> (${esc(m.avg_mar >= 0 ? H : A)} by ${f1(Math.abs(m.avg_mar))})` : ""].filter(Boolean).join(" · ");
  return `<div style="border:1px solid var(--gold);border-radius:12px;padding:10px;margin:6px 0 8px;background:#E5C1580d">
    <div class="lbl" style="margin-top:0;color:var(--gold)">Live value · is the live number worth it?</div>
    ${head}
    <div class="desc" style="display:flex;flex-wrap:wrap;gap:4px 12px;margin-bottom:4px">${lineRow("Total", c && c.tot, L.tot, f1)}${lineRow(H + " spread", c && c.sp, L.sp, spS)}</div>
    ${proj ? `<div class="desc" style="margin-bottom:4px">${proj}</div>` : ""}
    <div class="scroll"><table class="tbl"><tr><th>Live bet${stale ? ' <span class="m">(stale)</span>' : ""}</th><th>DK</th><th>CLV</th><th>Sim</th><th>Need</th><th>EV</th><th></th></tr>
      ${rows.map(r => `<tr${r.ev != null && r.ev >= 0.05 ? ' class="hl"' : ""}><td class="t">${esc(r.label)}${r.ev != null && r.ev >= 0.02 ? " " + verdict(r.ev) : ""}</td><td>${am_(r.price)}</td><td class="${r.vs == null || r.vs === 0 ? "dim" : r.vs > 0 ? "h" : "m"}">${r.vs == null ? "—" : (r.vs > 0 ? "+" : "") + f1(r.vs)}</td>
        <td><b>${pcS(r.p)}</b></td><td class="dim">${pcS(r.need)}</td><td class="${r.ev == null ? "dim" : r.ev >= 0.05 ? "gold" : r.ev > 0 ? "h" : "dim"}">${r.ev == null ? "—" : (r.ev > 0 ? "+" : "") + Math.round(r.ev * 100) + "%"}</td><td>${r.price ? `<button class="lbtn sm" data-lbet="${esc(lbetKey(r.kind, r.side, r.point, r.price, true))}" aria-label="Log this bet">+</button>` : ""}</td></tr>`).join("")}</table></div>
    ${m && m.mg ? priceForm(H, A) : ""}
    <div class="desc" style="margin-top:6px"><b>+</b> logs the bet in your book at DraftKings' live price. <b>CLV</b>: points better (+) or worse (−) than the closing number for that side, your live CLV. A better number isn't value by itself, because the score changed too. <b>Value</b> means the sim's chance from this exact score and clock beats what DraftKings' price needs (5%+ EV; Lean is 2-5%). The sim knows the score, clock and each player's rates but not foul trouble or in-game injuries, so trust your read of the flow on those. Its live calls are graded every morning in Trends.</div>
  </div>`;
}
function liveBoxHtml(n, A, H) {
  const B = n.box || {}; if (!(B.home || []).length && !(B.away || []).length) return "";
  const tb = (ab, rs) => {
    const sum = k => rs.reduce((t, r) => t + r[k], 0), tp = rs.reduce((t, r) => { const [m_, a_] = String(r.tp).split("-").map(Number); return [t[0] + (m_ || 0), t[1] + (a_ || 0)]; }, [0, 0]);
    return `<div class="scroll"><table class="tbl"><tr><th>${esc(ab)}</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>3PT</th><th>FG</th><th>PF</th><th>+/-</th></tr>
      ${rs.map(r => `<tr${r.pf >= 5 ? ' class="hl"' : ""}><td class="t">${esc(r.name)}${r.starter ? "" : ' <span class="dim">·</span>'}</td><td class="dim">${r.min}</td><td><b>${r.pts}</b></td><td>${r.reb}</td><td>${r.ast}</td><td>${esc(r.tp)}</td><td class="dim">${esc(r.fg)}</td><td class="${r.pf >= 4 ? "m" : "dim"}">${r.pf}</td><td class="dim">${esc(r.pm)}</td></tr>`).join("")}
      <tr><td class="t"><b>Team</b></td><td></td><td><b>${sum("pts")}</b></td><td><b>${sum("reb")}</b></td><td><b>${sum("ast")}</b></td><td><b>${tp[0]}-${tp[1]}</b></td><td></td><td>${sum("pf")}</td><td></td></tr></table></div>`;
  };
  return `<details id="liveBoxD"${S.boxOpen === false ? "" : " open"}><summary class="lbl" style="cursor:pointer">Live box score</summary>
    ${tb(A, B.away || [])}${tb(H, B.home || [])}<div class="desc">Bench players marked ·. Fouls in red at 4+, row highlighted at 5.</div></details>`;
}
function livePlaysHtml(n, A, H) {
  const ps = n.plays || []; if (!ps.length) return "";
  return `<details id="livePlayD"${S.playsOpen ? " open" : ""}><summary class="lbl" style="cursor:pointer">Last plays</summary>
    <div class="scroll"><table class="tbl">${ps.map(p => `<tr><td class="dim">Q${p.q} ${esc(p.clk || "")}</td><td class="t" style="white-space:normal">${p.sc ? "<b>" : ""}${esc(p.s === "home" ? H : p.s === "away" ? A : "")} ${esc(p.text)}${p.sc ? "</b>" : ""}</td><td>${p.score[0]}–${p.score[1]}</td></tr>`).join("")}</table></div></details>`;
}
function showLive() {
  if (typeof window.gamesLiveHook === "function") window.gamesLiveHook();
  const el = $("simLive"); if (!el || !S.P) return;
  if (editing() && el.contains(document.activeElement)) return;
  const G = (D.dkday && D.dkday.games || {})[S.gameId] || {}, P = S.P, H = P.teams.home.ab, A = P.teams.away.ab;
  const g = (D.players.games || []).find(x => x.espn_id === S.gameId) || {};
  const isLive = (S.now && S.now.state === "in") || g.state === "in" || (g.state === "pre" && g.start && new Date(g.start) < Date.now() && !(S.now && S.now.state === "post"));
  clearInterval(S.liveTimer);
  if (isLive || (g.state === "pre" && new Date(g.start) - Date.now() < 15 * 60e3)) S.liveTimer = setInterval(() => { if (store.get("tab") === "sim") pollLive(); }, 30000);
  if (isLive && !S.now) { pollLive(); }
  const o = G.open, c = G.close;
  const mv = (x, y) => x == null || y == null || x === y ? "" : ` <span class="${y > x ? "h" : "m"}">(${y > x ? "+" : ""}${f1(y - x)})</span>`;
  const oc = o || c ? `<div class="scroll"><table class="tbl"><tr><th></th><th>Opened</th><th>Closed</th></tr>
      <tr><td class="t">${esc(H)} spread</td><td>${spS(o && o.sp)}</td><td>${spS(c && c.sp)}${mv(o && o.sp, c && c.sp)}</td></tr>
      <tr><td class="t">Total</td><td>${f1(o && o.tot)}</td><td>${f1(c && c.tot)}${mv(o && o.tot, c && c.tot)}</td></tr>
      <tr><td class="t">${esc(H)} / ${esc(A)} ML</td><td>${am_(o && o.ml_h)} / ${am_(o && o.ml_a)}</td><td>${am_(c && c.ml_h)} / ${am_(c && c.ml_a)}</td></tr></table></div>` : "";
  const qc = s => s.detail && /half/i.test(s.detail) ? "Half" : `Q${Math.min(s.period, 4)}${s.period > 4 ? " OT" : ""} ${Math.floor(s.clock / 60)}:${String(Math.floor(s.clock % 60)).padStart(2, "0")}`;
  const snaps = (G.live || []);
  const rows = snaps.map(s => { const L = s.line || {}, m = s.sim || {};
    return `<tr><td class="t">${qc(s)}</td><td>${s.score[0]}–${s.score[1]}</td><td>${spS(L.sp)}</td><td>${f1(L.tot)}</td><td>${am_(L.ml_h)}</td>
      <td class="dim">${pcS(m.p_home_cover)} · ${pcS(m.p_over)} · ${pcS(m.p_home_win)}</td><td>${s.res && s.res.stale ? '<span class="dim">stale</span>' : s.res ? `${s.res.spread_home === "won" ? H + " ✓" : s.res.spread_home === "lost" ? A + " ✓" : "push"}` : ""}</td></tr>`; }).join("");
  let now = "";
  if (S.now && S.now.state === "in") {
    const n = S.now, up = n.at ? n.at.toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"}).toLowerCase() : "";
    now = `<div style="display:flex;align-items:center;gap:8px;justify-content:space-between">
        <div style="font-size:16px;font-weight:700">${esc(A)} ${n.score[0]} – ${n.score[1]} ${esc(H)} <span class="desc" style="font-weight:400">· ${esc(n.detail)}</span></div>
        <button class="btn ghost" id="liveRef" style="padding:5px 10px;font-size:12px">↻ Refresh</button></div>
      <div class="desc">Updated ${esc(up)} · refreshes every 30s while this tab is open</div>
      ${liveValueHtml(n, c, A, H)}${liveBoxHtml(n, A, H)}${livePlaysHtml(n, A, H)}`;
  } else if (S.now && S.now.state === "post") {
    now = `<div class="lbl">Final · ${esc(A)} ${S.now.score[0]}–${S.now.score[1]} ${esc(H)}</div>${liveBoxHtml(S.now, A, H)}`;
  }
  el.innerHTML = `${now}
    ${now ? '<div class="lbl">Opening and closing lines</div>' : '<div class="desc">DraftKings\' lines from ESPN\'s free feed: where they opened and closed, then live during the game with the score, a live box score and a sim from the current score and clock. Live snapshots are also saved about every 15 minutes and graded the next morning in Trends.</div>'}
    ${oc || '<div class="empty">No DraftKings lines saved for this game yet (they arrive with the morning research run).</div>'}
    ${rows ? `<div class="lbl">Saved live snapshots</div><div class="scroll"><table class="tbl"><tr><th>When</th><th>${esc(A)}–${esc(H)}</th><th>${esc(H)} sprd</th><th>Total</th><th>${esc(H)} ML</th><th>Sim cover · over · win</th><th>Spread</th></tr>${rows}</table></div>` : (isLive ? "" : '<div class="desc" style="margin-top:6px">Live tracking starts when the game tips off.</div>')}`;
  const r = $("liveRef"); if (r) r.onclick = () => { r.textContent = "…"; pollLive(); };
  const bd = $("liveBoxD"); if (bd) bd.ontoggle = () => { S.boxOpen = bd.open; };
  const pd = $("livePlayD"); if (pd) pd.ontoggle = () => { S.playsOpen = pd.open; };
}
// The sim's probabilities at DraftKings' own lines, next to DraftKings' no-vig price and payout.
function showVs(elId, gid) {
  const el = $(elId || "simVs"); if (!el) return;
  const G = D.sims && D.sims.games && D.sims.games[gid || S.gameId], V = G && G.vs;
  if (!V || (!V.game.length && !V.props.length)) {
    el.innerHTML = '<div class="empty">DraftKings lines show up here after the agent\'s next scans: game lines around 10am, 3pm and 6:35pm ET, player props around 2pm. The sim then re-prices each one at the next run.</div>'; return;
  }
  const amS_ = a => (a > 0 ? "+" : "") + a, pc = p => p == null ? "—" : Math.round(p * 100) + "%";
  const evc = ev => ev >= 0.05 ? "gold" : ev > 0 ? "h" : "dim";
  const MKN = {spreads: "Spread", totals: "Total", h2h: "Moneyline"};
  const gl = V.game.map(x => ({...x, ev: x.p_sim * x.price - 1, edge: x.p_sim - x.p_dk}));
  const order = {spreads: 0, totals: 1, h2h: 2};
  gl.sort((a, b) => order[a.market] - order[b.market]);
  const props = V.props.map(x => {
    const evO = x.p_over_sim * x.over - 1, evU = (1 - x.p_over_sim) * x.under - 1, over = evO >= evU;
    return {...x, side: over ? "Over" : "Under", p_sim: over ? x.p_over_sim : 1 - x.p_over_sim, p_dk: over ? x.p_over_dk : 1 - x.p_over_dk,
            price: over ? x.over : x.under, ev: over ? evO : evU};
  }).sort((a, b) => b.ev - a.ev);
  el.innerHTML = `<div class="desc">The sim's chance of each DraftKings bet hitting, next to DraftKings' own no-vig chance and the payout. <b>EV</b> is the expected return per $1 if the sim is right: <span class="gold">gold</span> = 5%+.</div>
    ${gl.length ? `<div class="lbl">Game lines</div><div class="scroll"><table class="tbl"><tr><th>Bet</th><th>DK price</th><th>Sim</th><th>DK</th><th>EV</th></tr>
      ${gl.map(x => `<tr><td class="t">${MKN[x.market]} · ${esc(x.market === "totals" ? x.name : (x.name.split(" ").slice(-1)[0]))}${x.point != null ? " " + (x.market === "spreads" && x.point > 0 ? "+" : "") + f1(x.point) : ""}</td>
        <td>${amS_(x.am)}</td><td><b>${pc(x.p_sim)}</b></td><td class="dim">${pc(x.p_dk)}</td><td class="${evc(x.ev)}">${(x.ev > 0 ? "+" : "") + Math.round(x.ev * 100)}%</td></tr>`).join("")}</table></div>
      <div class="desc" style="margin-top:4px">Game lines usually land close: the sim leans on the Vegas total and spread on purpose, so big gaps here are rare and worth a second look.</div>` : '<div class="desc">No DraftKings game lines for this game yet.</div>'}
    ${props.length ? `<div class="lbl">Player props</div><div class="scroll"><table class="tbl"><tr><th>Player</th><th>Bet</th><th>Sim avg</th><th>Sim</th><th>DK</th><th>Price</th><th>EV</th></tr>
      ${props.map(x => `<tr data-pid="${x.pid}" data-stat="${x.stat}" style="cursor:pointer"><td class="t">${esc(lastName(x.name))}</td><td>${x.side === "Over" ? "O" : "U"} ${f1(x.line)} ${STATN[x.stat]}</td><td>${f1(x.mean)}</td>
        <td><b>${pc(x.p_sim)}</b></td><td class="dim">${pc(x.p_dk)}</td><td>${amS_(x.price >= 2 ? Math.round((x.price - 1) * 100) : -Math.round(100 / (x.price - 1)))}</td>
        <td class="${evc(x.ev)}">${(x.ev > 0 ? "+" : "") + Math.round(x.ev * 100)}%</td></tr>`).join("")}</table></div>
      <div class="desc" style="margin-top:4px">Each row shows the side the sim likes better at DraftKings' price. Tap a player for his card (conviction, hit rates, line history) before acting on a gap.</div>` : '<div class="desc" style="margin-top:6px">No DraftKings player props for this game yet (props are priced around 2pm ET for the strongest matchups).</div>'}`;
  bindPlayerLinks(el);
}
function showStored() {
  const G = D.sims && D.sims.games && D.sims.games[S.gameId], P = S.P;
  if (!G) return;
  if (hasOverride(S.gameId)) { $("simRes").innerHTML = '<div class="desc" style="margin-top:10px">You set your own lineup for this game, so the overnight 10,000 sims (official lineup) are hidden. Tap <b>Run 5,000 sims</b> to simulate with yours.</div>'; return; }
  const st = S.stat, priced = (D.pz && D.pz.priced) || {};
  const rows = Object.entries(G.players).map(([pid, x]) => {
    const q = x.s[st], pr = priced[pid + "|" + st];
    const line = pr ? pr.line : estLine({pid, stat: st, base: q[0]}).line;
    return {pid, x, q, line, src: pr ? bookName(pr.book) : "est.", po: pOverQ(q, line)};
  }).filter(r => r.q[0] >= (st === "tpm" ? 0.3 : 1)).sort((a, b) => b.q[0] - a.q[0]);
  const vt = G.vegas && G.vegas.total, sp = G.vegas && G.vegas.spread_home;
  $("simRes").innerHTML = `<div class="desc" style="margin-top:10px"><b style="color:var(--gold)">Overnight run:</b> ${D.sims.n.toLocaleString()} simulations, saved ${ago(D.sims.updated)}. Tap Run for a fresh batch with the latest inputs.</div>
    <div class="kv" style="margin-top:8px">
      <div><div class="k">${esc(G.home)} win</div><div class="v">${Math.round(G.win_home * 100)}%</div></div>
      <div><div class="k">Avg score</div><div class="v">${f1(G.avg.away)}–${f1(G.avg.home)}</div></div>
      <div><div class="k">Avg total</div><div class="v">${f1(G.total[0])}${vt ? ` <small>vs ${f1(vt)}</small>` : ""}</div></div>
      ${G.over != null ? `<div><div class="k">Over ${f1(vt)}</div><div class="v">${Math.round(G.over * 100)}%</div></div>` : ""}
      ${G.cover_home != null ? `<div><div class="k">${esc(G.home)} ${sp > 0 ? "+" : ""}${f1(sp)}</div><div class="v">${Math.round(G.cover_home * 100)}%</div></div>` : ""}
    </div>
    <div class="chips" id="mcs">${STATS.map(k => `<button data-s="${k}" class="${k === st ? "on" : ""}">${STATN[k]}</button>`).join("")}</div>
    <div class="scroll"><table class="tbl"><tr><th>Player</th><th>Min</th><th>Avg</th><th>10–90%</th><th>Line</th><th>Over</th><th>Under</th></tr>
    ${rows.map(r => `<tr data-pid="${r.pid}" data-stat="${st}" style="cursor:pointer"><td class="t">${esc(lastName(r.x.name))} <span class="dim">${esc(r.x.team)}</span></td><td>${f1(r.x.min)}</td><td><b>${f1(r.q[0])}</b></td>
      <td class="dim">${r.q[1]}–${r.q[9]}</td><td>${f1(r.line)} <span class="dim">${esc(r.src)}</span></td>
      <td class="${r.po >= 0.55 ? "h" : ""}">≈${Math.round(r.po * 100)}%</td><td class="${1 - r.po >= 0.55 ? "h" : ""}">≈${Math.round((1 - r.po) * 100)}%</td></tr>`).join("")}</table></div>
    ${G.typical ? `<button class="btn ghost" id="typBtn" style="margin-top:8px">${S.showTyp ? "Hide" : "Show"} the most typical simulated game</button>${S.showTyp ? typicalHtml(G) : ""}` : ""}`;
  $("mcs").querySelectorAll("button").forEach(b => b.onclick = () => { S.stat = b.dataset.s; showStored(); showDay(); });
  const tb = $("typBtn"); if (tb) tb.onclick = () => { S.showTyp = !S.showTyp; showStored(); };
  bindPlayerLinks($("simRes"));
}
function showMC() {
  const M = S.mc, P = S.P, g = P.g; if (!M) return;
  const N = M.N;
  let hw = 0, tot = [], cover = 0;
  for (let i = 0; i < N; i++) { if (M.team.home[i] > M.team.away[i]) hw++; tot.push(M.team.home[i] + M.team.away[i]); if (P.spreadHome != null && M.team.home[i] + P.spreadHome > M.team.away[i]) cover++; }
  const avgH = mean(M.team.home), avgA = mean(M.team.away), avgT = avgH + avgA;
  const vt = g.vegas && g.vegas.total, overT = vt ? tot.filter(t => t > vt).length / N : null;
  const st = S.stat;
  const priced = (D.pz && D.pz.priced) || {};
  const rows = Object.keys(M.out).map(pid => {
    const a = M.out[pid][st], w = M.who[pid], pr = priced[pid + "|" + st];
    const line = pr ? pr.line : estLine({pid, stat: st, base: mean(a)}).line;
    return {pid, w, a, mean: mean(a), p10: quant(a, 0.1), p90: quant(a, 0.9), line, src: pr ? bookName(pr.book) : "est.", po: pOver(a, line), min: mean(M.out[pid].min)};
  }).filter(r => r.mean >= (st === "tpm" ? 0.3 : 1)).sort((x, y) => y.mean - x.mean);
  $("simRes").innerHTML = `
    <div class="kv" style="margin-top:10px">
      <div><div class="k">${esc(P.teams.home.ab)} win</div><div class="v">${Math.round(hw / N * 100)}%</div></div>
      <div><div class="k">Avg score</div><div class="v">${f1(avgA)}–${f1(avgH)}</div></div>
      <div><div class="k">Avg total</div><div class="v">${f1(avgT)}${vt ? ` <small>vs ${f1(vt)}</small>` : ""}</div></div>
      <div><div class="k">Pace</div><div class="v">${f1(P.pace)}${P.paceK !== 1 ? ` <small>model ${f1(P.modelPace)}</small>` : ""}</div></div>
      ${overT != null ? `<div><div class="k">Over ${f1(vt)}</div><div class="v">${Math.round(overT * 100)}%</div></div>` : ""}
      ${P.spreadHome != null ? `<div><div class="k">${esc(P.teams.home.ab)} ${P.spreadHome > 0 ? "+" : ""}${f1(P.spreadHome)}</div><div class="v">${Math.round(cover / N * 100)}%</div></div>` : ""}
    </div>
    <div class="chips" id="mcs">${STATS.map(k => `<button data-s="${k}" class="${k === st ? "on" : ""}">${STATN[k]}</button>`).join("")}</div>
    <div class="scroll"><table class="tbl"><tr><th>Player</th><th>Min</th><th>Avg</th><th>10–90%</th><th>Line</th><th>Over</th><th>Under</th></tr>
    ${rows.map(r => `<tr data-pid="${r.pid}" data-stat="${st}" style="cursor:pointer"><td class="t">${esc(r.w.last)} <span class="dim">${esc(P.teams[r.w.side].ab)}</span></td><td>${f1(r.min)}</td><td><b>${f1(r.mean)}</b></td>
      <td class="dim">${r.p10}–${r.p90}</td><td>${f1(r.line)} <span class="dim">${esc(r.src)}</span></td>
      <td class="${r.po >= 0.55 ? "h" : ""}">${Math.round(r.po * 100)}%</td><td class="${1 - r.po >= 0.55 ? "h" : ""}">${Math.round((1 - r.po) * 100)}%</td></tr>`).join("")}</table></div>
    <div class="desc" style="margin-top:6px">${N.toLocaleString()} simulated games. Lines are the book's when the agent priced one today, otherwise an estimate at his usual. Over/Under = share of sims that cleared the line. Tap a player for his card.</div>`;
  $("mcs").querySelectorAll("button").forEach(b => b.onclick = () => { S.stat = b.dataset.s; showMC(); });
  bindPlayerLinks($("simRes"));
}

RENDER.sim = renderSim;
window.simEngine = {prep, calibrate, simGame, monteCarlo, setSeed, ensureAvg, loadAvgGame};
window.SL = {lineBtns, editing, stop, pollLive, liveSimFromHere, liveValueHtml, liveBoxHtml, livePlaysHtml, showVs, prep, calibrate, am_, spS, pcS, lastName};
})();
