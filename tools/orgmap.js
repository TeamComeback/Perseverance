// City Planner: draws Perseverance City from the code itself.  node tools/orgmap.js [--check]
//
// Reads the hand-written cast (docs/city/cast.json: companies, desks, titles, jobs, which documents each desk reads
// and writes) and everything that can be measured (code size, the Actions schedule, git history, how fresh each
// document is, last night's results) and writes three files the city page draws from:
//   docs/city/org.json        structure: companies, desks with code size, documents with writers and readers,
//                             routes between companies, the clock schedule, git history (kept when the clone is shallow)
//   docs/data/city_live.json  live status: when each document last changed, each desk's last shift and output,
//                             the trading crew's records, and the coherence (health) report
//   docs/data/city_trace.json every recent bet, traced desk by desk from the first fact to the result
// --check validates the cast against the repo and exits 1 on an error (no files written). tools/check.js runs it.
// Runs after every agent run in GitHub Actions; needs no network and spends no Odds API credits.
const fs = require("fs"), path = require("path"), cp = require("child_process"), crypto = require("crypto");
const ROOT = path.join(__dirname, ".."), DATA = path.join(ROOT, "docs", "data"), CITY = path.join(ROOT, "docs", "city");
const CHECK = process.argv.includes("--check");
const readJ = (p, d) => { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return d; } };
const dataJ = (f, d) => readJ(path.join(DATA, f), d);
const exists = p => fs.existsSync(path.join(ROOT, p));
const sh = c => { try { return cp.execSync(c, {cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], maxBuffer: 64 << 20}).trim(); } catch (e) { return ""; } };
const nowIso = new Date().toISOString().replace(/\.\d+Z$/, "Z");
const etDate = (d = new Date()) => new Intl.DateTimeFormat("en-CA", {timeZone: "America/New_York"}).format(d);
const TODAY = etDate();

const cast = readJ(path.join(CITY, "cast.json"), null);
if (!cast) { console.error("[city] docs/city/cast.json is missing or not valid JSON"); process.exit(1); }
const errors = [], warns = [];
const STATUSES = ["active", "probation", "understudy", "benched", "vacant", "planned"];

// ------------------------------------------------------------------ validate the cast
const C = {}, D = {};
for (const c of cast.companies || []) { if (C[c.id]) errors.push(`company ${c.id} listed twice`); C[c.id] = c; }
const spots = new Set();
for (const c of cast.companies || []) { const k = String(c.at); if (spots.has(k)) errors.push(`two companies at ${k}`); spots.add(k); }
const SRC = Object.fromEntries((cast.sources || []).map(s => [s.id, s]));
const DOCS = cast.docs || {};
for (const d of cast.desks || []) {
  if (D[d.id]) errors.push(`desk ${d.id} listed twice`); D[d.id] = d;
  if (!C[d.company]) errors.push(`desk ${d.id}: unknown company ${d.company}`);
  if (d.status && !STATUSES.includes(d.status)) errors.push(`desk ${d.id}: status must be one of ${STATUSES.join(", ")}`);
  if (!d.title || (!d.job && d.status !== "planned")) errors.push(`desk ${d.id}: needs a title and a job`);
  for (const f of d.code || []) if (!exists(f.split("#")[0])) errors.push(`desk ${d.id}: ${f} does not exist`);
  for (const x of [...(d.reads || []), ...(d.writes || [])]) if (!DOCS[x]) errors.push(`desk ${d.id}: document ${x} is not in docs`);
  for (const s of d.sources || []) if (!SRC[s]) errors.push(`desk ${d.id}: unknown source ${s}`);
}
for (const d of cast.desks || []) if (d.via && !D[d.via]) errors.push(`desk ${d.id}: works via unknown desk ${d.via}`);
for (const [t, id] of Object.entries(cast.reason_desks || {})) if (!D[id]) errors.push(`reason "${t}" points at unknown desk ${id}`);
for (const id of Object.keys(cast.metrics || {})) if (!D[id]) errors.push(`metric for unknown desk ${id}`);
for (const [n, id] of Object.entries(cast.check_desks || {})) if (!D[id]) errors.push(`check "${n}" points at unknown desk ${id}`);

// ------------------------------------------------------------------ code: size, first docstring line, anchors
const lineCount = s => s.split("\n").length;
function anchorSpan(file, name) {
  const lines = fs.readFileSync(path.join(ROOT, file), "utf8").split("\n");
  const py = file.endsWith(".py");
  const start = lines.findIndex(l => py ? new RegExp(`^def ${name}\\(`).test(l) : new RegExp(`^\\s*(async )?function ${name}\\(`).test(l));
  if (start < 0) return null;
  const ind = lines[start].match(/^\s*/)[0].length;
  let end = start + 1;
  for (; end < lines.length; end++) {
    const l = lines[end]; if (!l.trim()) continue;
    const i = l.match(/^\s*/)[0].length;
    if (py ? i === 0 && !l.startsWith("#") : (i === ind && /^\s*}/.test(l))) { if (!py) end++; break; }
  }
  return {from: start + 1, lines: end - start};
}
function about(file) {
  const s = fs.readFileSync(path.join(ROOT, file), "utf8");
  let m = file.endsWith(".py") ? s.match(/"""([\s\S]*?)"""/) : file.endsWith(".yml") ? s.match(/^name:\s*(.*)$/m) : s.match(/^\s*(?:\/\/|\/\*)\s*(.+)$/m);
  if (file.endsWith(".html")) m = s.match(/<title>([^<]*)<\/title>/);
  return m ? m[1].trim().split("\n")[0].replace(/=+/g, "").trim().slice(0, 200) : "";
}
function codeInfo(ref) {
  const [file, anchor] = ref.split("#"), full = path.join(ROOT, file);
  if (!fs.existsSync(full)) return {ref, file, lines: 0, missing: true};
  if (fs.statSync(full).isDirectory()) {
    const fl = fs.readdirSync(full).filter(f => /\.(py|js)$/.test(f));
    return {ref, file, dir: true, files: fl.length, lines: fl.reduce((t, f) => t + lineCount(fs.readFileSync(path.join(full, f), "utf8")), 0)};
  }
  const s = fs.readFileSync(full, "utf8");
  if (anchor) { const sp = anchorSpan(file, anchor); if (!sp) { errors.push(`${ref}: no function ${anchor} in ${file}`); return {ref, file, anchor, lines: 0}; }
    return {ref, file, anchor, from: sp.from, lines: sp.lines}; }
  return {ref, file, lines: lineCount(s), about: about(file)};
}
// every module must have a desk: anything without one shows up in the city as a new hire
const MODULES = [
  ...fs.readdirSync(path.join(ROOT, "agent")).filter(f => f.endsWith(".py") && f !== "__init__.py").map(f => "agent/" + f),
  ...fs.readdirSync(path.join(ROOT, "tools")).filter(f => f.endsWith(".js")).map(f => "tools/" + f),
  ...fs.readdirSync(path.join(ROOT, "docs")).filter(f => /\.(js|html)$/.test(f)).map(f => "docs/" + f),
  ...fs.readdirSync(path.join(ROOT, ".github", "workflows")).map(f => ".github/workflows/" + f),
  "tests/"];
const owned = new Set((cast.desks || []).flatMap(d => (d.code || []).map(r => r.split("#")[0])));
const newHires = MODULES.filter(m => !owned.has(m));
for (const m of newHires) (CHECK ? errors : warns).push(`${m} has no desk in docs/city/cast.json (new hire)`);
if (CHECK) {
  if (errors.length) { console.error("[city] cast check failed:\n  " + errors.join("\n  ")); process.exit(1); }
  console.log(`[city] cast ok: ${Object.keys(C).length} companies, ${Object.keys(D).length} desks, every module has a desk`);
  process.exit(0);
}
if (errors.length) console.warn("[city] cast problems (city drawn anyway):\n  " + errors.join("\n  "));

// which documents a desk's code actually mentions, to catch a cast that drifted from the code (shown, never fatal)
function mentions(file) {
  if (!/\.(py|js|html)$/.test(file)) return new Set();
  const s = fs.readFileSync(path.join(ROOT, file), "utf8"), out = new Set();
  for (const id of Object.keys(DOCS)) {
    if (id.endsWith("/*")) { const dir = id.slice(0, -2); if (new RegExp(`["'\`/]${dir}/[\\{\\$]|${dir.toUpperCase()}_?DIR|DIR\\s*=\\s*["'][^"']*${dir}`).test(s)) out.add(id); }
    else if (id.startsWith("nba/")) { const k = {"nba/games": /games-\{|games-\$\{/, "nba/shots": /load_shots|shots\//, "nba/pbp": /pbp\//, "nba/refs": /refs\.json/}[id]; if (k && k.test(s)) out.add(id); }
    else if (new RegExp(`(^|[^a-z_])${id.replace(".", "\\.")}`).test(s)) out.add(id);
  }
  return out;
}

// ------------------------------------------------------------------ structure
const companies = (cast.companies || []).map(c => ({...c, desks: []}));
// a file shared by several desks (run.py, sim_all.js) is judged against everything those desks declare together
const declaredByFile = {};
for (const d of cast.desks || []) for (const r of d.code || []) { const f = r.split("#")[0]; (declaredByFile[f] = declaredByFile[f] || new Set()); for (const x of [...(d.reads || []), ...(d.writes || [])]) declaredByFile[f].add(x); }
const desks = (cast.desks || []).map(d => {
  const code = (d.code || []).map(codeInfo);
  const men = new Set(); for (const r of d.code || []) { const f = r.split("#")[0]; if (!r.includes("#") && exists(f) && !f.endsWith("/")) for (const m of mentions(f)) men.add(m); }
  const declared = new Set((d.code || []).flatMap(r => [...(declaredByFile[r.split("#")[0]] || [])]));
  return {id: d.id, company: d.company, title: d.title, job: d.job || "", status: d.status || "active", note: d.note || null,
    code, lines: code.reduce((t, c) => t + (c.lines || 0), 0), reads: d.reads || [], writes: d.writes || [], sources: d.sources || [],
    via: d.via || null, undeclared: [...men].filter(m => !declared.has(m)).sort()};
});
for (const d of desks) { const c = companies.find(x => x.id === d.company); if (c) c.desks.push(d.id); }
for (const c of companies) {
  const files = new Map();
  for (const id of c.desks) for (const k of desks.find(d => d.id === id).code) if (!k.anchor) files.set(k.file, k.lines);
  c.lines = [...files.values()].reduce((t, n) => t + n, 0) || desks.filter(d => d.company === c.id).reduce((t, d) => t + d.lines, 0);
}
const docs = Object.entries(DOCS).map(([id, x]) => ({id, name: x.name, path: x.path || "docs/data/" + id, fresh_h: x.fresh_h || null,
  writers: desks.filter(d => d.writes.includes(id)).map(d => d.id), readers: desks.filter(d => d.reads.includes(id)).map(d => d.id)}));
// routes: company to company, one per pair, carrying every document that flows between them
const routes = {};
for (const doc of docs) for (const w of doc.writers) for (const r of doc.readers) {
  const a = desks.find(d => d.id === w).company, b = desks.find(d => d.id === r).company;
  if (a === b) continue;
  const k = a + ">" + b; (routes[k] = routes[k] || {from: a, to: b, docs: []}).docs.includes(doc.id) || routes[k].docs.push(doc.id);
}
// source beams: outside feeds into the desks that call them
const beams = desks.flatMap(d => d.sources.map(s => ({source: s, desk: d.id, company: d.company})));

// the clock: every cron line with its comment, and the mode it runs
function schedule() {
  const out = [];
  for (const wf of fs.readdirSync(path.join(ROOT, ".github", "workflows"))) {
    const s = fs.readFileSync(path.join(ROOT, ".github", "workflows", wf), "utf8");
    const modes = {}; for (const m of s.matchAll(/^\s*((?:"[^"]+"\|?)+)\)\s*MODE=([\w-]+)/gm)) for (const c of m[1].match(/"[^"]+"/g)) modes[c.slice(1, -1)] = m[2];
    for (const m of s.matchAll(/^\s*- cron:\s*"([^"]+)"\s*(?:#\s*(.*))?$/gm))
      out.push({workflow: wf, cron: m[1], note: (m[2] || "").replace(/\s+/g, " ").trim(), mode: modes[m[1]] || (wf === "agent.yml" ? "cycle" : wf.replace(".yml", ""))});
  }
  return out;
}

// git history: kept from the last full clone when Actions checks out a shallow one
const prevOrg = readJ(path.join(CITY, "org.json"), {});
function gitInfo() {
  if (sh("git rev-parse --is-shallow-repository") !== "false") {
    // Actions checks out one commit; keep the history from the last full build and refresh the count GitHub gives us
    const g = prevOrg.git ? {...prevOrg.git} : null, n = +process.env.CITY_COMMITS;
    if (g && n > 0) { const today = TODAY; g.days = {...g.days}; g.days[today] = (g.days[today] || 0) + Math.max(0, n - g.commits); g.commits = n; g.last = today; }
    return g;
  }
  const days = {}; for (const d of sh("git log --format=%ad --date=short").split("\n").filter(Boolean)) days[d] = (days[d] || 0) + 1;
  const born = {}; let cur = null;
  for (const l of sh("git log --reverse --diff-filter=A --format=@%ad --date=short --name-only").split("\n")) {
    if (l.startsWith("@")) cur = l.slice(1); else if (l && cur && !born[l] && /\.(py|js|html|yml)$/.test(l) && !l.startsWith("docs/data")) born[l] = cur;
  }
  const human = sh("git log --format=%an").split("\n").filter(a => a && !/bot/i.test(a)).length;
  return {commits: Object.values(days).reduce((t, n) => t + n, 0), human_commits: human, first: Object.keys(days).sort()[0], last: Object.keys(days).sort().pop(), days, born};
}

const totalLines = MODULES.reduce((t, m) => t + (exists(m) ? codeInfo(m).lines : 0), 0);
const org = {generated: nowIso, city: cast.city, bulletin: cast.bulletin || [], companies, desks, docs, routes: Object.values(routes), beams,
  sources: cast.sources || [], schedule: schedule(), git: gitInfo(), new_hires: newHires, cast_problems: errors,
  totals: {lines: totalLines, modules: MODULES.length, desks: desks.length, companies: companies.length, documents: docs.length,
    routes: Object.keys(routes).length, schedules: 0, tests: fs.readdirSync(path.join(ROOT, "tests")).filter(f => f.endsWith(".py")).length}};
org.totals.schedules = org.schedule.length;

// ------------------------------------------------------------------ live: document freshness
const prevLive = dataJ("city_live.json", {docs: {}});
function docFiles(id, x) {
  if (id.endsWith("/*")) {
    const dir = path.join(DATA, id.slice(0, -2)); if (!fs.existsSync(dir)) return [];
    const fl = fs.readdirSync(dir).filter(f => f.endsWith(".json")).sort();
    if (!fl.length) return [];                                 // the folder exists but nothing is in it yet (briefing/ before the first edition)
    const today = fl.find(f => f.startsWith(TODAY)); return [path.join(dir, today || fl[fl.length - 1])];
  }
  if (x.path && x.path.startsWith("data/")) {
    const p = path.join(ROOT, x.path.replace("*", ""));
    if (x.path.endsWith("/")) { if (!fs.existsSync(p)) return []; const walk = d => fs.readdirSync(d).flatMap(f => { const q = path.join(d, f); return fs.statSync(q).isDirectory() ? walk(q) : [q]; });
      return walk(p).sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs).slice(0, 1); }
    if (x.path.includes("*")) { const dir = path.dirname(p), pre = path.basename(x.path).split("*")[0];
      return fs.existsSync(dir) ? fs.readdirSync(dir).filter(f => f.startsWith(pre)).sort().slice(-1).map(f => path.join(dir, f)) : []; }
    return fs.existsSync(p) ? [p] : [];
  }
  const p = path.join(DATA, id); return fs.existsSync(p) ? [p] : [];
}
const liveDocs = {};
for (const [id, x] of Object.entries(DOCS)) {
  const f = docFiles(id, x)[0];
  if (!f) { liveDocs[id] = {missing: true}; continue; }
  const buf = fs.readFileSync(f), hash = crypto.createHash("sha1").update(buf).digest("hex").slice(0, 12);
  let upd = null;
  if (f.endsWith(".json") && buf.length < 8e6) { try { const j = JSON.parse(buf); if (j && typeof j.updated === "string") upd = j.updated; } catch (e) {} }
  const prev = prevLive.docs && prevLive.docs[id];
  if (!upd) upd = prev && prev.hash === hash && prev.file === path.relative(ROOT, f) ? prev.at : (prev ? nowIso : (sh(`git log -1 --format=%cI -- "${path.relative(ROOT, f)}"`) || nowIso));
  const at = new Date(upd); upd = isNaN(at) ? nowIso : at.toISOString().replace(/\.\d+Z$/, "Z");
  liveDocs[id] = {at: upd, hash, kb: Math.round(buf.length / 1024), file: path.relative(ROOT, f)};
}

// ------------------------------------------------------------------ live: desk shifts and output
const getPath = (o, p) => p.split(".").reduce((x, k) => x == null ? x : x[k === "$today" ? TODAY : k], o);
function metric(spec) {
  if (!spec) return null;
  const f = spec.file.replace("$today", TODAY) + (spec.file.includes("$today") ? ".json" : "");
  const j = dataJ(f, null); if (j == null) return null;
  if (spec.count) { const v = getPath(j, spec.count); const n = Array.isArray(v) ? v.length : v && typeof v === "object" ? Object.keys(v).length : null; return n == null ? null : {v: n, label: spec.label}; }
  if (spec.record) { const r = getPath(j, spec.record); return r ? {v: `${r.w}-${r.l}${r.p ? "-" + r.p : ""}`, units: r.u, label: spec.label} : null; }
  const v = getPath(j, spec.value); return v == null ? null : {v, fmt: spec.fmt || null, label: spec.label};
}
const shift = {};
const latest = ids => ids.map(i => liveDocs[i] && liveDocs[i].at).filter(Boolean).sort().pop() || null;
for (const d of desks) shift[d.id] = d.writes.length ? latest(d.writes) : null;
for (let i = 0; i < 3; i++) for (const d of desks) if (!shift[d.id] && d.via) shift[d.id] = shift[d.via];
const deskLive = Object.fromEntries(desks.map(d => [d.id, {shift: d.status === "vacant" ? null : shift[d.id], out: metric((cast.metrics || {})[d.id])}]));

// the trading crew: one trader per playbook, with the record from the agent's ledger
const state = dataJ("state.json", {book: {bets: []}});
const paused = new Set(state.paused || []), crew = {};
for (const b of (state.book && state.book.bets) || []) {
  const c = crew[b.playbook] = crew[b.playbook] || {w: 0, l: 0, p: 0, open: 0, profit: 0, staked: 0};
  if (b.status === "won") c.w++; else if (b.status === "lost") c.l++; else if (b.status === "push") c.p++; else if (b.status === "open") c.open++;
  c.profit += b.profit || 0; c.staked += b.stake || 0;
}
for (const [k, c] of Object.entries(crew)) { c.profit = Math.round(c.profit * 100) / 100; c.staked = Math.round(c.staked * 100) / 100; c.benched = paused.has(k); }

// ------------------------------------------------------------------ coherence (health)
const checks = dataJ("checks.json", {checks: []});
const ageH = iso => iso ? (Date.now() - new Date(iso)) / 36e5 : null;
const stale = Object.entries(DOCS).filter(([id, x]) => x.fresh_h && liveDocs[id] && !liveDocs[id].missing && ageH(liveDocs[id].at) > x.fresh_h)
  .map(([id]) => ({id, age_h: Math.round(ageH(liveDocs[id].at))}));
const best = dataJ("best.json", {}), ranked = dataJ("ranked.json", {}), conv = dataJ("conv_switch.json", {}), obt = dataJ("ourbook_track.json", {});
const records = [
  best.record && {label: "Best Bets props", ...best.record.props, desk: "picker"},
  best.record && {label: "Best Bets game lines", ...best.record.games, desk: "picker"},
  best.record && best.record.dropped && {label: "Plays the picker dropped", ...best.record.dropped, desk: "picker"},
  ranked.perf && ranked.perf.props && ranked.perf.props["1-10"] && {label: "Ranked board, top 10", ...ranked.perf.props["1-10"], desk: "editor"},
  ranked.perf && ranked.perf.props && ranked.perf.props["31+"] && {label: "Ranked board, 31st and lower", ...ranked.perf.props["31+"], desk: "editor"},
  state.stats && {label: "Agent bankroll", w: state.stats.won, l: state.stats.lost, p: state.stats.push, u: null, money: state.stats.profit, desk: "trader"},
].filter(Boolean);
const coherence = {
  checks: (checks.checks || []).map(c => ({name: c.name, status: c.status, detail: c.detail, desk: (cast.check_desks || {})[c.name] || "inspector"})), checks_at: checks.updated || null, status: checks.status || null,
  stale, records,
  understudy: conv.latest ? {streak: conv.streak || 0, need: 3, mode: conv.mode, n: conv.latest.n, days: conv.latest.days, ll: conv.latest.ll} : null,
  ourbook: obt.moves && obt.moves.all ? {toward: obt.moves.all.rate, n: obt.moves.all.n} : null,
  statuses: desks.filter(d => d.status !== "active").map(d => ({id: d.id, status: d.status, note: d.note})),
  drift: desks.filter(d => d.undeclared.length).map(d => ({id: d.id, docs: d.undeclared})),
  new_hires: newHires, cast_problems: errors,
};
const live = {updated: nowIso, today: TODAY, docs: liveDocs, desks: deskLive, crew, coherence,
  bankroll: state.stats ? {equity: state.stats.equity, profit: state.stats.profit, bets: state.stats.bets} : null};

// ------------------------------------------------------------------ traces: every recent bet, desk by desk
const ORDER = ["wire", "exchange", "lab", "sim", "ourbook", "news", "trading", "audit", "front", "ops"];
const RD = cast.reason_desks || {};
const pct = p => p == null ? "?" : Math.round(p * 100) + "%";
const am = dec => dec == null ? "" : dec >= 2 ? "+" + Math.round((dec - 1) * 100) : "-" + Math.round(100 / (dec - 1));
const STAT = {pts: "points", reb: "rebounds", ast: "assists", tpm: "threes", pra: "pts+reb+ast", ra: "reb+ast", pr: "pts+reb", pa: "pts+ast", stl: "steals", blk: "blocks"};
const sname = (s, n) => { const w = STAT[s] || s; return n === 1 && w.endsWith("s") && !w.includes("+") ? w.slice(0, -1) : w; };
const etTime = iso => { try { return new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(new Date(iso)); } catch (e) { return iso; } };
function sortSteps(steps) {
  const co = id => ORDER.indexOf((D[id] || {}).company);
  return steps.map((s, i) => ({...s, i})).sort((a, b) => (a.last ? 1 : 0) - (b.last ? 1 : 0) || co(a.desk) - co(b.desk) || a.i - b.i).map(({i, last, ...s}) => s);
}
const rlog = dataJ("ranked_log.json", {days: {}}).days || {}, clog = dataJ("case_log.json", {days: {}}).days || {};
const rankOf = (day, key) => { const L = rlog[day]; if (!L || !L[key]) return null; return {rank: L[key][6], of: Object.keys(L).length}; };
const traces = [];
const briefCache = {}, briefOf = d => d in briefCache ? briefCache[d] : (briefCache[d] = dataJ(`briefing/${d}.json`, null));

// 1. Best Bets picks, the last 14 days
const bdays = Object.keys(best.days || {}).sort().slice(-14);
for (const day of bdays) {
  const B = best.days[day];
  for (const kind of ["props", "games"]) for (const x of B[kind] || []) {
    const prop = x.kind === "prop", steps = [];
    const label = prop ? `${x.name} ${x.side} ${x.point} ${sname(x.stat)}` : `${x.game} ${x.kind === "total" ? x.side + " " + x.point : (x.team || x.side) + " " + (x.point > 0 ? "+" : "") + x.point}`;
    if (prop) {
      const lad = x.ladder && x.ladder.rows && x.ladder.rows[String(x.point)];
      steps.push({desk: "boxscore", t: `Last 10 games: ${x.avg10 != null ? x.avg10 + " " + sname(x.stat) + " a game" : "averages filed"}${lad && lad.L10 ? `; over ${x.point} in ${lad.L10[0]} of ${lad.L10[1]}` : ""}.`});
      steps.push({desk: "research", t: `Dossier built for ${x.name} (${x.team} vs ${x.opp}).`});
    } else steps.push({desk: "team", t: x.why || "Team ratings priced the game."});
    steps.push({desk: "dk", t: `DraftKings: ${x.side} ${x.point} at ${x.am != null ? (x.am > 0 ? "+" : "") + x.am : am(x.dec)}${x.open != null && x.open !== x.point ? ` (opened ${x.open})` : ""}.`});
    if (x.p_mkt != null) steps.push({desk: "fair", t: `With the book's margin removed, DraftKings gives it ${pct(x.p_mkt)}.`});
    if (x.p_kalshi != null) steps.push({desk: "kalshi", t: `Kalshi prices the same side at ${pct(x.p_kalshi)}.`});
    const tags = new Set([...((x.case && x.case.for) || []), ...((x.case && x.case.against) || [])].map(r => r.tag));
    if (x.p_sim != null && !tags.has("simulation")) steps.push({desk: "foreman", t: `Ten thousand simulated games: ${x.side.toLowerCase()} in ${pct(x.p_sim)}${prop && x.sim != null ? ` (average ${x.sim})` : ""}.`});
    if (prop && x.proj != null && !tags.has("projection")) steps.push({desk: "matchup", t: `Projection: ${x.proj} ${sname(x.stat)} against a line of ${x.point}.`});
    if (x.ob_ind != null) steps.push({desk: "setter", t: `Our sealed line, set before seeing the market: ${x.ob_ind} (DraftKings ${x.point}).`});
    for (const r of (x.case && x.case.for) || []) if (RD[r.tag]) steps.push({desk: RD[r.tag], t: r.t, k: "for", tag: r.tag});
    for (const r of (x.case && x.case.against) || []) if (RD[r.tag]) steps.push({desk: RD[r.tag], t: r.t, k: "against", tag: r.tag});
    steps.push({desk: "conviction", t: `Conviction ${x.conv} of 99${x.p_conv != null ? `; leans this strong have won about ${pct(x.p_conv)}` : ""}.`});
    const rk = prop ? rankOf(day, x.pid + "|" + x.stat) : null;
    steps.push({desk: "editor", t: rk ? `Ranked #${rk.rank} of ${rk.of} lines that night.` : `Placed on the ranked board, one side only.`});
    if (x.verdict) steps.push({desk: "casebuilder", t: x.verdict});
    steps.push({desk: "picker", t: `Chosen as a ${x.tier || "best"} bet for ${day}.`});
    const BF = briefOf(day), bk = prop ? `p|${x.pid}|${x.stat}|${x.side}` : `g|${x.espn_id}|${x.kind}|${x.side}`, bc = BF && ((BF.picks || {})[bk] || (BF.leans || {})[bk]);
    if (bc) steps.push({desk: "writer", t: `In the Daily Briefing (${bc.published.edition} edition) at ${bc.published.point} ${am(bc.published.dec)}` +
      (bc.play_to && bc.play_to.floor_am != null ? `, good down to ${bc.play_to.floor_am > 0 ? "+" : ""}${bc.play_to.floor_am}` : "") + `${prop ? "" : " (a lean)"}.`});
    const res = x.res === "win" ? "won" : x.res === "loss" ? "lost" : x.res === "push" ? "push" : "open";
    steps.push({desk: "settle", last: true, t: res === "open" ? "Not graded yet: the outcome is still in superposition." :
      `${prop && x.actual != null ? `Final: ${x.actual} ${sname(x.stat, x.actual)}. ` : ""}${res === "won" ? "Won" : res === "lost" ? "Lost" : "Push"}${x.units != null ? ` (${x.units > 0 ? "+" : ""}${x.units}u)` : ""}.`});
    if (res !== "open") steps.push({desk: "teacher", last: true, t: "Result added to the record of every reason above; the reason weights learn from it tonight."});
    traces.push({id: `b-${day}-${prop ? x.pid + "-" + x.stat : x.espn_id + "-" + x.kind}`, kind: "best", day, at: B.updated || null, label, sub: `Best Bets · ${x.tier || ""} · conviction ${x.conv}`, status: res, steps: sortSteps(steps)});
  }
}

// 2. the agent's own bets
for (const b of ((state.book && state.book.bets) || []).slice(-60)) {
  const L = b.legs[0], m = L.model, steps = [];
  const label = L.player ? `${L.player} ${L.side} ${L.point} ${sname(L.stat)}` : `${L.outcome}${L.point != null ? " " + (L.point > 0 ? "+" : "") + L.point : ""}${b.legs.length > 1 ? ` + ${b.legs.length - 1} more` : ""}`;
  if (m) {
    steps.push({desk: "boxscore", t: `Normal level: ${m.base} ${sname(L.stat)} in about ${m.minutes} minutes.`});
    steps.push({desk: "lineup", t: `Minutes projected: ${m.minutes}.`});
    for (const s of m.signals || []) steps.push({desk: {dvp: "matchup", sim: "matchup", h2h: "matchup", chances: "team", pace: "team", usage: "film", rest: "travel"}[s.k] || "matchup", t: s.txt, k: s.v > 1 === (L.side === "Over") ? "for" : "against"});
    steps.push({desk: "matchup", t: `Projection ${m.proj} (norm ${m.base} × ${m.mult} matchup) against ${L.point}; model ${pct(m.p_model)}.`});
  }
  steps.push({desk: L.book === "draftkings" ? "dk" : "buyer", t: `${L.book}: ${L.outcome}${L.point != null ? " " + L.point : ""} at ${am(L.price)}.`});
  if (L.fair_p != null) steps.push({desk: "fair", t: `Fair chance across the books: ${pct(L.fair_p)}.`});
  steps.push({desk: L.player ? "props" : "trader", t: `Playbook ${b.playbook}${b.ev != null ? `: expected edge ${Math.round(b.ev * 1000) / 10}%` : ""}, stake $${b.stake.toFixed(2)}.`});
  steps.push({desk: "compliance", t: `Cleared: ${L.book} is an approved book and the stake meets the $5 minimum.`});
  steps.push({desk: "bookkeeper", t: `Placed ${etTime(b.placed)} ET.`});
  if (L.close_p != null) steps.push({desk: "historian", t: `Closing fair chance ${pct(L.close_p)} (${L.close_p > L.fair_p ? "the line moved our way" : "the line moved against us"}).`});
  const res = b.status === "open" ? "open" : b.status;
  steps.push({desk: "settle", last: true, t: res === "open" ? "Not graded yet: the outcome is still in superposition." : `${res === "won" ? "Won" : res === "lost" ? "Lost" : "Push"}: ${b.profit >= 0 ? "+" : "-"}$${Math.abs(b.profit || 0).toFixed(2)}.`});
  const rv = b.review && b.review.legs && b.review.legs[0];
  if (rv) { steps.push({desk: "coroner", last: true, t: rv.headline || b.review.headline}); for (const w of (rv.why || []).slice(1, 3)) steps.push({desk: "sportswriter", last: true, t: w}); }
  if (res !== "open") steps.push({desk: "teacher", last: true, t: `Playbook ${b.playbook}'s record updated; the strategist adjusts its size tonight.`});
  traces.push({id: "a-" + b.id, kind: "agent", day: etDate(new Date(b.placed)), at: b.placed, label, sub: `Agent · ${b.playbook} · $${b.stake.toFixed(2)}`, status: res, steps: sortSteps(steps)});
}

// 3. the chairman's sportsbook slips
for (const it of (dataJ("manual_import.json", {items: []}).items || [])) {
  const L = it.leg, steps = [], day = etDate(new Date(new Date(L.commence) - 6 * 36e5));
  const label = L.player ? `${L.player} ${L.side} ${L.point} ${sname(L.stat)}` : `${L.outcome} ${L.point > 0 ? "+" : ""}${L.point}`;
  const key = L.player_id + "|" + L.stat, dk = L.player ? (dataJ(`dk/${day}.json`, {props: {}}).props || {})[key] : null;
  steps.push({desk: "clerk", t: `The chairman took it at DraftKings: $${it.stake.toFixed(2)} at ${am(L.price)}.`});
  if (dk) {
    if (dk.o) steps.push({desk: "dk", t: `DraftKings opened ${dk.o[0]} and closed ${dk.c ? dk.c[0] : "?"} that day.`});
    if (dk.proj != null) steps.push({desk: "matchup", t: `Model projection ${dk.proj} ${sname(L.stat)}; matchup lean ${dk.lean}.`});
    if (dk.cv != null) steps.push({desk: "conviction", t: `Matchup strength ${dk.cv} of 5.`});
  }
  const rk = L.player && rlog[day] && rlog[day][key];
  if (rk) steps.push({desk: "editor", t: `Ranked board: ${rk[0]} ${rk[1]}, #${rk[6] || "–"} of ${Object.keys(rlog[day]).length}, conviction ${rk[3]}.`});
  const cs = L.player && clog[day] && clog[day][key];
  if (cs) { if (cs[5] && cs[5].length) steps.push({desk: "casebuilder", t: `Case for: ${cs[5].join(", ")}.`, k: "for"}); if (cs[6] && cs[6].length) steps.push({desk: "casebuilder", t: `Case against: ${cs[6].join(", ")}.`, k: "against"}); }
  if (!L.player) steps.push({desk: "team", t: "The team model priced the spread that night."});
  steps.push({desk: "settle", last: true, t: `${it.result === "won" ? "Won" : it.result === "lost" ? "Lost" : "Push"}${it.actual != null ? ` with ${it.actual} ${sname(L.stat, it.actual)}` : ""}.`});
  traces.push({id: "y-" + it.id, kind: "yours", day, at: L.commence, label, sub: `Chairman · DraftKings · $${it.stake.toFixed(2)}`, status: it.result || "open", steps: sortSteps(steps)});
}
traces.sort((a, b) => (b.day || "").localeCompare(a.day || "") || (b.at || "").localeCompare(a.at || ""));

// ------------------------------------------------------------------ at work: what each desk is doing, with a small chart
// For the city's inside view. Every line and number comes from the data files; a desk whose data is missing gets nothing.
const WORK = {};
{
  const put = (id, act, viz) => { WORK[id] = {act: (act || []).filter(Boolean).map(s => String(s).slice(0, 110)).slice(0, 5), viz: viz || null}; };
  const tryIt = (id, fn) => { try { fn(); } catch (e) { if (!WORK[id]) WORK[id] = {act: [], viz: null}; } };
  const pj = dataJ("players.json", {}), P = Object.values(pj.players || {}), G = pj.games || [], day = pj.date || TODAY;
  const sims = dataJ("sims.json", {}), SG = sims.date === day ? sims.games || {} : {};
  const dkd = dataJ(`dk/${day}.json`, {props: {}, games: {}}), DKP = Object.values(dkd.props || {});
  const yday = new Intl.DateTimeFormat("en-CA", {timeZone: "America/New_York"}).format(new Date(new Date(day + "T12:00:00Z") - 864e5));
  const dky = dataJ(`dk/${yday}.json`, {props: {}, games: {}});
  const BD = (best.days || {})[day] || {props: [], games: []}, BY = (best.days || {})[yday] || {props: [], games: []};
  const tm = dataJ("team_model.json", {}), sb = dataJ("scoreboard.json", {}), kt = dataJ("kalshi_track.json", {}), bud = dataJ("budget.json", {});
  const last = n => (n || "").split(" ").slice(1).join(" ") || n;
  const gn = g => `${g.away} @ ${g.home}`, pc = v => Math.round(v * 100) + "%", sg = v => (v > 0 ? "+" : "") + v;
  const agoS = iso => { const h = ageH(iso); return h == null ? "" : h < 1 ? Math.max(1, Math.round(h * 60)) + "m ago" : h < 36 ? Math.round(h) + "h ago" : Math.round(h / 24) + "d ago"; };
  const etH = iso => { const p = new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23"}).formatToParts(new Date(iso)); return +p.find(x => x.type === "hour").value + +p.find(x => x.type === "minute").value / 60; };
  const rot = P.filter(p => (p.minutes || 0) >= 20 && !p.out);
  const journal = k => (state.journal || []).filter(j => j.kind === k).slice(-1)[0];
  const binned = (h, step) => { const ks = Object.keys(h).map(Number), lo = Math.floor(Math.min(...ks) / step) * step, hi = Math.max(...ks), n = Math.floor((hi - lo) / step) + 1, b = Array(n).fill(0);
    for (const [k, c] of Object.entries(h)) b[Math.floor((+k - lo) / step)] += c; return {bins: b, lo, hi, step}; };
  const pre = G.filter(g => g.state === "pre"), showG = (pre[0] || G[0]);

  // ---- The Wire
  tryIt("boxscore", () => { const fin = Object.values(dky.games || {}).filter(g => g.final);
    put("boxscore", [`Filed ${fin.length} final box scores from last night`, `${P.length} players on file for tonight's ${G.length} games`, fin[0] && `${fin[0].away} ${fin[0].final[1]}, ${fin[0].home} ${fin[0].final[0]}`],
      {type: "rows", title: "Last night's finals", rows: fin.slice(0, 5).map(g => [`${g.away} @ ${g.home}`, `${g.final[1]}–${g.final[0]}`])}); });
  tryIt("film", () => { const top = rot.filter(p => p.proj && p.proj.reb).sort((a, b) => b.proj.reb[0] - a.proj.reb[0]).slice(0, 5);
    put("film", [`Usage and rebound share read for ${rot.length} rotation players`, top[0] && `${top[0].name}: most rebounds expected tonight (${top[0].proj.reb[0]})`],
      {type: "bars", title: "Rebounds expected tonight", fmt: "num", items: top.map(p => ({l: last(p.name), v: p.proj.reb[0]}))}); });
  tryIt("doctor", () => { const tag = P.filter(p => p.status && (p.minutes || 0) >= 15).sort((a, b) => b.minutes - a.minutes), out = tag.filter(p => p.status === "Out").length;
    put("doctor", [`${out} rotation players out, ${tag.length - out} with a question mark`, pj.injury_report && `Official report read ${agoS(pj.injury_report)}`, ...tag.filter(p => p.status !== "Out").slice(0, 2).map(p => `${p.name} (${p.team}): ${p.status.toLowerCase()}`)],
      {type: "checks", title: "Injury report", rows: tag.slice(0, 5).map(p => [`${p.name} · ${p.team}`, p.status === "Out" ? "fail" : "warn", p.status])}); });
  tryIt("lineup", () => { const lu = dataJ("lineups.json", {games: []}).games || [], sides = lu.flatMap(g => [g.visit, g.home]).filter(Boolean), conf = sides.filter(s => /confirmed/i.test(s.status || "")).length;
    put("lineup", [`${conf} of ${sides.length} starting lineups confirmed`, `Starters collected for ${lu.length} games`],
      {type: "checks", title: "Starting lineups", rows: lu.slice(0, 5).map(g => [`${g.visit.abbr} @ ${g.home.abbr}`, /confirmed/i.test(g.visit.status || "") && /confirmed/i.test(g.home.status || "") ? "ok" : "warn", /confirmed/i.test(g.visit.status || "") && /confirmed/i.test(g.home.status || "") ? "confirmed" : "projected"])}); });
  tryIt("refs", () => put("refs", [`Crews logged for ${G.length} games tonight`, "Assignments post about 9am ET on game days"],
    {type: "rows", title: "Tonight's tips", rows: G.slice(0, 5).map(g => [gn(g), etTime(g.start).split(", ").pop()])}));
  tryIt("travel", () => { const t = G.flatMap(g => ["home", "away"].map(s => ({team: g[s], ...(((g.vegas || {}).spots || {})[s] || {})}))).filter(x => x.miles7 != null).sort((a, b) => b.miles7 - a.miles7);
    put("travel", [...t.filter(x => x.b2b).slice(0, 2).map(x => `${x.team}: second night of a back-to-back`), t[0] && `${t[0].team} has flown ${Number(t[0].miles7).toLocaleString("en-US")} miles in 7 days`],
      {type: "bars", title: "Miles flown, last 7 days", fmt: "num", items: t.slice(0, 5).map(x => ({l: x.team, v: x.miles7}))}); });
  tryIt("probe", () => put("probe", ["Tests every outside feed from GitHub's servers when asked"], {type: "rows", title: "Feeds it tests", rows: (cast.sources || []).slice(0, 5).map(s => [s.name, "portal"])}));

  // ---- The Exchange
  tryIt("dk", () => { const mv = DKP.filter(r => r.o && r.c && r.o[0] !== r.c[0]), big = mv.sort((a, b) => Math.abs(b.c[0] - b.o[0]) - Math.abs(a.c[0] - a.o[0]))[0];
    const hrs = {}; for (const r of DKP) if (r.o_at) { const h = Math.floor(etH(r.o_at)); hrs[h] = (hrs[h] || 0) + 1; }
    put("dk", [`${DKP.length} prop lines collected today, ${mv.length} moved since they opened`, big && `${big.name} ${STAT[big.stat] || big.stat}: ${big.o[0]} → ${big.c[0]}`, `${Object.keys(dkd.games || {}).length} games with spread, total and moneyline`],
      {type: "bars", title: "Lines first seen, by hour (ET)", fmt: "num", items: Object.entries(hrs).sort((a, b) => a[0] - b[0]).slice(-7).map(([h, n]) => ({l: (h % 12 || 12) + (h < 12 ? "a" : "p"), v: n}))}); });
  tryIt("kalshi", () => { const a = (kt.props || {}).all || {}, d = a.disagree || {};
    put("kalshi", [d.n && `When the two markets split by 4+ points, Kalshi was right ${d.kalshi_right} of ${d.n}`, a.n && `${a.n.toLocaleString("en-US")} props graded: Kalshi ${a.ll_kalshi} vs DraftKings ${a.ll_dk} log loss`],
      d.n ? {type: "gauge", title: "Kalshi right when they split", v: d.kalshi_right / d.n, label: `${d.kalshi_right} of ${d.n}`} : null); });
  tryIt("buyer", () => put("buyer", [`${bud.remaining} credits left with ${bud.days_left} days to go`, `Allowance: ${bud.day_budget} a day; the free feeds do the rest`],
    {type: "gauge", title: "Odds API credits left", v: bud.remaining / (bud.monthly || 500), label: `${bud.remaining} of ${bud.monthly || 500}`}));
  tryIt("fair", () => put("fair", [`Margin removed on ${(ranked.props || []).length} lines so every chance adds to 100%`, BD.props[0] && `${BD.props[0].name}: DraftKings' fair chance ${pc(BD.props[0].p_mkt)}`],
    {type: "bars", title: "Fair chance, tonight's picks", fmt: "pct", max: 1, items: BD.props.slice(0, 5).map(x => ({l: last(x.name), v: x.p_mkt}))}));
  tryIt("historian", () => { const up = DKP.filter(r => r.o && r.c && r.c[0] > r.o[0]).length, dn = DKP.filter(r => r.o && r.c && r.c[0] < r.o[0]).length, same = DKP.filter(r => r.o && r.c).length - up - dn;
    put("historian", [`${Object.keys(dataJ("closes.json", {})).length} closing lines on file`, `Today: ${up} lines up, ${dn} down, ${same} unchanged since opening`],
      {type: "bars", title: "Line moves since opening", fmt: "num", items: [{l: "Up", v: up}, {l: "Down", v: dn}, {l: "Same", v: same}]}); });

  // ---- The Lab
  tryIt("research", () => { const j = journal("research");
    put("research", [j && j.text, pj.updated && `Dossiers rebuilt ${agoS(pj.updated)}`],
      {type: "rows", title: "Dossiers by game", rows: G.slice(0, 5).map(g => [gn(g), P.filter(p => p.game === g.espn_id).length + " players"])}); });
  tryIt("matchup", () => { const rows = []; for (const p of rot) for (const [s, v] of Object.entries(p.proj || {})) if (["pts", "reb", "ast"].includes(s) && v && v[2]) rows.push({p, s, m: v[2] - 1});
    rows.sort((a, b) => Math.abs(b.m) - Math.abs(a.m)); const top = []; const seen = new Set(); for (const r of rows) { if (seen.has(r.p.pid)) continue; seen.add(r.p.pid); top.push(r); if (top.length === 5) break; }
    put("matchup", top.slice(0, 3).map(r => `${r.p.name} vs ${r.p.opp}: ${sg(Math.round(r.m * 100))}% ${STAT[r.s]}`), {type: "bars", title: "Biggest matchup effects tonight", fmt: "+pct", div: true, items: top.map(r => ({l: `${last(r.p.name)} ${r.s}`, v: r.m}))}); });
  tryIt("schedule", () => { const rows = []; for (const p of rot) for (const [s, v] of Object.entries(p.sos || {})) if (["pts", "reb", "ast"].includes(s) && v && v[0]) rows.push({p, s, m: v[0] - 1});
    rows.sort((a, b) => Math.abs(b.m) - Math.abs(a.m)); const st = dataJ("sos_track.json", {days: {}});
    put("schedule", [`On probation: ${Object.keys(st.days || {}).length} days tracked, shown but not used`, rows[0] && `${rows[0].p.name}: schedule so far worth ${sg(Math.round(rows[0].m * 100))}% ${STAT[rows[0].s]}`],
      {type: "bars", title: "Schedule strength so far", fmt: "+pct", div: true, items: rows.slice(0, 5).map(r => ({l: `${last(r.p.name)} ${r.s}`, v: r.m}))}); });
  tryIt("team", () => { const T = Object.values(tm.tonight || {});
    put("team", T.slice(0, 3).map(t => `${t.margin >= 0 ? t.home : t.away} by ${Math.abs(t.margin).toFixed(1)} over ${t.margin >= 0 ? t.away : t.home}, total ${t.total}`),
      {type: "bars", title: "Home margin, team model", fmt: "+num", div: true, items: T.slice(0, 6).map(t => ({l: `${t.away}@${t.home}`, v: t.margin}))}); });
  tryIt("roster", () => { const outs = Object.values(tm.tonight || {}).flatMap(t => t.out || []).sort((a, b) => Math.abs(b.pts) - Math.abs(a.pts));
    put("roster", [`${outs.length} absences priced into tonight's ratings`, outs[0] && `${outs[0].team} without ${outs[0].names}: ${sg(outs[0].pts)} points`],
      {type: "bars", title: "Points lost to absences", fmt: "+num", div: true, items: outs.slice(0, 5).map(o => ({l: o.team, v: o.pts}))}); });
  tryIt("conviction", () => { const c = sb.conviction || {};
    put("conviction", [c.n && `Trained on ${c.n.toLocaleString("en-US")} graded leans over ${c.days} days`, BD.props[0] && `${BD.props[0].name}: conviction ${BD.props[0].conv} of 99`],
      {type: "bars", title: "Conviction, tonight's picks", fmt: "num", max: 99, items: BD.props.slice(0, 5).map(x => ({l: last(x.name), v: x.conv}))}); });
  tryIt("understudy", () => { const u = conv.latest || {}, ll = u.ll || {};
    put("understudy", [`Audition: ${conv.streak || 0} of 3 nights passed`, u.n && `Tested on ${u.n.toLocaleString("en-US")} props over ${u.days} days`, "Takes the job only after three passes and the chairman's approval"],
      {type: "rows", title: "Log loss (lower is better)", rows: [["Market alone", ll.market], ["Conviction Officer", ll.current], ["Understudy", ll.line]].filter(r => r[1] != null)}); });

  // ---- Simulation Floor
  const simG = showG && SG[showG.espn_id];
  tryIt("foreman", () => { const acts = G.filter(g => SG[g.espn_id]).slice(0, 4).map(g => { const s = SG[g.espn_id]; return `${gn(g)}: ${s.avg.away}–${s.avg.home}, ${g.home} wins ${pc(s.win_home)} of ${(sims.n || 10000).toLocaleString("en-US")} worlds`; });
    const b = simG && binned(simG.th, 4);
    put("foreman", [`${Object.keys(SG).length} games simulated ${agoS(sims.updated)}`, ...acts],
      b ? {type: "hist", title: `${gn(showG)} total points, ${(sims.n || 10000).toLocaleString("en-US")} worlds`, bins: b.bins, lo: b.lo, hi: b.hi, mark: simG.vegas && simG.vegas.total != null ? (simG.vegas.total - b.lo) / (b.hi - b.lo + b.step) : null, markLabel: simG.vegas && simG.vegas.total != null ? "DK " + simG.vegas.total : ""} : null); });
  tryIt("engine", () => { const b = simG && binned(simG.mh, 3);
    put("engine", [simG && `${gn(showG)}: pace ${simG.pace}, ${showG.home} by ${simG.margin} on average`, "Every possession, substitution, foul and blowout is played out"],
      b ? {type: "hist", title: `${showG.home} margin in every world`, bins: b.bins, lo: b.lo, hi: b.hi, mark: (0 - b.lo) / (b.hi - b.lo + b.step), markLabel: "tie"} : null); });
  tryIt("liveop", () => { const lg = Object.values(dkd.games || {}).filter(g => (g.live || []).length && !g.final).sort((a, b) => b.live.length - a.live.length)[0];
    if (!lg) return put("liveop", ["No games in progress: standing by for tip-off"], {type: "rows", title: "Next tips", rows: pre.slice(0, 4).map(g => [gn(g), etTime(g.start).split(", ").pop()])});
    const L = lg.live, z = L[L.length - 1];
    put("liveop", [`${lg.away} @ ${lg.home}, ${z.detail}: ${z.score[1]}–${z.score[0]}`, z.sim && `${lg.home} wins ${pc(z.sim.p_home_win)} of ${z.sim.n.toLocaleString("en-US")} worlds from here`],
      {type: "line", title: `${lg.home} win chance, live`, pts: L.filter(x => x.sim).map(x => x.sim.p_home_win), lo: 0, hi: 1, fmt: "pct"}); });
  tryIt("simgrader", () => { const a = dataJ("sims_log.json", {}).agg || {};
    put("simgrader", [a.winner && `Called ${a.winner[1]} of ${a.winner[0]} winners`, a.total && `Totals missed by ${(a.total[1] / a.total[0]).toFixed(1)} points on average`, a.winner_vegas && `DraftKings' favourite won ${a.winner_vegas[1]} of ${a.winner_vegas[0]}`],
      a.winner ? {type: "gauge", title: "Winners called right", v: a.winner[1] / a.winner[0], label: `${a.winner[1]} of ${a.winner[0]}`} : null); });

  // ---- Our Book
  tryIt("setter", () => { const ob = dataJ(`ourbook/${day}.json`, {freezes: []}), fr = (ob.freezes || []).map(f => f.id), its = BD.props.filter(x => x.ob_ind != null).slice(0, 5);
    put("setter", [fr.length ? `Lines sealed today at ${[...new Set(fr)].join(", ")}` : "First seal of the day is 9am ET", its[0] && `${its[0].name}: we make it ${its[0].ob_ind}, DraftKings ${its[0].point}`],
      {type: "bars", title: "Our line minus DraftKings'", fmt: "+num", div: true, items: its.map(x => ({l: `${last(x.name)} ${x.stat}`, v: Math.round((x.ob_ind - x.point) * 10) / 10}))}); });
  tryIt("gatekeeper", () => { const m = (obt.moves || {}).all || {}, c = (obt.moves || {}).clean || {};
    put("gatekeeper", [m.n && `${pc(m.rate)} of ${m.n.toLocaleString("en-US")} line moves went toward our sealed line`, c.n && `Without news, ${pc(c.rate)} of ${c.n} did`],
      m.n ? {type: "gauge", title: "Moves toward our line", v: m.rate, label: pc(m.rate)} : null); });

  // ---- The Newsroom
  tryIt("editor", () => { const R = (ranked.props || []).filter(x => x.rank).slice(0, 5);
    put("editor", [`${(ranked.props || []).length} lines ranked, one side each`, R[0] && `#1 tonight: ${R[0].name} ${R[0].side.toLowerCase()} ${R[0].point} ${STAT[R[0].stat]}`],
      {type: "rows", title: "Top of the ranked board", rows: R.map(x => [`#${x.rank} ${last(x.name)} ${x.side[0]}${x.point} ${x.stat}`, x.conv])}); });
  tryIt("casebuilder", () => { const x = BD.props[0]; if (!x) return put("casebuilder", ["No case to build until tonight's lines are priced"]);
    put("casebuilder", [x.verdict, `${x.case.for.length} reasons for, ${x.case.against.length} against on ${x.name}`],
      {type: "checks", title: `The case: ${last(x.name)} ${x.side.toLowerCase()} ${x.point}`, rows: [...x.case.for.slice(0, 3).map(r => [r.tag, "ok", "for"]), ...x.case.against.slice(0, 2).map(r => [r.tag, "fail", "against"])]}); });
  tryIt("picker", () => { const r = (best.record || {}).props || {};
    put("picker", [`${BD.props.length} props and ${BD.games.length} leans chosen for ${day}`, r.w != null && `Props record ${r.w}-${r.l} (${sg(r.u)}u)`, "Props are sticky: a pick leaves only on a kill condition"],
      {type: "rows", title: "Tonight's picks", rows: BD.props.slice(0, 5).map(x => [`${last(x.name)} ${x.side[0]}${x.point} ${x.stat}`, x.tier || ""])}); });
  tryIt("writer", () => { const bf = dataJ(`briefing/${TODAY}.json`, null), N = bf && (bf.narratives || []).slice(-1)[0];
    put("writer", [N ? N.headline : bf ? "Morning edition is out; the written edition follows" : "Morning edition publishes at 7am ET", bf && `${Object.keys(bf.picks).length} props, ${Object.keys(bf.leans).length} leans, ${bf.changes.length} changes logged today`],
      bf && bf.changes.length ? {type: "rows", title: "What changed today", rows: bf.changes.slice(-4).reverse().map(c => [c.text.slice(0, 46), ""])} : {type: "rows", title: "Editions (ET)", rows: [["Morning facts", "7:00am"], ["Morning, written", "7:22am"], ["Afternoon", "2:41pm"], ["Final", "6:33pm"]]}); });

  // ---- The Trading Desk
  tryIt("trader", () => { const s = state.stats || {}, j = journal("strategist") || journal("daily");
    put("trader", [`Agent bankroll $${(s.equity || 0).toFixed(2)} after ${s.bets} bets`, j && j.text, (state.paused || []).length ? `Benched: ${state.paused.join(", ")}` : null],
      {type: "bars", title: "Profit by playbook ($)", fmt: "+num", div: true, items: Object.entries(crew).map(([k, c]) => ({l: k.replace("matchup_", "m. ").replace("_", " "), v: c.profit}))}); });
  tryIt("props", () => { const bets = ((state.book || {}).bets || []).filter(b => b.legs[0].player).slice(-5).reverse();
    put("props", [bets[0] && `Latest: ${bets[0].legs[0].player} ${bets[0].legs[0].side.toLowerCase()} ${bets[0].legs[0].point} ${STAT[bets[0].legs[0].stat] || ""}, $${bets[0].stake.toFixed(2)}`, "Buys odds only for the spots that matter"],
      {type: "checks", title: "Latest prop bets", rows: bets.map(b => [`${last(b.legs[0].player)} ${b.legs[0].side[0]}${b.legs[0].point} ${b.legs[0].stat}`, b.status === "won" ? "ok" : b.status === "lost" ? "fail" : "warn", b.status])}); });
  tryIt("bookkeeper", () => { const h = dataJ("history.json", []), pts = h.map(x => x.a).filter(v => v != null), n = Math.max(1, Math.floor(pts.length / 40));
    put("bookkeeper", [`${((state.book || {}).bets || []).length} bets on the books, every dollar accounted for`, `Cash $${((state.book || {}).cash || 0).toFixed(2)}, paper money only`],
      {type: "line", title: "Agent bankroll ($)", pts: pts.filter((_, i) => i % n === 0 || i === pts.length - 1), fmt: "num"}); });
  tryIt("compliance", () => put("compliance", ["Every bet is checked against the bylaws before it is placed"],
    {type: "checks", title: "Bylaws in force", rows: [["Paper money only", "ok", "on"], ["$5 minimum bet", "ok", "on"], ["Ten approved books", "ok", "on"], ["Credit budget", "ok", "on"]]}));
  tryIt("messenger", () => { const a = dataJ("alerts.json", {sent: {}}), ks = Object.entries(a.sent || {}).sort((x, y) => y[1].localeCompare(x[1]));
    put("messenger", [`${ks.length} phone alerts sent in the last 14 days`, ks[0] && `Last alert ${agoS(ks[0][1])}`], {type: "gauge", title: "Briefing alerts used today", v: Math.min(1, ((dataJ(`briefing/${TODAY}.json`, {}) || {}).alerts || 0) / 6), label: `${(dataJ(`briefing/${TODAY}.json`, {}) || {}).alerts || 0} of 6`}); });

  // ---- Audit & Night School
  tryIt("settle", () => { const all = [...BY.props, ...BY.games].filter(x => x.res), w = all.filter(x => x.res === "win").length, l = all.filter(x => x.res === "loss").length;
    put("settle", [all.length ? `Last night's picks collapsed: ${w} won, ${l} lost` : "Waiting for last night's games to be graded", ...BY.props.filter(x => x.res).slice(0, 2).map(x => `${x.name} ${x.side.toLowerCase()} ${x.point}: ${x.actual} → ${x.res === "win" ? "won" : "lost"}`)],
      {type: "checks", title: "Last night, collapsed", rows: BY.props.filter(x => x.res).slice(0, 5).map(x => [`${last(x.name)} ${x.side[0]}${x.point} ${x.stat}`, x.res === "win" ? "ok" : "fail", x.res === "win" ? "won" : "lost"])}); });
  const reviews = ((state.book || {}).bets || []).filter(b => b.review).slice(-3).reverse();
  tryIt("coroner", () => put("coroner", reviews.map(b => b.review.headline), {type: "rows", title: "Latest post-mortems", rows: reviews.map(b => [(b.review.headline || "").slice(0, 44), ""])}));
  tryIt("sportswriter", () => { const w = reviews[0] && reviews[0].review.legs && reviews[0].review.legs[0].why || [];
    put("sportswriter", w.slice(0, 3), {type: "rows", title: "The story of the last bet", rows: w.slice(0, 4).map(t => [t.slice(0, 48), ""])}); });
  tryIt("audit_score", () => { const a = sb.all || {};
    put("audit_score", [a.n && `${a.n.toLocaleString("en-US")} lines scored against the closing price`, a.blend && `Model + market blend ${a.blend.ll.toFixed(4)} vs market alone ${a.market.ll.toFixed(4)}`],
      {type: "rows", title: "Log loss (lower is better)", rows: [["Model alone", a.model && a.model.ll.toFixed(4)], ["Market alone", a.market && a.market.ll.toFixed(4)], ["Blend", a.blend && a.blend.ll.toFixed(4)]].filter(r => r[1])}); });
  tryIt("teacher", () => { const le = BD.learned || {}, w = le.w_props || {}, edge = Object.entries(le.focus || {}).filter(([, v]) => v === "edge").map(([k]) => STAT[k]);
    put("teacher", [edge.length ? `Where the model has an edge: ${edge.join(", ")}` : null, le.total_bias != null && `Totals corrected by ${sg(le.total_bias)} points`, "Reads the record every night and adjusts the weights"],
      {type: "bars", title: "How much the model's view counts", fmt: "pct", max: 1, items: Object.entries(w).map(([k, v]) => ({l: k, v}))}); });
  tryIt("inspector", () => { const c = checks.checks || [], bad = c.filter(x => x.status !== "ok");
    put("inspector", [`${c.length} checks run ${agoS(checks.updated)}: ${c.length - bad.length} pass`, ...bad.slice(0, 2).map(x => `${x.name}: ${x.detail}`)],
      {type: "checks", title: "Inspection", rows: [...bad, ...c.filter(x => x.status === "ok")].slice(0, 5).map(x => [x.name, x.status, x.status])}); });
  tryIt("range", () => { const t = fs.readdirSync(path.join(ROOT, "tests")).filter(f => f.endsWith(".py"));
    put("range", [`${t.length} backtests on the range`, "Nothing reaches a bet before it passes here"], {type: "rows", title: "Backtests", rows: t.slice(0, 5).map(f => [f.replace(".py", "").replace(/_/g, " "), ""])}); });

  // ---- Dispatch
  tryIt("dispatcher", () => { const sd = dataJ("sims_day.json", {runs: []}), runs = sd.date === day ? sd.runs || [] : [], z = runs[runs.length - 1];
    put("dispatcher", [`${runs.length} simulation runs today`, z && `Last run ${agoS(z.ts)}: ${z.why}`], {type: "ticks", title: "Runs today (ET)", at: runs.map(r => etH(r.ts))}); });
  tryIt("clock", () => put("clock", [`${org.schedule.length} schedules on GitHub plus the 15-minute outside timer`], {type: "clock", title: "Next chimes"}));
  tryIt("archivist", () => { const ds = Object.entries(liveDocs).filter(([, v]) => v.kb != null).sort((a, b) => b[1].kb - a[1].kb), tot = ds.reduce((t, [, v]) => t + v.kb, 0);
    put("archivist", [`${ds.length} documents on file, ${(tot / 1024).toFixed(1)} MB`, "Every save is written whole, then swapped in"], {type: "bars", title: "Largest documents (KB)", fmt: "num", items: ds.slice(0, 5).map(([k, v]) => ({l: (DOCS[k] || {}).name || k, v: v.kb}))}); });
  tryIt("mailroom", () => { const mi = dataJ("manual_import.json", {items: []}).items || [], sv = dataJ("saved.json", {items: []}).items || [];
    put("mailroom", [`${mi.length} of the chairman's sportsbook slips logged`, "Opens requests the moment an issue is filed"], {type: "rows", title: "On file", rows: [["Sportsbook slips", mi.length], ["Saved props", sv.length], ["Switch requests", conv.switched ? 1 : 0]]}); });
  tryIt("planner", () => put("planner", [`Drew ${desks.length} desks, ${docs.length} documents and ${org.routes.length} routes from the code`, newHires.length ? `New hires waiting for a desk: ${newHires.length}` : "Every module has a desk"],
    {type: "rows", title: "The map", rows: [["Lines of code", totalLines.toLocaleString("en-US")], ["Desks", desks.length], ["Documents", docs.length], ["Routes", org.routes.length]]}));

  // ---- The Storefront
  tryIt("frontdesk", () => put("frontdesk", ["Open now: you are looking at it", `Tonight: ${G.length} games, ${BD.props.length} picks`], {type: "rows", title: "Dashboard tabs", rows: [["Tonight", "briefing, best bets"], ["Games", "live pages"], ["Players", "prop builder"], ["My bets", "your book"], ["Model", "health, records"]]}));
  tryIt("gameshost", () => put("gameshost", [`${G.filter(g => g.state === "in").length} live, ${pre.length} to tip, ${G.filter(g => g.state === "post").length} final`],
    {type: "checks", title: "Tonight's games", rows: G.slice(0, 5).map(g => [gn(g), g.state === "in" ? "fail" : g.state === "post" ? "ok" : "warn", g.state === "in" ? "live" : g.state === "post" ? "final" : "pre"])}));
  tryIt("clerk", () => { const mi = (dataJ("manual_import.json", {items: []}).items || []).slice(-5).reverse(), w = mi.filter(x => x.result === "won").length;
    put("clerk", [`The chairman's last ${mi.length} slips: ${w} won`], {type: "checks", title: "The chairman's slips", rows: mi.map(x => [`${x.leg.player ? last(x.leg.player) + " " + x.leg.side[0] + x.leg.point + " " + x.leg.stat : x.leg.outcome.split(" ").pop() + " " + x.leg.point}`, x.result === "won" ? "ok" : "fail", x.result])}); });
  tryIt("observatory", () => put("observatory", ["Showing the city, tracing bets, reporting coherence", `${traces.length} bets ready to trace`], {type: "rows", title: "Views", rows: [["Live", "now"], ["Inside", "tap a building"], ["Trace a bet", traces.length], ["Coherence", "health"]]}));
  for (const d of desks) if (deskLive[d.id]) deskLive[d.id].work = WORK[d.id] || null;
}

fs.mkdirSync(CITY, {recursive: true});
fs.writeFileSync(path.join(CITY, "org.json"), JSON.stringify(org));
fs.writeFileSync(path.join(DATA, "city_live.json"), JSON.stringify(live));
fs.writeFileSync(path.join(DATA, "city_trace.json"), JSON.stringify({updated: nowIso, traces}));
// the run log (agent/citynow.py): the City Planner signs off as the last desk of the run it just mapped
try { const np = path.join(DATA, "city_now.json"), now = JSON.parse(fs.readFileSync(np, "utf8"));
  if (now && Array.isArray(now.steps) && now.finished && !now.steps.some(s => s.desk === "planner")) {
    now.steps.push({desk: "planner", text: `Redrew the city map: ${desks.length} desks, ${traces.length} bets traced`, at: now.secs != null ? now.secs : (now.steps[now.steps.length - 1] || {at: 0}).at});
    fs.writeFileSync(np, JSON.stringify(now)); } } catch (e) { /* no run log yet */ }
console.log(`[city] ${companies.length} companies, ${desks.length} desks, ${docs.length} documents, ${org.routes.length} routes, ${traces.length} traces` +
  (newHires.length ? `; new hires without a desk: ${newHires.join(", ")}` : "") + (stale.length ? `; stale: ${stale.map(s => s.id).join(", ")}` : ""));
