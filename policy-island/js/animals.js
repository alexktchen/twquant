/* 攻擊方動物（Q 版，和行人同一個比例尺）。每隻一個 Group，零件用圓角方塊與球體。 */
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
export function createAnimals(scene, ANIMALS) {
  const geo = { rbox: new RoundedBoxGeometry(1, 1, 1, 2, 0.3), sphere: new THREE.SphereGeometry(0.5, 12, 10), cone: new THREE.ConeGeometry(0.5, 1, 8), cyl: new THREE.CylinderGeometry(0.5, 0.5, 1, 8), torus: new THREE.TorusGeometry(0.5, 0.1, 6, 16), shadow: new THREE.CircleGeometry(0.5, 16).rotateX(-Math.PI / 2), hit: new THREE.SphereGeometry(0.5, 8, 8) };
  const mats = new Map(); const mat = c => { if (!mats.has(c)) mats.set(c, new THREE.MeshStandardMaterial({ color: c, roughness: 0.7 })); return mats.get(c); };
  const shadowMat = new THREE.MeshBasicMaterial({ color: 0x2a2018, transparent: true, opacity: 0.22, depthWrite: false }); const hitMat = new THREE.MeshBasicMaterial({ visible: false });
  const hpBg = new THREE.SpriteMaterial({ color: 0x211d1d, depthTest: false }), hpFg = new THREE.SpriteMaterial({ color: 0x4f8a3f, depthTest: false }), hpMid = new THREE.SpriteMaterial({ color: 0xe8c547, depthTest: false }), hpLow = new THREE.SpriteMaterial({ color: 0xc23b2a, depthTest: false });
  const texCache = new Map();
  function chantTexture(txt) { if (texCache.has(txt)) return texCache.get(txt); const cv = document.createElement('canvas'); const g = cv.getContext('2d'); const font = '900 44px "Noto Sans TC", "PingFang TC", sans-serif'; g.font = font; const tw = Math.ceil(g.measureText(txt).width); cv.width = tw + 44; cv.height = 76; g.font = font; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.beginPath(); g.roundRect(3, 3, cv.width - 6, cv.height - 14, 16); g.fillStyle = '#fff8f4'; g.fill(); g.lineWidth = 4; g.strokeStyle = '#211d1d'; g.stroke(); g.beginPath(); g.moveTo(cv.width / 2 - 8, cv.height - 12); g.lineTo(cv.width / 2 + 8, cv.height - 12); g.lineTo(cv.width / 2, cv.height - 2); g.closePath(); g.fillStyle = '#211d1d'; g.fill();
    g.fillStyle = '#211d1d'; g.fillText(txt, cv.width / 2, cv.height / 2 - 4); const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.userData = { w: cv.width, h: cv.height }; texCache.set(txt, t); return t; }
  const U = 0.34; // 一隻 size=1 的動物約 0.34 單位寬（和 0.8 倍 Q 版行人差不多）
  function mesh(kind, c, pos, scale, rot) { const m = new THREE.Mesh(geo[kind], mat(c)); m.position.set(...pos); m.scale.set(...(Array.isArray(scale) ? scale : [scale, scale, scale])); if (rot) m.rotation.set(...rot); m.castShadow = true; return m; }
  function spawn(typeId) {
    const A = ANIMALS[typeId]; const s = A.size * U; const g = new THREE.Group(); const inner = new THREE.Group(); g.add(inner); const parts = { inner };
    if (A.model === 'bird') {
      inner.add(mesh('sphere', A.color, [0, 0.42 * s, 0], [0.62 * s, 0.46 * s, 0.46 * s])); inner.add(mesh('sphere', A.color, [0.3 * s, 0.6 * s, 0], 0.34 * s)); inner.add(mesh('cone', 0xf0a030, [0.52 * s, 0.57 * s, 0], [0.12 * s, 0.2 * s, 0.12 * s], [0, 0, -Math.PI / 2]));
      for (const side of [-1, 1]) { inner.add(mesh('sphere', 0x111111, [0.4 * s, 0.66 * s, side * 0.1 * s], 0.06 * s)); const wg = new THREE.Group(); wg.position.set(0, 0.5 * s, side * 0.18 * s); const w = mesh('rbox', A.accent, [0, 0, side * 0.2 * s], [0.36 * s, 0.04 * s, 0.4 * s]); wg.add(w); wg.userData.side = side; inner.add(wg); (parts.wings ||= []).push(wg); }
      inner.add(mesh('cone', A.accent, [-0.38 * s, 0.44 * s, 0], [0.16 * s, 0.3 * s, 0.1 * s], [0, 0, Math.PI / 2]));
      if (A.boss) inner.add(mesh('cyl', A.accent, [0.3 * s, 0.82 * s, 0], [0.26 * s, 0.14 * s, 0.26 * s]));
    } else if (A.model === 'frog') {
      parts.body = mesh('sphere', A.color, [0, 0.24 * s, 0], [0.7 * s, 0.42 * s, 0.6 * s]); inner.add(parts.body); inner.add(mesh('sphere', 0xd9f2c2, [0.06 * s, 0.18 * s, 0], [0.5 * s, 0.28 * s, 0.46 * s]));
      for (const side of [-1, 1]) { inner.add(mesh('sphere', 0xffffff, [0.18 * s, 0.46 * s, side * 0.18 * s], 0.18 * s)); inner.add(mesh('sphere', 0x111111, [0.26 * s, 0.47 * s, side * 0.18 * s], 0.08 * s)); inner.add(mesh('sphere', A.accent, [-0.18 * s, 0.1 * s, side * 0.32 * s], [0.3 * s, 0.14 * s, 0.18 * s])); }
      if (A.boss) inner.add(mesh('cyl', A.accent, [0, 0.54 * s, 0], [0.3 * s, 0.14 * s, 0.3 * s]));
    } else if (A.model === 'worm') {
      parts.segs = []; for (let i = 0; i < 6; i++) { const m = mesh('sphere', i % 2 ? A.color : A.accent, [-i * 0.22 * s, 0.14 * s, 0], (0.3 - i * 0.02) * s); inner.add(m); parts.segs.push(m); } for (const side of [-1, 1]) inner.add(mesh('sphere', 0x111111, [0.1 * s, 0.22 * s, side * 0.08 * s], 0.06 * s));
    } else if (A.model === 'lizard') {
      const body = mesh('rbox', A.color, [0, 0.12 * s, 0], [0.56 * s, 0.18 * s, 0.24 * s]); const head = mesh('cone', A.color, [0.38 * s, 0.14 * s, 0], [0.22 * s, 0.26 * s, 0.18 * s], [0, 0, -Math.PI / 2]); const tail = mesh('cone', A.color, [-0.5 * s, 0.1 * s, 0], [0.16 * s, 0.5 * s, 0.12 * s], [0, 0, Math.PI / 2]); inner.add(body, head, tail);
      for (const side of [-1, 1]) inner.add(mesh('sphere', 0xffffff, [0.3 * s, 0.22 * s, side * 0.1 * s], 0.09 * s)); parts.shift = [body, head, tail].map(m => { m.material = m.material.clone(); return m.material; }); parts.tail = tail;
    } else {
      inner.add(mesh('sphere', A.color, [0, 0.16 * s, 0], [0.46 * s, 0.3 * s, 0.32 * s])); for (const side of [-1, 1]) inner.add(mesh('sphere', 0xf4b6c2, [0.1 * s, 0.34 * s, side * 0.12 * s], [0.14 * s, 0.14 * s, 0.04 * s])); inner.add(mesh('sphere', 0x222222, [0.3 * s, 0.16 * s, 0], 0.06 * s)); inner.add(mesh('rbox', 0x333333, [0.14 * s, 0.05 * s, 0], [0.26 * s, 0.03 * s, 0.14 * s])); inner.add(mesh('cyl', 0xd7a0a8, [-0.4 * s, 0.14 * s, 0], [0.03 * s, 0.4 * s, 0.03 * s], [0, 0, Math.PI / 2 + 0.4]));
    }
    if (A.model === 'bird' && A.fly) inner.position.y = 0.5;
    const sh = new THREE.Mesh(geo.shadow, shadowMat); sh.scale.setScalar(0.7 * s); sh.position.y = 0.015; g.add(sh);
    const hit = new THREE.Mesh(geo.hit, hitMat); hit.scale.setScalar(Math.max(0.5, 1.4 * s)); hit.position.y = (A.fly ? 0.8 : 0.25) * s + 0.1; g.add(hit);
    const bw = 0.5 * Math.max(0.8, A.size); const bg = new THREE.Sprite(hpBg); bg.scale.set(bw, 0.05, 1); const fg = new THREE.Sprite(hpFg); fg.scale.set(bw, 0.05, 1); const barY = (A.fly ? 1.0 : 0.6) * s + 0.18; bg.position.y = barY; fg.position.y = barY; bg.renderOrder = 8; fg.renderOrder = 9; g.add(bg, fg);
    g.userData = { type: typeId, parts, hit, hp: { bg, fg, w: bw }, chant: null, chantY: barY + 0.26 }; scene.add(g); return g;
  }
  function setHp(g, f) { const b = g.userData.hp; b.fg.scale.x = Math.max(0.001, b.w * f); b.fg.position.x = -(b.w * (1 - f)) / 2; b.fg.material = f > 0.5 ? hpFg : f > 0.25 ? hpMid : hpLow; }
  function setChant(g, txt) { const u = g.userData; if (u.chant) { g.remove(u.chant); u.chant = null; } if (!txt) return; const tex = chantTexture(txt); const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true })); const asp = tex.userData.w / tex.userData.h; sp.scale.set(0.5 * asp, 0.5, 1); sp.position.y = u.chantY + 0.2; sp.renderOrder = 10; u.chant = sp; g.add(sp); }
  function animate(g, e, dt, t) { const A = ANIMALS[e.type]; const p = g.userData.parts;
    if (p.wings) { const f = Math.sin(e.wing) * 0.7; for (const wg of p.wings) wg.rotation.x = wg.userData.side * f; }
    if (A.hop) { const ph = e.hopT % 1; p.inner.position.y = Math.sin(ph * Math.PI) * 0.22 * A.size; p.inner.scale.y = ph < 0.1 || ph > 0.9 ? 0.78 : 1; }
    if (p.segs) p.segs.forEach((s, i) => { s.position.y = (0.14 + Math.abs(Math.sin(t * 6 - i * 0.8)) * 0.06) * A.size * U; });
    if (p.shift) { const hue = (t * 0.15 + e.seed) % 1; for (const m of p.shift) m.color.setHSL(hue, 0.55, 0.5); p.tail.rotation.y = Math.sin(t * 4) * 0.4; }
    if (A.model === 'mouse') p.inner.position.y = Math.abs(Math.sin(t * 18 + e.seed * 10)) * 0.02;
    if (A.fly) p.inner.position.y = 0.5 + Math.sin(t * 3 + e.seed * 6) * 0.04; }
  function remove(g) { scene.remove(g); }
  return { spawn, setHp, setChant, animate, remove };
}
