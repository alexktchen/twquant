"""本機後台的頁面（A-012）。一個檔、不用建置，只有本機的後台會送出它。__TOKEN__ 由伺服器換成這次啟動的通行碼。

畫面照網站的地鐵風格：大字、顏色分類、少字。四個分頁：設定、交易程式（A-014）、演練、終端機。
"""

PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>程式交易後台</title>
<style>
:root { --bg:#0e1014; --tile:#16181f; --line:#262a33; --text:#e8eaed; --mute:#8a8f98; --accent:#ff7a00; --blue:#3d8bfd; --red:#ff5a4f; --yellow:#f0b90b; --green:#2ec27e; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:16px/1.55 system-ui,"Microsoft JhengHei","PingFang TC",sans-serif; }
header { display:flex; align-items:center; gap:12px; padding:14px 18px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
header h1 { font-size:20px; margin:0; }
header .local { font-size:13px; color:var(--mute); }
nav { display:flex; gap:8px; padding:12px 18px 0; }
nav button { font:inherit; font-size:17px; font-weight:600; padding:10px 20px; border-radius:10px; border:0; background:var(--tile); color:var(--mute); cursor:pointer; }
nav button.on { background:rgba(255,122,0,.16); color:var(--accent); }
main { padding:14px 18px 40px; max-width:960px; }
section.card { background:var(--tile); border-radius:12px; padding:14px 16px; margin-bottom:12px; }
section.card h2 { font-size:17px; margin:0 0 10px; display:flex; gap:8px; align-items:center; }
.chip { font-size:13px; padding:2px 10px; border-radius:6px; background:#2a2f3a; color:var(--text); font-weight:600; }
.chip.sim { background:var(--blue); color:#fff; } .chip.live { background:#b3261e; color:#fff; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:10px; }
label.f { display:flex; flex-direction:column; gap:4px; background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
label.f b { font-size:15px; }
label.f small { color:var(--mute); font-size:13px; }
label.f.changed { border-color:var(--accent); }
input, select { font:inherit; font-size:16px; padding:8px 10px; border-radius:8px; border:1px solid var(--line); background:#0b0d11; color:var(--text); width:100%; }
.row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
.btn { font:inherit; font-size:17px; font-weight:600; padding:12px 20px; border-radius:10px; border:0; cursor:pointer; }
.btn.go { background:var(--accent); color:#111; } .btn.stop { background:#b3261e; color:#fff; } .btn.plain { background:#2a2f3a; color:var(--text); }
.btn:disabled { opacity:.5; cursor:default; }
.msg { padding:10px 14px; border-radius:10px; margin:10px 0; font-size:15px; }
.msg.ok { background:rgba(61,139,253,.15); border:1px solid rgba(61,139,253,.5); }
.msg.bad { background:rgba(255,90,79,.14); border:1px solid rgba(255,90,79,.55); }
.msg.warn { background:rgba(240,185,11,.12); border:1px solid rgba(240,185,11,.5); }
.lamp { display:flex; align-items:center; gap:10px; font-size:18px; font-weight:600; }
.lamp i { width:14px; height:14px; border-radius:50%; background:var(--mute); display:inline-block; }
.lamp.on i { background:var(--green); } .lamp.off i { background:#6b7280; }
pre.log { background:#0b0d11; border:1px solid var(--line); border-radius:10px; padding:10px 12px; font:13.5px/1.5 ui-monospace,Consolas,monospace; white-space:pre-wrap; overflow-wrap:anywhere; max-height:340px; overflow:auto; margin:6px 0 0; }
.muted { color:var(--mute); font-size:14px; }
[hidden] { display:none !important; }
pre.term { max-height:none; height:calc(100vh - 470px); min-height:260px; }
dl.where { display:grid; grid-template-columns:max-content 1fr; gap:6px 14px; margin:0; font-size:15px; }
dl.where dt { color:var(--mute); } dl.where dd { margin:0; font-family:ui-monospace,Consolas,monospace; font-size:13.5px; overflow-wrap:anywhere; }
@media (max-width:600px) { dl.where { grid-template-columns:1fr; gap:2px; } dl.where dd { margin-bottom:8px; } }
pre.term .t { color:var(--mute); } pre.term .warn { color:#f0b90b; }
.steps { margin:0; padding-left:20px; color:var(--mute); font-size:15px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
.tile { background:#0b0d11; border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
.tile small { display:block; color:var(--mute); font-size:13.5px; } .tile b { font-size:22px; }
.up { color:#f6465d; } .down { color:#2ebd85; }
table.fills { width:100%; border-collapse:collapse; margin-top:12px; font-size:15px; }
table.fills th { text-align:left; color:var(--mute); font-weight:500; font-size:13.5px; padding:4px 8px 4px 0; }
table.fills td { padding:6px 8px 6px 0; border-top:1px solid var(--line); }
label.check span { display:flex; align-items:center; gap:8px; } label.check input { width:20px; height:20px; }
@media (max-width:600px) { main, header, nav { padding-left:12px; padding-right:12px; } nav { gap:6px; } nav button { padding:10px 11px; font-size:16px; white-space:nowrap; } }
</style>
</head>
<body>
<header><h1>程式交易後台</h1><span class="local">只在這台電腦上開得到。網站只能按緊急停止；改設定、開演練都在這裡。</span></header>
<nav><button id="tab-settings" class="on">設定</button><button id="tab-run">交易程式</button><button id="tab-drill">演練</button><button id="tab-console">終端機</button></nav>
<main>
  <div id="flash"></div>
  <div id="view-settings"></div>
  <div id="view-run" hidden></div>
  <div id="view-drill" hidden></div>
  <div id="view-console" hidden></div>
</main>
<script>
const TOKEN = "__TOKEN__";
let state = null, tab = "settings", pick = null, edits = {};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
function flash(kind, text) { $("flash").innerHTML = text ? `<div class="msg ${kind}">${esc(text)}</div>` : ""; }
async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Admin-Token": TOKEN }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({ detail: `${res.status}` }));
  if (!res.ok) throw new Error(data.detail || `${res.status}`);
  return data;
}
async function load() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    state = await res.json();
    if (!pick && state.configs.length) pick = state.configs[0].name;
    render();
  } catch (e) { flash("bad", "讀不到後台的資料：" + e.message); }
}
const TABS = ["settings", "run", "drill", "console"];
function render() { ({ settings: renderSettings, run: renderRun, drill: renderDrill, console: renderConsole })[tab](); }
const PROC_NAME = { ticks: "行情中繼", relay: "指令中繼", run: "交易程式", drill: "演練程式" };
const STATE_NAME = { ok: "正常", halted: "停手", suspended: "暫停送單", link_down: "券商連線中斷" };
const money = (n) => (n > 0 ? "+" : "") + Math.round(n).toLocaleString("en-US");
let runForm = { source: "live" };
const readRunForm = () => ({ source: $("r-src").value, cfg: $("r-cfg").value, dsn: $("r-dsn").value, notify: $("r-notify").checked, release: $("r-release").checked, day: $("r-day").value, from: $("r-from").value, speed: $("r-speed").value });
function renderRun() {
  const v = $("view-run");
  const r = state.run;
  const running = r && r.running;
  const playing = r && r.source === "play";
  const sims = state.configs.filter((c) => c.ok && c.mode === "sim" && c.export);
  const days = state.ticks.filter((t) => t.feed === "recorded");
  // 表單打到一半不要被每 2 秒的更新洗掉
  if (!running && $("r-cfg")) runForm = readRunForm();
  let html = `<section class="card"><div class="lamp ${running ? "on" : "off"}"><i></i>${running ? (playing ? "回放中（模擬）" : "交易程式在跑（模擬）") : "交易程式沒在跑"}</div>`;
  if (r) html += `<p class="muted">${esc(r.started_at.slice(11, 19))} 啟動・${esc(r.config)}・${playing ? `回放 ${esc(r.play_day)} ${esc(r.start)} 起・${r.speed} 倍速・不上網站、不發通知` : `今天的即時行情・指令中繼連${r.relay === "TWQUANT_SUPABASE_DSN" ? "雲端" : "本機"}資料庫・${r.notify ? "發" : "不發"}手機通知`}</p>`;
  html += `</section>`;
  const st = r && r.status;
  if (st && st.error) html += `<div class="msg bad">${esc(st.error)}</div>`;
  else if (st) {
    const tone = (n) => (n > 0 ? "up" : n < 0 ? "down" : "");
    html += `<section class="card"><h2>${esc(st.day)} 的狀況<span class="chip">${st.status === "closed" ? "已收盤" : "最後一筆行情 " + esc(st.last_ts.slice(11, 19))}</span></h2><div class="tiles">
      <div class="tile"><small>部位</small><b>${st.position === 0 ? "空手" : (st.position > 0 ? "多 " : "空 ") + Math.abs(st.position) + " 口"}</b></div>
      <div class="tile"><small>今天損益（元）</small><b class="${tone(st.day_pnl_twd)}">${money(st.day_pnl_twd)}</b></div>
      <div class="tile"><small>成交</small><b>${st.fill_count} 筆</b></div>
      <div class="tile"><small>程式狀態</small><b class="${st.state === "ok" ? "" : "down"}">${esc(STATE_NAME[st.state] || st.state)}</b></div>
      <div class="tile"><small>最新價</small><b>${Number(st.last_price).toLocaleString("en-US")}</b></div>
    </div>`;
    if (st.fills.length) html += `<table class="fills"><tr><th>時間</th><th>買賣</th><th>口數</th><th>價格</th><th>這筆之後多的損益</th></tr>${st.fills.map((f) => `<tr><td>${esc(f.ts.slice(11, 19))}</td><td>${f.side === "BUY" ? "買" : "賣"}</td><td>${f.qty}</td><td>${Number(f.price).toLocaleString("en-US")}</td><td class="${tone(f.pnl_twd)}">${f.pnl_twd ? money(f.pnl_twd) : ""}</td></tr>`).join("")}</table>`;
    html += `</section>`;
  }
  if (r && !running && !r.stopped && r.procs.run !== 0) {
    const last = (r.logs.run || []).filter((l) => l.trim()).slice(-1)[0] || "沒有輸出";
    html += `<div class="msg bad">交易程式沒跑完（結束碼 ${r.procs.run}）：${esc(last)}</div>`;
  }
  if (!running) {
    const f = runForm;
    const live = f.source !== "play";
    const cfgs = sims.map((c) => `<option ${c.name === (f.cfg || pick) ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    const dayOpts = days.map((t) => `<option value="${esc(t.name)}" ${t.name === f.day ? "selected" : ""}>${esc(t.day)}</option>`).join("");
    html += `<section class="card"><h2>啟動交易程式<span class="chip sim">模擬</span></h2><p class="muted">現在只能跑模擬：真的下單要等元大的行情與下單接上。模擬用的是模擬券商，不會送出任何委託。</p><div class="grid">
      <label class="f"><b>設定檔</b><select id="r-cfg">${cfgs}</select><small>只列模擬的設定檔</small></label>
      <label class="f"><b>行情</b><select id="r-src"><option value="live" ${live ? "selected" : ""}>今天的即時行情（富邦錄製程式正在錄的）</option><option value="play" ${live ? "" : "selected"}>回放錄好的一天（先看它怎麼跑）</option></select><small>${live ? "要先把富邦錄製程式開起來；開盤前啟動會等到開盤" : "只在這台電腦看：不上網站、不發通知、不聽遠端停止"}</small></label>
      <label class="f" ${live ? "" : "hidden"}><b>指令中繼連哪個資料庫</b><select id="r-dsn"><option value="TWQUANT_SUPABASE_DSN" ${f.dsn === "TWQUANT_PG_DSN" ? "" : "selected"}>雲端（網站看得到、手機按得到緊急停止）</option><option value="TWQUANT_PG_DSN" ${f.dsn === "TWQUANT_PG_DSN" ? "selected" : ""}>本機（只在這台電腦測）</option></select></label>
      <label class="f check" ${live ? "" : "hidden"}><span><input type="checkbox" id="r-notify" ${f.notify === false ? "" : "checked"}> <b>發手機通知</b></span><small>成交與異常會送到 Telegram，開頭標「模擬」</small></label>
      <label class="f check" ${live ? "" : "hidden"}><span><input type="checkbox" id="r-release" ${f.release ? "checked" : ""}> <b>解除今天的停止</b></span><small>今天按過遠端緊急停止的話，要勾這個才會再交易；沒按過不用勾</small></label>
      <label class="f" ${live ? "hidden" : ""}><b>回放哪一天</b><select id="r-day">${dayOpts}</select><small>富邦即時錄的</small></label>
      <label class="f" ${live ? "hidden" : ""}><b>從幾點開始</b><input id="r-from" value="${esc(f.from || "08:44:50")}"><small>格式 HH:MM:SS；之前的行情會一次補上</small></label>
      <label class="f" ${live ? "hidden" : ""}><b>幾倍速</b><input id="r-speed" value="${esc(f.speed || "60")}" inputmode="decimal"><small>60＝一分鐘播一小時；1＝跟真的一樣快</small></label>
    </div>`;
    let why = "";
    if (!sims.length) why = "沒有可以用的模擬設定檔。";
    else if (!state.run_ready.ms_repo) why = "後台啟動時沒有給動能順勢原版程式的資料夾（--ms-repo）。";
    else if (!live && !days.length) why = "找不到富邦錄好的逐筆。後台啟動時要給 --recorded-dir。";
    else if (state.drill && state.drill.running) why = "演練正在跑，先到「演練」分頁停掉。";
    if (why) html += `<div class="msg bad">${why}</div>`;
    html += `<div class="row" style="margin-top:12px"><button class="btn go" id="r-start" ${why ? "disabled" : ""}>${live ? "啟動交易程式" : "開始回放"}</button></div></section>`;
  } else {
    html += `<div class="row"><button class="btn stop" id="r-stop">${playing ? "停止回放" : "結束交易程式"}</button><span class="muted">${playing ? "" : "結束只是把程式關掉；模擬的部位不會另外平倉，重開會從今天第一筆行情重算。"}</span></div>`;
  }
  if (r) for (const name of ["run", "ticks", "relay"]) {
    if (!(name in r.procs)) continue;
    const code = r.procs[name];
    html += `<section class="card"><h2>${PROC_NAME[name]}<span class="chip">${code === null ? "在跑" : name !== "run" && !running ? "已收掉" : `已結束（結束碼 ${code}）`}</span></h2><pre class="log" data-log="${name}">${esc(r.logs[name].join("\n") || "（還沒有輸出）")}</pre></section>`;
  }
  const scroll = {};
  v.querySelectorAll("pre.log").forEach((p) => { scroll[p.dataset.log] = p.scrollHeight - p.scrollTop - p.clientHeight < 40 ? null : p.scrollTop; });
  v.innerHTML = html;
  v.querySelectorAll("pre.log").forEach((p) => { const top = scroll[p.dataset.log]; p.scrollTop = top == null ? p.scrollHeight : top; });
  if ($("r-src")) $("r-src").onchange = () => { runForm.source = $("r-src").value; renderRun(); };
  if ($("r-start")) $("r-start").onclick = startRun;
  if ($("r-stop")) $("r-stop").onclick = async () => { $("r-stop").disabled = true; flash("ok", "請交易程式結束中…"); try { await post("/api/run/stop", {}); flash("ok", "交易程式已結束。"); await load(); } catch (e) { flash("bad", e.message); } };
}
async function startRun() {
  runForm = readRunForm();
  const live = $("r-src").value === "live";
  const body = live
    ? { config: $("r-cfg").value, source: "live", dsn_env: $("r-dsn").value, notify: $("r-notify").checked, release_stop: $("r-release").checked }
    : { config: $("r-cfg").value, source: "play", ticks: $("r-day").value, start: $("r-from").value.trim(), speed: $("r-speed").value.trim() };
  try { await post("/api/run/start", body); flash("ok", live ? "交易程式啟動了。網站「台指期 → 程式交易」的「模擬」會變成在跑。" : "回放開始了。"); await load(); }
  catch (e) { flash("bad", "沒有啟動：" + e.message); }
}
function renderConsole() {
  const v = $("view-console");
  const old = v.querySelector("pre.term");
  // 使用者往上捲在看舊的，就不要把畫面拉回最底
  const follow = !old || old.scrollHeight - old.scrollTop - old.clientHeight < 40;
  const top = old ? old.scrollTop : 0;
  const lines = state.console.map((l) => `<span class="t">${esc(l.t)}</span> <span class="${l.level === "warn" ? "warn" : ""}">${esc(l.text)}</span>`);
  const p = state.process, d = state.drill;
  const row = (k, val) => `<dt>${k}</dt><dd>${esc(val)}</dd>`;
  let where = `<section class="card"><h2>後台跑在哪裡</h2><dl class="where">${row("執行的程式", p.script)}${row("整行指令", p.command)}${row("用的 Python", p.python)}${row("在哪個資料夾執行", p.cwd)}${row("程序編號", p.pid + "（工作管理員裡找得到）")}${row("幾點開的", p.started_at.replace("T", " ").slice(0, 19))}</dl></section>`;
  for (const [job, on, off] of [[state.run, state.run && state.run.source === "play" ? "回放中" : "交易程式在跑", "上一次的交易程式"], [d, "演練進行中", "上一場演練"]]) {
    if (!job || !Object.keys(job.commands).length) continue;
    where += `<section class="card"><h2>後台開出來的程式<span class="chip">${job.running ? on : off}</span></h2><dl class="where">`;
    for (const [name, cmd] of Object.entries(job.commands)) where += row((job === d && name === "relay" ? "中繼程式" : PROC_NAME[name]) + (job.procs[name] === null ? "・在跑" : `・已結束（${job.procs[name]}）`) + "・程序編號 " + job.pids[name], cmd);
    where += row("輸出放在", job.folder) + `</dl></section>`;
  }
  v.innerHTML = where + `<section class="card"><h2>終端機<span class="chip">跟執行後台的那個視窗印的一樣・每 2 秒更新</span></h2><pre class="log term">${lines.join("\n") || "（還沒有輸出）"}</pre></section>`;
  const pre = v.querySelector("pre.term");
  pre.scrollTop = follow ? pre.scrollHeight : top;
}
function currentConfig() { return state.configs.find((c) => c.name === pick) || null; }
function renderSettings() {
  const v = $("view-settings");
  if (!state.configs.length) { v.innerHTML = `<p class="muted">設定資料夾裡沒有 .toml 設定檔。</p>`; return; }
  const cfg = currentConfig();
  const options = state.configs.map((c) => `<option ${c.name === pick ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  let html = `<section class="card"><div class="row"><b>設定檔</b><select id="cfg">${options}</select>`;
  if (cfg && cfg.mode) html += `<span class="chip ${cfg.mode}">${cfg.mode === "live" ? "真單" : "模擬"}</span>`;
  html += `</div><p class="muted">模擬／真單不能在這裡切換：真單要在檔案裡改，啟動時還要過真單的檢查。存檔前會用跟交易程式一樣的規則檢查，不合格不會存；存檔會留一份 .bak。改了設定，要重新啟動交易程式才會生效。</p></section>`;
  if (cfg && !cfg.ok) html += `<div class="msg bad">這個設定檔現在就不合格：${esc(cfg.error)}</div>`;
  if (cfg && cfg.ok) {
    const groups = {};
    for (const f of state.fields) { if (!cfg.has.includes(f.id)) continue; (groups[f.section] ||= []).push(f); }
    const names = { risk: "風控", contract: "商品與成本", sim: "模擬假設", control: "遠端緊急停止", momentum: "動能順勢", notify: "手機通知", export: "網站狀態" };
    for (const [sec, fields] of Object.entries(groups)) {
      html += `<section class="card"><h2>${esc(names[sec] || sec)}</h2><div class="grid">`;
      for (const f of fields) {
        const val = f.id in edits ? edits[f.id] : cfg.values[f.id];
        const changed = f.id in edits && edits[f.id] !== cfg.values[f.id];
        let input;
        if (f.kind === "bool") input = `<select data-id="${f.id}"><option value="true" ${val === "true" ? "selected" : ""}>是</option><option value="false" ${val === "false" ? "selected" : ""}>否</option></select>`;
        else input = `<input data-id="${f.id}" value="${esc(val)}" placeholder="${f.optional ? "留空＝不設" : ""}" inputmode="${f.kind === "time" || f.kind === "text" ? "text" : "decimal"}">`;
        html += `<label class="f ${changed ? "changed" : ""}"><b>${esc(f.label)}</b>${input}<small>${esc(f.help)}</small></label>`;
      }
      html += `</div></section>`;
    }
    const n = Object.keys(edits).filter((k) => edits[k] !== cfg.values[k]).length;
    html += `<div class="row"><button class="btn go" id="save" ${n ? "" : "disabled"}>存檔（改了 ${n} 項）</button><button class="btn plain" id="undo" ${n ? "" : "disabled"}>放棄修改</button></div>`;
  }
  v.innerHTML = html;
  $("cfg").onchange = (e) => { pick = e.target.value; edits = {}; flash(); render(); };
  v.querySelectorAll("[data-id]").forEach((el) => { el.onchange = el.oninput = () => { edits[el.dataset.id] = el.value.trim(); updateCount(); }; });
  if ($("save")) $("save").onclick = save;
  if ($("undo")) $("undo").onclick = () => { edits = {}; flash(); render(); };
}
function updateCount() {
  const cfg = currentConfig();
  const n = Object.keys(edits).filter((k) => edits[k] !== cfg.values[k]).length;
  const s = $("save"), u = $("undo");
  if (s) { s.disabled = !n; s.textContent = `存檔（改了 ${n} 項）`; }
  if (u) u.disabled = !n;
  document.querySelectorAll("[data-id]").forEach((el) => el.closest("label").classList.toggle("changed", el.dataset.id in edits && edits[el.dataset.id] !== cfg.values[el.dataset.id]));
}
async function save() {
  const cfg = currentConfig();
  const changes = {};
  for (const [k, val] of Object.entries(edits)) if (val !== cfg.values[k]) changes[k] = val;
  try {
    const r = await post("/api/settings", { config: pick, changes });
    edits = {};
    flash("ok", r.changed.length ? "已存檔：" + r.changed.join("；") + "。要重新啟動交易程式才會生效。" : "沒有改到任何值。");
    await load();
  } catch (e) { flash("bad", "沒有存檔：" + e.message); }
}
function renderDrill() {
  const v = $("view-drill");
  const d = state.drill;
  const running = d && d.running;
  const sims = state.configs.filter((c) => c.ok && c.control);
  let html = `<section class="card"><h2>演練遠端緊急停止</h2><ol class="steps"><li>按「開始演練」：後台會開中繼程式與演練程式（全程模擬，不碰元大）。</li><li>演練程式先買 1 口抱著。</li><li>到網站「台指期 → 程式交易」按「緊急停止」，看「模擬」那一行：在聽 → 等它收到 → 已收到 → 已停手，沒有部位。</li></ol></section>`;
  html += `<section class="card"><div class="lamp ${running ? "on" : "off"}"><i></i>${running ? "演練進行中" : "沒有演練在跑"}</div>`;
  if (d) html += `<p class="muted">${esc(d.started_at.slice(11, 19))} 開始・用${d.feed === "recorded" ? "富邦即時錄的" : "期交所盤後檔"} ${esc(d.day)} ${esc(d.start)} 之後的價格・${d.speed} 倍速・最多 ${d.minutes} 分鐘・${d.relay ? "中繼程式連 " + (d.relay === "TWQUANT_SUPABASE_DSN" ? "雲端" : "本機") + "資料庫" : "沒開中繼程式"}</p>`;
  html += `</section>`;
  if (d && !running && !d.stopped && d.procs.drill !== 0) {
    const last = (d.logs.drill || []).filter((l) => l.trim()).slice(-1)[0] || "沒有輸出";
    html += `<div class="msg bad">上一場演練沒跑完（結束碼 ${d.procs.drill}）：${esc(last)}</div>`;
  }
  if (!running) {
    const ticks = state.ticks.slice(0, 60).map((t) => `<option value="${esc(t.name)}">${esc(t.day)}・${t.feed === "recorded" ? "富邦即時錄的" : "期交所盤後檔"}</option>`).join("");
    const cfgs = sims.map((c) => `<option ${c.name === pick ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    html += `<section class="card"><h2>開一場演練</h2><div class="grid">
      <label class="f"><b>設定檔</b><select id="d-cfg">${cfgs}</select><small>要有遠端緊急停止那一段</small></label>
      <label class="f"><b>拿哪一天的價格</b><select id="d-ticks">${ticks}</select><small>富邦即時錄的排前面</small></label>
      <label class="f"><b>從那一天的幾點開始播</b><input id="d-from" value="09:00:00"><small>格式 HH:MM:SS</small></label>
      <label class="f"><b>最多跑幾分鐘</b><input id="d-min" value="10" inputmode="decimal"></label>
      <label class="f"><b>幾倍速</b><input id="d-speed" value="1" inputmode="decimal"><small>1＝跟真的一樣快</small></label>
      <label class="f"><b>中繼程式連哪個資料庫</b><select id="d-dsn"><option value="TWQUANT_SUPABASE_DSN">雲端（網站按得到）</option><option value="TWQUANT_PG_DSN">本機（只在這台電腦測）</option><option value="">不開中繼程式</option></select><small>${state.env_file ? "連線字串由中繼程式自己從 .env 讀" : "後台啟動時沒給 .env，中繼程式只看環境變數"}</small></label>
    </div>`;
    if (!sims.length) html += `<div class="msg bad">沒有可以演練的設定檔（要合格、而且有遠端緊急停止那一段）。</div>`;
    if (!state.ticks.length) html += `<div class="msg bad">找不到逐筆檔。後台啟動時要給 --ticks-dir。</div>`;
    html += `<div class="row" style="margin-top:12px"><button class="btn go" id="d-start" ${sims.length && state.ticks.length ? "" : "disabled"}>開始演練</button></div></section>`;
  } else {
    html += `<div class="row"><button class="btn stop" id="d-stop">停止演練</button></div>`;
  }
  if (d) for (const [name, lines] of Object.entries(d.logs)) {
    const code = d.procs[name];
    const title = name === "drill" ? "演練程式" : "中繼程式";
    const tail = code === null ? "在跑" : `已結束（結束碼 ${code}）`;
    html += `<section class="card"><h2>${title}<span class="chip">${tail}</span></h2><pre class="log">${esc(lines.join("\n") || "（還沒有輸出）")}</pre></section>`;
  }
  v.innerHTML = html;
  if ($("d-start")) $("d-start").onclick = startDrill;
  if ($("d-stop")) $("d-stop").onclick = async () => { try { await post("/api/drill/stop", {}); flash("ok", "演練已停止。"); await load(); } catch (e) { flash("bad", e.message); } };
  v.querySelectorAll("pre.log").forEach((p) => { p.scrollTop = p.scrollHeight; });
}
async function startDrill() {
  const body = { config: $("d-cfg").value, ticks: $("d-ticks").value, start: $("d-from").value.trim(), minutes: $("d-min").value.trim(), speed: $("d-speed").value.trim(), dsn_env: $("d-dsn").value };
  try { await post("/api/drill/start", body); flash("ok", "演練開始了。到網站按「緊急停止」試試看。"); await load(); }
  catch (e) { flash("bad", "演練沒開起來：" + e.message); }
}
for (const name of TABS) $("tab-" + name).onclick = () => {
  tab = name;
  for (const other of TABS) {
    $("tab-" + other).classList.toggle("on", name === other);
    $("view-" + other).hidden = name !== other;
  }
  flash();
  render();
};
load();
setInterval(() => { if (tab !== "settings" && document.visibilityState === "visible") load(); }, 2000);
</script>
</body>
</html>
"""
