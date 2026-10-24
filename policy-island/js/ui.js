/* DOM：大廳（登入英雄、選議題、全域戰績）、HUD、政策清單、英雄面板、日誌、結算。 */
(function () {
  const C = PI_CONFIG, RC = C.round, $ = id => document.getElementById(id);
  const S = PIStore, Gm = PIGame, G = Gm.G;
  const fmt = n => Number(n || 0).toLocaleString('zh-TW');
  // ── 日期（台北時間） ──
  const todayTPE = () => new Date().toLocaleDateString('en-CA', { timeZone: C.election.tz });
  const dayNum = s => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d) / 86400000; };
  const eveDate = () => { const n = dayNum(C.election.date) - 1; const d = new Date(n * 86400000); return d.toISOString().slice(0, 10); };
  const isFinalNow = () => dayNum(todayTPE()) >= dayNum(eveDate());
  const daysToEve = () => dayNum(eveDate()) - dayNum(todayTPE());
  function todaysIssue(list) { if (isFinalNow()) return list.find(i => i.id === C.election.finalIssueId) || list[0]; const today = todayTPE(); const open = list.filter(i => i.startsAt && i.startsAt <= today).sort((a, b) => a.startsAt < b.startsAt ? 1 : -1); return open[0] || list[0]; }

  let issues = [], chosenIssue = null, chosenCls = 'chief', globals = { waves: 0, blocked: 0, heroes: 0, finals: 0 };
  function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  // ── 大廳 ──
  function renderLobby() {
    const h = S.hero() || {}; const me = S.state.me;
    if (!$('nick').value) $('nick').value = h.nick || me.name || '';
    chosenCls = h.cls || chosenCls;
    $('classes').innerHTML = Object.entries(C.heroes).map(([id, H]) => `<button type="button" class="cls" data-id="${id}" aria-pressed="${id === chosenCls}"><span class="ic">${H.icon}</span><span><b>${H.name}</b><small>${H.desc}</small></span></button>`).join('');
    $('classes').querySelectorAll('button').forEach(b => b.addEventListener('click', () => { chosenCls = b.dataset.id; renderLobby(); }));
    const today = todaysIssue(issues); if (!chosenIssue || !issues.find(i => i.id === chosenIssue.id)) chosenIssue = today;
    $('issues').innerHTML = issues.map(i => `<button type="button" class="issue" data-id="${i.id}" aria-pressed="${chosenIssue && i.id === chosenIssue.id}"><span class="tag">${esc(i.tag)}</span><span><b>${esc(i.title)}${i.id === today.id ? ' <em>今日議題</em>' : ''}${i.id === C.election.finalIssueId ? ' <em class="final">大選前夜</em>' : ''}</b><small>${esc(i.desc || '')}${i.startsAt ? '・' + esc(i.startsAt) + ' 起' : ''}</small></span></button>`).join('');
    $('issues').querySelectorAll('button').forEach(b => b.addEventListener('click', () => { chosenIssue = issues.find(i => i.id === b.dataset.id); renderLobby(); }));
    const d = daysToEve();
    $('countdown').textContent = d > 0 ? `距離大選前夜總攻擊還有 ${d} 天（${eveDate()}）` : d === 0 ? '今晚就是總攻擊之夜' : `總攻擊已於 ${eveDate()} 擋下，可隨時重演`;
    $('lobbyStats').innerHTML = `<div><b>${fmt(globals.waves)}</b><span>波綠營攻擊已阻擋</span></div><div><b>${fmt(globals.blocked)}</b><span>隻網軍被擋下</span></div><div><b>${fmt(globals.heroes)}</b><span>位英雄參戰</span></div>`;
    $('mine').textContent = h.rounds ? `你已經守了 ${h.rounds} 波、擊退 ${fmt(h.kills)} 隻。` : '你還沒出戰過。';
    $('modeNote').textContent = S.state.mode === 'remote' ? (S.state.canWrite === false ? '目前只能瀏覽：你的戰績不會計入共享總數。' : '戰績會計入所有人的共享總數。') : '本機模式：戰績只存在這個瀏覽器。';
    $('adminBox').hidden = !(S.state.canEdit && S.state.mode === 'remote');
  }
  function renderBoard(list) { $('board').innerHTML = list.length ? list.map((h, i) => `<li><span class="rank">${i + 1}</span><span class="who">${esc(h.nick || '匿名英雄')} <small>${C.heroes[h.cls] ? C.heroes[h.cls].icon : ''}</small></span><b>${fmt(h.kills)}</b><small>${h.rounds || 0} 波</small></li>`).join('') : '<li class="empty">還沒有人出戰。第一個就是你。</li>'; }
  function renderGlobalStrip() { $('gWaves').textContent = fmt(globals.waves); $('gBlocked').textContent = fmt(globals.blocked); $('gHeroes').textContent = fmt(globals.heroes); const d = daysToEve(); $('gCountdown').textContent = d > 0 ? `總攻擊倒數 ${d} 天` : '總攻擊之夜'; }

  // ── HUD / 側欄 ──
  function renderHud() {
    const inRound = G.phase === 'round'; const left = Math.max(0, RC.seconds - G.t);
    $('timer').textContent = `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, '0')}`;
    $('money').textContent = Math.floor(G.money); $('approval').textContent = Math.max(0, Math.round(G.approval));
    const bar = $('approvalBar'); bar.style.width = Math.max(0, G.approval) + '%'; bar.dataset.level = G.approval > 50 ? 'ok' : G.approval > 25 ? 'mid' : 'low';
    $('waveLabel').textContent = inRound ? `第 ${G.waveNo} 波` : '—'; $('issueLabel').textContent = inRound ? `${G.issue.tag} ${G.issue.title}${G.final ? '・總攻擊' : ''}` : '在大廳選議題出戰';
    $('enemies').textContent = inRound ? `${G.enemies.length} 隻在場・${G.queue.length} 隻在路上` : '';
    $('btnPause').disabled = !inRound; $('btnSpeed').disabled = !inRound;
  }
  function renderPalette() {
    const el = $('palette');
    if (!el.children.length) C.towerOrder.forEach((id, i) => { const Tw = C.towers[id]; const b = document.createElement('button'); b.type = 'button'; b.dataset.id = id; b.style.setProperty('--c', '#' + Tw.color.toString(16).padStart(6, '0') + '33'); b.innerHTML = `<span class="ic">${Tw.icon}</span><span><span class="n">${Tw.name} <kbd>${i + 1}</kbd></span><br><span class="d">${Tw.desc}</span></span><span class="cost">${Tw.cost}<small>億</small></span>`; b.addEventListener('click', () => Gm.selectType(G.selType === id ? null : id)); el.appendChild(b); });
    for (const b of el.children) { b.disabled = G.phase !== 'round' || G.money < C.towers[b.dataset.id].cost; b.setAttribute('aria-pressed', String(G.selType === b.dataset.id)); }
  }
  function renderSel(t) {
    const p = $('selPanel'); if (!t) { p.hidden = true; return; } const Tw = C.towers[t.type], st = Gm.towerStats(t); p.hidden = false;
    $('selName').textContent = `${Tw.icon} ${Tw.name}　Lv.${t.level}`;
    const rows = [['位置', `${C.map.districts[t.key]}區`]];
    if (Tw.dmg) rows.push(['傷害', st.dmg], ['射程', st.range.toFixed(2) + ' 格'], ['間隔', st.rate.toFixed(2) + ' 秒' + (st.support ? `（加速 +${Math.round(st.support * 100)}%）` : '')]);
    if (Tw.slow) rows.push(['減速', Math.round(Tw.slow * 100) + '% / ' + Tw.slowTime + ' 秒']); if (Tw.splash) rows.push(['範圍', Tw.splash + ' 格']); if (Tw.chain) rows.push(['連鎖', Tw.chain + ' 隻']);
    if (Tw.support) rows.push(['加速', `鄰近塔 +${Math.round(Tw.support * t.level * 100)}%`]); if (Tw.income) rows.push(['收入', `每秒 +${(Tw.income * t.level).toFixed(1)} 億`]);
    $('selRows').innerHTML = rows.map(([k, v]) => `<div class="row"><span>${k}</span><b>${v}</b></div>`).join('');
    const up = $('btnUpgrade'); up.disabled = t.level >= RC.maxLevel || G.money < st.upgradeCost; up.textContent = t.level >= RC.maxLevel ? '已滿級' : `升級 ${st.upgradeCost} 億`; $('btnSell').textContent = `拆除 +${st.sell} 億`;
  }
  function renderHero() {
    if (!G.hero) { $('heroPanel').hidden = true; return; } const H = G.hero.cfg; $('heroPanel').hidden = false;
    $('heroName').textContent = `${H.icon} ${G.hero.nick || H.name}（${H.name}）`;
    $('focusCd').textContent = G.focusCd > 0 ? `重擊冷卻 ${G.focusCd.toFixed(1)} 秒` : '點敵人：重擊 ×' + RC.focusDmgMul;
    const u = $('btnUlt'); u.disabled = G.ultCd > 0 || G.phase !== 'round'; u.textContent = G.ultCd > 0 ? `台北人站出來（${Math.ceil(G.ultCd)}s）` : '🙌 台北人站出來';
  }
  function renderLog(log) { $('log').innerHTML = log.map(l => `<li class="${l.cls}"><time>${l.t}</time><span>${esc(l.txt)}</span></li>`).join(''); }
  let bannerT = 0; function banner(txt, secs) { const b = $('banner'); b.textContent = txt; b.classList.add('show'); clearTimeout(bannerT); bannerT = setTimeout(() => b.classList.remove('show'), secs * 1000); }
  let hintT = 0; function hint(txt, bad) { const h = $('hint'); h.textContent = txt || '左側挑一項政策，點地圖空格放下；點敵人可重擊；拖曳旋轉、滾輪縮放。'; h.classList.toggle('bad', !!bad); clearTimeout(hintT); if (bad) hintT = setTimeout(() => hint(''), 1500); }

  // ── 結算 ──
  async function onEnd(res) {
    await S.recordRound(res); globals = S.totals(); renderGlobalStrip();
    $('endTitle').textContent = res.final ? `總攻擊擋下了！第 ${res.waveNo} 波` : `第 ${res.waveNo} 波已阻擋！`;
    $('endText').textContent = `${res.tag}「${res.issueTitle}」的攻勢結束。${res.hero.nick || '英雄'}擊退 ${fmt(res.kills)} 隻，滿意度守在 ${res.approval}%${res.leaked ? `，有 ${res.leaked} 隻擠進市政府門口` : '，一隻都沒進門'}。`;
    $('endGlobal').innerHTML = `我們已經阻擋了 <b>${fmt(globals.waves)}</b> 波綠營攻擊，擋下 <b>${fmt(globals.blocked)}</b> 隻網軍。`;
    $('endStats').textContent = `蓋了 ${res.towers} 座政策塔・突襲 ${res.raids} 次・全民護城 ${res.surges} 次`;
    $('endOverlay').hidden = false;
  }

  // ── 啟動 ──
  function heroFromForm() { const nick = $('nick').value.trim().slice(0, 24); return { nick: nick || C.heroes[chosenCls].name, cls: chosenCls }; }
  async function startRound() {
    const hero = heroFromForm(); await S.saveHero({ nick: hero.nick, cls: hero.cls });
    const final = isFinalNow() || chosenIssue.id === C.election.finalIssueId;
    const waveNo = (S.totals().waves || 0) + 1;
    $('lobby').hidden = true; $('endOverlay').hidden = true; Gm.startRound(chosenIssue, hero, waveNo, final); renderHero(); hint('');
  }
  function bind() {
    $('btnGo').addEventListener('click', startRound);
    $('btnAgain').addEventListener('click', () => { $('endOverlay').hidden = true; startRound(); });
    $('btnLobby').addEventListener('click', () => { $('endOverlay').hidden = true; Gm.backToLobby(); renderLobby(); $('lobby').hidden = false; renderHero(); });
    $('btnPause').addEventListener('click', () => { G.paused = !G.paused; $('btnPause').textContent = G.paused ? '繼續' : '暫停'; $('btnPause').setAttribute('aria-pressed', String(G.paused)); });
    $('btnSpeed').addEventListener('click', () => { G.speed = G.speed === 1 ? 2 : 1; $('btnSpeed').setAttribute('aria-pressed', String(G.speed === 2)); });
    $('btnUpgrade').addEventListener('click', () => G.selTower && Gm.upgradeTower(G.selTower)); $('btnSell').addEventListener('click', () => G.selTower && Gm.sellTower(G.selTower));
    $('btnUlt').addEventListener('click', () => Gm.ultimate());
    $('issueForm').addEventListener('submit', async ev => { ev.preventDefault(); const f = ev.target; const mix = {}; for (const t of f.mix.value.split(',')) { const [k, v] = t.split(':').map(s => s.trim()); if (C.animals[k]) mix[k] = Number(v) || 1; }
      const issue = { id: f.iid.value.trim().replace(/[^a-z0-9_-]/gi, '').slice(0, 32), title: f.title.value.trim().slice(0, 20), tag: f.tag.value.trim().slice(0, 16), startsAt: f.startsAt.value || null, intensity: Number(f.intensity.value) || 1, routes: [...f.querySelectorAll('input[name=routes]:checked')].map(i => i.value), boss: f.boss.value || null, mix: Object.keys(mix).length ? mix : { bird: 1 }, chants: f.chants.value.split(/[,，]/).map(s => s.trim()).filter(Boolean).slice(0, 12), desc: f.desc.value.trim().slice(0, 60) };
      if (!issue.id || !issue.title || !issue.routes.length) { $('issueMsg').textContent = '需要代號、標題、至少一條路線。'; return; }
      try { await S.addIssue(issue); $('issueMsg').textContent = `已新增「${issue.title}」。`; f.reset(); } catch (e) { $('issueMsg').textContent = '新增失敗：' + (e.message || e.code || e); } });
    document.addEventListener('keydown', ev => { if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA') return; if (ev.key === 'Escape') { Gm.selectType(null); Gm.select(null); } else if (ev.key === ' ' && G.phase === 'round') { ev.preventDefault(); $('btnPause').click(); } else if (/^[1-6]$/.test(ev.key)) Gm.selectType(C.towerOrder[+ev.key - 1]); else if (ev.key.toLowerCase() === 'q') Gm.ultimate(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden && G.phase === 'round' && !G.paused) $('btnPause').click(); });
    const stage = $('stage'); stage.addEventListener('pointermove', ev => Gm.hover(ev));
  }
  function boot() {
    PIWorld.init($('stage')); PIWorld.setTimeOfDay(0.3);
    Gm.init({ hud: renderHud, palette: renderPalette, sel: renderSel, log: renderLog, banner, hint, end: onEnd, hero: renderHero });
    bind(); renderHud(); renderPalette(); renderHero(); hint('');
    $('siteName').textContent = C.site.name; $('siteSub').textContent = C.site.sub; $('siteTag').textContent = C.site.tag; $('disclaimer').textContent = C.site.disclaimer + ' ' + C.site.credit;
    S.onGlobal(g => { globals = g; renderGlobalStrip(); if (!$('lobby').hidden) renderLobby(); }); S.onLeaderboard(renderBoard); S.onIssues(list => { issues = list; if (!$('lobby').hidden) renderLobby(); });
    S.init().then(() => renderLobby());
    let last = performance.now(), acc = 0;
    function frame(now) { const raw = Math.min(0.05, (now - last) / 1000); last = now;
      if (G.phase === 'round' && !G.paused) { const dt = raw * G.speed; const sub = Math.ceil(dt / 0.02); for (let i = 0; i < sub; i++) Gm.step(dt / sub); acc += raw; if (acc > 0.2) { acc = 0; renderHud(); renderHero(); if (G.selTower) renderSel(G.selTower); } }
      PIWorld.ambient(raw, now / 1000); PIWorld.render(); requestAnimationFrame(frame); }
    requestAnimationFrame(frame);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
