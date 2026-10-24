/* 六邊形政策島：島面材質、步道環、路緣、島側、淺灘與浪花、橋、行人路網。做法參考原站 world.js，重新實作。 */
import * as THREE from 'three';
import { WATER_Y, mulberry } from './env.js';
export const R = 2.02, SPACING = 2.28, TOP = 0.65, RING_R = 1.8, INNER = 1.4;
const SQ3 = Math.sqrt(3);
export const NEIGHBORS = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, -1], [-1, 1]];
export function axialToWorld(q, r) { return new THREE.Vector3(SQ3 * SPACING * (q + r / 2) - 2, TOP, 1.5 * SPACING * r - 10.26); }
export function dirOf(dq, dr) { const x = SQ3 * (dq + dr / 2), z = 1.5 * dr, l = Math.hypot(x, z); return [x / l, z / l]; }
export function hexVertex(cx, cz, r, k) { const a = Math.PI / 2 + (k * Math.PI) / 3; return [cx + Math.cos(a) * r, cz + Math.sin(a) * r]; }
export function insideHex(x, z, ri) { const ax = Math.abs(x), az = Math.abs(z); return ax <= ri && ax * 0.5 + az * 0.8660254 <= ri; }
const TONE = { granite: 0xd9cbb4, lawn: 0xb9cd98, gravel: 0xc7b08f, street: 0xcfc3b2, meadow: 0xbccf9c, terracotta: 0xdcb79a, garden: 0xb4c993, forest: 0x9fb683, rubber: 0xe2a46f, boardwalk: 0xc9a27c, ringtile: 0xf4f1ec, schoolyard: 0xc9785a, softpad: 0xb9d6e6, courtyard: 0xd9b08c };
const SHALLOW = { granite: 0xb9d9d6, lawn: 0xa9d6e0, gravel: 0xc9c4a8, street: 0x9fc7cf, schoolyard: 0xd8d4a4, forest: 0x8fb89a, softpad: 0xd9cfc6, terracotta: 0xd9c3a8, garden: 0xa9cfa8, meadow: 0x9fcfbf, boardwalk: 0x8fc0d6, rubber: 0xbfd0e6, courtyard: 0xb9c6d6 };
const RING_TINT = { granite: 0xe3d3b8, lawn: 0xbfd6df, gravel: 0xc9b89a, street: 0xb9b3ab, schoolyard: 0xf1dc9a, forest: 0xa98e6c, softpad: 0xf2d2d6, terracotta: 0xe6b796, garden: 0xdfe6cf, meadow: 0xd9866a, boardwalk: 0xc9a27c, rubber: 0x8fc6c4, courtyard: 0xe6cfb0 };
const BAND = { granite: ['blocks', '#d8ccb8', '#bfae95'], lawn: ['glass', '#9fc4c6', '#e6f0ef'], gravel: ['hoarding', '#1f4e79', '#f2efe8'], street: ['hazard', '#e8c547', '#3b3a42'], schoolyard: ['mural', '#e8c547', '#58a6a6'], forest: ['moss', '#6f8a62', '#9cc27a'], softpad: ['pastel', '#f2b5c6', '#9fc4e6'], terracotta: ['tiles', '#d08a64', '#f2d0b4'], garden: ['ivy', '#8db86f', '#b4c993'], meadow: ['stripe', '#c8775a', '#ffffff'], boardwalk: ['timber', '#9a7a5a', '#c9a27c'], rubber: ['graffiti', '#e7a383', '#58a6a6'], courtyard: ['blocks', '#c77b5c', '#e9d8c0'] };
function groundTexture(type) {
  const c = document.createElement('canvas'); c.width = c.height = 256; const g = c.getContext('2d'); const rnd = mulberry(type.length * 97 + 3);
  g.fillStyle = '#' + TONE[type].toString(16).padStart(6, '0'); g.fillRect(0, 0, 256, 256);
  const speck = (n, cols, rmin, rmax, a = 1) => { for (let i = 0; i < n; i++) { g.globalAlpha = a * (0.4 + rnd() * 0.6); g.fillStyle = cols[Math.floor(rnd() * cols.length)]; g.beginPath(); g.arc(rnd() * 256, rnd() * 256, rmin + rnd() * (rmax - rmin), 0, Math.PI * 2); g.fill(); } g.globalAlpha = 1; };
  const tiles = (sz, grout, jitter, cols) => { for (let y = 0; y < 256; y += sz) for (let x = 0; x < 256; x += sz) { g.fillStyle = cols[Math.floor(rnd() * cols.length)]; g.fillRect(x + 1, y + 1, sz - 2, sz - 2); g.globalAlpha = jitter; g.fillStyle = rnd() > 0.5 ? '#fff' : '#000'; g.fillRect(x + 1, y + 1, sz - 2, sz - 2); g.globalAlpha = 1; } g.strokeStyle = grout; g.lineWidth = 2; for (let i = 0; i <= 256; i += sz) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 256); g.moveTo(0, i); g.lineTo(256, i); g.stroke(); } };
  switch (type) {
    case 'granite': tiles(32, '#bfae95', 0.04, ['#dccfb8', '#d6c7ae', '#e0d3bd', '#d2c2a7']); speck(400, ['#b8a68b', '#efe4d2'], 0.4, 1.1, 0.5); break;
    case 'street': tiles(21, '#b3a796', 0.03, ['#d0c4b3', '#c8bba8', '#d6cbbb']); break;
    case 'schoolyard': g.strokeStyle = '#f6eee6'; g.lineWidth = 3; for (let y = 16; y < 256; y += 32) { g.beginPath(); g.moveTo(0, y); g.lineTo(256, y); g.stroke(); } speck(900, ['#b86c50', '#d68a6a'], 0.4, 1.0, 0.6); break;
    case 'softpad': for (let y = 0; y < 256; y += 32) for (let x = 0; x < 256; x += 32) { g.fillStyle = ['#b9d6e6', '#f2c4d0', '#f6e3a6', '#c4e2c4'][((x + y) / 32) % 4]; g.fillRect(x + 1, y + 1, 30, 30); } break;
    case 'ringtile': tiles(18, '#c9c2b8', 0.035, ['#f6f3ee', '#efebe4', '#faf8f4']); break;
    case 'terracotta': tiles(26, '#b98d6f', 0.05, ['#dcb79a', '#d4ab8c', '#e1c0a4']); break;
    case 'courtyard': tiles(30, '#b58a68', 0.04, ['#d9b08c', '#d2a682', '#e0b995']); break;
    case 'boardwalk': for (let y = 0; y < 256; y += 16) { g.fillStyle = ['#c9a27c', '#bf9771', '#d1ab86'][Math.floor(rnd() * 3)]; g.fillRect(0, y + 1, 256, 14); g.fillStyle = '#a9805e'; for (let x = (y * 7) % 64; x < 256; x += 64) g.fillRect(x, y + 1, 2, 14); } break;
    case 'rubber': g.fillStyle = '#e8c06a'; g.fillRect(0, 0, 128, 128); g.fillRect(128, 128, 128, 128); speck(1800, ['#c98a59', '#f1c48f', '#d9965f'], 0.5, 1.3, 0.6); break;
    case 'gravel': speck(2600, ['#b29a78', '#d8c4a3', '#a68c6c', '#c9b392'], 0.5, 1.6, 0.8); break;
    default: { const deep = type === 'forest' ? ['#8ea873', '#a9bf8e', '#7e9866'] : ['#a9bf8e', '#c7d7a8', '#9fb684', '#b5c995']; speck(3200, deep, 0.4, 1.4, 0.55); if (type !== 'forest') speck(70, ['#f3e7a6', '#ffffff', '#f2c4b2', '#d9c6f0'], 0.9, 1.6, 0.85); }
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 4; return t;
}
function bandTexture(type) {
  const [kind, a, b] = BAND[type]; const c = document.createElement('canvas'); c.width = 512; c.height = 64; const g = c.getContext('2d'); const rnd = mulberry(type.length * 31 + 7); g.fillStyle = a; g.fillRect(0, 0, 512, 64);
  switch (kind) {
    case 'blocks': case 'tiles': for (let y = 0; y < 64; y += 21) for (let x = (y / 21) % 2 ? -18 : 0; x < 512; x += 36) { g.fillStyle = rnd() < 0.5 ? a : b; g.fillRect(x + 1, y + 1, 34, 19); } break;
    case 'glass': for (let x = 0; x < 512; x += 32) { g.fillStyle = b; g.fillRect(x + 2, 6, 26, 52); g.fillStyle = '#3e7c8c'; g.fillRect(x, 0, 2, 64); } break;
    case 'hoarding': case 'stripe': for (let x = 0; x < 512; x += 32) { g.fillStyle = (x / 32) % 2 ? a : b; g.fillRect(x, 0, 32, 64); } break;
    case 'hazard': g.fillStyle = b; for (let x = -64; x < 576; x += 32) { g.beginPath(); g.moveTo(x, 64); g.lineTo(x + 16, 64); g.lineTo(x + 48, 0); g.lineTo(x + 32, 0); g.fill(); } break;
    case 'mural': case 'graffiti': case 'pastel': for (let k = 0; k < 40; k++) { g.fillStyle = [a, b, '#fcea8b', '#1f4e79', '#ffffff', '#6f7fb5'][k % 6]; g.beginPath(); g.arc(rnd() * 512, rnd() * 64, 6 + rnd() * 14, 0, Math.PI * 2); g.fill(); } break;
    case 'moss': case 'ivy': for (let x = 0; x < 512; x += 6) { g.fillStyle = b; const h = 10 + rnd() * 40; g.fillRect(x, 0, 5, h); g.beginPath(); g.arc(x + 2.5, h, 3.5, 0, Math.PI * 2); g.fill(); } break;
    case 'timber': for (let x = 0; x < 512; x += 24) { g.fillStyle = rnd() < 0.5 ? a : b; g.fillRect(x + 1, 0, 22, 64); } break;
  }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = THREE.RepeatWrapping; t.repeat.set(3, 1); return t;
}
function cliffGeometry(cx, cz, r, yTop, yBot, type) {
  const segH = 8, segV = 7, posA = [], colA = [], idx = []; const shallow = new THREE.Color(SHALLOW[type] || 0xb9d9d6), waterTone = new THREE.Color(0x9fcfd6);
  for (let e = 0; e < 6; e++) { const [ax, az] = hexVertex(cx, cz, r, e), [bx, bz] = hexVertex(cx, cz, r, e + 1); const base = posA.length / 3;
    for (let v = 0; v <= segV; v++) { const t = v / segV, y = yTop + (yBot - yTop) * t; for (let h = 0; h <= segH; h++) { const s = h / segH; posA.push(ax + (bx - ax) * s, y, az + (bz - az) * s); const c = shallow.clone().lerp(waterTone, Math.min(1, t * 1.4)); colA.push(c.r, c.g, c.b); } }
    for (let v = 0; v < segV; v++) for (let h = 0; h < segH; h++) { const a = base + v * (segH + 1) + h, b = a + 1, c = a + segH + 1, d = c + 1; idx.push(a, c, b, b, c, d); } }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(posA, 3)); g.setAttribute('color', new THREE.Float32BufferAttribute(colA, 3)); g.setIndex(idx); g.computeVertexNormals(); return g;
}
function hexRing(rOut, rIn) { const s = new THREE.Shape(), h = new THREE.Path(); for (let k = 0; k < 6; k++) { const [x, z] = hexVertex(0, 0, rOut, k); k ? s.lineTo(x, -z) : s.moveTo(x, -z); const [x2, z2] = hexVertex(0, 0, rIn, k); k ? h.lineTo(x2, -z2) : h.moveTo(x2, -z2); } s.closePath(); h.closePath(); s.holes.push(h); const g = new THREE.ShapeGeometry(s); g.rotateX(-Math.PI / 2); return g; }

export function buildIslands(scene, islands, kit, colors) {
  const centers = new Map(), axialMap = new Map(), views = new Map(), tops = [];
  for (const i of islands) { centers.set(i.id, axialToWorld(...i.axial)); axialMap.set(i.axial.join(','), i.id); }
  const topMats = {}; for (const i of islands) if (!topMats[i.ground]) topMats[i.ground] = new THREE.MeshStandardMaterial({ map: groundTexture(i.ground), roughness: 0.92 });
  const cliffMat = new THREE.MeshLambertMaterial({ vertexColors: true });
  const ringTex = groundTexture('ringtile'); const curbMat = new THREE.MeshStandardMaterial({ color: 0xe4d6c3, roughness: 0.8, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 });
  const foamMat = new THREE.MeshBasicMaterial({ color: 0xf6fbfa, transparent: true, opacity: 0.55, depthWrite: false });
  const neighbors = new Map();
  for (const i of islands) {
    const c = centers.get(i.id);
    const topGeo = new THREE.CircleGeometry(R - 0.05, 6); topGeo.rotateZ(Math.PI / 6); topGeo.rotateX(-Math.PI / 2); const uv = topGeo.attributes.uv, p = topGeo.attributes.position; for (let n = 0; n < p.count; n++) uv.setXY(n, (p.getX(n) + c.x) / 2.2, (p.getZ(n) + c.z) / 2.2);
    const top = new THREE.Mesh(topGeo, topMats[i.ground]); top.position.set(c.x, TOP, c.z); top.receiveShadow = true; top.userData = { island: i.id }; scene.add(top); tops.push(top);
    const rm = new THREE.MeshStandardMaterial({ map: ringTex, roughness: 0.9, color: RING_TINT[i.ground], polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 });
    const ring = new THREE.Mesh(hexRing(RING_R + 0.14, RING_R - 0.14), rm); ring.position.set(c.x, TOP + 0.012, c.z); ring.receiveShadow = true; scene.add(ring);
    const curb = new THREE.Mesh(hexRing(R, R - 0.07), curbMat); curb.position.set(c.x, TOP + 0.04, c.z); scene.add(curb);
    const inner = new THREE.Mesh(hexRing(RING_R - 0.14, RING_R - 0.18), curbMat); inner.position.set(c.x, TOP + 0.02, c.z); scene.add(inner);
    const cliff = new THREE.Mesh(cliffGeometry(c.x, c.z, R, TOP + 0.04, WATER_Y - 0.5, i.ground), cliffMat); cliff.castShadow = true; cliff.receiveShadow = true; cliff.userData = { island: i.id }; scene.add(cliff); tops.push(cliff);
    const band = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.012, R - 0.002, 0.16, 6, 1, true), new THREE.MeshStandardMaterial({ map: bandTexture(i.ground), roughness: 0.85 })); band.position.set(c.x, TOP - 0.04, c.z); scene.add(band);
    const shallow = new THREE.Mesh(hexRing(R + 0.7, R - 0.02), new THREE.MeshBasicMaterial({ color: SHALLOW[i.ground], transparent: true, opacity: 0.55, depthWrite: false })); shallow.position.set(c.x, WATER_Y + 0.008, c.z); scene.add(shallow);
    const foam = new THREE.Mesh(hexRing(R + 0.16, R - 0.02), foamMat); foam.position.set(c.x, WATER_Y + 0.012, c.z); scene.add(foam);
    const pts = Array.from({ length: 7 }, (_, k) => { const [x, z] = hexVertex(c.x, c.z, R + 0.01, k); return new THREE.Vector3(x, TOP + 0.07, z); });
    const outline = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: colors.accent })); outline.visible = false; scene.add(outline);
    const nb = []; for (const [dq, dr] of NEIGHBORS) { const id = axialMap.get([i.axial[0] + dq, i.axial[1] + dr].join(',')); nb.push({ id: id || null, dq, dr }); } neighbors.set(i.id, nb);
    views.set(i.id, { top, cliff, outline, center: c });
    // 步道環上的路燈與長椅
    for (let k = 0; k < 6; k++) { const [vx, vz] = hexVertex(c.x, c.z, RING_R + 0.3, k); kit.cyl(vx, TOP + 0.22, vz, 0.03, 0.44, 0x6a665f); kit.bulb(vx, TOP + 0.47, vz, 0.07); }
  }
  // 橋
  const bridges = [], seen = new Set();
  for (const [id, nb] of neighbors) for (const n of nb) { if (!n.id) continue; const key = [id, n.id].sort().join('|'); if (seen.has(key)) continue; seen.add(key);
    const a = centers.get(id), b = centers.get(n.id); const [dx, dz] = dirOf(n.dq, n.dr); const ri = RING_R * 0.8660254;
    const pa = new THREE.Vector3(a.x + dx * ri, TOP, a.z + dz * ri), pb = new THREE.Vector3(b.x - dx * ri, TOP, b.z - dz * ri); bridges.push({ a: id, b: n.id, pa, pb });
    const len = pa.distanceTo(pb), yaw = Math.atan2(dx, dz), mid = pa.clone().lerp(pb, 0.5), arch = 0.08; const deck = [0xb48560, 0xa77a55];
    for (let s = 0; s <= 8; s++) { const t = s / 8, pnt = pa.clone().lerp(pb, t); kit.hbox(pnt.x, TOP + 0.03 + Math.sin(t * Math.PI) * arch, pnt.z, 0.3, 0.035, len / 9 + 0.01, s % 2 ? deck[0] : deck[1], null, [Math.cos(t * Math.PI) * -arch * 2, yaw, 0]); }
    for (const side of [-1, 1]) { const ox = Math.cos(yaw) * 0.15 * side, oz = -Math.sin(yaw) * 0.15 * side; for (let s = 0; s <= 4; s++) { const t = s / 4, pnt = pa.clone().lerp(pb, t); kit.hbox(pnt.x + ox, TOP + 0.13 + Math.sin(t * Math.PI) * arch, pnt.z + oz, 0.022, 0.16, 0.022, colors.cream); } kit.hbox(mid.x + ox, TOP + 0.21 + arch * 0.66, mid.z + oz, 0.02, 0.02, len, 0x9a7454, null, [0, yaw, 0]); }
  }
  return { centers, neighbors, views, bridges, tops };
}
// 行人路網：每島 12 個環節點（頂點 + 邊中點），橋連接邊中點。
export function buildGraph(islands, centers, neighbors) {
  const nodes = [], adj = [], ringIndex = new Map();
  const add = (p, island) => { nodes.push({ p, island }); adj.push([]); return nodes.length - 1; };
  const link = (a, b) => { const d = nodes[a].p.distanceTo(nodes[b].p); adj[a].push([b, d]); adj[b].push([a, d]); };
  for (const i of islands) { const c = centers.get(i.id), ids = []; for (let k = 0; k < 6; k++) { const [vx, vz] = hexVertex(c.x, c.z, RING_R, k), [wx, wz] = hexVertex(c.x, c.z, RING_R, k + 1); ids.push(add(new THREE.Vector3(vx, TOP + 0.03, vz), i.id)); ids.push(add(new THREE.Vector3((vx + wx) / 2, TOP + 0.03, (vz + wz) / 2), i.id)); } for (let k = 0; k < 12; k++) link(ids[k], ids[(k + 1) % 12]); ringIndex.set(i.id, ids); }
  const midFor = (id, dq, dr) => { const c = centers.get(id), [dx, dz] = dirOf(dq, dr), ids = ringIndex.get(id); let best = null, bd = 1e9; for (let k = 1; k < 12; k += 2) { const p = nodes[ids[k]].p, d = (p.x - c.x - dx * 2) ** 2 + (p.z - c.z - dz * 2) ** 2; if (d < bd) { bd = d; best = ids[k]; } } return best; };
  for (const [id, nb] of neighbors) for (const n of nb) if (n.id && id < n.id) link(midFor(id, n.dq, n.dr), midFor(n.id, -n.dq, -n.dr));
  function shortest(a, b) { const dist = new Array(nodes.length).fill(Infinity), prev = new Array(nodes.length).fill(-1), done = new Array(nodes.length).fill(false); dist[a] = 0;
    for (;;) { let u = -1, best = Infinity; for (let i = 0; i < nodes.length; i++) if (!done[i] && dist[i] < best) { best = dist[i]; u = i; } if (u < 0 || u === b) break; done[u] = true; for (const [v, w] of adj[u]) if (dist[u] + w < dist[v]) { dist[v] = dist[u] + w; prev[v] = u; } }
    const path = []; for (let v = b; v >= 0; v = prev[v]) path.unshift(v); return path[0] === a ? path : [a]; }
  function nodeNear(id, p) { let best = null, bd = 1e9; for (const n of ringIndex.get(id)) { const d = nodes[n].p.distanceToSquared(p); if (d < bd) { bd = d; best = n; } } return best; }
  function nearestNode(p) { let best = 0, bd = 1e9; for (let i = 0; i < nodes.length; i++) { const d = nodes[i].p.distanceToSquared(p); if (d < bd) { bd = d; best = i; } } return best; }
  return { nodes, adj, ringIndex, shortest, nodeNear, nearestNode, midFor };
}
// 路線：節點序列 → 可取樣的折線
export function makeRoute(pts) { const lens = [0]; for (let k = 1; k < pts.length; k++) lens.push(lens[k - 1] + pts[k].distanceTo(pts[k - 1])); return { pts, lens, total: lens[lens.length - 1] }; }
export function sampleRoute(route, s) { if (route.total < 0.001) return { p: route.pts[0].clone(), dir: new THREE.Vector3(0, 0, 1) }; let k = 1; while (k < route.lens.length - 1 && route.lens[k] < s) k++; const a = route.pts[k - 1], b = route.pts[k], seg = route.lens[k] - route.lens[k - 1] || 1; const t = Math.min(1, Math.max(0, (s - route.lens[k - 1]) / seg)); return { p: a.clone().lerp(b, t), dir: new THREE.Vector3().subVectors(b, a).setY(0).normalize() }; }
// 島內建築槽位
export function slotLayout(n, heroSize) { const hh = heroSize / 2, heroZ = -0.58;
  for (let s = n <= 4 ? 0.62 : 0.56; s > 0.3; s -= 0.03) { const h = s / 2, gapX = s + 0.1, gapZ = s + 0.08, out = [];
    for (let zi = -4; zi <= 4; zi++) for (let xi = -5; xi <= 5; xi++) { const z = 0.2 + zi * gapZ, x = (xi + (Math.abs(zi) % 2 ? 0.5 : 0)) * gapX; if (![[x - h, z - h], [x + h, z - h], [x - h, z + h], [x + h, z + h]].every(([px, pz]) => insideHex(px, pz, INNER - 0.04))) continue; if (Math.abs(x) < hh + h + 0.06 && Math.abs(z - heroZ) < hh + h + 0.06) continue; out.push([x, z]); }
    if (out.length >= n) { out.sort((a, b) => Math.hypot(a[0], a[1] - 0.25) - Math.hypot(b[0], b[1] - 0.25)); return { size: s, slots: out.slice(0, n), heroZ }; } }
  return { size: 0.3, slots: [], heroZ: -0.58 };
}
