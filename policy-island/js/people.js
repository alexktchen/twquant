/* Q 版行人（居民與英雄）。零件按幾何種類實例化，每幀合成矩陣做走路擺動。比例與做法參考原站 people.js，重新實作。 */
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mulberry } from './env.js';
export const ROLE_LABEL = { citizen: '市民', elder: '長者', child: '學童', parent: '家長', cyclist: '通勤族', officeworker: '上班族', youth: '年輕人', worker: '工地人員', hiker: '登山客', hero: '英雄' };
const SKIN = [0xf3cfae, 0xe6b48f, 0xcf9670, 0xf0c4a0], HAIR = [0x2e2522, 0x4a3426, 0x1f1d22, 0x6b4a33], CASUAL = [0x6f7fb5, 0x58a6a6, 0xe8c547, 0xf2a1a1, 0x8fb37a, 0xb58fd0];
export function createPeople(scene, colors, heroCfgs) {
  const G = { leg: new RoundedBoxGeometry(1, 1, 1, 2, 0.3), body: new THREE.CapsuleGeometry(0.5, 0.6, 4, 10), arm: new RoundedBoxGeometry(1, 1, 1, 2, 0.35), head: new THREE.SphereGeometry(0.5, 16, 12), cap: new THREE.SphereGeometry(0.5, 16, 8, 0, Math.PI * 2, 0, Math.PI * 0.55), eye: new THREE.SphereGeometry(0.5, 8, 6), box: new RoundedBoxGeometry(1, 1, 1, 2, 0.2), cyl: new THREE.CylinderGeometry(0.5, 0.5, 1, 10), cone: new THREE.ConeGeometry(0.5, 1, 14), torus: new THREE.TorusGeometry(0.5, 0.09, 6, 18), shadow: new THREE.CircleGeometry(0.5, 20) };
  G.shadow.rotateX(-Math.PI / 2);
  const mat = new THREE.MeshStandardMaterial({ roughness: 0.7 });
  const shadowMat = new THREE.MeshBasicMaterial({ color: 0x2a2018, transparent: true, opacity: 0.22, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8 });
  let rnd = mulberry(41);
  function plan(role, heroCls) {
    const P = []; const H = heroCls ? heroCfgs[heroCls] : null;
    const skin = SKIN[Math.floor(rnd() * SKIN.length)]; let hair = HAIR[Math.floor(rnd() * HAIR.length)];
    let shirt = CASUAL[Math.floor(rnd() * CASUAL.length)], pants = [0x3b3a42, 0x4a5a78, 0x6b5a4a][Math.floor(rnd() * 3)]; let sc = 1;
    if (role === 'child') sc = 0.78; if (role === 'elder') hair = 0xd9d4cc; if (role === 'worker') shirt = 0x4a5a78;
    if (H) { sc = 1.16; shirt = 0xfbfbf7; pants = 0x2f3a4a; hair = 0x241c1a; }
    const add = (g, off, s, c, ch = null, r = null) => P.push({ g, off, s, c, ch, r });
    add('leg', [-0.032, 0.065, 0], [0.05, 0.13, 0.058], pants, 'legL'); add('leg', [0.032, 0.065, 0], [0.05, 0.13, 0.058], pants, 'legR');
    const shoe = H ? 0xf6f4ef : 0x2a2524; add('box', [-0.032, 0.012, 0.012], [0.055, 0.025, 0.085], shoe, 'legL'); add('box', [0.032, 0.012, 0.012], [0.055, 0.025, 0.085], shoe, 'legR');
    add('body', [0, 0.2, 0], [0.135, 0.11, 0.115], shirt);
    if (role === 'worker') { add('box', [0, 0.205, 0.003], [0.128, 0.11, 0.104], 0xf2a33a); add('box', [0, 0.2, 0], [0.13, 0.014, 0.106], 0xf5f5ef); }
    add('arm', [-0.077, 0.205, 0], [0.034, 0.1, 0.038], shirt, 'armL'); add('arm', [0.077, 0.205, 0], [0.034, 0.1, 0.038], shirt, 'armR');
    add('eye', [-0.077, 0.15, 0], [0.034, 0.034, 0.034], skin, 'armL'); add('eye', [0.077, 0.15, 0], [0.034, 0.034, 0.034], skin, 'armR');
    add('cyl', [0, 0.268, 0], [0.05, 0.03, 0.05], skin);
    if (!H) { add('box', [0, 0.157, 0], [0.139, 0.016, 0.119], 0x3b3a42); add('box', [0, 0.255, 0.035], [0.075, 0.022, 0.05], 0xfbfbf7); }
    add('head', [0, 0.345, 0], [0.19, 0.178, 0.172], skin, 'head');
    const hatted = ['child', 'cyclist', 'hiker', 'worker'].includes(role) || (H && ['chief', 'driver', 'fireman'].includes(heroCls));
    add('cap', [0, 0.352, -0.006], [0.206, hatted ? 0.17 : 0.19, 0.204], hair, 'head', [-0.32, 0, 0]);
    if (!hatted) { const styles = role === 'elder' ? ['short', 'bun', 'short'] : role === 'youth' ? ['beanie', 'long', 'pony', 'puff'] : ['short', 'long', 'bun', 'pony', 'short', 'puff']; const st = styles[Math.floor(rnd() * styles.length)];
      if (st === 'long') add('box', [0, 0.31, -0.07], [0.19, 0.13, 0.05], hair, 'head', [0.12, 0, 0]); if (st === 'bun') add('eye', [0, 0.44, -0.07], [0.075, 0.07, 0.07], hair, 'head'); if (st === 'pony') add('eye', [0, 0.33, -0.115], [0.055, 0.1, 0.05], hair, 'head', [0.35, 0, 0]);
      if (st === 'puff') for (const [dx, dy] of [[-0.07, 0.4], [0.07, 0.4], [0, 0.435]]) add('eye', [dx, dy, -0.02], [0.09, 0.08, 0.09], hair, 'head');
      if (st === 'beanie') { add('cap', [0, 0.376, -0.008], [0.222, 0.17, 0.218], [0x1f4e79, 0x58a6a6, 0xe8c547, 0x6f7fb5][Math.floor(rnd() * 4)], 'head', [-0.2, 0, 0]); add('eye', [0, 0.465, -0.02], [0.045, 0.045, 0.045], 0xfff8f0, 'head'); } }
    add('eye', [-0.036, 0.348, 0.081], [0.021, 0.027, 0.012], 0x221c1c, 'head'); add('eye', [0.036, 0.348, 0.081], [0.021, 0.027, 0.012], 0x221c1c, 'head');
    add('eye', [-0.056, 0.324, 0.074], [0.024, 0.013, 0.008], 0xf2a1a1, 'head'); add('eye', [0.056, 0.324, 0.074], [0.024, 0.013, 0.008], 0xf2a1a1, 'head');
    add('eye', [-0.03, 0.356, 0.089], [0.008, 0.008, 0.004], 0xffffff, 'head'); add('eye', [0.042, 0.356, 0.089], [0.008, 0.008, 0.004], 0xffffff, 'head');
    add('box', [0, 0.318, 0.084], [0.026, 0.007, 0.004], 0x8a4a3a, 'head');
    // 隨身物：背包、肩背包、提袋
    if (!H) { const r2 = rnd(); const bagC = [0xdb6a41, 0x58a6a6, 0xe8c547, 0x6f7fb5, 0x3b3a42][Math.floor(rnd() * 5)];
      if (r2 < 0.18 && role !== 'child' && role !== 'hiker' && role !== 'youth') add('box', [0, 0.215, -0.072], [0.1, 0.12, 0.05], bagC);
      else if (r2 < 0.36) { add('box', [0.083, 0.165, 0.01], [0.028, 0.07, 0.075], bagC); add('box', [0.01, 0.235, 0.005], [0.15, 0.01, 0.012], bagC, null, [0, 0, -0.75]); }
      else if (r2 < 0.5) { add('box', [-0.085, 0.1, 0.02], [0.045, 0.06, 0.03], 0xfbfbf7, 'armL'); add('box', [-0.085, 0.14, 0.02], [0.004, 0.025, 0.02], bagC, 'armL'); } }
    switch (role) {
      case 'elder': add('cyl', [0.105, 0.09, 0.03], [0.012, 0.18, 0.012], 0x7a5a3e, 'armR'); add('box', [0, 0.21, 0], [0.13, 0.1, 0.11], 0x9c8a6a); break;
      case 'child': add('cap', [0, 0.39, 0], [0.205, 0.13, 0.195], 0xf1c232, 'head'); add('cyl', [0, 0.39, 0.02], [0.24, 0.008, 0.22], 0xf1c232, 'head'); add('box', [0, 0.2, -0.07], [0.1, 0.11, 0.05], [colors.coral, 0x4a6fa5, 0x58a6a6][Math.floor(rnd() * 3)]); break;
      case 'parent': add('box', [0, 0.1, 0.25], [0.1, 0.08, 0.14], 0x6f7fb5, 'stroller'); add('cap', [0, 0.14, 0.21], [0.11, 0.1, 0.1], 0x58a6a6, 'stroller'); for (const [dx, dz] of [[-0.05, 0.2], [0.05, 0.2], [-0.05, 0.3], [0.05, 0.3]]) add('torus', [dx, 0.03, dz], [0.045, 0.045, 0.045], 0x2a2524, 'wheelX'); add('box', [0, 0.17, 0.13], [0.008, 0.008, 0.12], 0x2a2524, 'stroller', [0.6, 0, 0]); break;
      case 'cyclist': add('cap', [0, 0.385, 0], [0.205, 0.12, 0.195], 0xe8c547, 'head'); break;
      case 'hiker': add('box', [0, 0.21, -0.075], [0.11, 0.14, 0.07], 0xc06a3a); add('cyl', [0.1, 0.12, 0.03], [0.01, 0.24, 0.01], 0x4a4a52, 'armR'); add('cap', [0, 0.39, 0], [0.214, 0.1, 0.206], 0x58a6a6, 'head'); break;
      case 'worker': add('cap', [0, 0.395, 0], [0.21, 0.13, 0.2], 0xf1c232, 'head'); add('cyl', [0, 0.39, 0.02], [0.22, 0.01, 0.22], 0xf1c232, 'head'); break;
      case 'officeworker': add('cyl', [0.1, 0.16, 0.03], [0.03, 0.05, 0.03], 0xf6efe3, 'armR'); break;
      case 'youth': add('box', [0, 0.21, -0.07], [0.1, 0.1, 0.05], 0x2f3a46); break;
    }
    if (H) { const c = H.color;
      add('box', [0, 0.205, 0.004], [0.13, 0.112, 0.106], c); add('box', [0, 0.235, 0.058], [0.03, 0.05, 0.004], 0xfbfbf7);
      if (heroCls === 'chief') { add('cap', [0, 0.39, 0], [0.205, 0.12, 0.195], c, 'head'); add('cyl', [0, 0.39, 0.06], [0.2, 0.008, 0.12], c, 'head'); add('cone', [0.1, 0.2, 0.1], [0.07, 0.1, 0.07], 0xfbfbf7, 'armR', [1.4, 0, 0]); }
      if (heroCls === 'driver') { add('cap', [0, 0.39, 0], [0.205, 0.11, 0.195], c, 'head'); add('cyl', [0, 0.385, 0.08], [0.18, 0.008, 0.1], 0x211d1d, 'head'); add('torus', [0, 0.19, 0.1], [0.11, 0.11, 0.11], 0x211d1d, 'carry', [0.5, 0, 0]); add('box', [0.04, 0.24, 0.056], [0.03, 0.02, 0.004], 0xe8c547); }
      if (heroCls === 'fireman') { add('cap', [0, 0.385, 0], [0.22, 0.15, 0.21], c, 'head'); add('cyl', [0, 0.36, 0.02], [0.25, 0.012, 0.24], c, 'head'); add('box', [0, 0.41, 0], [0.03, 0.06, 0.16], 0xe8c547, 'head'); add('cyl', [0, 0.19, 0.11], [0.03, 0.2, 0.03], 0x3b3a42, 'carry', [Math.PI / 2, 0, 0]); add('cyl', [0, 0.19, 0.22], [0.045, 0.04, 0.045], 0xbfbfc4, 'carry', [Math.PI / 2, 0, 0]); }
      if (heroCls === 'volunteer') { add('box', [0, 0.17, 0.05], [0.11, 0.15, 0.03], 0xf2b5c6); add('box', [0, 0.17, 0.11], [0.1, 0.07, 0.08], 0xb48560, 'carry'); add('torus', [0, 0.23, 0.11], [0.08, 0.08, 0.08], 0x7a5a3a, 'carry', [0, Math.PI / 2, 0]); add('eye', [0.02, 0.22, 0.12], [0.05, 0.04, 0.05], 0x8fb37a, 'carry'); }
      if (heroCls === 'engineer') { for (const dx of [-0.038, 0.038]) add('torus', [dx, 0.35, 0.09], [0.05, 0.044, 0.05], 0x211d1d, 'head'); add('box', [0, 0.352, 0.091], [0.026, 0.006, 0.006], 0x211d1d, 'head'); add('box', [0, 0.19, 0.1], [0.1, 0.012, 0.07], 0x211d1d, 'carry'); add('box', [0, 0.23, 0.065], [0.1, 0.07, 0.008], 0x211d1d, 'carry', [-0.3, 0, 0]); add('box', [0, 0.23, 0.07], [0.085, 0.055, 0.004], 0x86a8a6, 'carry', [-0.3, 0, 0]); } }
    // 頭身比：頭部零件以頭心放大並上移（Q 版大頭）
    const HC = 0.345, HK = 1.28, HUP = 0.032;
    for (const pt of P) if (pt.ch === 'head') { pt.off = [pt.off[0] * HK, HC + (pt.off[1] - HC) * HK + HUP, pt.off[2] * HK]; pt.s = pt.s.map(v => v * HK); }
    return { parts: P, scale: sc };
  }
  // ── 實例化 ──
  let meshes = {}, shadowMesh = null, people = [];
  function build(list) { dispose(); people = list; const counts = {}; for (const w of people) for (const pt of w.plan.parts) counts[pt.g] = (counts[pt.g] || 0) + 1;
    for (const [g, n] of Object.entries(counts)) { const m = new THREE.InstancedMesh(G[g], mat, n); m.instanceMatrix.setUsage(THREE.DynamicDrawUsage); m.castShadow = g !== 'eye'; m.frustumCulled = false; scene.add(m); meshes[g] = { mesh: m, n: 0 }; }
    shadowMesh = new THREE.InstancedMesh(G.shadow, shadowMat, people.length); shadowMesh.frustumCulled = false; shadowMesh.renderOrder = 1; scene.add(shadowMesh);
    for (const w of people) for (const pt of w.plan.parts) { const slot = meshes[pt.g]; pt.idx = slot.n++; slot.mesh.setColorAt(pt.idx, new THREE.Color(pt.c)); pt.L0 = null; }
    for (const s of Object.values(meshes)) s.mesh.instanceColor.needsUpdate = true; }
  function dispose() { for (const s of Object.values(meshes)) scene.remove(s.mesh); if (shadowMesh) scene.remove(shadowMesh); meshes = {}; shadowMesh = null; people = []; }
  const dummy = new THREE.Object3D(), base = new THREE.Matrix4(), local = new THREE.Matrix4(), tmp = new THREE.Matrix4(), piv = new THREE.Matrix4(), unpiv = new THREE.Matrix4(), rot = new THREE.Matrix4();
  const pivots = { legL: 0.13, legR: 0.13, armL: 0.255, armR: 0.255, head: 0.27 };
  function update(dt, time, zoom = 1) {
    for (const [pi, w] of people.entries()) {
      const p = w.pos; const moving = w.moving ? 1 : 0; const ph = (time * 9 + w.phase * 20) * (moving || (w.pose === 'fight' ? 1 : 0)); const bob = moving ? Math.abs(Math.sin(ph)) * 0.012 : 0; const sc = w.plan.scale * 0.8 * zoom;
      const lift = w.pose === 'wave' && w.hero ? 0.0 : 0;
      dummy.position.set(p.x, p.y + bob + lift, p.z); dummy.rotation.set(0, w.yaw, 0); dummy.scale.setScalar(sc); dummy.updateMatrix(); base.copy(dummy.matrix);
      for (const pt of w.plan.parts) { let ang = 0, axis = 'x';
        if (moving) { if (pt.ch === 'legL') ang = Math.sin(ph) * 0.55; if (pt.ch === 'legR') ang = -Math.sin(ph) * 0.55; if (pt.ch === 'armL') ang = -Math.sin(ph) * 0.45; if (pt.ch === 'armR') ang = Math.sin(ph) * 0.45; }
        if (pt.ch === 'head') ang = Math.sin(time * 1.3 + w.phase * 9) * 0.06;
        if (w.pose === 'fight') { if (pt.ch === 'armR') { axis = 'z'; ang = 2.2 + Math.sin(time * 14 + w.phase * 9) * 0.6; } if (pt.ch === 'armL') ang = -0.8; }
        if (w.pose === 'wave' && pt.ch === 'armR') { axis = 'z'; ang = 2.5 + Math.sin(time * 5 + w.phase * 9) * 0.35; }
        if (!pt.L0) pt.L0 = new THREE.Matrix4().compose(new THREE.Vector3(...pt.off), pt.r ? new THREE.Quaternion().setFromEuler(new THREE.Euler(...pt.r)) : new THREE.Quaternion(), new THREE.Vector3(...pt.s));
        local.copy(pt.L0); if (pt.ch === 'wheelX') local.multiply(rot.makeRotationY(Math.PI / 2));
        if (ang && pivots[pt.ch] !== undefined) { const py = pivots[pt.ch]; const px = axis === 'z' && (pt.ch === 'armL' || pt.ch === 'armR') ? (pt.ch === 'armL' ? -0.077 : 0.077) : 0; piv.makeTranslation(px, py, 0); unpiv.makeTranslation(-px, -py, 0); if (axis === 'z') rot.makeRotationZ(ang); else rot.makeRotationX(ang); tmp.multiplyMatrices(piv, rot).multiply(unpiv).multiply(local); local.copy(tmp); }
        tmp.multiplyMatrices(base, local); meshes[pt.g].mesh.setMatrixAt(pt.idx, tmp); }
      dummy.position.set(p.x, p.y + 0.02, p.z); dummy.rotation.set(0, 0, 0); dummy.scale.setScalar(0.2 * sc); dummy.updateMatrix(); shadowMesh.setMatrixAt(pi, dummy.matrix);
      if (w.sprite) { w.sprite.position.set(p.x, p.y + 0.66 * sc + bob, p.z); w.sprite.scale.set(w.sprite.userData.w0 * zoom, w.sprite.userData.h0 * zoom, 1); }
    }
    for (const s of Object.values(meshes)) s.mesh.instanceMatrix.needsUpdate = true; if (shadowMesh) shadowMesh.instanceMatrix.needsUpdate = true;
  }
  function makeWalker(role, pos, heroCls = null) { return { role, hero: !!heroCls, plan: plan(role, heroCls), pos: pos.clone(), yaw: rnd() * 6.28, moving: false, pose: null, phase: rnd() }; }
  return { makeWalker, build, dispose, update, get people() { return people; }, reseed: (s) => { rnd = mulberry(s); } };
}
