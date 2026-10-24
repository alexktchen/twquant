/* 天空、光、水、台北盆地地形、雲、晝夜。純裝飾。做法參考原站 environment.js，重新實作。 */
import * as THREE from 'three';
import { SimplexNoise } from 'three/addons/math/SimplexNoise.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
export const WATER_Y = -0.18;
export function mulberry(a) { return function () { let t = (a += 0x6d2b79f5); t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const fbm = (n, x, z, o) => { let s = 0, a = 0.5, f = 1; for (let i = 0; i < o; i++) { s += a * n.noise(x * f, z * f); f *= 2; a *= 0.5; } return s * 0.5 + 0.5; };
// [分鐘, 天頂, 地平線, 太陽色, 太陽強度, 半球天, 半球地, 半球強度, 夜, 水色]
const KEYS = [
  [0, 0x1e2c58, 0x3f4f80, 0xb4c2ff, 1.15, 0x94a2d8, 0x64566a, 1.85, 1, 0x5a88b2],
  [360, 0x7f8fc4, 0xf6c3a6, 0xffb48a, 1.3, 0xf3c6b5, 0x6b5a5a, 1.1, 0.55, 0x8fb7c4],
  [450, 0xa9d1ea, 0xfbe6d3, 0xffe2c4, 2.4, 0xeaf2f6, 0x8a7a68, 1.25, 0, 0x9fcfd6],
  [720, 0x9fd0ec, 0xf4eee3, 0xfff1de, 2.9, 0xeef6fa, 0x8f8270, 1.35, 0, 0xa9d8db],
  [960, 0xa6cfe6, 0xf8e7d2, 0xffe3c0, 2.5, 0xf1efe8, 0x8a7a66, 1.25, 0, 0x9fcbd2],
  [1065, 0x8b8fc4, 0xf7b98f, 0xff9f6a, 1.8, 0xf6d2bc, 0x7d6660, 1.2, 0.3, 0x93a9bd],
  [1140, 0x5a5e9e, 0xd99aa6, 0xff9a7a, 1.3, 0xc0b2e0, 0x655868, 1.35, 0.75, 0x6a7cab],
  [1260, 0x22325f, 0x445488, 0xc0ccff, 1.2, 0x98a8dc, 0x685a6e, 1.9, 1, 0x5a88b2],
  [1440, 0x1e2c58, 0x3f4f80, 0xb4c2ff, 1.15, 0x94a2d8, 0x64566a, 1.85, 1, 0x5a88b2],
];
export function createEnvironment(scene, { center, quality = 'medium' }) {
  const noise = new SimplexNoise({ random: mulberry(7) }); const cx = center.x, cz = center.z;
  const env = { night: 0 };
  const skyMat = new THREE.ShaderMaterial({ side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { uTop: { value: new THREE.Color() }, uHorizon: { value: new THREE.Color() }, uSunDir: { value: new THREE.Vector3(0, 1, 0) }, uSun: { value: new THREE.Color() }, uNight: { value: 0 } },
    vertexShader: `varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
    fragmentShader: `uniform vec3 uTop,uHorizon,uSun,uSunDir; uniform float uNight; varying vec3 vDir;
      float hash(vec3 p){ return fract(sin(dot(p, vec3(12.9898,78.233,37.719)))*43758.5453); }
      void main(){ float h = clamp(vDir.y, -0.2, 1.0); vec3 col = mix(uHorizon, uTop, smoothstep(-0.02, 0.55, h));
        float sd = max(dot(normalize(vDir), normalize(uSunDir)), 0.0); col += uSun * pow(sd, 18.0) * 0.35 * (1.0 - uNight*0.6);
        float star = step(0.9965, hash(floor(vDir*180.0))) * smoothstep(0.08, 0.5, h) * uNight; col += vec3(star*0.8);
        gl_FragColor = vec4(col, 1.0); }` });
  const sky = new THREE.Mesh(new THREE.SphereGeometry(170, 32, 16), skyMat); sky.position.set(cx, 0, cz); sky.renderOrder = -10; scene.add(sky);
  scene.fog = new THREE.Fog(0xf4eee3, 42, 150);
  const hemi = new THREE.HemisphereLight(0xeef6fa, 0x8f8270, 1.3); scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xfff1de, 2.8); sun.castShadow = true; const sm = quality === 'high' ? 2048 : 1024; sun.shadow.mapSize.set(sm, sm);
  Object.assign(sun.shadow.camera, { left: -20, right: 20, top: 22, bottom: -22, near: 1, far: 90 }); sun.shadow.bias = -0.0006; sun.shadow.normalBias = 0.045; sun.shadow.radius = 3;
  sun.target.position.set(cx, 0, cz); scene.add(sun, sun.target);
  const fill = new THREE.DirectionalLight(0xbfd6ff, 0.35); fill.position.set(cx + 20, 18, cz - 25); scene.add(fill);
  // 水
  const waterMat = new THREE.ShaderMaterial({ fog: true, uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uColor: { value: new THREE.Color(0xa4d4da) }, uDeep: { value: new THREE.Color(0x6fa9b8) }, uTime: { value: 0 }, uNight: { value: 0 }, uCenter: { value: new THREE.Vector2(cx, cz) } }]),
    vertexShader: `#include <fog_pars_vertex>
      varying vec3 vW; void main(){ vec4 w = modelMatrix*vec4(position,1.0); vW = w.xyz; vec4 mvPosition = viewMatrix*w; gl_Position = projectionMatrix*mvPosition;
      #include <fog_vertex>
      }`,
    fragmentShader: `#include <fog_pars_fragment>
      uniform vec3 uColor,uDeep; uniform float uTime,uNight; uniform vec2 uCenter; varying vec3 vW;
      void main(){ float d = length(vW.xz-uCenter);
        float w1 = sin(vW.x*0.35 + uTime*0.35 + sin(vW.z*0.27)) * sin(vW.z*0.31 - uTime*0.28);
        float w2 = sin((vW.x*0.6+vW.z)*1.1 + uTime*0.7) * sin(vW.x*0.9 - vW.z*0.4);
        float aa = clamp(1.0 - fwidth(w2) * 3.0, 0.0, 1.0);
        vec3 col = mix(uColor, uDeep, smoothstep(9.0, 38.0, d) * 0.5); col = mix(col, uColor * 1.06, (1.0 - smoothstep(4.0, 12.0, d)) * 0.25);
        col += w1 * 0.018 + w2 * 0.006 * aa; col += smoothstep(0.975, 1.0, w2 * 0.5 + 0.5) * 0.06 * (1.0 - uNight) * aa * (1.0 - smoothstep(18.0, 40.0, d));
        gl_FragColor = vec4(col,1.0);
        #include <fog_fragment>
      }` });
  const water = new THREE.Mesh(new THREE.CircleGeometry(160, 64), waterMat); water.rotation.x = -Math.PI / 2; water.position.set(cx, WATER_Y, cz); scene.add(water);
  // 盆地地形
  const features = [ { x: cx - 12, z: cz - 42, r: 13, h: 8, shape: 'dome' }, { x: cx + 6, z: cz - 42, r: 16, h: 7, shape: 'ridge' }, { x: cx - 26, z: cz - 32, r: 12, h: 8, shape: 'ridge' }, { x: cx - 37, z: cz - 6, r: 9, h: 8.5, shape: 'dome' }, { x: cx - 40, z: cz + 3, r: 8, h: 7, shape: 'dome' }, { x: cx - 35, z: cz + 11, r: 7, h: 5.5, shape: 'dome' }, { x: cx + 31, z: cz - 13, r: 11, h: 6, shape: 'ridge' }, { x: cx + 36, z: cz + 5, r: 10, h: 5, shape: 'ridge' }, { x: cx + 27, z: cz + 26, r: 14, h: 5.5, shape: 'terrace' }, { x: cx - 5, z: cz + 46, r: 18, h: 5, shape: 'ridge' } ];
  const size = 170, seg = quality === 'low' ? 110 : 160, K = 2 / 3;
  const heightAt = (wx, wz) => { const x = cx + (wx - cx) / K, z = cz + (wz - cz) / K; const d = Math.hypot(x - cx, (z - cz) * 0.92); const rim = smooth(25, 50, d);
    let h = -1.6 + rim * (3.5 + 6 * fbm(noise, x * 0.03, z * 0.03, 4) + 2.2 * Math.abs(noise.noise(x * 0.07, z * 0.07)));
    for (const f of features) { const fd = Math.hypot(x - f.x, (z - f.z) * (f.shape === 'ridge' ? 1.6 : 1)); if (fd > f.r * 1.6) continue; let k = Math.max(0, 1 - fd / f.r); k = f.shape === 'dome' ? Math.pow(k, 1.25) : k * k * (3 - 2 * k); let fh = f.h * k * (0.85 + 0.25 * noise.noise(x * 0.11, z * 0.11)); if (f.shape === 'terrace') fh = Math.floor(fh / 0.55) * 0.55; h += fh; }
    const front = Math.max(0, Math.cos(Math.atan2(wx - cx, wz - cz) - 0.27)); if (h > 0) h *= 1 - 0.72 * Math.pow(front, 2.2); return h; };
  const tg = new THREE.PlaneGeometry(size, size, seg, seg); tg.rotateX(-Math.PI / 2); const pos = tg.attributes.position, col = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) { const x = pos.getX(i) + cx, z = pos.getZ(i) + cz; pos.setXYZ(i, x, heightAt(x, z), z); }
  tg.computeVertexNormals(); const nrm = tg.attributes.normal; const C = h => new THREE.Color(h);
  const cShore = C(0xd9c9a3), cField = C(0xaec493), cGrass = C(0xa3b986), cForest = C(0x6f8a62), cForest2 = C(0x5d7656), cRock = C(0xb2a38d), cBed = C(0x8fb3ad);
  for (let i = 0; i < pos.count; i++) { const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i), slope = 1 - nrm.getY(i); const n = noise.noise(x * 0.25, z * 0.25) * 0.5 + 0.5; let c;
    if (y < WATER_Y + 0.02) c = cBed.clone(); else if (y < WATER_Y + 0.18) c = cShore.clone().lerp(cField, smooth(WATER_Y + 0.04, WATER_Y + 0.18, y)); else if (y < 2.2) c = cField.clone().lerp(cGrass, smooth(0.2, 1.6, y) * 0.8 + n * 0.2); else if (y < 9) c = cGrass.clone().lerp(n > 0.5 ? cForest : cForest2, smooth(1.6, 4, y)); else c = cForest2.clone().lerp(cRock, smooth(9, 14, y));
    if (slope > 0.35 && y > 2.5) c.lerp(cRock, 0.3 + (slope - 0.35) * 0.8); c.offsetHSL(0, 0, (n - 0.5) * 0.05); col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b; }
  tg.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const terrain = new THREE.Mesh(tg, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, flatShading: true })); terrain.receiveShadow = true; scene.add(terrain);
  // 山上的樹
  const rnd = mulberry(11); const treeMax = quality === 'low' ? 1500 : 3000;
  const treeGeo = mergeGeometries([new THREE.ConeGeometry(0.5, 0.95, 7).translate(0, 0.72, 0), new THREE.ConeGeometry(0.38, 0.8, 7).translate(0, 1.12, 0), new THREE.ConeGeometry(0.24, 0.6, 7).translate(0, 1.45, 0)]);
  const trees = new THREE.InstancedMesh(treeGeo, new THREE.MeshStandardMaterial({ roughness: 0.9, flatShading: true }), treeMax); const dummy = new THREE.Object3D(); let nt = 0;
  const treeCols = [0x6f8a62, 0x7f9a6a, 0x88a173, 0x6a8460, 0x93aa7a].map(h => new THREE.Color(h));
  for (let k = 0; k < treeMax * 30 && nt < treeMax; k++) { const x = cx + (rnd() - 0.5) * size * 0.9, z = cz + (rnd() - 0.5) * size * 0.9; const d = Math.hypot(x - cx, (z - cz) * 0.92); if (d < 16 || d > 52) continue; const y = heightAt(x, z); if (y < 0.5 || y > 12.5) continue;
    const grove = noise.noise(x * 0.09, z * 0.09); if (rnd() > smooth(0.5, 2.0, y) * (grove > 0 ? 1 : grove > -0.3 ? 0.5 : 0.14)) continue; const s = 0.3 + rnd() * 0.32;
    dummy.position.set(x, y - 0.12, z); dummy.scale.set(s, s * (0.8 + rnd() * 0.5), s); dummy.rotation.set(0, rnd() * 6.28, 0); dummy.updateMatrix(); trees.setMatrixAt(nt, dummy.matrix); trees.setColorAt(nt++, treeCols[Math.floor(rnd() * treeCols.length)]); }
  trees.count = nt; trees.receiveShadow = true; scene.add(trees);
  // 遠山稜線
  const ridges = [];
  for (const [rad, hmin, hmax, seed, tint] of [[74 * K, 9, 17, 3, 0.3], [100 * K, 12, 23, 9, 0.55], [130 * K, 15, 30, 17, 0.78]]) { const segs = 220, p = [], idx = [];
    for (let k = 0; k <= segs; k++) { const a = (k / segs) * Math.PI * 2; const n = fbm(noise, Math.cos(a) * 3 + seed, Math.sin(a) * 3 - seed, 5); const peak = Math.pow(Math.abs(noise.noise(Math.cos(a) * 9 + seed, Math.sin(a) * 9)), 0.7); const h = hmin + (hmax - hmin) * (0.55 * n + 0.45 * peak); const x = cx + Math.cos(a) * rad, z = cz + Math.sin(a) * rad * 0.92; p.push(x, -2, z, x, h, z); if (k < segs) { const i = k * 2; idx.push(i, i + 1, i + 2, i + 1, i + 3, i + 2); } }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(p, 3)); g.setIndex(idx); const m = new THREE.MeshBasicMaterial({ color: 0x8fa6a8, fog: false, side: THREE.DoubleSide }); const mesh = new THREE.Mesh(g, m); mesh.renderOrder = -5; scene.add(mesh); ridges.push({ mat: m, tint }); }
  // 雲
  const cloudMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, emissive: 0xfff4ea, emissiveIntensity: 0.25 }); const cloudGeo = new THREE.IcosahedronGeometry(1, 2); const cr = mulberry(23); const clouds = [];
  for (let k = 0; k < 12; k++) { const g = new THREE.Group(); const n = 6 + Math.floor(cr() * 6), w = 2.4 + cr() * 2.6;
    for (let j = 0; j < n; j++) { const m = new THREE.Mesh(cloudGeo, cloudMat); const t = j / (n - 1) - 0.5; const s = (0.8 + cr() * 0.8) * (1 - Math.abs(t) * 0.9); m.position.set(t * w * 2 + (cr() - 0.5) * 0.6, s * 0.35 + cr() * 0.4, (cr() - 0.5) * 1.6); m.scale.set(s * 1.25, s * 0.9, s * 1.05); g.add(m); }
    const ang = -Math.PI * 0.95 + (k / 12) * Math.PI * 1.25 + (cr() - 0.5) * 0.3, rad = 18 + cr() * 14; g.position.set(cx + Math.cos(ang) * rad, (k % 3 === 0 ? 7.5 : k % 2 ? 9 : 11.5) + cr() * 2, cz + Math.sin(ang) * rad * 0.9 - 8); g.scale.setScalar(0.8 + cr() * 0.4); g.userData = { speed: 0.15 + cr() * 0.2, home: g.position.x }; scene.add(g); clouds.push(g); }
  const ca = new THREE.Color(), cb = new THREE.Color();
  env.setTime = (minutes) => { const min = ((minutes % 1440) + 1440) % 1440; let i = 0; while (i < KEYS.length - 2 && KEYS[i + 1][0] <= min) i++; const a = KEYS[i], b = KEYS[i + 1], t = (min - a[0]) / (b[0] - a[0]); const mix = (p, q) => ca.setHex(p).lerp(cb.setHex(q), t).clone(); const L = (j) => a[j] + (b[j] - a[j]) * t;
    const top = mix(a[1], b[1]), hor = mix(a[2], b[2]), sunC = mix(a[3], b[3]), night = L(8), waterC = mix(a[9], b[9]);
    skyMat.uniforms.uTop.value.copy(top); skyMat.uniforms.uHorizon.value.copy(hor); skyMat.uniforms.uSun.value.copy(sunC); skyMat.uniforms.uNight.value = night; scene.fog.color.copy(hor);
    hemi.color.copy(mix(a[5], b[5])); hemi.groundColor.copy(mix(a[6], b[6])); hemi.intensity = L(7); sun.color.copy(sunC); sun.intensity = L(4);
    const tq = Math.round(minutes / 10) * 10, tt = Math.min(1, Math.max(0, (tq - 360) / 720)); const az = Math.PI * (0.15 + 0.7 * tt), el = 0.35 + Math.sin(tt * Math.PI) * 0.75; const dir = new THREE.Vector3(Math.cos(az) * Math.cos(el), Math.sin(el), 0.55 * Math.cos(el) + 0.35).normalize();
    sun.position.set(cx + dir.x * 40, dir.y * 40, cz + dir.z * 40); skyMat.uniforms.uSunDir.value.copy(dir); waterMat.uniforms.uColor.value.copy(waterC); waterMat.uniforms.uDeep.value.copy(waterC).multiplyScalar(0.72); waterMat.uniforms.uNight.value = night; env.night = night;
    ridges.forEach((r, j) => r.mat.color.setHex([0x5f826c, 0x7b98a0, 0x9fb4c4][j]).lerp(hor, r.tint * 0.75).lerp(new THREE.Color(0x101830), night * 0.35));
    cloudMat.color.copy(hor).lerp(new THREE.Color(0xffffff), 0.65 - night * 0.35).lerp(new THREE.Color(0x8e9bb5), night * 0.7); cloudMat.emissive.copy(sunC).lerp(new THREE.Color(0x9aa3b4), night); cloudMat.emissiveIntensity = 0.12 + (1 - night) * 0.12; };
  env.update = (dt) => { waterMat.uniforms.uTime.value += dt; for (const g of clouds) { g.position.x += g.userData.speed * dt; if (g.position.x > g.userData.home + 18) g.position.x = g.userData.home - 18; } };
  env.sun = sun; env.hemi = hemi; env.heightAt = heightAt; return env;
}
