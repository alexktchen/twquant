/* 一場（約 3 分鐘）的模擬：排程、敵人移動、塔與英雄攻擊、保證守住的「全民護城」。
   畫面交給 PIWorld，DOM 交給 ui.js（透過 callbacks）。 */
window.PIGame = (function () {
  const C = PI_CONFIG, RC = C.round, M = C.map, Wd = PIWorld;
  const W = Wd.W, ROAD_Y = Wd.LAND_H - 0.12;
  const G = { phase: 'lobby', t: 0, money: 0, approval: 100, towers: [], enemies: [], projs: [], beams: [], floats: [], rings: [], queue: [],
    issue: null, hero: null, selType: null, selTower: null, hoverKey: null, speed: 1, paused: false, kills: 0, leaked: 0, raids: 0, waveNo: 1,
    raidT: 0, windT: 0, windLeft: 0, surgeT: 0, surges: 0, final: false, log: [], spawnedTotal: 0, ultCd: 0, focusCd: 0 };
  const cb = {}; const emit = (k, ...a) => cb[k] && cb[k](...a);
  const dist2 = (a, b) => Math.hypot(a.x - b.x, a.z - b.z);
  let enemyHits = [];

  function init(callbacks) {
    Object.assign(cb, callbacks);
    Wd.on('click', onClick);
    Wd.init && null;
  }
  function onClick(ev) {
    if (G.phase !== 'round') return;
    const hit = Wd.pick(ev, enemyHits);
    if (hit.enemy && !hit.enemy.dead) { focusStrike(hit.enemy); return; }
    if (!hit.tile) { select(null); return; }
    const t = G.towers.find(t => t.key === hit.tile);
    if (t) { select(t); return; }
    if (G.selType) { placeTower(G.selType, hit.tile); return; }
    if (Wd.PATH_SET.has(hit.tile)) emit('hint', `這是${Object.values(M.paths).find(p => p.cells.includes(hit.tile)).name}方向的網軍航線`, true);
    select(null);
  }
  function hover(ev) { if (G.phase !== 'round' || !G.selType) { Wd.highlightTile(null); return; } const hit = Wd.pick(ev, []); const k = hit.tile; const ok = k && !Wd.PATH_SET.has(k) && k !== M.goal && k !== M.heroTile && !G.towers.some(t => t.key === k) && G.money >= C.towers[G.selType].cost; Wd.highlightTile(k, !!ok); }
  function select(t) { if (G.selTower) Wd.setRangeRing(G.selTower.obj, 0, false); G.selTower = t; if (t) { G.selType = null; Wd.setRangeRing(t.obj, towerStats(t).range, true); } emit('sel', t); emit('palette'); }
  function selectType(id) { G.selType = id; if (G.selTower) select(null); emit('palette'); emit('hint', id ? `點地圖空格放下「${C.towers[id].name}」，Esc 取消` : ''); }
  function addLog(txt, cls = '') { const m = Math.floor(G.t / 60), s = Math.floor(G.t % 60); G.log.unshift({ t: `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`, txt, cls }); G.log.length = Math.min(G.log.length, C.ui.logMax); emit('log', G.log); }

  // ── 一場的開始 / 結束 ──
  function startRound(issue, hero, waveNo, isFinal) {
    clearAll();
    Object.assign(G, { phase: 'round', t: 0, money: RC.startMoney, approval: RC.startApproval, issue, waveNo, final: !!isFinal, kills: 0, leaked: 0, raids: 0, surges: 0, spawnedTotal: 0, selType: null, selTower: null, speed: 1, paused: false, windLeft: 0, surgeT: 0, log: [], ultCd: 0, focusCd: 0 });
    G.hero = { ...hero, cfg: C.heroes[hero.cls], obj: Wd.makeHero(hero.cls, hero.nick), cd: 0 };
    const p = Wd.posKey(M.heroTile); G.hero.x = p.x; G.hero.z = p.z;
    buildSchedule(issue);
    G.raidT = RC.raid.minGap + Math.random() * (RC.raid.maxGap - RC.raid.minGap);
    G.windT = RC.windShift.minGap + Math.random() * (RC.windShift.maxGap - RC.windShift.minGap);
    Wd.setTimeOfDay(0);
    addLog(`第 ${waveNo} 波開始：${issue.tag}「${issue.title}」，${G.queue.length} 隻從 ${issue.routes.map(r => M.paths[r].name).join('、')} 方向來`, G.final ? 'bad' : '');
    if (G.final) emit('banner', '⚠ 大選前夜總攻擊：四面八方全軍出動', 4);
    emit('hud'); emit('palette'); emit('log', G.log); emit('sel', null);
  }
  function buildSchedule(issue) {
    const total = Math.round(RC.baseSpawn * (issue.intensity || 1));
    const types = Object.keys(issue.mix), wsum = Object.values(issue.mix).reduce((a, b) => a + b, 0);
    const pickType = () => { let r = Math.random() * wsum; for (const t of types) { r -= issue.mix[t]; if (r <= 0) return t; } return types[0]; };
    let ri = 0; const route = () => issue.routes[(ri++) % issue.routes.length];
    G.queue = [];
    for (const b of RC.bursts) { const n = Math.round(total * b.share); for (let i = 0; i < n; i++) G.queue.push({ t: b.from + Math.random() * (b.to - b.from), type: pickType(), path: route() }); }
    if (issue.boss) G.queue.push({ t: RC.bossAt, type: issue.boss, path: issue.routes[0], boss: true });
    if (issue.boss2) G.queue.push({ t: RC.bossAt + 6, type: issue.boss2, path: issue.routes[issue.routes.length - 1], boss: true });
    G.queue.sort((a, b) => a.t - b.t);
  }
  function clearAll() {
    for (const e of G.enemies) Wd.removeObj(e.obj); for (const t of G.towers) Wd.removeObj(t.obj); for (const p of G.projs) Wd.removeObj(p.obj);
    for (const b of G.beams) Wd.removeObj(b.obj); for (const f of G.floats) Wd.removeObj(f.obj); for (const r of G.rings) Wd.removeObj(r.obj);
    if (G.hero && G.hero.obj) Wd.removeObj(G.hero.obj);
    G.enemies = []; G.towers = []; G.projs = []; G.beams = []; G.floats = []; G.rings = []; G.queue = []; enemyHits = []; Wd.highlightTile(null);
  }
  function endRound() {
    G.phase = 'result';
    const res = { waveNo: G.waveNo, kills: G.kills, leaked: G.leaked, approval: Math.round(G.approval), towers: G.towers.length, raids: G.raids, surges: G.surges, issueId: G.issue.id, issueTitle: G.issue.title, tag: G.issue.tag, final: G.final, hero: { nick: G.hero.nick, cls: G.hero.cls } };
    addLog(`第 ${G.waveNo} 波守住了。擊退 ${G.kills} 隻，滿意度 ${res.approval}%`, 'good');
    emit('end', res);
  }
  function backToLobby() { clearAll(); G.phase = 'lobby'; Wd.setTimeOfDay(0.3); emit('hud'); }

  // ── 塔 ──
  function towerStats(t) {
    const Tw = C.towers[t.type]; const lv = t.level;
    const dmg = Tw.dmg ? Math.round(Tw.dmg * Math.pow(RC.upgradeDmgRate, lv - 1)) : 0;
    const range = Tw.range ? Tw.range + RC.upgradeRangeAdd * (lv - 1) : 0;
    let support = G.towers.filter(o => o !== t && o.type === 'care' && dist2(o, t) <= (C.towers.care.range + RC.upgradeRangeAdd * (o.level - 1)) * W).reduce((s, o) => s + C.towers.care.support * o.level, 0);
    if (G.hero && G.hero.cfg.support && dist2(G.hero, t) <= 1.5 * W) support += G.hero.cfg.support;
    const rate = Tw.rate ? Tw.rate / (1 + support) : 0;
    return { dmg, range, rate, support, upgradeCost: Math.round(Tw.cost * RC.upgradeCostRate * lv), sell: Math.round(t.spent * RC.sellRate) };
  }
  function placeTower(type, key) {
    const Tw = C.towers[type];
    if (G.money < Tw.cost) return emit('hint', '預算不夠', true);
    if (Wd.PATH_SET.has(key) || key === M.goal) return emit('hint', '這格是網軍航線，不能蓋', true);
    if (key === M.heroTile) return emit('hint', '這格是英雄站的地方', true);
    if (G.towers.some(t => t.key === key)) return emit('hint', '這格已經有政策了', true);
    const p = Wd.posKey(key); G.money -= Tw.cost;
    const t = { type, key, level: 1, cd: 0, x: p.x, z: p.z, spent: Tw.cost, obj: Wd.makeTower(type, 1, key) };
    G.towers.push(t); addLog(`${M.districts[key]}區蓋了「${Tw.name}」`);
    if (G.money < Tw.cost) selectType(null);
    emit('hud'); emit('palette');
  }
  function upgradeTower(t) { const st = towerStats(t); if (t.level >= RC.maxLevel || G.money < st.upgradeCost) return; G.money -= st.upgradeCost; t.spent += st.upgradeCost; t.level++; Wd.removeObj(t.obj); t.obj = Wd.makeTower(t.type, t.level, t.key); Wd.setRangeRing(t.obj, towerStats(t).range, true); addLog(`「${C.towers[t.type].name}」升到 Lv.${t.level}`); emit('hud'); emit('sel', t); emit('palette'); }
  function sellTower(t) { const st = towerStats(t); G.money += st.sell; Wd.removeObj(t.obj); G.towers = G.towers.filter(o => o !== t); G.selTower = null; addLog(`拆掉「${C.towers[t.type].name}」，回收 ${st.sell} 億`); emit('hud'); emit('sel', null); emit('palette'); }

  // ── 敵人 ──
  let eid = 0;
  function spawn(type, pathId, scale = 1) {
    const A = C.animals[type]; const pts = Wd.PATH_PTS[pathId]; const hp = Math.round(A.hp * scale);
    const e = { id: ++eid, type, pathId, hp, maxHp: hp, seg: 0, t: 0, x: pts[0].x, z: pts[0].z, slow: 0, slowT: 0, speedMul: 1, hairT: 0, chant: null, chantT: Math.random() * RC.chantOff, fixed: null, wing: Math.random() * 6, hopT: Math.random(), seed: Math.random(), spawnT: 0, prog: 0, jitter: (Math.random() - 0.5) * 0.6, obj: Wd.makeAnimal(type), dead: false };
    if (A.repeat) e.fixed = pickChant(e);
    e.obj.userData.hit.userData.enemy = e; enemyHits.push(e.obj.userData.hit);
    e.obj.position.set(e.x, ROAD_Y, e.z); G.enemies.push(e); G.spawnedTotal++;
    if (A.boss) { emit('banner', `${A.name} 出現了！`, 3); addLog(`${A.name}從${M.paths[pathId].name}方向出現`, 'bad'); }
    return e;
  }
  function pickChant(e) { const A = C.animals[e.type]; const pool = Math.random() < 0.65 ? G.issue.chants : A.chants; return pool[Math.floor(Math.random() * pool.length)]; }
  function damage(e, amt) {
    if (e.dead) return; e.hp -= amt; Wd.setHp(e.obj, Math.max(0, e.hp / e.maxHp));
    if (e.hp <= 0) { e.dead = true; G.kills++; const b = Math.round(C.animals[e.type].bounty * RC.killBountyScale); G.money += b; floatText(`+${b}`, e.x, 1.2, e.z, { bg: '#f2c14e' }); emit('hud'); emit('palette'); }
  }
  function leak(e) { const A = C.animals[e.type]; G.leaked++; G.approval = Math.max(RC.approvalFloor, G.approval - A.leak); const gp = Wd.posKey(M.goal); floatText(`−${A.leak}% 滿意度`, gp.x, 2.6, gp.z, { bg: '#c23b2a', fg: '#fff' }); if (A.boss) addLog(`${A.name}衝進市政府，滿意度 −${A.leak}%`, 'bad'); emit('hud'); }
  function floatText(txt, x, y, z, opt) { G.floats.push({ obj: Wd.makeFloat(txt, x, y, z, opt), ttl: 1.1 }); }
  function removeEnemy(e) { Wd.removeObj(e.obj); enemyHits = enemyHits.filter(h => h.userData.enemy !== e); }

  // ── 英雄 ──
  function focusStrike(e) {
    if (G.focusCd > 0) return; const H = G.hero.cfg; G.focusCd = RC.focusCooldown;
    G.beams.push({ obj: Wd.makeBeam({ x: G.hero.x, y: Wd.LAND_H + 1, z: G.hero.z }, { x: e.x, y: ROAD_Y + 0.6, z: e.z }, H.color), ttl: 0.25 });
    damage(e, H.dmg * RC.focusDmgMul); floatText('重擊', e.x, 1.5, e.z, { bg: '#1f4e79', fg: '#fff' }); emit('hero');
  }
  function ultimate() {
    if (G.phase !== 'round' || G.ultCd > 0) return; G.ultCd = RC.ultCooldown;
    const gp = Wd.posKey(M.goal); G.rings.push({ obj: Wd.makeRing(gp.x, gp.z, 1, 0xf2c14e), ttl: 1.2, grow: 10 });
    for (const e of [...G.enemies]) { damage(e, e.maxHp * RC.ultPct); if (!e.dead) { e.slow = Math.max(e.slow, RC.ultSlow); e.slowT = RC.ultSlowTime; } }
    emit('banner', '🙌 台北人站出來！', 2.5); addLog('英雄大招：台北人站出來', 'good'); emit('hero');
  }
  function surge() {
    G.surges++; const gp = Wd.posKey(M.goal); G.rings.push({ obj: Wd.makeRing(gp.x, gp.z, 1, 0x1f4e79), ttl: 1.2, grow: 12 });
    for (const e of [...G.enemies]) damage(e, e.maxHp * RC.surgePct);
    if (G.surges === 1) { emit('banner', '🏙️ 全民護城！市民自發守住市政府', 3); addLog('全民護城啟動：市民一起把網軍趕出去', 'good'); }
  }

  // ── 主迴圈 ──
  function step(dt) {
    if (G.phase !== 'round') return;
    G.t += dt;
    while (G.queue.length && G.queue[0].t <= G.t) { const q = G.queue.shift(); spawn(q.type, q.path); }
    // 突襲與風向
    G.raidT -= dt; if (G.raidT <= 0 && G.t < RC.bossAt) { if (Math.random() < RC.raid.chance) raid(); G.raidT = RC.raid.minGap + Math.random() * (RC.raid.maxGap - RC.raid.minGap); }
    G.windT -= dt; if (G.windT <= 0 && G.t < RC.surgeAt) { G.windLeft = RC.windShift.duration; emit('banner', '💨 風向變了！敵人全體加速', 2.5); addLog('有人帶風向，網軍短暫加速', 'raid'); G.windT = RC.windShift.minGap + Math.random() * (RC.windShift.maxGap - RC.windShift.minGap); }
    if (G.windLeft > 0) G.windLeft -= dt;
    // 保證守住
    if ((G.t >= RC.surgeAt || G.approval <= RC.surgeBelowApproval) && G.enemies.length) { G.surgeT -= dt; if (G.surgeT <= 0) { surge(); G.surgeT = RC.surgeEvery; } }
    if (G.t >= RC.wipeAt) for (const e of [...G.enemies]) damage(e, e.maxHp);
    // 經濟 / 回復
    for (const t of G.towers) if (C.towers[t.type].income) G.money += C.towers[t.type].income * t.level * dt;
    if (G.hero.cfg.regen) G.approval = Math.min(RC.startApproval, G.approval + G.hero.cfg.regen * dt);
    if (G.ultCd > 0) G.ultCd -= dt; if (G.focusCd > 0) G.focusCd -= dt;
    // 敵人
    const wind = G.windLeft > 0 ? RC.windShift.speedMul : 1;
    for (const e of G.enemies) {
      const A = C.animals[e.type], pts = Wd.PATH_PTS[e.pathId];
      if (e.slowT > 0) { e.slowT -= dt; if (e.slowT <= 0) e.slow = 0; }
      if (A.hairpin) { e.hairT -= dt; if (e.hairT <= 0) { e.speedMul = A.hairpin[0] + Math.random() * (A.hairpin[1] - A.hairpin[0]); e.hairT = 1.5 + Math.random() * 2; } }
      const v = A.speed * e.speedMul * (1 - e.slow) * wind * RC.pxPerSec * W; let remain = v * dt;
      while (remain > 0 && e.seg < pts.length - 1) { const a = pts[e.seg], b = pts[e.seg + 1]; const L = Math.hypot(b.x - a.x, b.z - a.z); const left = (1 - e.t) * L; if (remain < left) { e.t += remain / L; remain = 0; } else { remain -= left; e.seg++; e.t = 0; } }
      if (e.seg >= pts.length - 1) { e.dead = true; leak(e); continue; }
      const a = pts[e.seg], b = pts[e.seg + 1]; const nx = -(b.z - a.z), nz = (b.x - a.x), nl = Math.hypot(nx, nz) || 1;
      e.x = a.x + (b.x - a.x) * e.t + nx / nl * e.jitter; e.z = a.z + (b.z - a.z) * e.t + nz / nl * e.jitter; e.prog = e.seg + e.t;
      e.obj.position.set(e.x, ROAD_Y, e.z); e.obj.rotation.y = Math.atan2(-(b.z - a.z), (b.x - a.x));
      e.wing += dt * (A.model === 'mouse' ? 22 : 12); if (A.hop) e.hopT += dt * A.speed * 1.6 * e.speedMul;
      e.chantT -= dt; if (e.chantT <= 0) { if (e.chant) { e.chant = null; e.chantT = RC.chantOff * (0.6 + Math.random()); } else { e.chant = e.fixed || pickChant(e); e.chantT = RC.chantOn; } Wd.setChant(e.obj, e.chant); }
      if (A.spawn) { e.spawnT += dt; if (e.spawnT >= A.spawn.every) { e.spawnT = 0; const n = spawn(A.spawn.type, e.pathId); n.seg = e.seg; n.t = e.t; } }
      Wd.animateAnimal(e.obj, e, dt, G.t);
    }
    for (const e of G.enemies) if (e.dead) removeEnemy(e); G.enemies = G.enemies.filter(e => !e.dead);
    // 塔
    for (const t of G.towers) { const Tw = C.towers[t.type]; if (!Tw.dmg) continue; t.cd -= dt; if (t.cd > 0) continue; const st = towerStats(t); const target = nearestToGoal(t, st.range * W); if (!target) continue; t.cd = st.rate; fire(t, { x: t.x, y: Wd.LAND_H + 0.6, z: t.z }, target, st.dmg, Tw); }
    // 英雄
    { const H = G.hero; H.cd -= dt; if (H.cd <= 0) { const target = nearestToGoal(H, H.cfg.range * W); if (target) { H.cd = H.cfg.rate; fire(H, { x: H.x, y: Wd.LAND_H + 1, z: H.z }, target, H.cfg.dmg, { proj: H.cfg.style, chain: H.cfg.chain, slow: H.cfg.slow, slowTime: H.cfg.slowTime, color: H.cfg.color }); H.obj.userData.body.rotation.y = Math.atan2(-(target.z - H.z), target.x - H.x); } } H.obj.userData.head.position.y = 0.92 + Math.sin(G.t * 4) * 0.02; }
    // 子彈
    for (const p of G.projs) { const tg = p.target; if (tg.dead) { p.dead = true; continue; } const ty = ROAD_Y + (C.animals[tg.type].fly ? 1.1 : 0.3); const d = Math.hypot(tg.x - p.x, ty - p.y, tg.z - p.z), v = RC.projSpeed * W * dt;
      if (d <= v + 0.15) { p.dead = true; if (p.splash) { const Rr = p.splash * W; for (const e of G.enemies) if (dist2(e, tg) <= Rr) damage(e, p.dmg); G.rings.push({ obj: Wd.makeRing(tg.x, tg.z, Rr, 0xd96a44), ttl: 0.4, grow: 0 }); } else damage(tg, p.dmg); if (p.slow && !C.animals[tg.type].boss) { tg.slow = Math.max(tg.slow, p.slow); tg.slowT = p.slowTime; } }
      else { p.x += (tg.x - p.x) / d * v; p.y += (ty - p.y) / d * v; p.z += (tg.z - p.z) / d * v; p.obj.position.set(p.x, p.y, p.z); p.obj.lookAt(tg.x, ty, tg.z); } }
    for (const p of G.projs) if (p.dead) Wd.removeObj(p.obj); G.projs = G.projs.filter(p => !p.dead);
    for (const e of G.enemies) if (e.dead) removeEnemy(e); G.enemies = G.enemies.filter(e => !e.dead);
    for (const b of G.beams) { b.ttl -= dt; b.obj.material.opacity = Math.max(0, b.ttl * 4); } for (const b of G.beams) if (b.ttl <= 0) Wd.removeObj(b.obj); G.beams = G.beams.filter(b => b.ttl > 0);
    for (const f of G.floats) { f.ttl -= dt; f.obj.position.y += dt * 0.8; f.obj.material.opacity = Math.min(1, f.ttl); } for (const f of G.floats) if (f.ttl <= 0) Wd.removeObj(f.obj); G.floats = G.floats.filter(f => f.ttl > 0);
    for (const r of G.rings) { r.ttl -= dt; if (r.grow) r.obj.scale.setScalar(r.obj.scale.x + r.grow * dt); r.obj.material.opacity = Math.max(0, r.ttl); } for (const r of G.rings) if (r.ttl <= 0) Wd.removeObj(r.obj); G.rings = G.rings.filter(r => r.ttl > 0);
    Wd.setTimeOfDay(Math.min(1, G.t / RC.seconds));
    if (G.t >= RC.seconds || (!G.queue.length && !G.enemies.length && G.t > RC.bossAt + 4)) endRound();
  }
  function nearestToGoal(src, R) { let best = null; for (const e of G.enemies) if (!e.dead && dist2(e, src) <= R && (!best || e.prog > best.prog)) best = e; return best; }
  function fire(src, from, target, dmg, Tw) {
    if (Tw.chain) { let cur = target; const hit = new Set([cur]); let f = from; for (let i = 0; i < Tw.chain && cur; i++) { const to = { x: cur.x, y: ROAD_Y + 0.6, z: cur.z }; G.beams.push({ obj: Wd.makeBeam(f, to, Tw.color || 0xf2c14e), ttl: 0.2 }); damage(cur, dmg * Math.pow(0.8, i)); f = to; let nxt = null, bd = W * 1.4; for (const e of G.enemies) { if (hit.has(e) || e.dead) continue; const d = dist2(e, cur); if (d < bd) { bd = d; nxt = e; } } cur = nxt; if (cur) hit.add(cur); } return; }
    G.projs.push({ obj: Wd.makeProj(Tw.proj, Tw.color || 0xffffff), x: from.x, y: from.y, z: from.z, target, dmg, splash: Tw.splash, slow: Tw.slow, slowTime: Tw.slowTime });
  }
  function raid() {
    G.raids++; const routes = G.issue.routes; const pathId = routes[Math.floor(Math.random() * routes.length)];
    const tag = C.raidTags[Math.floor(Math.random() * C.raidTags.length)]; const n = RC.raid.size + Math.floor(G.t / 30);
    for (let i = 0; i < n; i++) G.queue.push({ t: G.t + i * 0.3, type: 'mouse', path: pathId }); G.queue.sort((a, b) => a.t - b.t);
    emit('banner', `🐭 網軍突襲 ${tag}　從${M.paths[pathId].name}方向`, 3); addLog(`網軍突襲 ${tag}：${M.paths[pathId].name}方向冒出一群帳號`, 'raid');
  }
  return { init, step, hover, startRound, backToLobby, selectType, select, upgradeTower, sellTower, towerStats, ultimate, G };
})();
