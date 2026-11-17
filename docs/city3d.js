// Perseverance City in 3D (Nov 17, 2026): the same night city as the 2D map, built with three.js so it can be turned,
// tilted and zoomed. Same data (docs/city/org.json + docs/data/city_live.json), same layout, same colours:
// one tower per company, one floor per desk with its worker behind the glass, document routes arcing between towers,
// outside portals in the sky and the Chairman's Spire in the middle. city.js loads this file (and three.js) only when
// the 3D switch is used, and falls back to the 2D map when WebGL is not available.
//   City3D.supported()            can this device draw it
//   City3D.create(host, api)      -> {update, setView, pulse, setBusy, resume, pause, resize}
(() => {
"use strict";
const supported = () => { try { const c = document.createElement("canvas"); return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl"))); } catch (e) { return false; } };

function create(host, api) {
  const T = window.THREE, {ORG, CO, DK} = api;
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new T.WebGLRenderer({antialias: true, alpha: true, powerPreference: "low-power"});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  const cv = renderer.domElement; cv.className = "c3d"; cv.setAttribute("role", "img"); cv.setAttribute("aria-label", "Perseverance City in 3D");
  host.appendChild(cv);
  const scene = new T.Scene(); scene.fog = new T.FogExp2(0x050810, 0.0058);
  const cam = new T.PerspectiveCamera(36, 1, 0.5, 600);
  scene.add(new T.AmbientLight(0xffffff, 0.9));
  const sun = new T.DirectionalLight(0x9fdcff, 0.55); sun.position.set(-30, 60, 40); scene.add(sun);

  // ---------------------------------------------------------------- small helpers
  const col = c => new T.Color(c);
  const texCache = {};
  function canvasTex(key, w, h, draw) {
    if (texCache[key]) return texCache[key];
    const c = document.createElement("canvas"); c.width = w; c.height = h; draw(c.getContext("2d"), w, h);
    const t = new T.CanvasTexture(c); t.minFilter = T.LinearFilter; t.generateMipmaps = false; t.anisotropy = 4;
    return texCache[key] = t;
  }
  const glowTex = canvasTex("glow", 64, 64, (g, w) => { const r = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
    r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(.25, "rgba(255,255,255,.55)"); r.addColorStop(1, "rgba(255,255,255,0)"); g.fillStyle = r; g.fillRect(0, 0, w, w); });
  const glow = (c, size, op = 1) => { const s = new T.Sprite(new T.SpriteMaterial({map: glowTex, color: col(c), transparent: true, opacity: op, blending: T.AdditiveBlending, depthWrite: false})); s.scale.set(size, size, 1); return s; };
  function label(lines, opts = {}) {
    const key = "lbl|" + lines.map(l => l[0]).join("|") + (opts.small ? "s" : "");
    const tex = canvasTex(key, 512, 160, (g, w, h) => {
      g.textAlign = "center"; g.lineJoin = "round";
      lines.forEach(([txt, c, bold], i) => { let size = i === 0 ? (opts.small ? 44 : 56) : 30; const t = txt.toUpperCase();
        const F = z => `${bold === false ? 500 : 700} ${z}px "Space Grotesk", Inter, system-ui, sans-serif`; g.font = F(size);
        while (g.measureText(t).width > w - 20 && size > 22) { size -= 2; g.font = F(size); }
        const y = i === 0 ? 70 : 118; g.lineWidth = 9; g.strokeStyle = "rgba(4,6,12,.92)"; g.strokeText(t, w / 2, y); g.fillStyle = c; g.fillText(t, w / 2, y); });
    });
    const s = new T.Sprite(new T.SpriteMaterial({map: tex, transparent: true, depthTest: false, depthWrite: false}));
    const W = opts.width || 15; s.scale.set(W, W * 160 / 512, 1); s.renderOrder = 20; return s;
  }
  // one floor's glass: tint by the desk's state, the worker at the desk with a lit monitor
  function windowTex(state, color) {
    return canvasTex("win|" + state + "|" + color, 192, 64, (g, w, h) => {
      const [fill, fo] = api.winStyle(state, color);
      g.fillStyle = "#070B16"; g.fillRect(0, 0, w, h);
      g.globalAlpha = Math.min(1, fo * (state === "hot" ? 1.5 : 1.25)); g.fillStyle = fill; g.fillRect(0, 0, w, h); g.globalAlpha = 1;
      const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, "rgba(255,255,255,.10)"); grad.addColorStop(1, "rgba(0,0,0,.25)"); g.fillStyle = grad; g.fillRect(0, 0, w, h);
      g.strokeStyle = color; g.globalAlpha = state === "hot" ? .95 : .5; g.lineWidth = 3; if (state === "vacant") g.setLineDash([8, 7]); g.strokeRect(1.5, 1.5, w - 3, h - 3); g.setLineDash([]); g.globalAlpha = 1;
      if (state === "vacant") return;
      const wc = state === "hot" ? "#ffffff" : state === "probation" ? "#FBBF24" : state === "understudy" ? "#A78BFA" : state === "lit" ? color : "#4A5670";
      for (const x0 of [48, 132]) {                         // two workers' worth of desk along the band reads well from any side
        g.fillStyle = wc; g.beginPath(); g.arc(x0, 24, 8, 0, 7); g.fill();
        g.beginPath(); g.moveTo(x0 - 12, 54); g.quadraticCurveTo(x0 - 12, 33, x0, 33); g.quadraticCurveTo(x0 + 12, 33, x0 + 12, 54); g.closePath(); g.fill();
        g.fillStyle = state === "hot" || state === "lit" ? "#E8FBFF" : "#2B3754"; g.fillRect(x0 + 18, 30, 15, 12);
        g.fillStyle = color; g.globalAlpha = .75; g.fillRect(x0 - 22, 54, 62, 3); g.globalAlpha = 1;
      }
    });
  }

  // ---------------------------------------------------------------- layout (the same lanes as the 2D map)
  const pos = c => ({x: c.at[0] * (c.at[1] % 2 ? 10.5 : 18), z: (c.at[1] - 2) * 13.5});
  const hgt = c => Math.max(42, Math.min(104, 34 + Math.sqrt(c.lines || 100) * 1.08)) / 6;
  const FOOT = 7.2;
  const G = {};                                             // company id -> {x, z, h, group, floors: {deskId: [meshes]}}
  let root = null, pick = [], hotMats = [], routes = [], qubits = [], spin = [], blink = [], halos = {}, fx = [], view = "live";
  let spireCore = null, spireRings = null, busy = false;

  function build(entrance) {
    if (root) { scene.remove(root); root.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material && !o.material.map) o.material.dispose(); }); }
    root = new T.Group(); scene.add(root); pick = []; hotMats = []; routes = []; qubits = []; spin = []; blink = []; halos = {}; fx = [];
    const wide = host.clientWidth >= 600;                   // phones get tower names only: subtitles collide at that size
    // ground
    const grid = new T.GridHelper(260, 104, 0x7FE3FF, 0x7FE3FF); grid.material.transparent = true; grid.material.opacity = .07; grid.material.depthWrite = false; root.add(grid);
    const floorM = new T.Mesh(new T.CircleGeometry(150, 48), new T.MeshBasicMaterial({color: 0x04060C, transparent: true, opacity: .55, depthWrite: false})); floorM.rotation.x = -Math.PI / 2; floorM.position.y = -.05; root.add(floorM);
    const core = glow("#7FE3FF", 70, .22); core.position.set(0, .4, 0); root.add(core);
    // stars
    { const n = 420, p = new Float32Array(n * 3); let s = 7; const R = () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
      for (let i = 0; i < n; i++) { const a = R() * Math.PI * 2, e = .12 + R() * 1.25, r = 260; p[i * 3] = Math.cos(a) * Math.cos(e) * r; p[i * 3 + 1] = Math.sin(e) * r * .8 + 10; p[i * 3 + 2] = Math.sin(a) * Math.cos(e) * r; }
      const g = new T.BufferGeometry(); g.setAttribute("position", new T.BufferAttribute(p, 3));
      root.add(new T.Points(g, new T.PointsMaterial({color: 0xffffff, size: 1.5, sizeAttenuation: false, transparent: true, opacity: .7, fog: false, depthWrite: false}))); }

    // towers
    ORG.companies.forEach((c, ci) => {
      const {x, z} = pos(c), h = hgt(c), grp = new T.Group(); grp.position.set(x, 0, z); root.add(grp);
      const g = G[c.id] = {x, z, h, group: grp, floors: {}, i: c.at[1] * 2 + (c.at[0] > 0 ? 1 : 0)};
      const body = new T.Mesh(new T.BoxGeometry(FOOT, h, FOOT), new T.MeshLambertMaterial({color: 0x0C1324})); body.position.y = h / 2; body.userData = {co: c.id}; grp.add(body); pick.push(body);
      const edges = new T.LineSegments(new T.EdgesGeometry(new T.BoxGeometry(FOOT, h, FOOT)), new T.LineBasicMaterial({color: col(c.color), transparent: true, opacity: .7})); edges.position.y = h / 2; grp.add(edges);
      const roof = new T.Mesh(new T.PlaneGeometry(FOOT, FOOT), new T.MeshBasicMaterial({color: col(c.color), transparent: true, opacity: .3, blending: T.AdditiveBlending, depthWrite: false})); roof.rotation.x = -Math.PI / 2; roof.position.y = h + .03; grp.add(roof);
      const pool = glow(c.color, 30, .3); pool.position.set(0, .3, 0); grp.add(pool);
      const mast = new T.Mesh(new T.CylinderGeometry(.06, .06, 2.4, 6), new T.MeshBasicMaterial({color: col(c.color)})); mast.position.y = h + 1.2; grp.add(mast);
      const bea = glow(c.color, 2.6, 1); bea.position.y = h + 2.5; grp.add(bea); blink.push({s: bea, sp: 2 + (c.desks.length % 3), ph: ci});
      const halo = new T.Mesh(new T.RingGeometry(FOOT * .86, FOOT * .98, 48), new T.MeshBasicMaterial({color: 0x4ADE80, transparent: true, opacity: 0, side: T.DoubleSide, depthWrite: false})); halo.rotation.x = -Math.PI / 2; halo.position.y = .12; grp.add(halo); halos[c.id] = halo;
      // one floor per desk, the first desk at the top like the inside view
      const n = c.desks.length, fh = Math.min(2.5, (h - 1.4) / n);
      c.desks.forEach((did, i) => {
        const st = api.deskState(did), yy = h - .7 - (i + .5) * fh, ms = [];
        for (let f = 0; f < 4; f++) {
          const m = new T.Mesh(new T.PlaneGeometry(FOOT * .84, fh * .76), new T.MeshBasicMaterial({map: windowTex(st, c.color), transparent: true}));
          const a = f * Math.PI / 2; m.position.set(Math.sin(a) * (FOOT / 2 + .03), yy, Math.cos(a) * (FOOT / 2 + .03)); m.rotation.y = a;
          m.userData = {co: c.id, desk: did, st, color: c.color}; grp.add(m); pick.push(m); ms.push(m);
          if (st === "hot" || st === "probation" || st === "understudy") hotMats.push({m: m.material, ph: ci * 1.7 + i, slow: st !== "hot"});
        }
        g.floors[did] = {ms, y: yy, fh};
      });
      const lb = label(wide ? [[c.name.replace(/^The /, ""), "#ffffff"], [c.subtitle, "#B9C3D6", false]] : [[c.name.replace(/^The /, ""), "#ffffff"]]); lb.position.y = h + 5.4; grp.add(lb); g.label = lb;
    });

    // the Chairman's Spire
    { const sp = new T.Group(); root.add(sp);
      const shaft = new T.Mesh(new T.CylinderGeometry(.5, 1.7, 25, 4), new T.MeshLambertMaterial({color: 0x4FA9C9, emissive: 0x16425a})); shaft.position.y = 12.5; shaft.rotation.y = Math.PI / 4; shaft.userData = {spire: 1}; sp.add(shaft); pick.push(shaft);
      const tip = new T.Mesh(new T.ConeGeometry(.9, 6, 4), new T.MeshBasicMaterial({color: 0xE8FBFF})); tip.position.y = 28; tip.rotation.y = Math.PI / 4; sp.add(tip);
      spireRings = new T.Group(); spireRings.position.y = 16; sp.add(spireRings);
      [["#7FE3FF", 0, 7], ["#A78BFA", 1.05, 9], ["#E5C158", 2.1, 11]].forEach(([c, tilt, dur], i) => {
        const hold = new T.Group(); hold.rotation.set(Math.PI / 2 - .35, 0, tilt); spireRings.add(hold);
        const ring = new T.Mesh(new T.TorusGeometry(6.2, .07, 6, 72), new T.MeshBasicMaterial({color: col(c), transparent: true, opacity: .9})); hold.add(ring);
        const arm = new T.Group(); hold.add(arm); const e = glow(c, 1.8, 1); e.position.x = 6.2; arm.add(e); spin.push({o: arm, axis: "z", sp: Math.PI * 4 / dur});
        const hit = new T.Mesh(new T.SphereGeometry(6.4, 8, 8), new T.MeshBasicMaterial({visible: false})); hit.userData = {spire: 1}; if (!i) { spireRings.add(hit); pick.push(hit); }
      });
      spireCore = glow("#ffffff", 5, 1); spireCore.position.y = 16; sp.add(spireCore);
      for (const [r, o] of [[9, .3], [14, .2], [19.5, .12]]) { const ring = new T.Mesh(new T.RingGeometry(r, r + .14, 96), new T.MeshBasicMaterial({color: 0x7FE3FF, transparent: true, opacity: o, side: T.DoubleSide, depthWrite: false})); ring.rotation.x = -Math.PI / 2; ring.position.y = .1; sp.add(ring); }
      const lb = label(wide ? [["Chairman's Spire", "#ffffff"], ["Bylaws · Clock · Bulletin", "#B9C3D6", false]] : [["Chairman's Spire", "#ffffff"]], {small: true, width: 13}); lb.position.y = 34.5; sp.add(lb);
      G.spire = {group: sp, i: 4.5};
    }

    // outside portals: an arc of rings in the sky behind the city, beams to the towers that call them
    const S = ORG.sources, PP = {};
    S.forEach((p, i) => {
      const t = i / (S.length - 1), a = Math.PI * (1.12 + t * .76), R = 46, grp = new T.Group();
      grp.position.set(Math.cos(a) * R, 25 + Math.sin(t * Math.PI) * 6, Math.sin(a) * R + 4); root.add(grp); PP[p.id] = grp.position;
      const ring = new T.Mesh(new T.TorusGeometry(1.5, .1, 6, 36), new T.MeshBasicMaterial({color: 0x7FE3FF})); grp.add(ring); spin.push({o: ring, axis: "y", sp: .5 + i * .07});
      grp.add(glow("#7FE3FF", 6, .5));
      const hit = new T.Mesh(new T.SphereGeometry(2.6, 8, 8), new T.MeshBasicMaterial({visible: false})); hit.userData = {portal: p.id}; grp.add(hit); pick.push(hit);
      const lb = label([[p.name.length > 14 ? p.name.split(" ")[0] : p.name, "#8FD8F0"]], {small: true, width: 9}); lb.position.y = 3; grp.add(lb);
    });
    { const seen = new Set(), pts = [];
      for (const bm of ORG.beams) { const k = bm.source + bm.company; if (seen.has(k) || !PP[bm.source] || !G[bm.company]) continue; seen.add(k);
        const a = PP[bm.source], g = G[bm.company]; pts.push(a.x, a.y, a.z, g.x, g.h + 2.5, g.z); }
      const geo = new T.BufferGeometry(); geo.setAttribute("position", new T.Float32BufferAttribute(pts, 3));
      const ln = new T.LineSegments(geo, new T.LineDashedMaterial({color: 0x7FE3FF, transparent: true, opacity: .2, dashSize: .7, gapSize: 1.3, depthWrite: false})); ln.computeLineDistances(); root.add(ln); G.beams = ln; }

    // document routes: arcs between towers, brighter and carrying qubits when the documents are fresh
    ORG.routes.forEach((r, i) => {
      const a = G[r.from], b = G[r.to]; if (!a || !b) return;
      const A = new T.Vector3(a.x, a.h * .5, a.z), B = new T.Vector3(b.x, b.h * .5, b.z), d = A.distanceTo(B);
      const mid = A.clone().add(B).multiplyScalar(.5); mid.x *= .62; mid.z *= .62; mid.y = Math.max(a.h, b.h) * .55 + 5 + d * .22 + (i % 5) * .9;
      const cv3 = new T.QuadraticBezierCurve3(A, mid, B), f = api.routeFresh(r), c = CO[r.from].color;
      const op = f != null && f < 6 ? .62 : f != null && f < 30 ? .32 : .13, rad = Math.min(.17, .05 + r.docs.length * .022);
      const tube = new T.Mesh(new T.TubeGeometry(cv3, 28, rad, 5, false), new T.MeshBasicMaterial({color: col(c), transparent: true, opacity: op, blending: T.AdditiveBlending, depthWrite: false}));
      root.add(tube); routes.push({tube, n: tube.geometry.index.count});
      if (f != null && f < 6) for (let k = 0; k < Math.min(2, r.docs.length); k++) { const q = glow(c, 1.5, 1); root.add(q); qubits.push({q, cv3, sp: 1 / (3.2 + (i * 7 % 10) * .3), ph: k / 2 + i * .13}); }
    });
    applyView();
    t0 = performance.now(); entering = !!entrance && !reduce;
    for (const k in G) if (G[k].group) G[k].group.scale.y = entering ? .001 : 1;
    if (entering) for (const r of routes) r.tube.geometry.setDrawRange(0, 0);
  }

  function applyView() {
    for (const c of ORG.companies) { const h = halos[c.id]; if (h) { h.material.opacity = view === "coh" ? .9 : 0; h.material.color.set(api.HC[api.coHealth(c.id)]); }
      for (const did of c.desks) { const fl = G[c.id].floors[did]; if (!fl) continue;
        for (const m of fl.ms) { if (view === "coh") { m.material.map = null; m.material.color.set(api.HC[api.deskHealth(did)]); m.material.opacity = .62; }
          else { m.material.map = windowTex(m.userData.st, m.userData.color); m.material.color.set(0xffffff); m.material.opacity = 1; } m.material.needsUpdate = true; } } }
  }

  // ---------------------------------------------------------------- camera: orbit by drag, pinch or buttons
  const tgt = new T.Vector3(0, 9, 0);
  let POL0 = 1.05, moved = false, az = .62, pol = 1.05, rad = 120, fit = 120, auto = !reduce, lastTouch = 0, t0 = 0, entering = false, zoomK = 1;
  function place() {
    const r = rad, sp = Math.sin(pol);
    cam.position.set(tgt.x + r * sp * Math.sin(az), tgt.y + r * Math.cos(pol), tgt.z + r * sp * Math.cos(az)); cam.lookAt(tgt);
  }
  function resize() {
    const w = host.clientWidth, h = host.clientHeight; if (!w || !h) return;
    renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
    const vf = cam.fov * Math.PI / 360, hf = Math.atan(Math.tan(vf) * cam.aspect);
    fit = Math.max(35 / Math.tan(hf), 31 / Math.tan(vf)) * 1.06; POL0 = cam.aspect < .8 ? .86 : 1.05; if (!moved) pol = POL0; rad = fit * zoomK; place(); draw();
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const ptr = new Map(); let down = null, pinch0 = 0;
  const touched = () => { lastTouch = performance.now(); auto = false; moved = true; };
  cv.addEventListener("pointerdown", e => { ptr.set(e.pointerId, [e.clientX, e.clientY]); if (ptr.size === 1) down = {x: e.clientX, y: e.clientY, t: performance.now(), moved: 0};
    if (ptr.size === 2) { const p = [...ptr.values()]; pinch0 = Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]); down = null; }
    try { cv.setPointerCapture(e.pointerId); } catch (x) { /* older browsers */ } });
  cv.addEventListener("pointermove", e => { const p = ptr.get(e.pointerId); if (!p) return; const dx = e.clientX - p[0], dy = e.clientY - p[1]; ptr.set(e.pointerId, [e.clientX, e.clientY]);
    if (ptr.size === 1) { if (down) down.moved += Math.abs(dx) + Math.abs(dy); if (down && down.moved < 6) return; touched();
      az -= dx * .0075; if (e.pointerType === "mouse") pol = clamp(pol - dy * .006, .3, 1.45); place(); }
    else if (ptr.size === 2) { const q = [...ptr.values()], d = Math.hypot(q[0][0] - q[1][0], q[0][1] - q[1][1]); if (pinch0) { touched(); zoomK = clamp(zoomK * pinch0 / d, .38, 1.5); rad = fit * zoomK; place(); } pinch0 = d; } });
  const up = e => { const had = ptr.delete(e.pointerId); if (had && down && e.type === "pointerup" && down.moved < 8 && performance.now() - down.t < 500) tap(e); if (!ptr.size) down = null; pinch0 = 0; };
  cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up); cv.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") up(e); });
  cv.addEventListener("wheel", e => { if (!e.ctrlKey && !e.metaKey) return; e.preventDefault(); touched(); zoomK = clamp(zoomK * (1 + e.deltaY * .01), .38, 1.5); rad = fit * zoomK; place(); }, {passive: false});
  const ray = new T.Raycaster(), v2 = new T.Vector2();
  function hitAt(cx, cy) { const r = cv.getBoundingClientRect(); v2.set((cx - r.left) / r.width * 2 - 1, -(cy - r.top) / r.height * 2 + 1); ray.setFromCamera(v2, cam); const h = ray.intersectObjects(pick, false)[0]; return h ? h.object.userData : null; }
  function tap(e) { const u = hitAt(e.clientX, e.clientY); if (u) api.onPick(u); }
  cv.addEventListener("pointermove", e => { if (e.pointerType === "mouse" && !ptr.size) cv.style.cursor = hitAt(e.clientX, e.clientY) ? "pointer" : "grab"; });
  // tween helper for the buttons
  let tw = null;
  function nudge(o) { touched(); const from = {az, pol, zoomK}, to = {az: o.az != null ? o.az : az, pol: o.pol != null ? o.pol : pol, zoomK: clamp(o.zoomK != null ? o.zoomK : zoomK, .38, 1.5)}; tw = {from, to, t: performance.now(), d: 420}; }

  // ---------------------------------------------------------------- effects: a desk that just worked, a run in progress
  function pulse(co, desk) {
    const g = G[co]; if (!g || !g.group) return; const c = CO[co].color, now = performance.now();
    const ring = new T.Mesh(new T.RingGeometry(.92, 1, 64), new T.MeshBasicMaterial({color: col(c), transparent: true, opacity: 1, side: T.DoubleSide, depthWrite: false, blending: T.AdditiveBlending})); ring.rotation.x = -Math.PI / 2; ring.position.set(g.x, .2, g.z); root.add(ring);
    const beam = new T.Mesh(new T.CylinderGeometry(.5, .5, 46, 12, 1, true), new T.MeshBasicMaterial({color: col(c), transparent: true, opacity: .5, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide})); beam.position.set(g.x, g.h + 23, g.z); root.add(beam);
    fx.push({t: now, d: 2400, f: k => { const s = 5 + k * 13; ring.scale.set(s, s, 1); ring.material.opacity = 1 - k; beam.material.opacity = .5 * (1 - k); beam.scale.x = beam.scale.z = 1 + k * .8; }, end: () => { root.remove(ring); root.remove(beam); ring.geometry.dispose(); beam.geometry.dispose(); }});
    const fl = desk && g.floors[desk];
    if (fl) { const band = new T.LineSegments(new T.EdgesGeometry(new T.BoxGeometry(FOOT + .5, fl.fh, FOOT + .5)), new T.LineBasicMaterial({color: 0xffffff, transparent: true})); band.position.set(g.x, fl.y, g.z); root.add(band);
      const gl = glow("#ffffff", 9, .9); gl.position.set(g.x, fl.y, g.z); root.add(gl);
      fx.push({t: now, d: 3200, f: k => { band.material.opacity = 1 - k * k; gl.material.opacity = .9 * (1 - k); const s = 1 + Math.sin(k * Math.PI * 5) * .03; band.scale.set(s, 1, s); }, end: () => { root.remove(band); root.remove(gl); band.geometry.dispose(); }}); }
  }

  // ---------------------------------------------------------------- the loop
  let raf = 0, running = false, last = 0;
  const ease = k => 1 - Math.pow(1 - k, 3);
  function frame(now) {
    raf = running ? requestAnimationFrame(frame) : 0;
    const dt = Math.min(.06, (now - last) / 1000) || 0, t = now / 1000; last = now;
    if (entering) { const e = (now - t0) / 1000; let done = true;
      for (const k in G) { const g = G[k]; if (!g.group) continue; const u = clamp((e - .15 - g.i * .09) / .95, 0, 1); g.group.scale.y = Math.max(.001, ease(u)); if (g.label) g.label.material.opacity = clamp((u - .6) / .4, 0, 1); if (u < 1) done = false; }
      const ur = clamp((e - 1.0) / 1.5, 0, 1); for (const r of routes) r.tube.geometry.setDrawRange(0, Math.floor(r.n * ease(ur) / 3) * 3); for (const q of qubits) q.q.visible = ur >= 1;
      if (G.beams) G.beams.material.opacity = .2 * clamp(e / 1.2, 0, 1);
      const uc = clamp(e / 2.6, 0, 1), k = ease(uc); rad = fit * zoomK * (1.55 - .55 * k); pol = .5 + (POL0 - .5) * k; az = .62 - 1.1 * (1 - k);
      if (ur < 1 || uc < 1) done = false;
      if (done) { entering = false; for (const r of routes) r.tube.geometry.setDrawRange(0, Infinity); }
    } else if (tw) { const k = clamp((now - tw.t) / tw.d, 0, 1), e = ease(k); az = tw.from.az + (tw.to.az - tw.from.az) * e; pol = tw.from.pol + (tw.to.pol - tw.from.pol) * e; zoomK = tw.from.zoomK + (tw.to.zoomK - tw.from.zoomK) * e; rad = fit * zoomK; if (k >= 1) tw = null; }
    else if (auto) az += dt * .045;
    else if (!reduce && !ptr.size && now - lastTouch > 14000) auto = true;
    place();
    for (const s of spin) s.o.rotation[s.axis] += dt * s.sp;
    if (spireRings) spireRings.rotation.y += dt * (busy ? 1.6 : .22);
    if (spireCore) { const s = busy ? 7 + Math.sin(t * 7) * 2.6 : 5 + Math.sin(t * 1.6) * .5; spireCore.scale.set(s, s, 1); }
    for (const b of blink) b.s.material.opacity = .35 + .65 * (.5 + .5 * Math.sin(t * 6.283 / b.sp + b.ph));
    if (view !== "coh") for (const h of hotMats) h.m.opacity = h.slow ? .7 + .3 * Math.sin(t * 3.6 + h.ph) : .86 + .14 * Math.sin(t * 9 + h.ph);
    for (const q of qubits) { const u = (t * q.sp + q.ph) % 1; q.cv3.getPoint(u, q.q.position); }
    for (let i = fx.length - 1; i >= 0; i--) { const f = fx[i], k = (now - f.t) / f.d; if (k >= 1) { f.end(); fx.splice(i, 1); } else f.f(k); }
    draw();
  }
  function draw() { renderer.render(scene, cam); }
  function resume() { if (running) return; running = true; last = performance.now(); resize(); raf = requestAnimationFrame(frame); }
  function pause() { running = false; cancelAnimationFrame(raf); }
  document.addEventListener("visibilitychange", () => { if (document.hidden) cancelAnimationFrame(raf); else if (running) { last = performance.now(); raf = requestAnimationFrame(frame); } });
  if (window.ResizeObserver) new ResizeObserver(resize).observe(host); else window.addEventListener("resize", resize);

  build(true);
  return {
    update() { const keep = [az, pol, zoomK, auto]; build(false); [az, pol, zoomK, auto] = keep; rad = fit * zoomK; place(); },
    setView(v) { view = v; applyView(); },
    setBusy(b) { busy = !!b; },
    pulse, resume, pause, resize,
    zoom(k) { nudge({zoomK: zoomK * k}); },
    turn(d) { nudge({az: az + d}); },
    tilt() { nudge({pol: pol < .6 ? 1.3 : pol > 1.15 ? POL0 : .42}); },
    home() { nudge({az: .62, pol: POL0, zoomK: 1}); },
    info: () => ({towers: ORG.companies.filter(c => G[c.id] && G[c.id].group).length, floors: Object.values(G).reduce((n, g) => n + (g.floors ? Object.keys(g.floors).length : 0), 0), routes: routes.length, entering, az, pol, zoomK}),
    pickAt: hitAt, canvas: cv
  };
}
window.City3D = {supported, create};
})();
