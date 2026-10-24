/* 進入點：建場景、島、建築、居民、動物、遊戲與 UI；主迴圈。 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CONFIG as C } from './config.js';
import { createEnvironment } from './env.js';
import { buildIslands, buildGraph, slotLayout, TOP, INNER, R, RING_R, hexVertex, dirOf } from './islands.js';
import { createKit, createModels } from './buildings.js';
import { createPeople } from './people.js';
import { createAnimals } from './animals.js';
import { createGame } from './game.js';
import { createUI } from './ui.js';
import { mulberry } from './env.js';
const stage = document.getElementById('stage');
const quality = (navigator.hardwareConcurrency || 4) >= 6 && !/Mobi|Android/i.test(navigator.userAgent) ? 'high' : 'medium';
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, quality === 'high' ? 1.75 : 1.25)); renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.0;
stage.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(32, 1, 0.3, 320);
const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.dampingFactor = 0.08; controls.minPolarAngle = THREE.MathUtils.degToRad(25); controls.maxPolarAngle = THREE.MathUtils.degToRad(70); controls.minDistance = 4; controls.maxDistance = 70; controls.enablePan = true; controls.screenSpacePanning = false;
const colors = C.palette;
// 島與建築
const kit = createKit(TOP);
const { centers, neighbors, views, tops } = buildIslands(scene, C.islands, kit, colors);
const center = new THREE.Vector3(); for (const c of centers.values()) center.add(c); center.divideScalar(centers.size); center.y = TOP;
const model = createModels(kit, colors); const rnd = mulberry(5);
// 每座島固定 8 個槽位：前 5 個給政策建築（動態），後 3 個放小房子（靜態）
const POLICY_SLOTS = 5, LAYOUT = new Map();
const TREE_STYLE = { forest: 'conifer', garden: 'blossom', softpad: 'topiary', boardwalk: 'palm', rubber: 'palm', street: 'poplar', courtyard: 'blossom', meadow: 'willow', lawn: 'round', granite: 'topiary', terracotta: 'round', schoolyard: 'round', gravel: 'round' };
const TRIM = { granite: 'wall', lawn: 'hedge', gravel: 'hoarding', street: 'railing', schoolyard: 'railing', forest: 'logs', softpad: 'picket', terracotta: 'wall', garden: 'hedge', meadow: 'railing', boardwalk: 'rope', rubber: 'picket', courtyard: 'wall' };
for (const isl of C.islands) { const c = centers.get(isl.id); const lay = slotLayout(8, 1.18); LAYOUT.set(isl.id, lay); const style = TREE_STYLE[isl.ground] || 'round'; const occ = [[0, lay.heroZ, 0.95], ...lay.slots.map(([x, z]) => [x, z, lay.size * 0.55])];
  const free = (x, z, r) => occ.every(([ox, oz, orr]) => Math.hypot(x - ox, z - oz) > r + orr);
  model(isl.landmark, c.x, c.z + lay.heroZ, 1.18, isl.number);
  lay.slots.slice(POLICY_SLOTS).forEach(([sx, sz], i) => model(i % 2 ? 'house2' : 'house', c.x + sx, c.z + sz, lay.size * 1.05, isl.number * 3 + i));
  // 島緣：沒有橋的邊放圍欄／樹籬／矮牆
  const nb = neighbors.get(isl.id); const bridgeDirs = nb.filter(n => n.id).map(n => dirOf(n.dq, n.dr)); const trim = TRIM[isl.ground];
  for (let k = 0; k < 6; k++) { const [ax, az] = hexVertex(0, 0, RING_R + 0.3, k), [bx, bz] = hexVertex(0, 0, RING_R + 0.3, k + 1); const mx = (ax + bx) / 2, mz = (az + bz) / 2, ml = Math.hypot(mx, mz); if (bridgeDirs.some(([dx, dz]) => (dx * mx + dz * mz) / ml > 0.95)) continue; const yaw = Math.atan2(bx - ax, bz - az), len = Math.hypot(bx - ax, bz - az);
    for (let s = 0; s <= 8; s++) { const t = 0.08 + (s / 8) * 0.84; const x = c.x + ax + (bx - ax) * t, z = c.z + az + (bz - az) * t;
      if (trim === 'hedge') kit.part('leaf', [x, TOP + 0.1, z], [0.22, 0.18, 0.22], s % 2 ? 0x9cc27a : 0x8db86f);
      else if (trim === 'wall') { if (s % 2 === 0) kit.hbox(x, TOP + 0.08, z, 0.08, 0.16, 0.08, 0xd9cbb4); }
      else if (trim === 'logs') { if (s % 2 === 0) kit.cyl(x, TOP + 0.09, z, 0.06, 0.18, 0x7a5c42); }
      else if (trim === 'picket') kit.hbox(x, TOP + 0.08, z, 0.025, 0.16, 0.025, [0xf2b5c6, 0xfff8f4, 0x9fc4e6][s % 3]);
      else kit.hbox(x, TOP + 0.09, z, 0.022, 0.18, 0.022, trim === 'rope' ? 0x9a7a5a : 0xfff8f4); }
    if (trim === 'wall') kit.hbox(c.x + mx, TOP + 0.08, c.z + mz, 0.06, 0.14, len * 0.84, 0xe4d6c3, null, [0, yaw, 0]);
    else if (trim === 'railing' || trim === 'picket') kit.hbox(c.x + mx, TOP + 0.16, c.z + mz, 0.015, 0.015, len * 0.84, trim === 'picket' ? 0xfff8f4 : 0x1f4e79, null, [0, yaw, 0]);
    else if (trim === 'rope') kit.hbox(c.x + mx, TOP + 0.14, c.z + mz, 0.012, 0.012, len * 0.84, 0x7a5c42, null, [0, yaw, 0]);
    else if (trim === 'hoarding') kit.hbox(c.x + mx, TOP + 0.12, c.z + mz, 0.03, 0.24, len * 0.84, k % 2 ? 0x1f4e79 : 0xf2efe8, null, [0, yaw, 0]); }
  // 散景：樹、灌木、花、長椅（有人坐）、單車架、垃圾桶、花盆、鵝卵石、小攤
  const MIX = ['tree', 'bush', 'flowers', 'bench', 'tree', 'pot', 'bikerack', 'bin', 'flowers', 'hedge', 'tree', 'pebble', 'stand', 'bush'];
  let placed = 0; for (let tries = 0; tries < 160 && placed < 16; tries++) { const lx = (rnd() - 0.5) * 2.7, lz = (rnd() - 0.5) * 2.3; const t = MIX[placed % MIX.length]; const r = t === 'tree' ? 0.22 : t === 'bench' || t === 'stand' ? 0.2 : 0.1; if (Math.hypot(lx, lz) > INNER - 0.12 || Math.abs(lx) * 0.5 + Math.abs(lz) * 0.866 > INNER - 0.12 || !free(lx, lz, r)) continue; occ.push([lx, lz, r]);
    if (t === 'tree') kit.tree(c.x + lx, c.z + lz, 0.3 + rnd() * 0.22, placed + isl.number, style); else model(t, c.x + lx, c.z + lz, 0.5, placed + isl.number); placed++; }
  // 彩旗：夜市與老街風格的島，在路燈間掛旗
  if (['boardwalk', 'courtyard', 'rubber'].includes(isl.ground)) for (let k = 0; k < 6; k++) { const [ax, az] = hexVertex(c.x, c.z, RING_R + 0.3, k), [bx, bz] = hexVertex(c.x, c.z, RING_R + 0.3, k + 1); for (let s = 1; s < 9; s++) { const t = s / 9; kit.hbox(ax + (bx - ax) * t, TOP + 0.5 - Math.sin(t * Math.PI) * 0.06, az + (bz - az) * t, 0.05, 0.07, 0.01, [0xdb6a41, 0xe8c547, 0x58a6a6, 0xf2a1a1][s % 4], null, [0, Math.atan2(bx - ax, bz - az), 0]); } kit.hbox((ax + bx) / 2, TOP + 0.47, (az + bz) / 2, 0.008, 0.008, Math.hypot(bx - ax, bz - az), 0x211d1d, null, [0, Math.atan2(bx - ax, bz - az), 0]); } }
kit.build(scene);
const env = createEnvironment(scene, { center, quality }); env.setTime(720);
const graph = buildGraph(C.islands, centers, neighbors);
// 政策建築：有指定行政區就放該區，沒有就依 id 雜湊隨機落島；清單改變時整批重建
const pkit = createKit(TOP); const pmodel = createModels(pkit, colors); let policyMeshes = [], policyById = new Map(), policyIsland = new Map();
const hash = s => { let h = 2166136261; for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0; return h; };
function placePolicies(list) { pkit.dispose(scene); policyById = new Map(); policyIsland = new Map(); const used = new Map(C.islands.map(i => [i.id, 0])); const ids = C.islands.map(i => i.id);
  const pick = (pol) => { let idx = pol.district && ids.includes(pol.district) ? ids.indexOf(pol.district) : hash(pol.id) % ids.length; for (let k = 0; k < ids.length; k++) { const id = ids[(idx + k) % ids.length]; if (used.get(id) < POLICY_SLOTS) return id; } return ids[idx]; };
  for (const pol of list) { const islId = pick(pol); const n = used.get(islId); used.set(islId, n + 1); const c = centers.get(islId), lay = LAYOUT.get(islId); const [sx, sz] = lay.slots[Math.min(n, POLICY_SLOTS - 1)]; pkit.owner = pol.id; pmodel(pol.model || 'kiosk', c.x + sx, c.z + sz, lay.size * 1.08, hash(pol.id) % 7); policyById.set(pol.id, pol); policyIsland.set(pol.id, islId); }
  pkit.owner = null; policyMeshes = pkit.build(scene); }
placePolicies(C.policies);
const people = createPeople(scene, colors, C.heroes); const animals = createAnimals(scene, C.animals);
// 島名與登陸方向：螢幕固定大小的名牌（DOM），每幀投影
const labelLayer = document.getElementById('labels'); const labels = [];
function addLabel(txt, pos, cls) { const el = document.createElement('div'); el.className = 'nameplate ' + (cls || ''); el.textContent = txt; labelLayer.appendChild(el); labels.push({ el, pos: pos.clone(), v: new THREE.Vector3() }); }
for (const isl of C.islands) { const c = centers.get(isl.id); addLabel(isl.goal ? '🏛 市政府' : `${isl.number}・${isl.name}`, c.clone().setY(TOP + (isl.goal ? 2.4 : 1.7)), isl.goal ? 'goal' : ''); }
for (const l of Object.values(C.landings)) { const lc = centers.get(l.island); const away = lc.clone().sub(center).setY(0).normalize(); addLabel(`${l.name} →`, lc.clone().add(away.multiplyScalar(4.2)).setY(TOP + 0.3), 'river'); }
function updateLabels() { const w = stage.clientWidth, h = stage.clientHeight; for (const L of labels) { L.v.copy(L.pos).project(camera); const vis = L.v.z < 1 && Math.abs(L.v.x) < 1.2 && Math.abs(L.v.y) < 1.2; L.el.style.display = vis ? '' : 'none'; if (vis) L.el.style.transform = `translate(-50%,-100%) translate(${((L.v.x + 1) / 2 * w).toFixed(1)}px,${((1 - L.v.y) / 2 * h).toFixed(1)}px)`; } }
// 遊戲
const callbacks = {}; const game = createGame({ scene, graph, centers, people, animals, islands: C.islands, colors, callbacks });
people.build(game.spawnResidents());
// 相機
function fit() { const w = stage.clientWidth, h = stage.clientHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
fit(); window.addEventListener('resize', fit);
const narrow = () => stage.clientWidth < 700;
function overview(instant) { controls.target.copy(center); const d = narrow() ? 44 : 32; const pol = THREE.MathUtils.degToRad(50); const to = new THREE.Vector3(center.x + Math.sin(0.08) * Math.sin(pol) * d, center.y + Math.cos(pol) * d, center.z + Math.cos(0.08) * Math.sin(pol) * d); if (instant) camera.position.copy(to); else fly = { from: camera.position.clone(), to, t: 0 }; }
let fly = null; overview(true);
// 點擊
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(); let down = null;
renderer.domElement.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY }; });
renderer.domElement.addEventListener('pointerup', e => { if (!down) return; const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y); down = null; if (moved > 6) return; const r = renderer.domElement.getBoundingClientRect(); ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(ndc, camera); const eh = ray.intersectObjects(game.enemyHits, false); if (eh.length) return game.onPick({ enemy: eh[0].object.userData.enemy }); const ph = ray.intersectObjects(policyMeshes, false); if (ph.length && ph[0].object.userData.owners) { const id = ph[0].object.userData.owners[ph[0].instanceId]; if (id && policyById.has(id)) { callbacks.card && callbacks.card(policyById.get(id), policyIsland.get(id)); return; } } const th = ray.intersectObjects(tops, false); if (th.length) game.onPick({ island: th[0].object.userData.island, point: th[0].point }); });
// UI
function flyTo(islandId) { const c = centers.get(islandId); if (!c) return; controls.target.copy(c); const pol = THREE.MathUtils.degToRad(48), d = narrow() ? 13 : 10; fly = { from: camera.position.clone(), to: new THREE.Vector3(c.x + Math.sin(0.3) * Math.sin(pol) * d, c.y + Math.cos(pol) * d, c.z + Math.cos(0.3) * Math.sin(pol) * d), t: 0 }; }
const ui = createUI({ game, callbacks, overview: () => overview(false), flyTo, placePolicies, renderer });
// 迴圈
let last = performance.now(), acc = 0; const G = game.G;
function frame(now) { const raw = Math.min(0.05, (now - last) / 1000); last = now; const t = now / 1000;
  if (!G.paused) { const dt = raw * G.speed; const sub = Math.ceil(dt / 0.025); for (let i = 0; i < sub; i++) game.step(dt / sub); }
  people.update(raw, t); env.update(raw);
  env.setTime(G.phase === 'round' ? 360 + (G.t / C.round.seconds) * 1080 : 720);
  if (fly) { fly.t = Math.min(1, fly.t + raw * 1.2); const k = fly.t * fly.t * (3 - 2 * fly.t); camera.position.lerpVectors(fly.from, fly.to, k); if (fly.t >= 1) fly = null; }
  controls.update(); renderer.render(scene, camera); updateLabels();
  acc += raw; if (acc > 0.2) { acc = 0; ui.tick(); }
  requestAnimationFrame(frame); }
requestAnimationFrame(frame);
window.PI = { game, people, scene, camera, controls, graph, centers, get policies() { return policyById; }, get policyIsland() { return policyIsland; }, pickPolicy: id => callbacks.card(policyById.get(id), policyIsland.get(id)) }; // 除錯用
