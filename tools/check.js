// Pre-push check: run before pushing dashboard or engine changes.  node tools/check.js [--ui]
//  1. every script parses (docs/*.js and the inline script in index.html)
//  2. the data files the dashboard needs exist and have the shapes it reads
//  3. the sim engine runs: one game, 300 sims, sane scores
//  4. the city map: docs/city/cast.json is valid and every module has a desk (tools/orgmap.js --check)
//  5. --ui: every tab renders headless at phone width with no page errors (needs Playwright; serves docs/ on :8899)
const fs = require("fs"), path = require("path");
const ROOT = path.resolve(__dirname, ".."), DOCS = path.join(ROOT, "docs"), DATA = path.join(DOCS, "data");
let fails = 0; const ok = m => console.log("  ok   " + m), bad = m => { fails++; console.log("  FAIL " + m); };
const read = f => JSON.parse(fs.readFileSync(path.join(DATA, f), "utf8"));

console.log("1. scripts parse");
for (const f of ["case.js", "refresh.js", "sim.js", "games.js", "city.js", "city3d.js"]) { try { new Function(fs.readFileSync(path.join(DOCS, f), "utf8")); ok(f); } catch (e) { bad(`${f}: ${e.message}`); } }
const html = fs.readFileSync(path.join(DOCS, "index.html"), "utf8");
try { new Function(html.slice(html.indexOf("<script>\nconst $") + 8, html.indexOf("</script>\n<script src="))); ok("index.html inline script"); } catch (e) { bad("index.html inline script: " + e.message); }

console.log("2. data shapes");
const need = {"players.json": d => d.date && Array.isArray(d.games) && d.players, "sims.json": d => d.date && d.games, "best.json": d => d.days,
  "state.json": d => d.book && d.stats, "scoreboard.json": d => d.by_stat, "team_model.json": d => d.teams && d.params, "matchup_hist.json": d => Array.isArray(d.rows) && d.n === d.rows.length && d.rows.length > 0 && d.rows[0].length === 10, "sims_day.json": d => Array.isArray(d.runs)};
for (const [f, t] of Object.entries(need)) { try { t(read(f)) ? ok(f) : bad(f + ": missing fields"); } catch (e) { bad(`${f}: ${e.message}`); } }
try { const pj = read("players.json"); for (const g of pj.games.filter(g => g.state === "pre")) if (!fs.existsSync(path.join(DATA, "games", g.espn_id + ".json"))) bad(`game file missing for ${g.away}@${g.home}`); ok("game files present"); } catch (e) { bad("game files: " + e.message); }

console.log("3. sim engine");
try {
  global.window = global; global.RENDER = {}; global.D = {};
  eval(fs.readFileSync(path.join(DOCS, "sim.js"), "utf8"));
  const pj = read("players.json"); D.players = pj;
  const g = pj.games.find(x => fs.existsSync(path.join(DATA, "games", x.espn_id + ".json")));
  if (!g) bad("no game file to simulate");
  else {
    const E = window.simEngine, gf = read(path.join("games", g.espn_id + ".json"));
    E.setSeed(7); const P = E.calibrate(E.prep(g, gf), 2, 120);
    let tot = 0; for (let i = 0; i < 300; i++) { const r = E.simGame(P, false); tot += r.score.home + r.score.away; }
    const avg = tot / 300;
    avg > 180 && avg < 280 ? ok(`${g.away}@${g.home}: 300 sims, average total ${avg.toFixed(1)}`) : bad(`average total ${avg.toFixed(1)} is not believable`);
  }
} catch (e) { bad("sim engine: " + e.message); }

console.log("4. city map");
try { const out = require("child_process").execFileSync(process.execPath, [path.join(ROOT, "tools", "orgmap.js"), "--check"], {encoding: "utf8", stdio: ["ignore", "pipe", "pipe"]}); ok(out.trim().replace("[city] ", "")); }
catch (e) { bad("city map: " + ((e.stderr || "") + (e.stdout || "")).trim().replace(/\n\s*/g, "; ")); }

(async () => {
  if (process.argv.includes("--ui")) {
    console.log("5. every tab renders (phone width)");
    const http = require("http"), mime = {".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".css": "text/css"};
    const srv = http.createServer((q, r) => { const f = path.join(DOCS, decodeURIComponent(q.url.split("?")[0]).replace(/\/$/, "/index.html"));
      fs.readFile(f, (e, b) => { if (e) { r.writeHead(404); return r.end(); } r.writeHead(200, {"content-type": mime[path.extname(f)] || "application/octet-stream"}); r.end(b); }); }).listen(8899);
    try {
      const {chromium} = require("playwright"), b = await chromium.launch({args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]}), p = await b.newPage({viewport: {width: 390, height: 844}});
      const errs = []; p.on("pageerror", e => errs.push(e.message));
      await p.route(/espn\.com|cdn\.|github\.com/, r => r.fulfill({status: 200, contentType: "application/json", headers: {"access-control-allow-origin": "*"}, body: "{}"}));
      await p.goto("http://localhost:8899/index.html"); await p.waitForTimeout(2500);
      for (const t of ["today", "games", "props", "bets", "health", "book", "trends", "agent", "sim"]) {
        const n = errs.length; await p.evaluate(v => go(v), t); await p.waitForTimeout(1200);
        const txt = await p.evaluate(() => [...document.querySelectorAll(".view.on")].map(v => v.innerText.length).join(","));
        errs.length === n && +txt > 50 ? ok(`${t} (${txt} chars)`) : bad(`${t}: ${errs.slice(n).join(" | ") || "renders nearly empty"}`);
      }
      // the one prop list (Props tab): presets, filters and sorter act on the same rows; Winning bets are all real wins; Clear restores the list
      console.log("5b. prop list: presets, filters and sorter");
      try {
        const gid0 = await p.evaluate(() => { const g = D.players.games.find(x => x.state !== "post" && new Date(x.start) > Date.now() - 3 * 3600e3); return g && g.espn_id; });
        if (!gid0) ok("prop list: no game left to test against (skipped)");
        else {
          await p.evaluate(g => { go("props"); store.set("flt", {}); setFlt("game", g); setFlt("pre", "all"); }, gid0); await p.waitForTimeout(700);
          const base = await p.evaluate(() => document.querySelectorAll("#v-props .ur").length);
          base ? ok(`${base} rows in one list (preset All, one game)`) : bad("prop list is empty for a live game");
          const side = await p.evaluate(() => { setFlt("side", "Over"); return [...document.querySelectorAll("#v-props .ur")].every(el => el.querySelector(".ubt.Over")); });
          side ? ok("side filter: every row is an over") : bad("side filter leaks unders");
          await p.evaluate(() => { setFlt("side", ""); setFlt("pre", "priced"); setFlt("sort", "edge"); }); await p.waitForTimeout(500);
          const ev = await p.evaluate(() => { const rows = [...document.querySelectorAll("#v-props .ur")].map(el => propRows().find(x => String(x.pid) === el.dataset.pid && x.stat === el.dataset.stat)).filter(Boolean);
            const e = rows.map(r => r.ev ?? -9); return {n: rows.length, priced: rows.every(r => r.line != null), sorted: e.every((v, i) => i === 0 || v <= e[i - 1] + 1e-9)}; });
          ev.n && ev.priced && ev.sorted ? ok(`priced + sort by edge: ${ev.n} rows in order`) : bad("priced/edge sort: " + JSON.stringify(ev));
          const win = await p.evaluate(() => { setFlt("sort", ""); setFlt("pre", "win"); return [...document.querySelectorAll("#v-props .ur")].map(el => propRows().find(x => String(x.pid) === el.dataset.pid && x.stat === el.dataset.stat)).filter(Boolean).every(isWin); });
          win ? ok("Winning bets: every row clears the bar") : bad("Winning bets shows a row that is not a win");
          const rec = await p.evaluate(() => { const x = mxRecord({stat: "ast", mxPos: "soft"}); return {n: x.A.n, ok: x.A.n > 0 && x.A.o <= x.A.n}; });
          rec.ok ? ok(`record panel: ${rec.n} graded assist props in soft position matchups`) : bad("matchup record panel is empty");
          // names stay on every row; one sort changes who is on the list; one matchup dropdown filters; stale hidden settings are cleared
          const nm = await p.evaluate(() => { store.set("flt", {}); setFlt("pre", "all"); const r = [...document.querySelectorAll("#v-props .ur .nm")]; return {n: r.length, ok: r.every(e => e.getBoundingClientRect().width > 30 && e.textContent.trim().length > 1)}; });
          nm.n && nm.ok ? ok(`names visible on all ${nm.n} rows`) : bad("player name missing or squeezed on a row: " + JSON.stringify(nm));
          const srt = await p.evaluate(() => { const ids = () => [...document.querySelectorAll("#v-props .ur")].map(e => e.dataset.pid + e.dataset.stat).join(",");
            setFlt("pre", "priced"); setFlt("sort", ""); const a = ids(); setFlt("sort", "win"); const b = ids(); setFlt("sort", "conv"); const c = ids(); return {same: a === b && b === c, n: a.split(",").length}; });
          !srt.same ? ok("sort: Best, Hit chance and Conviction give different lists") : bad("sorting does not change the list");
          const mxs = await p.evaluate(() => { setFlt("sort", ""); setFlt("mxPos", "tough"); const r = [...document.querySelectorAll("#v-props .ur")].map(el => propRows().find(x => String(x.pid) === el.dataset.pid && x.stat === el.dataset.stat)).filter(Boolean);
            return {n: r.length, okAll: r.every(x => mxInfo(x).pos >= MX.hi), line: !!document.querySelector("#v-props .mxrec"), sel: !!document.querySelector('#fbar select[data-f="mxPos"]')}; });
          mxs.sel && mxs.okAll && mxs.line ? ok(`matchup dropdown: ${mxs.n} rows, all tough, record line shown`) : bad("matchup dropdown: " + JSON.stringify(mxs));
          // hit chance and price limits: every row meets both; Value preset sets 55% / -130; an empty result explains itself
          const hp = await p.evaluate(() => { store.set("flt", {}); setFlt("pre", "priced"); setFlt("hit", "55"); setFlt("price", "-130");
            const r = [...document.querySelectorAll("#v-props .ur")].map(el => propRows().find(x => String(x.pid) === el.dataset.pid && x.stat === el.dataset.stat)).filter(Boolean);
            return {n: r.length, ok: r.every(x => x.p_win >= 0.55 && x.am >= -130), sel: !!document.querySelector('#fbar select[data-f="hit"]') && !!document.querySelector('#fbar select[data-f="price"]')}; });
          hp.sel && hp.ok ? ok(`hit chance 55%+ and price -130 or better: ${hp.n} rows, all within both limits`) : bad("hit/price filter: " + JSON.stringify(hp));
          const vp = await p.evaluate(() => { document.querySelector('#fbar button[data-fp="value"]').click(); const f = FLT(); return {f: [f.pre, f.hit, f.price, f.sort].join("|")}; });
          vp.f === "value|55|-130|win" ? ok("Value preset sets 55% / -130 / hit-chance sort") : bad("Value preset: " + vp.f);
          const em = await p.evaluate(() => { setFlt("hit", "65"); setFlt("price", "100"); const e = document.querySelector("#v-props .empty"); return e ? e.textContent : (document.querySelectorAll("#v-props .ur").length + " rows"); });
          /Nothing has both|rows/.test(em) ? ok("empty hit/price result explains itself") : bad("empty message: " + em);
          const old = await p.evaluate(() => { store.set("flt", {mxSort: "win", mxDir: "u", mxPace: "lo", sort: "mx"}); return JSON.stringify(FLT()); });
          /mx(Sort|Dir|Pace)/.test(old) || /"sort":"mx"/.test(old) ? bad("old matchup settings not cleared: " + old) : ok("old hidden matchup settings are cleared");
          await p.evaluate(() => { store.set("flt", {}); fbar("props"); renderProps(); });
        }
      } catch (e) { bad("prop list: " + e.message); }
      // line movement on My bets: history files exist for the days with bets, rows and chart render, no page errors
      console.log("5c. line movement and CLV");
      try {
        const n = errs.length; await p.evaluate(() => go("bets")); await p.waitForTimeout(2200);
        const r = await p.evaluate(async () => { const m = L.book.bets.filter(b => b.legs.length === 1); let ok = 0, alt = 0;
          for (const b of m) { const x = await moveOf(b).catch(() => null); if (x) { ok++; if (x.alt) alt++; if (!x.pts.length || !x.last) return {bad: b.id}; } }
          return {bets: m.length, ok, alt, rows: document.querySelectorAll(".lmrow").length}; });
        r.bad ? bad("line movement: broken series for " + r.bad) : r.bets && !r.ok ? bad("line movement found no history for any bet") : ok(`line movement: ${r.ok}/${r.bets} bets have history (${r.alt} alternate lines), ${r.rows} rows`);
        errs.length === n ? ok("no page errors on My bets") : bad("My bets errors: " + errs.slice(n).join(" | "));
      } catch (e) { bad("line movement: " + e.message); }
      // edit a bet: a lost under 2.5 (finished 3) edited to 3.5 re-grades to a win, cash and record move together, the original number stays in the note
      console.log("5d. edit a bet");
      try {
        const r = await p.evaluate(async () => { go("bets"); saveL = () => {};
          const bet = (id, st) => ({id, placed: "2026-11-23T20:00:00Z", run: L.book.run, source: "you", playbook: "manual", dec: 1.9, stake: 5, note: "", status: st, payout: st === "lost" ? 0 : null, profit: st === "lost" ? -5 : null, settled: st === "lost" ? "2026-11-24T03:00:00Z" : null,
            legs: [{sport: "basketball_nba", commence: "2026-11-24T00:00Z", market: "player_rebounds", outcome: "Test Player Under", side: "Under", player: "Test Player", stat: "reb", point: 2.5, price: 1.9, book: "draftkings", result: st === "lost" ? "loss" : null, actual: st === "lost" ? 3 : undefined}]});
          L.book.bets.push(bet("edt1", "lost"), bet("edt2", "open")); L.book.cash = cashFromBets(L.book); syncManual();
          const c0 = L.book.cash; editBet("edt1"); document.getElementById("el").value = "3.5"; document.getElementById("er").value = "auto"; document.getElementById("ego").click();
          const b1 = L.book.bets.find(x => x.id === "edt1"), c1 = L.book.cash;
          editBet("edt2"); document.getElementById("ek").value = "10"; document.getElementById("ego").click();
          const b2 = L.book.bets.find(x => x.id === "edt2");
          L.book.bets = L.book.bets.filter(x => !x.id.startsWith("edt")); L.book.cash = cashFromBets(L.book); syncManual();
          return {st: b1.status, d1: +(c1 - c0).toFixed(2), ed: b1.edited, note: /line 2\.5 → 3\.5/.test(b1.note), st2: b2.status, stake2: b2.stake, d2: +(L.book.cash - c1).toFixed(2) }; });
        r.st === "won" && r.d1 === 9.5 && r.ed && r.note && r.st2 === "open" && r.stake2 === 10 ? ok("edit: regraded to a win, cash +$9.50, note kept; open bet stake change re-prices cash") : bad("edit bet: " + JSON.stringify(r));
      } catch (e) { bad("edit bet: " + e.message); }
      // My bets opens fully collapsed (every date and every menu) and lists newest first
      console.log("5e. My bets collapsed, newest first");
      try {
        await p.evaluate(() => { go("today"); }); await p.waitForTimeout(300);
        await p.evaluate(() => { D.bfOpen = new Set(["x"]); store.set("o-b-race", true); go("bets"); }); await p.waitForTimeout(1200);
        const r = await p.evaluate(() => { const days = [...document.querySelectorAll("#betList details.bday")], keys = days.map(d => d.dataset.day).filter(k => k !== "unknown");
          return {n: days.length, open: days.filter(d => d.open).length, sorted: keys.every((k, i) => i === 0 || k <= keys[i - 1]), menus: [...document.querySelectorAll("#v-bets > details.sec")].filter(d => d.open).length, sortBtn: !!document.getElementById("bfSort")}; });
        r.n && !r.open && r.sorted && !r.menus && !r.sortBtn ? ok(`${r.n} dates, all closed, newest first, all menus closed`) : bad("My bets layout: " + JSON.stringify(r));
      } catch (e) { bad("my bets collapsed: " + e.message); }
      // live tracker: record by sim edge size, and the live table says "Move vs pregame" not CLV
      console.log("5f. live record by sim edge size");
      try {
        const r = await p.evaluate(async () => { D.livetrack = D.livetrack || await get("live_track.json", {}); const h = edgeHtml(); return {rows: (h.match(/<tr>/g) || []).length, has: /Live record by sim edge size/.test(h) || h === ""}; });
        r.has ? ok(`edge table renders (${r.rows} rows)`) : bad("edge table: " + JSON.stringify(r));
        const sj = await (await fetch("http://localhost:8899/sim.js")).text();
        /Move vs pregame/.test(sj) && !/<th>CLV<\/th>/.test(sj) ? ok("live column renamed Move vs pregame") : bad("live CLV header not renamed");
      } catch (e) { bad("edge table: " + e.message); }
      // simulation box on the player card: shown with avg, typical range, over/under at the card's line; moves with the line; shown for every player with a saved sim
      console.log("5c. simulation box on the player card");
      try {
        const sc = await p.evaluate(async () => { D.sims = D.sims || await get("sims.json", {games: {}}); const r = propRows().find(x => x.line != null && D.sims.games[x.espn_id] && D.sims.games[x.espn_id].players[x.pid] && D.sims.games[x.espn_id].players[x.pid].h[x.stat]); return r && {pid: r.pid, stat: r.stat}; });
        if (!sc) ok("sim box: no simmed player tonight (skipped)");
        else {
          await p.evaluate(x => openPlayer(x.pid, x.stat), sc); await p.waitForTimeout(1500);
          const a = await p.evaluate(() => { const b = document.querySelector(".simb"); return b ? {txt: b.innerText, over: b.querySelectorAll(".kv .v")[2].textContent, bars: b.querySelectorAll(".sbars i").length} : null; });
          await p.evaluate(() => { for (let i = 0; i < 6; i++) document.querySelector('.lineset .step[data-d="1"]').click(); }); await p.waitForTimeout(700);
          const b2 = await p.evaluate(() => { const b = document.querySelector(".simb"); return b ? b.querySelectorAll(".kv .v")[2].textContent : null; });
          a && /range/i.test(a.txt) && a.bars > 3 ? ok(`sim box on the card: ${a.over} over, ${a.bars} bars`) : bad("sim box missing or empty: " + JSON.stringify(a));
          a && b2 && a.over !== b2 ? ok(`sim over % moves with the line (${a.over} → ${b2})`) : bad(`sim over % did not move with the line (${a && a.over} → ${b2})`);
          const nos = await p.evaluate(() => { const g = Object.values(D.sims.games)[0], r = propRows().find(x => x.line != null && !(D.sims.games[x.espn_id] && D.sims.games[x.espn_id].players[x.pid])); return r ? (document.body.dataset.x = 1, simCardHtml(r, r.stat, r.line, "Over")) : "skip"; });
          nos === "skip" || /No sim yet/.test(nos) ? ok("player without a sim: gray note, no empty boxes") : bad("player without a sim shows: " + nos.slice(0, 80));
          await p.evaluate(() => history.back()); await p.waitForTimeout(300);
        }
      } catch (e) { bad("sim box: " + e.message); }
      // colors: green is only for winning / hitting, red only for losing / missing; Over and Under tags are neutral; the amber note color is not the pink brand color
      console.log("5d. color rules and line history");
      try {
        const cr = await p.evaluate(async () => { const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim().toLowerCase();
          const el = document.createElement("span"); el.className = "lean Under"; document.body.appendChild(el); const under = getComputedStyle(el).color; el.className = "lean Over"; const over = getComputedStyle(el).color; el.remove();
          const lossRGB = "rgb(255, 93, 115)", winRGB = "rgb(61, 220, 151)";
          D.dkRecent = D.dkRecent || await get("dk_recent.json", {}); const pl = Object.values(D.players.players).find(x => x.logs && x.logs.length > 3 && ["pts", "ast", "reb"].some(s => (D.dkRecent[x.pid + "|" + s] || []).length)); let hist = "";
          if (pl) { const s = ["pts", "ast", "reb"].find(s => (D.dkRecent[pl.pid + "|" + s] || []).length); hist = dkHistHtml(pl, s); }
          return {gold: v("--gold"), accent: v("--accent"), tagsNeutral: ![lossRGB, winRGB].includes(under) && ![lossRGB, winRGB].includes(over), hist: /<table/.test(hist) && !/class="m"|class="h"/.test(hist.replace(/<td class="(h|m)">/g, "")) , histNoRedOver: !/<td class="(h|m)">/.test(hist)}; });
        cr.gold !== cr.accent && cr.tagsNeutral && cr.histNoRedOver ? ok("Over/Under tags are neutral, amber is not the brand pink, line history O / U is not win/loss colored") : bad("color rules: " + JSON.stringify(cr));
      } catch (e) { bad("color rules: " + e.message); }
      // one side per prop (dropped best-bet cards are the morning record, so they are left out): every OVER/UNDER tag on every screen, for the same player-stat, must be the same side
      console.log("6. one side per prop on every screen");
      const seen = {};
      const grab = () => p.evaluate(() => [...document.querySelectorAll(".view.on [data-pid][data-stat]:not(.drop)")].map(el => {
        const t = el.querySelector(":scope > div > .lean, :scope .lean"); return t ? [el.dataset.pid + "|" + el.dataset.stat, t.textContent.trim()] : null; }).filter(Boolean));
      for (const t of ["today", "props"]) { await p.evaluate(v => go(v), t); await p.waitForTimeout(900);
        if (t === "props") { await p.evaluate(() => { setFlt("pre", "all"); }); await p.waitForTimeout(600); }
        for (const [k, v] of await grab()) (seen[k] = seen[k] || new Set()).add(v); }
      const gl = await p.evaluate(() => D.players.games.filter(g => g.state === "pre").map(g => g.espn_id));
      for (const g of gl) { await p.evaluate(x => { go("games"); openGame(x, "props"); }, g); await p.waitForTimeout(500); for (const [k, v] of await grab()) (seen[k] = seen[k] || new Set()).add(v); }
      const two = Object.entries(seen).filter(([, v]) => v.size > 1);
      two.length ? bad(`${two.length} prop(s) shown with both sides: ${two.slice(0, 4).map(([k, v]) => k + " " + [...v].join("/")).join("; ")}`) : ok(`${Object.keys(seen).length} props tagged, one side each`);
      const gid = await p.evaluate(() => (D.players.games[0] || {}).espn_id);
      if (gid) for (const sub of ["game", "match", "props", "lines", "box"]) { const n = errs.length; await p.evaluate(([g, s]) => { go("games"); openGame(g, s); }, [gid, sub]); await p.waitForTimeout(800);
        errs.length === n ? ok(`game page · ${sub}`) : bad(`game page ${sub}: ${errs.slice(n).join(" | ")}`); }
      // the city: draws, opens a building, traces a bet to its end
      { const n = errs.length; await p.goto("http://localhost:8899/city.html"); await p.waitForTimeout(1500);
        const blds = await p.evaluate(() => document.querySelectorAll("#city .bld[data-co]").length);
        // inside a building: every desk is a room with a worker and a chart; walk to the next building and back out
        await p.evaluate(() => document.querySelector("#b-lab polygon").dispatchEvent(new MouseEvent("click", {bubbles: true}))); await p.waitForTimeout(1200);
        const rooms = await p.evaluate(() => [document.querySelectorAll("#inroom .room").length, document.querySelectorAll("#inroom .room .holo").length, document.querySelectorAll("#inroom .room .wk").length]);
        await p.evaluate(() => document.querySelector("#inroom .nextb").click()); await p.waitForTimeout(500);
        const next = await p.evaluate(() => document.querySelectorAll("#inroom .room").length);
        await p.evaluate(() => document.querySelector('#inroom [data-in="back"]').click()); await p.waitForTimeout(700);
        const out = await p.evaluate(() => !document.querySelector("#inroom") && document.querySelector("#city").style.display !== "none");
        rooms[0] >= 5 && rooms[1] === rooms[0] && rooms[2] === rooms[0] && next >= 2 && out ? ok(`city inside: ${rooms[0]} rooms, each with a worker and a chart; next building ${next} rooms; back to the city`) : bad(`city inside: rooms ${rooms}, next ${next}, back ${out}`);
        await p.evaluate(() => document.querySelector("#seg [data-v=trace]").click()); await p.waitForTimeout(800);
        const bets = await p.evaluate(() => document.querySelectorAll("#pane .bet").length);
        if (bets) { await p.evaluate(() => document.querySelector("#pane .bet").click()); await p.waitForTimeout(400);
          for (let i = 0; i < 40; i++) await p.evaluate(() => { const b = document.querySelector('#pane [data-act="next"]'); b && b.click(); }); }
        const end = await p.evaluate(() => (document.querySelector("#pane .ctl span") || {}).textContent || "");
        await p.evaluate(() => document.querySelector("#seg [data-v=coh]").click()); await p.waitForTimeout(500);
        errs.length === n && blds >= 10 && bets ? ok(`city: ${blds} buildings, ${bets} bets listed, trace reached ${end}, coherence opens`) : bad(`city: ${errs.slice(n).join(" | ") || `${blds} buildings, ${bets} bets`}`); }
      // live status: the run log replays desk by desk; a run in progress (GitHub) takes over the strip
      { const n = errs.length; let ghs = "in_progress";
        await p.unroute(/espn\.com|cdn\.|github\.com/);
        await p.route(/api\.github\.com/, r => r.fulfill({status: 200, contentType: "application/json", headers: {"access-control-allow-origin": "*"}, body: JSON.stringify({workflow_runs: [{id: 1, status: ghs, conclusion: null, created_at: new Date(Date.now() - 9000).toISOString(), run_started_at: new Date(Date.now() - 6000).toISOString()}]})}));
        const three = await new Promise(res => { const f = path.join(require("os").tmpdir(), "three-r128.min.js"); if (fs.existsSync(f) && fs.statSync(f).size > 3e5) return res(fs.readFileSync(f));
          require("https").get("https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js", r => { if (r.statusCode !== 200) return res(null); const ch = []; r.on("data", d => ch.push(d)); r.on("end", () => { const bf = Buffer.concat(ch); try { fs.writeFileSync(f, bf); } catch (e) {} res(bf); }); }).on("error", () => res(null)).setTimeout(15000, function () { this.destroy(); res(null); }); });
        await p.route(/cdnjs\.cloudflare\.com/, r => three ? r.fulfill({status: 200, contentType: "text/javascript", body: three}) : r.abort());
        await p.goto("http://localhost:8899/city.html"); await p.waitForTimeout(1200);
        const steps = (() => { try { return read("city_now.json").steps.length; } catch (e) { return 0; } })();
        if (!steps) ok("city live: no run log yet (the next agent run writes docs/data/city_now.json)");
        else { await p.evaluate(() => { const r = document.querySelector("#rp"); if (r && r.textContent.includes("stop")) r.click(); });
          await p.evaluate(() => document.querySelector("#rp").click()); await p.waitForTimeout(600);
          const rpl = await p.evaluate(() => [document.querySelector("#now").classList.contains("run"), document.querySelector("#now em").textContent, document.querySelectorAll(".bld.act").length]);
          await p.evaluate(() => document.querySelector("#rp").click()); await p.waitForTimeout(13000);
          const busy = await p.evaluate(() => [document.querySelector("#b-spire").classList.contains("busy"), document.querySelector("#now b").textContent]);
          ghs = "completed";
          rpl[0] && /1\//.test(rpl[1]) && rpl[2] >= 1 && busy[0] ? ok(`city live: ${steps}-step run log replays desk by desk; a run in progress shows "${busy[1]}"`) : bad(`city live: replay ${rpl}, in progress ${busy}`); }
        // the 3D city: same towers and floors, tap a tower to go inside, 2D still there without WebGL or the library
        if (!three) ok("city 3D: skipped (three.js could not be fetched here)");
        else { await p.evaluate(() => document.querySelector('#mode [data-m="3d"]').click()); await p.waitForTimeout(4500);
          const gl = await p.evaluate(() => !!window.City3D && window.City3D.supported());
          if (!gl) ok("city 3D: skipped (no WebGL in this browser); 2D map stays");
          else { const s3 = await p.evaluate(() => { const cv = document.querySelector("#map3d canvas"), r = cv && cv.getBoundingClientRect(); return {cv: !!cv, w: r ? r.width : 0, svg: document.querySelector("#city").style.display, ctl: document.querySelectorAll(".c3ctl button").length, x: r ? r.left + r.width / 2 : 0, y: r ? r.top + r.height * .6 : 0}; });
            let inside = null; for (const dy of [0, -40, 40, -80, 80, -120]) { await p.mouse.click(s3.x, s3.y + dy); await p.waitForTimeout(700); inside = await p.evaluate(() => document.querySelectorAll("#inroom .room").length); if (inside) break; }
            await p.evaluate(() => { const x = document.querySelector('#inroom [data-in="back"]'); x && x.click(); }); await p.waitForTimeout(400);
            const back3 = await p.evaluate(() => document.querySelector("#map3d").style.display !== "none");
            await p.evaluate(() => document.querySelector('#mode [data-m="2d"]').click()); await p.waitForTimeout(300);
            const two = await p.evaluate(() => document.querySelector("#city").style.display !== "none" && document.querySelector("#map3d").style.display === "none");
            s3.cv && s3.w > 300 && s3.svg === "none" && s3.ctl === 6 && inside && back3 && two ? ok(`city 3D: draws, a tapped tower opens ${inside} rooms, back to 3D, switch back to 2D`) : bad(`city 3D: ${JSON.stringify(s3)}, inside ${inside}, back ${back3}, 2D ${two}`); } }
        if (errs.length !== n) bad("city live/3D: " + errs.slice(n).join(" | ")); }
      await b.close();
    } catch (e) { bad("ui: " + e.message); }
    srv.close();
  }
  console.log(fails ? `\n${fails} check(s) failed` : "\nall checks passed");
  process.exit(fails ? 1 : 0);
})();
