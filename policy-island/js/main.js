/* 進入點：建場景、島、建築、居民、動物、遊戲與 UI；主迴圈。 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CONFIG as C } from './config.js';
import { createEnvironment } from './env.js';
import { buildIslands, buildGraph, slotLayout, TOP, INNER } from './islands.js';
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
for (const isl of C.islands) { const c = centers.get(isl.id); const lay = slotLayout(8, 1.18); LAYOUT.set(isl.id, lay); model(isl.landmark, c.x, c.z + lay.heroZ, 1.18, isl.number);
  lay.slots.slice(POLICY_SLOTS).forEach(([sx, sz], i) => model(i % 2 ? 'house2' : 'house', c.x + sx, c.z + sz, lay.size * 1.05, isl.number * 3 + i));
  for (let k = 0; k < 10; k++) { const a = rnd() * Math.PI * 2, r = INNER - 0.05 - rnd() * 0.12; const x = c.x + Math.cos(a) * r, z = c.z + Math.sin(a) * r; const kinds = ['bush', 'bush', 'flowers', 'pot', 'bin', 'bush']; model(kinds[Math.floor(rnd() * kinds.length)], x, z, 0.5, k + isl.number); }
  for (let k = 0; k < 3; k++) { const a = rnd() * Math.PI * 2, r = INNER - 0.25 - rnd() * 0.15; const x = c.x + Math.cos(a) * r, z = c.z + Math.sin(a) * r; if (Math.abs(z - c.z - lay.heroZ) < 0.8 && Math.abs(x - c.x) < 0.9) continue; kit.tree(x, z, 0.3 + rnd() * 0.2, k + isl.number); } }
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
