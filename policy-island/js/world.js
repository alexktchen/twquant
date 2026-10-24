/* three.js 場景：六邊形台北島、河道、市政府、政策塔、英雄、動物與特效。
   只負責畫；模擬在 game.js。所有尺寸以「一格半徑 = 1」為單位。 */
window.PIWorld = (function () {
  const C = PI_CONFIG, M = C.map;
  const R = 1, W = Math.sqrt(3) * R;
  const CELLS = Object.keys(M.districts);
  const PATH_SET = new Set(Object.values(M.paths).flatMap(p => p.cells));
  const parseKey = k => { const [r, c] = k.split(',').map(Number); return { r, c }; };
  let offX = 0, offZ = 0;
  function raw(r, c) { return { x: W * (c + 0.5 * (r & 1)), z: 1.5 * R * r }; }
  function pos(r, c) { const p = raw(r, c); return { x: p.x - offX, z: p.z - offZ }; }
  function posKey(k) { const { r, c } = parseKey(k); return pos(r, c); }
  { const ps = CELLS.map(k => { const { r, c } = parseKey(k); return raw(r, c); }); offX = ps.reduce((s, p) => s + p.x, 0) / ps.length; offZ = ps.reduce((s, p) => s + p.z, 0) / ps.length; }
  const PATH_PTS = {};
  for (const [id, p] of Object.entries(M.paths)) {
    const f = parseKey(p.cells[0]); const s = pos(f.r + p.from.dr, f.c + p.from.dc);
    s.x += p.from.dc * W * 1.2; s.z += p.from.dr * R * 2.0;
    PATH_PTS[id] = [s, ...p.cells.map(posKey)];
  }
  const LAND_H = 0.6, ROAD_H = 0.48, WATER_Y = 0.22;

  const T = THREE;
  let renderer, scene, camera, container, hemi, sun;
  const tiles = new Map(), tileMeshes = [];
  const sky = { dawn: 0xffd9b0, day: 0xbfe6f5, dusk: 0xf4b183, night: 0x1f3550 };
  const cam = { az: -0.55, pol: 0.98, rad: 20, target: new T.Vector3(0, 0.4, 0.6) };
  const texCache = new Map();

  function init(el) {
    container = el;
    renderer = new T.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.shadowMap.enabled = true; renderer.shadowMap.type = T.PCFSoftShadowMap;
    renderer.outputEncoding = T.sRGBEncoding;
    el.appendChild(renderer.domElement);
    scene = new T.Scene(); scene.background = new T.Color(sky.day); scene.fog = new T.Fog(sky.day, 28, 60);
    camera = new T.PerspectiveCamera(42, 1, 0.1, 200);
    hemi = new T.HemisphereLight(0xffffff, 0x8899aa, 0.95); scene.add(hemi);
    sun = new T.DirectionalLight(0xfff2dc, 0.9); sun.position.set(12, 22, 10); sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, { left: -16, right: 16, top: 16, bottom: -16, near: 1, far: 60 }); scene.add(sun);
    buildWater(); buildTiles(); buildGoal(); buildRouteMarkers();
    resize(); window.addEventListener('resize', resize);
    bindCamera();
    return { scene, camera, renderer };
  }
  function resize() {
    const w = container.clientWidth; const narrow = w < 700;
    const h = Math.round(Math.min(narrow ? window.innerHeight * 0.58 : 760, Math.max(360, w * (narrow ? 1.05 : 0.68))));
    renderer.setSize(w, h, false); renderer.domElement.style.width = '100%'; renderer.domElement.style.height = h + 'px';
    camera.aspect = w / h; camera.updateProjectionMatrix();
    cam.rad = Math.max(12, 14.5 * (narrow ? 1.3 : 1) * Math.max(1, 1.5 / camera.aspect)); applyCamera();
  }
  function applyCamera() {
    const { az, pol, rad, target } = cam;
    camera.position.set(target.x + rad * Math.sin(pol) * Math.sin(az), target.y + rad * Math.cos(pol), target.z + rad * Math.sin(pol) * Math.cos(az));
    camera.lookAt(target);
  }
  function bindCamera() {
    const el = renderer.domElement; let drag = null, pinch = null;
    el.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, moved: 0 }; el.setPointerCapture(e.pointerId); });
    el.addEventListener('pointermove', e => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.moved += Math.abs(dx) + Math.abs(dy); if (drag.moved > 6) { cam.az -= dx * 0.005; cam.pol = Math.min(1.25, Math.max(0.55, cam.pol - dy * 0.004)); applyCamera(); } drag.x = e.clientX; drag.y = e.clientY; });
    el.addEventListener('pointerup', e => { const wasClick = drag && drag.moved <= 6; drag = null; if (wasClick) handlers.click && handlers.click(e); });
    el.addEventListener('pointercancel', () => { drag = null; });
    el.addEventListener('wheel', e => { e.preventDefault(); cam.rad = Math.min(34, Math.max(11, cam.rad + e.deltaY * 0.02)); applyCamera(); }, { passive: false });
    el.addEventListener('touchstart', e => { if (e.touches.length === 2) pinch = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY); }, { passive: true });
    el.addEventListener('touchmove', e => { if (e.touches.length === 2 && pinch) { const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY); cam.rad = Math.min(34, Math.max(11, cam.rad * pinch / d)); pinch = d; applyCamera(); } }, { passive: true });
    el.addEventListener('touchend', () => { pinch = null; });
  }
  const handlers = {};
  function on(ev, fn) { handlers[ev] = fn; }

  // ── 地形 ──
  function buildWater() {
    const g = new T.PlaneGeometry(90, 90, 1, 1); const m = new T.MeshStandardMaterial({ color: 0x8fc9e0, roughness: 0.35, metalness: 0.05 });
    const w = new T.Mesh(g, m); w.rotation.x = -Math.PI / 2; w.position.y = WATER_Y; w.receiveShadow = true; scene.add(w); water = w;
  }
  let water;
  function seeded(k) { let h = 0; for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return () => { h = (h * 1664525 + 1013904223) >>> 0; return h / 4294967296; }; }
  function buildTiles() {
    const hexGeo = new T.CylinderGeometry(R * 0.97, R * 0.97, 1, 6);
    for (const k of CELLS) {
      const p = posKey(k); const isPath = PATH_SET.has(k); const isGoal = k === M.goal;
      const h = isGoal ? LAND_H + 0.12 : isPath ? ROAD_H : LAND_H;
      const color = isGoal ? 0xf0cf7a : isPath ? 0xe3c184 : (M.districtTint[M.districts[k]] || 0xe9dcc3);
      const m = new T.Mesh(hexGeo, new T.MeshStandardMaterial({ color, roughness: 0.9 }));
      m.scale.y = h; m.position.set(p.x, h / 2, p.z); m.receiveShadow = true; m.castShadow = true;
      m.userData = { key: k, isPath, isGoal, baseColor: color };
      scene.add(m); tiles.set(k, m); tileMeshes.push(m);
      if (!isPath && !isGoal) decorate(k, p, h);
    }
  }
  function decorate(k, p, h) {
    const lm = M.landmarks[k]; const rnd = seeded(k);
    const g = new T.Group(); g.position.set(p.x, h, p.z); scene.add(g);
    if (lm) {
      if (lm.kind === 'ring') { const t = new T.Mesh(new T.TorusGeometry(0.42, 0.07, 8, 24), new T.MeshStandardMaterial({ color: 0xd96a44 })); t.rotation.x = Math.PI / 2; t.position.y = 0.05; g.add(t); const x = new T.Mesh(new T.BoxGeometry(0.9, 0.04, 0.12), new T.MeshStandardMaterial({ color: 0x1c2531 })); x.rotation.y = 0.7; x.position.y = 0.1; g.add(x); }
      if (lm.kind === 'airport') { const s = new T.Mesh(new T.BoxGeometry(1.3, 0.04, 0.3), new T.MeshStandardMaterial({ color: 0x555b60 })); s.position.y = 0.03; g.add(s); const pl = new T.Mesh(new T.ConeGeometry(0.1, 0.5, 6), new T.MeshStandardMaterial({ color: 0xffffff })); pl.rotation.z = -Math.PI / 2; pl.position.set(0.2, 0.18, 0); g.add(pl); }
      if (lm.kind === 'spring') { const b = new T.Mesh(new T.CylinderGeometry(0.4, 0.4, 0.08, 16), new T.MeshStandardMaterial({ color: 0x9fd6e8 })); b.position.y = 0.04; g.add(b); for (let i = 0; i < 3; i++) { const st = new T.Mesh(new T.SphereGeometry(0.06, 6, 6), new T.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.7 })); st.position.set((i - 1) * 0.18, 0.25, 0); st.userData.steam = i; g.add(st); } }
      if (lm.kind === 'tech') { for (let i = 0; i < 3; i++) { const bh = 0.6 + i * 0.25; const b = new T.Mesh(new T.BoxGeometry(0.28, bh, 0.28), new T.MeshStandardMaterial({ color: 0xcfd8e3 })); b.position.set((i - 1) * 0.38, bh / 2, 0.1); b.castShadow = true; g.add(b); } }
      g.userData.landmark = lm; decorGroups.push(g); return;
    }
    const n = 2 + Math.floor(rnd() * 3);
    for (let i = 0; i < n; i++) {
      const tx = (rnd() - 0.5) * 1.1, tz = (rnd() - 0.5) * 1.0;
      if (rnd() < 0.55) { const th = 0.25 + rnd() * 0.2; const trunk = new T.Mesh(new T.CylinderGeometry(0.03, 0.04, 0.15, 5), new T.MeshStandardMaterial({ color: 0x7a5a3a })); trunk.position.set(tx, 0.075, tz); g.add(trunk); const crown = new T.Mesh(new T.ConeGeometry(0.16 + rnd() * 0.06, th, 7), new T.MeshStandardMaterial({ color: rnd() < 0.5 ? 0x5b8a4b : 0x6f9a5a })); crown.position.set(tx, 0.15 + th / 2, tz); crown.castShadow = true; g.add(crown); }
      else { const bh = 0.2 + rnd() * 0.45; const b = new T.Mesh(new T.BoxGeometry(0.22 + rnd() * 0.12, bh, 0.22 + rnd() * 0.12), new T.MeshStandardMaterial({ color: [0xf7efe2, 0xe8e0d2, 0xd9cfc0, 0xf1e4cf][Math.floor(rnd() * 4)] })); b.position.set(tx, bh / 2, tz); b.castShadow = true; g.add(b); }
    }
    decorGroups.push(g);
  }
  const decorGroups = [];
  function buildGoal() {
    const p = posKey(M.goal); const g = new T.Group(); g.position.set(p.x, LAND_H + 0.12, p.z);
    const base = new T.Mesh(new T.BoxGeometry(1.25, 0.35, 0.75), new T.MeshStandardMaterial({ color: 0xf4efe6 })); base.position.y = 0.175; base.castShadow = true; g.add(base);
    for (const sx of [-0.33, 0.33]) { const tw = new T.Mesh(new T.BoxGeometry(0.34, 1.1, 0.4), new T.MeshStandardMaterial({ color: 0xe9e2d6 })); tw.position.set(sx, 0.9, 0); tw.castShadow = true; g.add(tw); }
    const bridge = new T.Mesh(new T.BoxGeometry(0.5, 0.12, 0.3), new T.MeshStandardMaterial({ color: 0xd6cdbf })); bridge.position.y = 1.2; g.add(bridge);
    const pole = new T.Mesh(new T.CylinderGeometry(0.015, 0.015, 0.5, 5), new T.MeshStandardMaterial({ color: 0x555 })); pole.position.set(0.33, 1.7, 0); g.add(pole);
    const flag = new T.Mesh(new T.PlaneGeometry(0.28, 0.16), new T.MeshStandardMaterial({ color: 0x1f4e79, side: T.DoubleSide })); flag.position.set(0.48, 1.85, 0); g.add(flag); flagMesh = flag;
    g.add(label('市政府', { size: 1.5, y: 2.3, bg: '#1c2531', fg: '#fff' }));
    scene.add(g);
  }
  let flagMesh;
  function buildRouteMarkers() {
    const mat = new T.MeshStandardMaterial({ color: 0xd59c4a });
    for (const pts of Object.values(PATH_PTS)) for (let i = 1; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1]; const c = new T.Mesh(new T.ConeGeometry(0.1, 0.22, 5), mat); c.position.set((a.x + b.x) / 2, ROAD_H + 0.1, (a.z + b.z) / 2); c.rotation.x = Math.PI / 2; c.rotation.z = -Math.atan2(b.z - a.z, b.x - a.x) + Math.PI / 2; c.rotateOnAxis(new T.Vector3(1, 0, 0), 0); scene.add(c);
      c.lookAt(b.x, ROAD_H + 0.1, b.z); c.rotateX(Math.PI / 2);
    }
    for (const [id, p] of Object.entries(M.paths)) { const s = PATH_PTS[id][0]; const l = label(p.name + ' →', { size: 0.9, y: 0.9, bg: 'rgba(255,255,255,.85)', fg: '#1c2531' }); l.position.set(s.x, 0.9, s.z); scene.add(l); }
  }

  // ── 文字貼圖 / 標籤 ──
  function textTexture(txt, { bg = '#fff', fg = '#1c2531', font = '900 44px "Noto Sans TC", sans-serif', pad = 22, radius = 18, border = '#1c2531' } = {}) {
    const key = [txt, bg, fg, font].join('|'); if (texCache.has(key)) return texCache.get(key);
    const cv = document.createElement('canvas'); const ctx = cv.getContext('2d'); ctx.font = font;
    const tw = Math.ceil(ctx.measureText(txt).width); cv.width = tw + pad * 2; cv.height = 64 + pad;
    ctx.font = font; ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
    const rr = (x, y, w, h, r) => { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); };
    rr(2, 2, cv.width - 4, cv.height - 4, radius); ctx.fillStyle = bg; ctx.fill(); if (border) { ctx.lineWidth = 4; ctx.strokeStyle = border; ctx.stroke(); }
    ctx.fillStyle = fg; ctx.fillText(txt, cv.width / 2, cv.height / 2 + 2);
    const tex = new T.CanvasTexture(cv); tex.encoding = T.sRGBEncoding; tex.minFilter = T.LinearFilter; tex.userData = { w: cv.width, h: cv.height };
    texCache.set(key, tex); return tex;
  }
  function label(txt, { size = 1, y = 0, bg, fg, border } = {}) {
    const tex = textTexture(txt, { bg, fg, border }); const s = new T.Sprite(new T.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    const aspect = tex.userData.w / tex.userData.h; s.scale.set(size * aspect * 0.42, size * 0.42, 1); s.position.y = y; s.renderOrder = 10; return s;
  }
  function emojiTexture(emoji) {
    const key = 'emoji|' + emoji; if (texCache.has(key)) return texCache.get(key);
    const cv = document.createElement('canvas'); cv.width = cv.height = 128; const ctx = cv.getContext('2d'); ctx.font = '96px serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(emoji, 64, 72);
    const tex = new T.CanvasTexture(cv); tex.encoding = T.sRGBEncoding; texCache.set(key, tex); return tex;
  }

  // ── 塔 / 英雄 ──
  function makeTower(type, level, key) {
    const Tw = C.towers[type]; const p = posKey(key); const g = new T.Group(); g.position.set(p.x, LAND_H, p.z);
    const h = 0.35 + 0.12 * level; const body = new T.Mesh(new T.CylinderGeometry(0.62, 0.68, h, 6), new T.MeshStandardMaterial({ color: Tw.color, roughness: 0.6 })); body.position.y = h / 2; body.castShadow = true; g.add(body);
    const cap = new T.Mesh(new T.CylinderGeometry(0.5, 0.5, 0.06, 6), new T.MeshStandardMaterial({ color: 0xfff8ee })); cap.position.y = h + 0.03; g.add(cap);
    const ic = new T.Sprite(new T.SpriteMaterial({ map: emojiTexture(Tw.icon), transparent: true })); ic.scale.set(0.9, 0.9, 1); ic.position.y = h + 0.55; g.add(ic);
    for (let i = 0; i < level; i++) { const ring = new T.Mesh(new T.TorusGeometry(0.72, 0.03, 6, 24), new T.MeshStandardMaterial({ color: 0xf2c14e })); ring.rotation.x = Math.PI / 2; ring.position.y = 0.06 + i * 0.1; g.add(ring); }
    const sel = new T.Mesh(new T.RingGeometry(Tw.range * W - 0.05, Tw.range * W, 48), new T.MeshBasicMaterial({ color: Tw.color, transparent: true, opacity: 0.55, side: T.DoubleSide })); sel.rotation.x = -Math.PI / 2; sel.position.y = 0.05; sel.visible = false; g.add(sel);
    g.userData = { kind: 'tower', key, body, cap, sel, baseY: h }; scene.add(g); return g;
  }
  function setRangeRing(g, range, visible) { const r = g.userData.sel; r.geometry.dispose(); r.geometry = new T.RingGeometry(Math.max(0.01, range * W - 0.05), Math.max(0.02, range * W), 48); r.visible = visible && range > 0; }
  function makeHero(cls, nick) {
    const H = C.heroes[cls]; const p = posKey(M.heroTile); const g = new T.Group(); g.position.set(p.x, LAND_H, p.z);
    const legs = new T.Mesh(new T.CylinderGeometry(0.16, 0.18, 0.3, 8), new T.MeshStandardMaterial({ color: 0x2b3a4a })); legs.position.y = 0.15; g.add(legs);
    const body = new T.Mesh(new T.CylinderGeometry(0.22, 0.2, 0.45, 10), new T.MeshStandardMaterial({ color: H.color })); body.position.y = 0.52; body.castShadow = true; g.add(body);
    const head = new T.Mesh(new T.SphereGeometry(0.19, 12, 10), new T.MeshStandardMaterial({ color: 0xf1c9a5 })); head.position.y = 0.92; head.castShadow = true; g.add(head);
    const hat = new T.Mesh(new T.CylinderGeometry(0.2, 0.22, 0.12, 10), new T.MeshStandardMaterial({ color: H.color })); hat.position.y = 1.08; g.add(hat);
    const ic = new T.Sprite(new T.SpriteMaterial({ map: emojiTexture(H.icon), transparent: true, depthTest: false })); ic.scale.set(0.7, 0.7, 1); ic.position.set(0.45, 1.25, 0); g.add(ic);
    const nm = label(nick || H.name, { size: 1.3, y: 1.75, bg: '#1f4e79', fg: '#fff', border: null }); g.add(nm);
    const ring = new T.Mesh(new T.RingGeometry(H.range * W - 0.04, H.range * W, 64), new T.MeshBasicMaterial({ color: H.color, transparent: true, opacity: 0.18, side: T.DoubleSide })); ring.rotation.x = -Math.PI / 2; ring.position.y = 0.04; g.add(ring);
    g.userData = { kind: 'hero', body, head, ring, bob: 0 }; scene.add(g); return g;
  }

  // ── 動物 ──
  const hitGeo = new T.SphereGeometry(0.6, 8, 8); const hitMat = new T.MeshBasicMaterial({ visible: false });
  const hpBg = new T.SpriteMaterial({ color: 0x1c2531, depthTest: false }); const hpFg = new T.SpriteMaterial({ color: 0x4f8a3f, depthTest: false }), hpMid = new T.SpriteMaterial({ color: 0xf2c14e, depthTest: false }), hpLow = new T.SpriteMaterial({ color: 0xc23b2a, depthTest: false });
  function makeAnimal(typeId) {
    const A = C.animals[typeId]; const g = new T.Group(); const s = A.size; const inner = new T.Group(); g.add(inner);
    const mat = (c) => new T.MeshStandardMaterial({ color: c, roughness: 0.7 });
    const parts = { inner };
    if (A.model === 'bird') {
      const body = new T.Mesh(new T.SphereGeometry(0.28 * s, 12, 10), mat(A.color)); body.scale.set(1.25, 0.9, 0.9); body.castShadow = true; inner.add(body);
      const head = new T.Mesh(new T.SphereGeometry(0.16 * s, 10, 8), mat(A.color)); head.position.set(0.3 * s, 0.16 * s, 0); inner.add(head);
      const beak = new T.Mesh(new T.ConeGeometry(0.06 * s, 0.18 * s, 6), mat(0xf0a030)); beak.rotation.z = -Math.PI / 2; beak.position.set(0.5 * s, 0.14 * s, 0); inner.add(beak);
      for (const side of [-1, 1]) { const eye = new T.Mesh(new T.SphereGeometry(0.035 * s, 6, 6), mat(0x111111)); eye.position.set(0.38 * s, 0.22 * s, side * 0.1 * s); inner.add(eye); }
      const wings = [];
      for (const side of [-1, 1]) { const wg = new T.Group(); wg.position.set(0, 0.08 * s, side * 0.18 * s); const w = new T.Mesh(new T.BoxGeometry(0.34 * s, 0.03 * s, 0.4 * s), mat(A.accent)); w.position.z = side * 0.2 * s; w.castShadow = true; wg.add(w); wg.userData.side = side; inner.add(wg); wings.push(wg); }
      const tail = new T.Mesh(new T.ConeGeometry(0.1 * s, 0.3 * s, 4), mat(A.accent)); tail.rotation.z = Math.PI / 2; tail.position.set(-0.4 * s, 0.05 * s, 0); inner.add(tail);
      if (A.boss) { const crown = new T.Mesh(new T.CylinderGeometry(0.14 * s, 0.1 * s, 0.14 * s, 5), mat(A.accent)); crown.position.set(0.3 * s, 0.36 * s, 0); inner.add(crown); }
      parts.wings = wings;
    } else if (A.model === 'frog') {
      const body = new T.Mesh(new T.SphereGeometry(0.3 * s, 12, 10), mat(A.color)); body.scale.set(1.2, 0.75, 1.1); body.castShadow = true; inner.add(body);
      const belly = new T.Mesh(new T.SphereGeometry(0.22 * s, 10, 8), mat(0xd9f2c2)); belly.scale.set(1.1, 0.6, 1); belly.position.set(0.05 * s, -0.05 * s, 0); inner.add(belly);
      for (const side of [-1, 1]) { const eye = new T.Mesh(new T.SphereGeometry(0.09 * s, 8, 8), mat(0xffffff)); eye.position.set(0.18 * s, 0.24 * s, side * 0.16 * s); inner.add(eye); const pupil = new T.Mesh(new T.SphereGeometry(0.045 * s, 6, 6), mat(0x111111)); pupil.position.set(0.26 * s, 0.25 * s, side * 0.16 * s); inner.add(pupil);
        const leg = new T.Mesh(new T.SphereGeometry(0.11 * s, 8, 6), mat(A.accent)); leg.scale.set(1.4, 0.7, 0.8); leg.position.set(-0.18 * s, -0.12 * s, side * 0.3 * s); inner.add(leg); }
      if (A.boss) { const crown = new T.Mesh(new T.CylinderGeometry(0.16 * s, 0.12 * s, 0.16 * s, 5), mat(A.accent)); crown.position.set(0, 0.32 * s, 0); inner.add(crown); }
      parts.body = body;
    } else if (A.model === 'worm') {
      const segs = [];
      for (let i = 0; i < 6; i++) { const seg = new T.Mesh(new T.SphereGeometry((0.16 - i * 0.01) * s, 10, 8), mat(i % 2 ? A.color : A.accent)); seg.position.set(-i * 0.22 * s, 0, 0); seg.castShadow = true; inner.add(seg); segs.push(seg); }
      for (const side of [-1, 1]) { const eye = new T.Mesh(new T.SphereGeometry(0.035 * s, 6, 6), mat(0x111111)); eye.position.set(0.1 * s, 0.08 * s, side * 0.08 * s); inner.add(eye); }
      parts.segs = segs;
    } else if (A.model === 'lizard') {
      const body = new T.Mesh(new T.BoxGeometry(0.55 * s, 0.18 * s, 0.22 * s), mat(A.color)); body.castShadow = true; inner.add(body);
      const head = new T.Mesh(new T.ConeGeometry(0.11 * s, 0.26 * s, 6), mat(A.color)); head.rotation.z = -Math.PI / 2; head.position.set(0.38 * s, 0.02 * s, 0); inner.add(head);
      const tail = new T.Mesh(new T.ConeGeometry(0.08 * s, 0.5 * s, 6), mat(A.color)); tail.rotation.z = Math.PI / 2; tail.position.set(-0.5 * s, 0, 0); inner.add(tail);
      for (const side of [-1, 1]) { const eye = new T.Mesh(new T.SphereGeometry(0.05 * s, 6, 6), mat(0xffffff)); eye.position.set(0.3 * s, 0.1 * s, side * 0.1 * s); inner.add(eye); }
      parts.shiftMats = [body.material, head.material, tail.material]; parts.tail = tail;
    } else { // mouse
      const body = new T.Mesh(new T.SphereGeometry(0.22 * s, 10, 8), mat(A.color)); body.scale.set(1.3, 0.8, 0.9); body.castShadow = true; inner.add(body);
      for (const side of [-1, 1]) { const ear = new T.Mesh(new T.CircleGeometry(0.1 * s, 10), new T.MeshStandardMaterial({ color: 0xf4b6c2, side: T.DoubleSide })); ear.position.set(0.1 * s, 0.22 * s, side * 0.12 * s); inner.add(ear); }
      const nose = new T.Mesh(new T.SphereGeometry(0.03 * s, 6, 6), mat(0x222222)); nose.position.set(0.3 * s, 0, 0); inner.add(nose);
      const kb = new T.Mesh(new T.BoxGeometry(0.26 * s, 0.03 * s, 0.14 * s), mat(0x333333)); kb.position.set(0.12 * s, -0.12 * s, 0); inner.add(kb);
      const tail = new T.Mesh(new T.CylinderGeometry(0.015 * s, 0.01 * s, 0.4 * s, 4), mat(0xd7a0a8)); tail.rotation.z = Math.PI / 2 + 0.4; tail.position.set(-0.4 * s, 0.05 * s, 0); inner.add(tail);
    }
    const hit = new T.Mesh(hitGeo, hitMat); hit.scale.setScalar(Math.max(0.9, s)); hit.userData.enemy = null; g.add(hit);
    const bw = 0.9 * Math.max(0.8, s); const bg = new T.Sprite(hpBg); bg.scale.set(bw, 0.09, 1); const fg = new T.Sprite(hpFg); fg.scale.set(bw, 0.09, 1);
    const barY = (A.fly ? 0.55 : 0.5) * s + 0.25; bg.position.y = barY; fg.position.y = barY; bg.renderOrder = 8; fg.renderOrder = 9; g.add(bg); g.add(fg);
    g.userData = { kind: 'animal', type: typeId, parts, hit, hpBar: { bg, fg, w: bw }, chant: null, chantY: barY + 0.45 };
    scene.add(g); return g;
  }
  function setHp(g, frac) { const b = g.userData.hpBar; b.fg.scale.x = Math.max(0.001, b.w * frac); b.fg.position.x = -(b.w * (1 - frac)) / 2; b.fg.material = frac > 0.5 ? hpFg : frac > 0.25 ? hpMid : hpLow; }
  function setChant(g, txt) {
    const u = g.userData; if (u.chant) { g.remove(u.chant); u.chant = null; }
    if (!txt) return; const s = label(txt, { size: 1.35, y: u.chantY, bg: '#fff', fg: '#1c2531', border: '#1c2531' }); u.chant = s; g.add(s);
  }
  function animateAnimal(g, e, dt, t) {
    const A = C.animals[e.type]; const p = g.userData.parts; const sp = A.speed;
    if (p.wings) { const f = Math.sin(e.wing) * 0.7; for (const wg of p.wings) wg.rotation.x = wg.userData.side * f; }
    if (A.hop) { const ph = (e.hopT % 1); const air = Math.sin(ph * Math.PI); p.inner.position.y = air * 0.45 * A.size; const sq = ph < 0.1 || ph > 0.9 ? 0.75 : 1; p.inner.scale.set(1, sq, 1); }
    if (p.segs) p.segs.forEach((s, i) => { s.position.y = Math.abs(Math.sin(t * 6 - i * 0.8)) * 0.08; });
    if (p.shiftMats) { const hue = (t * 0.15 + e.seed) % 1; for (const m of p.shiftMats) m.color.setHSL(hue, 0.55, 0.5); if (p.tail) p.tail.rotation.y = Math.sin(t * 4) * 0.4; }
    if (A.model === 'mouse') p.inner.position.y = Math.abs(Math.sin(t * 18 + e.seed * 10)) * 0.05;
    if (A.fly) p.inner.position.y = 1.15 + Math.sin(t * 3 + e.seed * 6) * 0.08;
    if (e.slow > 0) { g.userData.hpBar.bg.material = hpBg; }
  }
  function removeObj(g) { scene.remove(g); g.traverse(o => { if (o.geometry && o.geometry !== hitGeo) o.geometry.dispose(); }); }

  // ── 子彈 / 光束 / 浮字 ──
  const projGeo = new T.SphereGeometry(0.09, 8, 8), busGeo = new T.BoxGeometry(0.26, 0.14, 0.14), waterGeo = new T.SphereGeometry(0.07, 6, 6);
  const projMats = {};
  function makeProj(kind, color) {
    const key = kind + '|' + color; projMats[key] = projMats[key] || new T.MeshBasicMaterial({ color: kind === 'ball' ? 0xffffff : kind === 'water' ? 0x66c6ff : color });
    const m = new T.Mesh(kind === 'bus' ? busGeo : kind === 'water' ? waterGeo : projGeo, projMats[key]); scene.add(m); return m;
  }
  function makeBeam(a, b, color) {
    const geo = new T.BufferGeometry().setFromPoints([new T.Vector3(a.x, a.y, a.z), new T.Vector3(b.x, b.y, b.z)]); const l = new T.Line(geo, new T.LineBasicMaterial({ color, transparent: true, opacity: 0.95 })); scene.add(l); return l;
  }
  function makeFloat(txt, x, y, z, { bg = 'rgba(255,255,255,.9)', fg = '#1c2531' } = {}) { const s = label(txt, { size: 1.1, bg, fg, border: null }); s.position.set(x, y, z); scene.add(s); return s; }
  function makeRing(x, z, r, color) { const m = new T.Mesh(new T.RingGeometry(r * 0.8, r, 40), new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.8, side: T.DoubleSide })); m.rotation.x = -Math.PI / 2; m.position.set(x, LAND_H + 0.05, z); scene.add(m); return m; }

  // ── 時間 / 天色 ──
  const tmp = new T.Color();
  function setTimeOfDay(f) { // f: 0..1 一場的進度 → 清晨→白天→黃昏→夜
    const keys = [[0, sky.dawn], [0.3, sky.day], [0.72, sky.dusk], [1, sky.night]]; let a = keys[0], b = keys[1];
    for (let i = 0; i < keys.length - 1; i++) if (f >= keys[i][0]) { a = keys[i]; b = keys[i + 1]; }
    const k = Math.min(1, Math.max(0, (f - a[0]) / (b[0] - a[0] || 1))); tmp.set(a[1]).lerp(new T.Color(b[1]), k);
    scene.background.copy(tmp); scene.fog.color.copy(tmp); hemi.intensity = 0.95 - 0.45 * Math.max(0, f - 0.7) / 0.3; sun.intensity = 0.9 - 0.5 * Math.max(0, f - 0.7) / 0.3;
    water.material.color.setHex(f > 0.85 ? 0x2b5f7a : 0x8fc9e0);
  }
  function highlightTile(key, ok) { for (const m of tileMeshes) { const u = m.userData; const want = u.key === key && !u.isPath && !u.isGoal; m.material.emissive.setHex(want ? (ok ? 0x335533 : 0x663322) : 0x000000); } }
  function ambient(dt, t) { if (flagMesh) flagMesh.rotation.y = Math.sin(t * 3) * 0.25; for (const g of decorGroups) if (g.userData.landmark && g.userData.landmark.kind === 'spring') g.children.forEach(c => { if (c.userData.steam != null) { c.position.y = 0.25 + ((t * 0.3 + c.userData.steam * 0.33) % 1) * 0.5; c.material.opacity = 0.7 - ((t * 0.3 + c.userData.steam * 0.33) % 1) * 0.7; } }); }

  // ── 射線 ──
  const ray = new T.Raycaster(), ndc = new T.Vector2();
  function pick(ev, enemyHits) {
    const r = renderer.domElement.getBoundingClientRect(); ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(ndc, camera);
    const eh = ray.intersectObjects(enemyHits, false); if (eh.length) return { enemy: eh[0].object.userData.enemy };
    const th = ray.intersectObjects(tileMeshes, false); if (th.length) return { tile: th[0].object.userData.key };
    return {};
  }
  function render() { renderer.render(scene, camera); }
  return { init, on, render, resize, W, R, LAND_H, PATH_PTS, PATH_SET, posKey, pick, makeTower, setRangeRing, makeHero, makeAnimal, setHp, setChant, animateAnimal, removeObj, makeProj, makeBeam, makeFloat, makeRing, setTimeOfDay, highlightTile, ambient, label, get scene() { return scene; } };
})();
