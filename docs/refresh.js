// Best Bets refresh.
//  1. "Re-price now" (on this phone, seconds): pulls DraftKings' current game lines and player props from ESPN, re-prices
//     every candidate against the saved 10,000-sim distributions with the same learned weights, checks and conviction
//     model the agent uses (tools/sim_all.js), and shows what moved. It can't see lineup news: that needs a full refresh.
//  2. "Full refresh" (on GitHub, about two minutes): opens a prefilled "Refresh:" issue; the issue workflow re-runs
//     research (lineups, projections, sims) and re-ranks the official picks. One per 10 minutes.
// Phone re-prices are shown with a banner and are not recorded; only the agent's picks are graded.
(() => {
const CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba";
const PT = {"Total Points": "pts", "Total Rebounds": "reb", "Total Assists": "ast", "Total 3-Point Field Goals": "tpm",
  "Total Points, Rebounds, and Assists": "pra", "Total Assists and Rebounds": "ra"};
const num = v => v == null || v === "" ? null : parseFloat(String(v).replace("+", ""));
const novig = (a, b) => (1 / a) / (1 / a + 1 / b);
const amOf = d => d >= 2 ? Math.round((d - 1) * 100) : -Math.round(100 / (d - 1));
const r3 = v => Math.round(v * 1000) / 1000;
const keyOf = x => [x.kind, x.espn_id, x.pid || "", x.stat || "", x.side].join("|");
const nc = {cache: "no-store"};
const J = u => fetch(u + (u.includes("?") ? "&" : "?") + "_=" + Date.now(), nc).then(r => r.ok ? r.json() : null).catch(() => null);

// share of a {value: count} distribution above / below a line, pushes left out
function pFrom(h, f) { let w = 0, l = 0; for (const [k, c] of Object.entries(h || {})) { const r = f(+k); if (r > 0) w += c; else if (r < 0) l += c; } return w + l ? w / (w + l) : null; }
function meanOf(h) { let t = 0, n = 0; for (const [k, c] of Object.entries(h || {})) { t += +k * c; n += c; } return n ? t / n : null; }

async function dkGame(gid) {
  const d = await J(`${CORE}/events/${gid}/competitions/${gid}/odds`); if (!d) return null;
  const it = (d.items || []).find(i => String((i.provider || {}).id) === "100"); if (!it) return null;
  const t = it.current || {}, h = (it.homeTeamOdds || {}).current || {}, a = (it.awayTeamOdds || {}).current || {};
  return {sp: h.pointSpread ? num(h.pointSpread.american) : null, sp_h: h.spread ? num(h.spread.decimal) : null, sp_a: a.spread ? num(a.spread.decimal) : null,
          tot: t.total ? num(t.total.american) : null, ov: t.over ? num(t.over.decimal) : null, un: t.under ? num(t.under.decimal) : null};
}
async function dkProps(gid) {
  const d = await J(`${CORE}/events/${gid}/competitions/${gid}/odds/100/propBets?lang=en&region=us&limit=1000`); if (!d) return {};
  const pairs = {};
  for (const i of d.items || []) {
    const st = PT[(i.type || {}).name]; if (!st || !i.athlete) continue;
    const aid = i.athlete.$ref.split("/athletes/")[1].split("?")[0];
    const od = (i.odds || {}).decimal || {};
    (pairs[aid + "|" + st] = pairs[aid + "|" + st] || []).push([num(((i.current || {}).target || {}).value), num(od.value)]);
  }
  const out = {};
  for (const [k, v] of Object.entries(pairs)) if (v.length >= 2 && v[0][0] != null && v[0][1] && v[1][1]) out[k] = [v[0][0], v[0][1], v[1][1]];
  return out;
}

async function reprice(onStep) {
  const step = t => onStep && onStep(t);
  const date = D.players.date, day = ((D.best || {}).days || {})[date];
  if (!day) throw new Error("No agent picks for today yet");
  const L = day.learned || {}, CM = D.sb && D.sb.conviction && D.sb.conviction.ready ? D.sb.conviction : null;
  step("Loading the saved sims…");
  const sims = await J("data/sims.json");
  if (!sims || sims.date !== date) throw new Error("The saved sims are from another day");
  const games = (D.players.games || []).filter(g => g.state === "pre" && new Date(g.start) > Date.now() && sims.games[g.espn_id]);
  const dkFile = (await J(`data/dk/${date}.json`)) || {props: {}};
  const cands = {props: [], games: []}, lines = {}, allProps = {};
  const wG = L.w_game != null ? L.w_game : 0.35, totBias = L.total_bias || 0;
  for (const [n, g] of games.entries()) {
    step(`DraftKings lines ${n + 1}/${games.length}: ${g.away} @ ${g.home}`);
    const G = sims.games[g.espn_id], base = {espn_id: g.espn_id, game: `${g.away} @ ${g.home}`, start: g.start, home: g.home, away: g.away, home_name: g.home_name, away_name: g.away_name};
    const [ln, props] = await Promise.all([dkGame(g.espn_id), dkProps(g.espn_id)]);
    lines[g.espn_id] = ln;
    Object.assign(allProps, props);
    // game lines, from the saved margin and total distributions
    if (ln && G.mh && ln.sp != null && ln.sp_h && ln.sp_a) {
      const ps = pFrom(G.mh, m => m + ln.sp), pm = novig(ln.sp_h, ln.sp_a);
      for (const side of ["home", "away"]) {
        const s_ = side === "home" ? ps : 1 - ps, m_ = side === "home" ? pm : 1 - pm, dec = side === "home" ? ln.sp_h : ln.sp_a, p = wG * s_ + (1 - wG) * m_;
        const conv = Math.min(59, CASE.convScore(p, dec)), pt = side === "home" ? ln.sp : -ln.sp;
        cands.games.push({...base, kind: "spread", side, team: g[side], point: pt, dec, am: amOf(dec), p: r3(p), p_sim: r3(s_), p_mkt: r3(m_), ev: r3(p * dec - 1), conv, tier: CASE.convTier(conv),
          why: `${g[side]} ${pt > 0 ? "+" : ""}${pt} covers in ${Math.round(s_ * 100)}% of sims vs ${Math.round(m_ * 100)}% DraftKings (re-priced on your phone)`});
      }
    }
    if (ln && G.th && ln.tot != null && ln.ov && ln.un) {
      const ps = pFrom(G.th, t => t - totBias - ln.tot), pm = novig(ln.ov, ln.un);
      for (const side of ["Over", "Under"]) {
        const s_ = side === "Over" ? ps : 1 - ps, m_ = side === "Over" ? pm : 1 - pm, dec = side === "Over" ? ln.ov : ln.un, p = wG * s_ + (1 - wG) * m_;
        const conv = Math.min(59, CASE.convScore(p, dec));
        cands.games.push({...base, kind: "total", side, point: ln.tot, dec, am: amOf(dec), p: r3(p), p_sim: r3(s_), p_mkt: r3(m_), ev: r3(p * dec - 1), conv, tier: CASE.convTier(conv),
          why: `${side.toLowerCase()} ${ln.tot} in ${Math.round(s_ * 100)}% of sims vs ${Math.round(m_ * 100)}% DraftKings (re-priced on your phone)`});
      }
    }
    // props, from each rotation player's saved distribution, with the same checks as the agent
    let gf = null;
    for (const [k, c] of Object.entries(props)) {
      const [pid, stat] = k.split("|"), sp = G.players[pid];
      if (!sp || !sp.h || !sp.h[stat]) continue;
      const foc = (L.focus || {})[stat], dkr = (dkFile.props || {})[k] || {}, idx = (D.players.players || {})[pid] || {};
      const cvN = dkr.cv || null;
      if (foc === "skip" && !(CM && (cvN || 0) >= 3)) continue;
      if (sp.min < 18 || idx.out || /quest|doubt|gtd|day.to.day/i.test(idx.status || "")) continue;
      const b = (L.stat_bias || {})[stat] || 0, w = (L.w_props || {})[stat] != null ? L.w_props[stat] : 0.3, line = c[0];
      const ps = pFrom(sp.h[stat], v => v - b - line), simAvg = meanOf(sp.h[stat]) - b;
      if (ps == null) continue;
      const gap = Math.abs(simAvg - line); if (gap >= 2.5 && gap / Math.max(line, 1) > 0.3) continue;
      const proj = dkr.proj != null ? dkr.proj : null; if (proj != null && Math.abs(simAvg - proj) > Math.max(2, 0.25 * proj)) continue;
      const pm = novig(c[1], c[2]);
      for (const side of ["Over", "Under"]) {
        const s_ = side === "Over" ? ps : 1 - ps, m_ = side === "Over" ? pm : 1 - pm, dec = side === "Over" ? c[1] : c[2], pb = w * s_ + (1 - w) * m_;
        cands.props.push({...base, kind: "prop", pid, name: sp.name, team: sp.team, opp: sp.team === g.home ? g.away : g.home, stat, side, point: line, dec, am: amOf(dec),
          p: r3(pb), pb, p_sim: r3(s_), p_mkt: r3(m_), ev: r3(pb * dec - 1), cv: cvN, sim: Math.round(simAvg * 10) / 10, proj, open: dkr.o ? dkr.o[0] : null, foc,
          why: `Sim avg ${simAvg.toFixed(1)} vs ${line} · ${side.toLowerCase()} in ${Math.round(s_ * 100)}% of sims vs ${Math.round(m_ * 100)}% DraftKings (re-priced on your phone)`});
      }
    }
    // the case and the conviction model, for this game's strongest provisional props only (keeps it fast)
    const mine = cands.props.filter(x => x.espn_id === g.espn_id).sort((a, b) => b.ev - a.ev).slice(0, 8);
    if (mine.length) { gf = await gameFile(g.espn_id); }
    for (const x of cands.props.filter(x => x.espn_id === g.espn_id)) {
      let cs = null;
      if (mine.includes(x) && gf && gf.players && gf.players[x.pid]) try {
        const hist = ((D.dkRecent || {})[x.pid + "|" + x.stat]) || [];
        cs = CASE.buildCase({p: gf.players[x.pid], players: gf.players, stat: x.stat, side: x.side, line: x.point, dec: x.dec, pMkt: x.p_mkt, pSim: x.p_sim, simAvg: x.sim,
          open: x.open, focus: x.foc, tend: [hist.filter(r => r[6] === "over").length, hist.filter(r => r[6] === "under").length], underRate: CM && CM.unders, convModel: CM,
          pKalshi: window.kalshiP ? kalshiP(x.pid, x.stat, x.point, x.side) : null, kalshiTrack: window.kalshiTrack ? kalshiTrack() : null});
      } catch (e) {}
      const p = cs && cs.p_conv != null ? (cs.p_conv + x.pb) / 2 : x.pb, conv = CASE.convScore(p, x.dec);
      Object.assign(x, {p_kalshi: window.kalshiP ? kalshiP(x.pid, x.stat, x.point, x.side) : null, p: r3(p), ev: r3(p * x.dec - 1), conv, tier: CASE.convTier(conv), p_conv: cs && cs.p_conv != null ? r3(cs.p_conv) : null,
        case: cs ? {for: cs.for, against: cs.against} : null, ladder: cs ? cs.ladder : null, verdict: cs ? CASE.verdict(cs, conv) : null});
      delete x.pb; delete x.foc;
    }
  }
  step("Ranking…");
  // same selection as the agent: games that have tipped keep the agent's picks; at most 2 props per game, 1 per player
  const tipped = x => { const g = (D.players.games || []).find(y => y.espn_id === x.espn_id); return g ? g.state !== "pre" || new Date(g.start) <= Date.now() : true; };
  const score = x => x.conv != null ? x.conv + x.ev : 100 * x.ev;
  const pick = (list, lockedOld, nMax, perGame, perPlayer) => {
    const chosen = lockedOld.filter(tipped), usedG = {}, usedP = new Set();
    chosen.forEach(x => { usedG[x.espn_id] = (usedG[x.espn_id] || 0) + 1; if (x.pid) usedP.add(x.pid); });
    for (const x of list.filter(x => !tipped(x)).sort((a, b) => score(b) - score(a))) {
      if (chosen.length >= nMax) break;
      if ((usedG[x.espn_id] || 0) >= perGame || (perPlayer && usedP.has(x.pid))) continue;
      if (chosen.some(c => c.espn_id === x.espn_id && c.kind === x.kind && (c.pid || "") === (x.pid || "") && (c.stat || "") === (x.stat || ""))) continue;
      chosen.push(x); usedG[x.espn_id] = (usedG[x.espn_id] || 0) + 1; if (x.pid) usedP.add(x.pid);
    }
    return chosen.sort((a, b) => score(b) - score(a)).map(x => ({...x, plus: x.ev > 0}));
  };
  const out = {at: new Date().toISOString(), date, base: day.updated, props: pick(cands.props, day.props || [], 5, 2, true), games: pick(cands.games, day.games || [], 5, 1, false), learned: L};
  out.changes = diff(day, out, cands);
  // the full ranked board, same rules as the agent's ranked.json (tools/sim_all.js): one side per line, conviction
  // first, sim win rate breaks ties, nothing shorter than -200 gets a rank. Rows the phone didn't build a full case for
  // carry the quick conviction (sim blended with the price), so they can sit a few points off the agent's.
  const bestOf = (list, keyF) => { const by = {}; for (const x of list) { const k = keyF(x), b = by[k]; if (!b || x.conv > b.conv || (x.conv === b.conv && x.p_sim > b.p_sim)) by[k] = x; } return Object.values(by); };
  const slim = x => ({k: x.kind === "prop" ? x.pid + "|" + x.stat : "g|" + x.espn_id + "|" + x.kind, kind: x.kind, espn_id: x.espn_id,
    ...(x.kind === "prop" ? {pid: x.pid, name: x.name, team: x.team, opp: x.opp, stat: x.stat, sim: x.sim} : {game: x.game, team: x.team || null}),
    side: x.side, point: x.point, dec: x.dec, am: x.am, conv: x.conv, tier: x.tier, p: x.p, p_sim: x.p_sim, p_mkt: x.p_mkt, p_kalshi: x.p_kalshi != null ? r3(x.p_kalshi) : null, ev: x.ev, quick: x.p_conv == null && x.kind === "prop" ? 1 : undefined});
  const rankB = rows => { rows.sort((a, b) => (b.conv - a.conv) || (b.p_sim - a.p_sim)); let i = 0; rows.forEach(r => { r.rank = r.dec >= 1.5 ? ++i : null; }); return rows; };
  out.board = {props: rankB(bestOf(cands.props, x => x.pid + "|" + x.stat).map(slim)), games: rankB(bestOf(cands.games, x => x.espn_id + "|" + x.kind).map(slim))};
  // every prop line DraftKings had just now, so the Players tab and player cards show today's numbers too
  try { localStorage.setItem("pz-dklive", JSON.stringify({date, at: out.at, props: allProps})); } catch (e) {}
  try { localStorage.setItem("pz-bestlocal", JSON.stringify(out)); } catch (e) {}
  return out;
}

// what moved between the agent's picks and this re-price
function diff(day, out, cands) {
  const lab = x => x.kind === "prop" ? `${x.name} ${x.side.toLowerCase()} ${x.point} ${STATN[x.stat] || x.stat}` : x.kind === "spread" ? `${x.team} ${x.point > 0 ? "+" : ""}${x.point}` : `${x.game} ${x.side.toLowerCase()} ${x.point}`;
  const all = [...cands.props, ...cands.games], ch = [];
  const before = [...(day.props || []), ...(day.games || [])], after = [...out.props, ...out.games];
  const aK = new Set(after.map(keyOf)), bK = new Set(before.map(keyOf));
  for (const x of before) {
    const now = all.find(y => keyOf(y) === keyOf(x));
    if (!now) { if (!aK.has(keyOf(x))) ch.push({t: "out", txt: `${lab(x)}: no longer passes the checks or no line posted`}); continue; }
    const parts = [];
    if (now.point !== x.point) parts.push(`line ${x.point} → ${now.point}`);
    if (now.am !== x.am) parts.push(`price ${x.am > 0 ? "+" : ""}${x.am} → ${now.am > 0 ? "+" : ""}${now.am}`);
    if (now.tier !== x.tier) parts.push(`${x.tier || "—"} → ${now.tier}`);
    if (parts.length) ch.push({t: aK.has(keyOf(x)) ? "moved" : "out", txt: `${lab(x)}: ${parts.join(", ")}${aK.has(keyOf(x)) ? "" : " · dropped from the top 5"}`});
  }
  for (const x of after) if (!bK.has(keyOf(x))) ch.push({t: "new", txt: `${lab(x)} · ${x.tier} ${x.conv}`});
  return ch;
}

// "Full refresh": a prefilled GitHub issue the issue workflow acts on (no token on the phone)
function fullRefreshUrl() {
  const body = `Re-run research for tonight (lineups, projections, 10,000 sims per game) and re-rank the best bets.\n\nAsked from the dashboard at ${new Date().toLocaleString()}.`;
  return `https://github.com/${OWNER}/${REPO}/issues/new?title=${encodeURIComponent("Refresh: best bets " + D.players.date)}&body=${encodeURIComponent(body)}`;
}
window.BESTREFRESH = {reprice, fullRefreshUrl, keyOf};
})();
