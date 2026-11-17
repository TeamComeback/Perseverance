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
for (const f of ["case.js", "refresh.js", "sim.js", "games.js", "city.js"]) { try { new Function(fs.readFileSync(path.join(DOCS, f), "utf8")); ok(f); } catch (e) { bad(`${f}: ${e.message}`); } }
const html = fs.readFileSync(path.join(DOCS, "index.html"), "utf8");
try { new Function(html.slice(html.indexOf("<script>\nconst $") + 8, html.indexOf("</script>\n<script src="))); ok("index.html inline script"); } catch (e) { bad("index.html inline script: " + e.message); }

console.log("2. data shapes");
const need = {"players.json": d => d.date && Array.isArray(d.games) && d.players, "sims.json": d => d.date && d.games, "best.json": d => d.days,
  "state.json": d => d.book && d.stats, "scoreboard.json": d => d.by_stat, "team_model.json": d => d.teams && d.params, "sims_day.json": d => Array.isArray(d.runs)};
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
      const {chromium} = require("playwright"), b = await chromium.launch(), p = await b.newPage({viewport: {width: 390, height: 844}});
      const errs = []; p.on("pageerror", e => errs.push(e.message));
      await p.route(/espn\.com|cdn\.|github\.com/, r => r.fulfill({status: 200, contentType: "application/json", headers: {"access-control-allow-origin": "*"}, body: "{}"}));
      await p.goto("http://localhost:8899/index.html"); await p.waitForTimeout(2500);
      for (const t of ["today", "games", "props", "bets", "health", "book", "trends", "agent", "sim"]) {
        const n = errs.length; await p.evaluate(v => go(v), t); await p.waitForTimeout(1200);
        const txt = await p.evaluate(() => [...document.querySelectorAll(".view.on")].map(v => v.innerText.length).join(","));
        errs.length === n && +txt > 50 ? ok(`${t} (${txt} chars)`) : bad(`${t}: ${errs.slice(n).join(" | ") || "renders nearly empty"}`);
      }
      // one side per prop: every OVER/UNDER tag on every screen, for the same player-stat, must be the same side
      console.log("6. one side per prop on every screen");
      const seen = {};
      const grab = () => p.evaluate(() => [...document.querySelectorAll(".view.on [data-pid][data-stat]")].map(el => {
        const t = el.querySelector(":scope > div > .lean, :scope .lean"); return t ? [el.dataset.pid + "|" + el.dataset.stat, t.textContent.trim()] : null; }).filter(Boolean));
      for (const t of ["today", "props"]) { await p.evaluate(v => go(v), t); await p.waitForTimeout(900);
        if (t === "props") { await p.evaluate(() => { D.pf.all = true; renderProps(); }); await p.waitForTimeout(600); }
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
      await b.close();
    } catch (e) { bad("ui: " + e.message); }
    srv.close();
  }
  console.log(fails ? `\n${fails} check(s) failed` : "\nall checks passed");
  process.exit(fails ? 1 : 0);
})();
