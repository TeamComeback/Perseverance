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
    let ps = Object.values(gf.players || {}).filter(p => p.team === ab && !p.out && p.minutes > 0);
    ps.sort((a, b) => ((b.rw && b.rw.starter) ? 100 : 0) + b.minutes - (((a.rw && a.rw.starter) ? 100 : 0) + a.minutes));
    ps = ps.slice(0, 13);
    const rows = ps.map((p, i) => {
      const L = (p.logs || []).filter(r => r[3] > 0).slice(-60);
      const sum = k => L.reduce((t, r) => t + (r[k] || 0), 0);
      const mins = Math.max(1, sum(3)), pts = sum(4), reb = sum(5), ast = sum(6), tpm = sum(7), tpa = sum(8), fga = sum(9);
      const fta = 0.2 * pts / 0.78;                                  // free throws aren't logged: ~20% of points
      const p3 = (tpm + 0.36 * 15) / (tpa + 15);                     // shrunk toward league 36%
      const twoA = Math.max(1, fga - tpa), twoM = Math.max(0, (0.8 * pts - 3 * tpm) / 2);
      const pr = s => (p.proj[s] && p.proj[s].proj) || 0;
      return {pid: p.pid, name: p.name, ini: initials(p.name), last: lastName(p.name), starter: !!(p.rw && p.rw.starter), idx: i,
        min: p.minutes, tgt: {pts: pr("pts"), reb: pr("reb"), ast: pr("ast"), tpm: pr("tpm")},
        u: Math.max(0.05, (fga + 0.44 * fta) / mins), r3: fga > 0 ? clamp(tpa / fga, 0.01, 0.9) : 0.25, p3: clamp(p3, 0.2, 0.47),
        p2: clamp(twoM / twoA, 0.38, 0.7), ft: clamp((fta / 2) / Math.max(1, fga + fta / 2), 0.03, 0.3),
        rw: Math.max(0.02, reb / mins), aw: Math.max(0.01, ast / mins)};
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
    P.teams[side] = {ab, name: g[side + "_name"] || ab, players: rows, eff: 1};
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
      if (rnd() < (second ? 0.08 : 0.125)) {
        const p = pick(on[off], x => x.u); box[p.pid].tov++;
        ev({kind: "tov", team: off, a: p.pid, text: `${p.last} turnover`}); break;
      }
      const sh = pick(on[off], x => x.u);
      if (rnd() < sh.ft) {
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
      const oreb = rnd() < 0.24;
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
  const line = 'stroke="#e9d9b8" stroke-opacity=".55" stroke-width="2" fill="none"';
  const half = side => {
    const b = BX[side], s = side ? -1 : 1, x0 = side ? W : 0;
    return `<rect x="${side ? W - 190 : 0}" y="170" width="190" height="160" ${line} fill="#ADD8E6" fill-opacity=".07"/>
      <path d="M${x0} 30 L${x0 + s * 140} 30 A237.5 237.5 0 0 ${side ? 0 : 1} ${x0 + s * 140} 470 L${x0} 470" ${line}/>
      <circle cx="${x0 + s * 190}" cy="250" r="60" ${line}/>
      <path d="M${b - s * 0} 210 A40 40 0 0 ${side ? 0 : 1} ${b} 290" ${line}/>
      <line x1="${x0 + s * 40}" x2="${x0 + s * 40}" y1="220" y2="280" stroke="#e9d9b8" stroke-width="3"/>
      <circle cx="${b}" cy="250" r="7.5" stroke="#E5C158" stroke-width="2.5" fill="none"/>`;
  };
  let planks = "";
  for (let y = 0; y < H; y += 25) planks += `<line x1="0" x2="${W}" y1="${y}" y2="${y}" stroke="#000" stroke-opacity=".08"/>`;
  return `<svg viewBox="-20 -20 ${W + 40} ${H + 40}" class="cv" id="simCourt">
    <defs><linearGradient id="wood" x1="0" x2="1"><stop offset="0" stop-color="#6b4a2c"/><stop offset=".5" stop-color="#83603b"/><stop offset="1" stop-color="#6b4a2c"/></linearGradient></defs>
    <rect x="-20" y="-20" width="${W + 40}" height="${H + 40}" rx="6" fill="#0e1422"/>
    <rect x="0" y="0" width="${W}" height="${H}" fill="url(#wood)"/>${planks}
    <rect x="0" y="0" width="${W}" height="${H}" ${line}/>
    <line x1="${W / 2}" x2="${W / 2}" y1="0" y2="${H}" ${line}/><circle cx="${W / 2}" cy="250" r="60" ${line}/>
    <circle cx="${W / 2}" cy="250" r="20" ${line}/>
    ${half(0)}${half(1)}
    <g id="simDots"></g>
    <circle id="simBall" r="7" fill="#ff9a3c" stroke="#3a1d00" stroke-width="1.5" cx="${W / 2}" cy="250"/>
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
  $("v-sim").innerHTML = `
    <div class="chips" id="simg">${chips || '<span class="desc">No NBA games on the slate.</span>'}</div>
    <div id="simStage"></div>
    <h2>Monte Carlo</h2>
    <div class="card" id="simMC"><div class="desc">Plays this game thousands of times with the same inputs, so every player gets a full range of outcomes and a hit rate for any line.</div>
      <button class="btn" id="simRun" style="margin-top:8px">Run 5,000 sims</button><div class="meter" id="simBar" style="display:none"><i style="width:0%"></i></div><div id="simRes"></div></div>
    <h2>Live lines · DraftKings</h2>
    <div class="card" id="simLive"><div class="empty">Loading…</div></div>
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

function stage() {
  const P = S.P, g = P.g;
  $("simStage").innerHTML = `
    <div class="simwrap">
      <div class="sbx">
        <div class="tm a"><span class="nm">${esc(P.teams.away.ab)}</span><span class="sc" id="scA">0</span></div>
        <div class="mid"><div class="clk" id="simClk">Q1 12:00</div><div class="wp"><span id="wpA">50%</span><div class="wpb"><i id="wpBar" style="width:50%"></i></div><span id="wpH">50%</span></div><div class="wpl">win probability</div></div>
        <div class="tm h"><span class="sc" id="scH">0</span><span class="nm">${esc(P.teams.home.ab)}</span></div>
      </div>
      <div class="simgrid">
        <div class="bxs" id="bxA"></div>
        <div class="simc">
          <div class="court3d"><div class="tilt">${courtSvg()}</div></div>
          <div class="cap" id="simCap"><span class="pos" id="capT">TIP-OFF</span><span id="capX">Press play to simulate ${esc(g.away)} @ ${esc(g.home)}</span></div>
          <div class="ctl">
            <button class="btn" id="simPlay">▶ Watch the average game</button>
            <button class="btn ghost" id="simRand">Random game</button>
            <div class="seg" id="simSpd">${[1, 4, 16].map(s => `<button data-s="${s}" class="${S.speed === s ? "on" : ""}">${s}×</button>`).join("")}</div>
            <button class="btn ghost" id="simSkip">Skip to final</button>
          </div>
          <div class="ctl"><span class="desc">Box score:</span><div class="seg" id="simView">${[["live", "This game"], ["avg", "Average"], ["mode", "Most common"]].map(([k, l]) => `<button data-v="${k}" class="${(S.view || "live") === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
        </div>
        <div class="bxs" id="bxH"></div>
      </div>
    </div>`;
  $("simPlay").onclick = () => { if (S.playing) stop(); else play("avg"); };
  $("simRand").onclick = () => { stop(); S.kind = "random"; newGame("random"); S.view = "live"; syncView(); play("random", true); };
  $("simView").querySelectorAll("button").forEach(b => b.onclick = async () => { S.view = b.dataset.v; syncView(); if (S.view !== "live") await ensureAvg(); boxes(S.i); });
  $("simSpd").querySelectorAll("button").forEach(b => b.onclick = () => { S.speed = +b.dataset.s; $("simSpd").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); });
  $("simSkip").onclick = async () => { if (!S.game || S.kind === "init") await loadAvgGame(); stop(); S.i = S.game.events.length - 1; replayTo(S.i); };
  S.view = S.view || "live";
  newGame("init"); drawDots(S.game.events[0], false); boxes(0);
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
  const stored = sg && sg.at && D.players && sg.at >= (D.players.updated || "") ? sg : null;
  if (stored) for (const pid in players) { const x = stored.players[pid]; if (x) Object.assign(players[pid].avg, {min: x.min, pts: x.s.pts[0], reb: x.s.reb[0], ast: x.s.ast[0], tpm: x.s.tpm[0]}); }
  S.avgRun = {gid: S.gameId, N: stored ? D.sims.n : N, bi, seedBase, players, biScore: {home: M.team.home[bi], away: M.team.away[bi]},
    score: stored ? stored.avg : {home: mean(M.team.home), away: mean(M.team.away)},
    modeTot: mode(tot), modeMar: mode(mar), homeWin: Array.from(M.team.home).filter((h, i) => h > M.team.away[i]).length / N};
  return S.avgRun;
}
async function loadAvgGame() {
  const A = await ensureAvg();
  setSeed(A.seedBase + A.bi); S.game = simGame(S.P, true); setSeed(null);
  S.i = 0; S.kind = "avg-ready";
}

function newGame(kind) { setSeed(null); S.game = simGame(S.P, true); S.i = 0; S.cum = null; S.kind = kind || "random"; }
function stop() { S.playing = false; clearTimeout(S.timer); const b = $("simPlay"); if (b) b.textContent = S.game && S.i >= S.game.events.length - 1 ? "▶ Watch the average game" : "▶ Resume"; }
async function play(kind, fresh) {
  if (kind === "avg" && (S.kind !== "avg-ready" || S.i >= S.game.events.length - 1)) { $("simPlay").textContent = "Finding…"; await loadAvgGame(); drawDots(S.game.events[0], false); boxes(0); }
  else if (!S.game || (!fresh && S.i >= S.game.events.length - 1)) { newGame("random"); boxes(0); }
  S.playing = true; $("simPlay").textContent = "❚❚ Pause";
  const step = () => {
    if (!S.playing) return;
    if (S.i >= S.game.events.length - 1) { stop(); return; }
    S.i++; show(S.game.events[S.i], true);
    S.timer = setTimeout(step, 1150 / S.speed);
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

function show(e, animate) {
  const P = S.P;
  if (S.view && S.view !== "live" && S.avgRun && S.avgRun.gid === S.gameId) { drawDots(e, animate); return aggBoxes(S.view); }
  $("scA").textContent = e.score.away; $("scH").textContent = e.score.home;
  $("simClk").textContent = e.kind === "final" ? "FINAL" + (e.period > 4 ? "/OT" : "") : fmtClock(e);
  const wp = winProb(e);
  $("wpBar").style.width = (100 - wp * 100).toFixed(1) + "%"; $("wpA").textContent = Math.round((1 - wp) * 100) + "%"; $("wpH").textContent = Math.round(wp * 100) + "%";
  const tm = P.teams[e.team] || P.teams.home;
  $("capT").textContent = e.kind === "final" ? "FINAL" : e.kind === "period" ? "QUARTER" : tm.ab + " BALL";
  $("capT").className = e.team === "home" ? "h" : "a";
  $("capX").textContent = e.text;
  drawDots(e, animate);
  boxes(S.i);
}

// box score up to event i (cumulative)
function boxAt(i) {
  const G = S.game, P = S.P, b = {};
  for (const s of ["home", "away"]) P.teams[s].players.forEach(p => b[p.pid] = {pts: 0, reb: 0, ast: 0, tpm: 0, fgm: 0, fga: 0, sec: 0});
  for (let k = 1; k <= i; k++) {
    const e = G.events[k], prev = G.events[k - 1];
    const dt = (prev.period === e.period ? prev.clock - e.clock : 0);
    if (dt > 0) for (const s of ["home", "away"]) prev.on[s].forEach(pid => b[pid].sec += dt);
    if (e.kind === "2pm" || e.kind === "3pm") { b[e.a].pts += e.pts; b[e.a].fgm++; b[e.a].fga++; if (e.kind === "3pm") b[e.a].tpm++; if (e.b) b[e.b].ast++; }
    else if (e.kind === "2miss" || e.kind === "3miss") { b[e.a].fga++; if (e.b) b[e.b].reb++; }
    else if (e.kind === "ft") b[e.a].pts += e.pts;
  }
  return b;
}
function boxes(i) {
  if (S.view && S.view !== "live" && S.avgRun && S.avgRun.gid === S.gameId) return aggBoxes(S.view);
  const P = S.P, b = boxAt(i), e = S.game.events[i];
  const hot = new Set([e.a, e.b].filter(Boolean));
  const tbl = s => {
    const on = new Set(e.on[s]);
    return `<div class="bxh ${s === "home" ? "h" : "a"}">${esc(P.teams[s].name)}</div><table class="bxt"><tr><th></th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>3PM</th><th>FG</th></tr>
      ${P.teams[s].players.map(p => { const x = b[p.pid]; return `<tr class="${on.has(p.pid) ? "on" : ""} ${hot.has(p.pid) ? "hot" : ""}" data-pid="${p.pid}">
        <td class="t">${on.has(p.pid) ? "●" : ""} ${esc(p.last)}</td><td>${Math.floor(x.sec / 60)}</td><td><b>${x.pts}</b></td><td>${x.reb}</td><td>${x.ast}</td><td>${x.tpm}</td><td>${x.fgm}-${x.fga}</td></tr>`; }).join("")}</table>`;
  };
  $("bxA").innerHTML = tbl("away"); $("bxH").innerHTML = tbl("home");
}

function replayTo(i) { show(S.game.events[i], false); const b = $("simPlay"); if (b) b.textContent = "▶ Watch the average game"; }
function aggBoxes(view) {
  const P = S.P, A = S.avgRun, avg = view === "avg";
  const sc = avg ? A.score : {home: (A.modeTot[0] + A.modeMar[0]) / 2, away: (A.modeTot[0] - A.modeMar[0]) / 2};
  $("scA").textContent = avg ? f1(sc.away) : Math.round(sc.away); $("scH").textContent = avg ? f1(sc.home) : Math.round(sc.home);
  $("simClk").textContent = avg ? `AVG OF ${A.N.toLocaleString()}` : "MOST COMMON";
  $("wpBar").style.width = (100 - A.homeWin * 100).toFixed(1) + "%"; $("wpA").textContent = Math.round((1 - A.homeWin) * 100) + "%"; $("wpH").textContent = Math.round(A.homeWin * 100) + "%";
  $("capT").textContent = avg ? "AVERAGE" : "MOST COMMON"; $("capT").className = "h";
  $("capX").textContent = avg ? `Average box score across ${A.N.toLocaleString()} simulations.`
    : `Most frequent result for each stat. Final margin ${A.modeMar[0] > 0 ? P.teams.home.ab + " by " + A.modeMar[0] : A.modeMar[0] < 0 ? P.teams.away.ab + " by " + (-A.modeMar[0]) : "tie"} (${Math.round(A.modeMar[1] * 100)}% of sims), total ${A.modeTot[0]} (${Math.round(A.modeTot[1] * 100)}%).`;
  const cell = (x, k) => avg ? f1(x.avg[k]) : `${x.mode[k][0]}<span class="dim" style="font-size:9px"> ${Math.round(x.mode[k][1] * 100)}%</span>`;
  const tbl = s => `<div class="bxh ${s === "home" ? "h" : "a"}">${esc(P.teams[s].name)}</div><table class="bxt"><tr><th></th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>3PM</th></tr>
    ${P.teams[s].players.slice().sort((a, b) => ((A.players[b.pid] || {}).avg || {}).min - ((A.players[a.pid] || {}).avg || {}).min).map(p => { const x = A.players[p.pid]; if (!x) return "";
      return `<tr class="on" data-pid="${p.pid}"><td class="t">${esc(p.last)}</td><td>${Math.round(x.avg.min)}</td><td><b>${cell(x, "pts")}</b></td><td>${cell(x, "reb")}</td><td>${cell(x, "ast")}</td><td>${cell(x, "tpm")}</td></tr>`; }).join("")}</table>`;
  $("bxA").innerHTML = tbl("away"); $("bxH").innerHTML = tbl("home");
}

function drawDots(e, animate) {
  const P = S.P, layer = $("simDots"); if (!layer) return;
  // home attacks right in the 1st half, left in the 2nd (and overtime)
  const homeRight = e.period <= 2;
  const offRight = e.team === "home" ? homeRight : !homeRight;
  const off = e.team || "home", def = off === "home" ? "away" : "home";
  const pos = {};
  const offIds = e.on[off], defIds = e.on[def];
  offIds.forEach((pid, k) => pos[pid] = spot(offRight, OFF[k][0] + (rnd() - .5) * 3, OFF[k][1] + (rnd() - .5) * 3));
  // the shooter moves to his shot
  let shotAt = null;
  if (e.a && ["2pm", "3pm", "2miss", "3miss", "ft"].includes(e.kind)) {
    const L = e.kind[0] === "3" ? SHOT3 : e.kind === "ft" ? [[15, 0]] : SHOT2;
    const s0 = L[Math.floor(rnd() * L.length)];
    shotAt = spot(offRight, s0[0], s0[1]);
    if (e.kind === "ft") { offIds.forEach((pid, k) => { if (pid !== e.a) pos[pid] = spot(offRight, [10, 10, 8, 8, 22][k], [-7, 7, -7, 7, 0][k]); }); }
    pos[e.a] = shotAt;
  }
  defIds.forEach((pid, k) => {
    const o = pos[offIds[k]] || spot(offRight, 10, 0), bx = offRight ? BX[1] : BX[0];
    pos[pid] = [o[0] + (bx - o[0]) * 0.22 + (rnd() - .5) * 10, o[1] + (250 - o[1]) * 0.2 + (rnd() - .5) * 10];
  });
  if (e.kind === "tip" || e.kind === "period") {
    const all = [...e.on.home, ...e.on.away];
    all.forEach((pid, k) => { const hm = k < 5, j = k % 5; pos[pid] = [W / 2 + (hm ? -1 : 1) * (j === 4 ? 12 : 60 + j * 35), 250 + [-120, 120, -60, 60, 0][j]]; });
  }
  const dur = animate ? Math.min(900, 900 / S.speed) : 0;
  const tmOf = pid => P.teams.home.players.some(p => p.pid === pid) ? "home" : "away";
  const ply = pid => P.teams[tmOf(pid)].players.find(p => p.pid === pid);
  const have = new Set();
  for (const pid of [...e.on.home, ...e.on.away]) {
    have.add(pid);
    let g = layer.querySelector(`[data-p="${pid}"]`);
    const p = ply(pid), side = tmOf(pid);
    if (!g) {
      g = document.createElementNS("http://www.w3.org/2000/svg", "g"); g.setAttribute("data-p", pid); g.setAttribute("class", "pdot " + side);
      g.innerHTML = `<circle r="20"/><text dy="6" text-anchor="middle">${esc(p ? p.ini : "")}</text>`;
      g.style.transform = `translate(${W / 2}px,250px)`;
      layer.appendChild(g);
    }
    g.style.transition = `transform ${dur}ms ease-in-out`;
    const [x, y] = pos[pid] || [W / 2, 250];
    g.style.transform = `translate(${x}px,${y}px)`;
    g.classList.toggle("act", pid === e.a || pid === e.b);
  }
  layer.querySelectorAll(".pdot").forEach(g => { if (!have.has(g.getAttribute("data-p"))) g.remove(); });
  // ball: to the shooter, then the rim, then the rebounder
  const ball = $("simBall"), bx = offRight ? BX[1] : BX[0];
  const set = (x, y, d) => { ball.style.transition = `transform ${d}ms ease-out`; ball.style.transform = `translate(${x - W / 2}px,${y - 250}px)`; };
  if (shotAt) {
    set(shotAt[0], shotAt[1], dur * 0.5);
    setTimeout(() => {
      set(bx, 250, dur * 0.4);
      if (e.kind === "2miss" || e.kind === "3miss") setTimeout(() => { const r = pos[e.b]; if (r) set(r[0], r[1], dur * 0.4); }, dur * 0.45);
    }, dur * 0.55);
  } else if (e.a && pos[e.a]) set(pos[e.a][0], pos[e.a][1], dur * 0.6);
  else set(W / 2, 250, dur * 0.6);
  // pop label: +2 / +3 / FT and a name tag
  const pop = $("simPop");
  if (pop) {
    pop.innerHTML = "";
    if (e.a && pos[e.a] && ["2pm", "3pm", "ft", "tov", "2miss", "3miss"].includes(e.kind)) {
      const [x, y] = pos[e.a], p = ply(e.a), side = tmOf(e.a);
      const tag = e.kind === "3pm" ? "+3" : e.kind === "2pm" ? "+2" : e.kind === "ft" ? (e.pts ? "+" + e.pts + " FT" : "FT") : e.kind === "tov" ? "TO" : "MISS";
      pop.innerHTML = `<g class="ptag ${side}" style="transform:translate(${x}px,${y - 34}px)"><rect x="-90" y="-20" width="180" height="30" rx="7"/>
        <text text-anchor="middle" dy="2">${esc(p ? p.last.toUpperCase() : "")} · ${tag}</text></g>`;
    }
  }
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
    const [od, sb] = await Promise.all([fetch(`${CORE}/events/${gid}/competitions/${gid}/odds`).then(r => r.json()),
      fetch(`https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=${gid}`).then(r => r.json())]);
    const item = (od.items || []).find(i => String((i.provider || {}).id) === "100");
    const comp = sb.header.competitions[0], st = comp.status, t = {};
    comp.competitors.forEach(c => t[c.homeAway] = +c.score || 0);
    const clk = String(st.displayClock || "0"), sec = clk.includes(":") ? +clk.split(":")[0] * 60 + +clk.split(":")[1] : +clk;
    S.now = {state: st.type.state, period: st.period, clock: sec, detail: st.type.shortDetail, score: [t.away || 0, t.home || 0], line: item ? dkSnap(item, "current") : null, at: new Date()};
  } catch (e) { S.now = S.now || null; }
  if (S.gameId === gid) showLive();
}
function liveSimFromHere() {
  const n = S.now; if (!n || !S.P) return;
  const L = n.line || {}, N = 2000; let cw = 0, cl = 0, ow = 0, ol = 0, hw = 0;
  setSeed(null);
  for (let i = 0; i < N; i++) {
    const r = simGame(S.P, false, {period: n.period, clock: n.clock, away: n.score[0], home: n.score[1]}), h = r.score.home, a = r.score.away;
    if (L.sp != null) { const m = h + L.sp - a; if (m > 0) cw++; else if (m < 0) cl++; }
    if (L.tot != null) { const d = h + a - L.tot; if (d > 0) ow++; else if (d < 0) ol++; }
    if (h > a) hw++;
  }
  S.nowSim = {p_home_cover: cw + cl ? cw / (cw + cl) : null, p_over: ow + ol ? ow / (ow + ol) : null, p_home_win: hw / N, at: n.at};
  showLive();
}
function showLive() {
  const el = $("simLive"); if (!el || !S.P) return;
  const G = (D.dkday && D.dkday.games || {})[S.gameId] || {}, P = S.P, H = P.teams.home.ab, A = P.teams.away.ab;
  const g = (D.players.games || []).find(x => x.espn_id === S.gameId) || {};
  const isLive = (S.now && S.now.state === "in") || g.state === "in";
  clearInterval(S.liveTimer);
  if (isLive || (g.state === "pre" && new Date(g.start) - Date.now() < 15 * 60e3)) S.liveTimer = setInterval(() => { if (store.get("tab") === "sim") pollLive(); }, 30000);
  if (isLive && !S.now) { pollLive(); }
  const am_ = d => d == null ? "—" : d >= 2 ? "+" + Math.round((d - 1) * 100) : "−" + Math.round(100 / (d - 1));
  const sp = v => v == null ? "—" : (v > 0 ? "+" : "") + f1(v);
  const nv = (a, b) => a && b ? (1 / a) / (1 / a + 1 / b) : null;
  const pc = p => p == null ? "—" : Math.round(p * 100) + "%";
  const o = G.open, c = G.close;
  const mv = (x, y) => x == null || y == null || x === y ? "" : ` <span class="${y > x ? "h" : "m"}">(${y > x ? "+" : ""}${f1(y - x)})</span>`;
  const oc = o || c ? `<div class="scroll"><table class="tbl"><tr><th></th><th>Opened</th><th>Closed</th></tr>
      <tr><td class="t">${esc(H)} spread</td><td>${sp(o && o.sp)}</td><td>${sp(c && c.sp)}${mv(o && o.sp, c && c.sp)}</td></tr>
      <tr><td class="t">Total</td><td>${f1(o && o.tot)}</td><td>${f1(c && c.tot)}${mv(o && o.tot, c && c.tot)}</td></tr>
      <tr><td class="t">${esc(H)} / ${esc(A)} ML</td><td>${am_(o && o.ml_h)} / ${am_(o && o.ml_a)}</td><td>${am_(c && c.ml_h)} / ${am_(c && c.ml_a)}</td></tr></table></div>` : "";
  const qc = s => s.detail && /half/i.test(s.detail) ? "Half" : `Q${Math.min(s.period, 4)}${s.period > 4 ? " OT" : ""} ${Math.floor(s.clock / 60)}:${String(Math.floor(s.clock % 60)).padStart(2, "0")}`;
  const snaps = (G.live || []);
  const rows = snaps.map(s => { const L = s.line || {}, m = s.sim || {};
    return `<tr><td class="t">${qc(s)}</td><td>${s.score[0]}–${s.score[1]}</td><td>${sp(L.sp)}</td><td>${f1(L.tot)}</td><td>${am_(L.ml_h)}</td>
      <td class="dim">${pc(m.p_home_cover)} · ${pc(m.p_over)} · ${pc(m.p_home_win)}</td><td>${s.res ? `${s.res.spread_home === "won" ? H + " ✓" : s.res.spread_home === "lost" ? A + " ✓" : "push"}` : ""}</td></tr>`; }).join("");
  let now = "";
  if (S.now && S.now.state === "in" && S.now.line) {
    const n = S.now, L = n.line, m = S.nowSim && S.nowSim.at === n.at ? S.nowSim : null;
    const bet = (label, p, price, other) => { const fair = nv(price, other), ev = p != null && price ? p * price - 1 : null;
      return `<tr><td class="t">${label}</td><td>${am_(price)}</td><td><b>${pc(p)}</b></td><td class="dim">${pc(fair)}</td><td class="${ev == null ? "dim" : ev >= 0.05 ? "gold" : ev > 0 ? "h" : "dim"}">${ev == null ? "—" : (ev > 0 ? "+" : "") + Math.round(ev * 100) + "%"}</td></tr>`; };
    const drift = (c && c.sp != null && L.sp != null) || (c && c.tot != null && L.tot != null) ? `<div class="desc" style="margin:2px 0 4px">vs the close: ${c && c.sp != null && L.sp != null ? `${esc(H)} ${sp(c.sp)} → <b style="color:var(--text)">${sp(L.sp)}</b>${L.sp !== c.sp ? ` <span class="gold">(moved ${f1(Math.abs(L.sp - c.sp))})</span>` : ""}` : ""}${c && c.tot != null && L.tot != null ? ` · total ${f1(c.tot)} → <b style="color:var(--text)">${f1(L.tot)}</b>${L.tot !== c.tot ? ` <span class="gold">(${L.tot > c.tot ? "+" : ""}${f1(L.tot - c.tot)})</span>` : ""}` : ""}</div>` : "";
    now = `<div class="lbl">Now · ${esc(n.detail)} · ${esc(A)} ${n.score[0]}–${n.score[1]} ${esc(H)} <span class="dim">(refreshes every 30s)</span></div>${drift}
      <button class="btn" id="simHere" style="margin:4px 0 6px">Sim from here (2,000 from this score and clock)</button>
      <div class="scroll"><table class="tbl"><tr><th>Live bet</th><th>DK</th><th>Sim</th><th>DK %</th><th>EV</th></tr>
        ${bet(`${H} ${sp(L.sp)}`, m && m.p_home_cover, L.sp_h, L.sp_a)}${bet(`${A} ${sp(L.sp == null ? null : -L.sp)}`, m && m.p_home_cover != null ? 1 - m.p_home_cover : null, L.sp_a, L.sp_h)}
        ${bet(`Over ${f1(L.tot)}`, m && m.p_over, L.ov, L.un)}${bet(`Under ${f1(L.tot)}`, m && m.p_over != null ? 1 - m.p_over : null, L.un, L.ov)}
        ${bet(`${H} ML`, m && m.p_home_win, L.ml_h, L.ml_a)}${bet(`${A} ML`, m && m.p_home_win != null ? 1 - m.p_home_win : null, L.ml_a, L.ml_h)}</table></div>`;
  }
  el.innerHTML = `<div class="desc">DraftKings' lines from ESPN's free feed: where they opened and closed, then live snapshots about every 15 minutes during the game, each with the sim's chance of the home team covering, the over, and the home team winning from that score and clock. Graded the next morning in Trends.</div>
    ${oc || '<div class="empty">No DraftKings lines saved for this game yet (they arrive with the morning research run).</div>'}
    ${now}
    ${rows ? `<div class="lbl">Live snapshots</div><div class="scroll"><table class="tbl"><tr><th>When</th><th>${esc(A)}–${esc(H)}</th><th>${esc(H)} sprd</th><th>Total</th><th>${esc(H)} ML</th><th>Sim cover · over · win</th><th>Spread</th></tr>${rows}</table></div>` : (isLive ? "" : '<div class="desc" style="margin-top:6px">Live snapshots start when the game tips off.</div>')}`;
  const b = $("simHere"); if (b) b.onclick = () => { b.textContent = "Simulating…"; setTimeout(liveSimFromHere, 20); };
}
// The sim's probabilities at DraftKings' own lines, next to DraftKings' no-vig price and payout.
function showVs() {
  const el = $("simVs"); if (!el) return;
  const G = D.sims && D.sims.games && D.sims.games[S.gameId], V = G && G.vs;
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
})();
