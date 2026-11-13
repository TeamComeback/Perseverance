// Pre-push check: run before pushing dashboard or engine changes.  node tools/check.js [--ui]
//  1. every script parses (docs/*.js and the inline script in index.html)
//  2. the data files the dashboard needs exist and have the shapes it reads
//  3. the sim engine runs: one game, 300 sims, sane scores
//  4. --ui: every tab renders headless at phone width with no page errors (needs Playwright; serves docs/ on :8899)
const fs = require("fs"), path = require("path");
const ROOT = path.resolve(__dirname, ".."), DOCS = path.join(ROOT, "docs"), DATA = path.join(DOCS, "data");
let fails = 0; const ok = m => console.log("  ok   " + m), bad = m => { fails++; console.log("  FAIL " + m); };
const read = f => JSON.parse(fs.readFileSync(path.join(DATA, f), "utf8"));

console.log("1. scripts parse");
for (const f of ["case.js", "refresh.js", "sim.js", "games.js"]) { try { new Function(fs.readFileSync(path.join(DOCS, f), "utf8")); ok(f); } catch (e) { bad(`${f}: ${e.message}`); } }
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

(async () => {
  if (process.argv.includes("--ui")) {
    console.log("4. every tab renders (phone width)");
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
      const gid = await p.evaluate(() => (D.players.games[0] || {}).espn_id);
      if (gid) for (const sub of ["game", "match", "props", "lines", "box"]) { const n = errs.length; await p.evaluate(([g, s]) => { go("games"); openGame(g, s); }, [gid, sub]); await p.waitForTimeout(800);
        errs.length === n ? ok(`game page · ${sub}`) : bad(`game page ${sub}: ${errs.slice(n).join(" | ")}`); }
      await b.close();
    } catch (e) { bad("ui: " + e.message); }
    srv.close();
  }
  console.log(fails ? `\n${fails} check(s) failed` : "\nall checks passed");
  process.exit(fails ? 1 : 0);
})();
