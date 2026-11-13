"""本機後台的頁面（A-012）。一個檔、不用建置，只有本機的後台會送出它。__TOKEN__ 由伺服器換成這次啟動的通行碼。

畫面照網站的地鐵風格：大字、顏色分類、少字。兩個分頁：設定、演練。
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
pre.log { background:#0b0d11; border:1px solid var(--line); border-radius:10px; padding:10px 12px; font:13.5px/1.5 ui-monospace,Consolas,monospace; white-space:pre-wrap; max-height:340px; overflow:auto; margin:6px 0 0; }
.muted { color:var(--mute); font-size:14px; }
.steps { margin:0; padding-left:20px; color:var(--mute); font-size:15px; }
@media (max-width:600px) { main, header, nav { padding-left:12px; padding-right:12px; } }
</style>
</head>
<body>
<header><h1>程式交易後台</h1><span class="local">只在這台電腦上開得到。網站只能按緊急停止；改設定、開演練都在這裡。</span></header>
<nav><button id="tab-settings" class="on">設定</button><button id="tab-drill">演練</button></nav>
<main>
  <div id="flash"></div>
  <div id="view-settings"></div>
  <div id="view-drill" hidden></div>
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
function render() { if (tab === "settings") renderSettings(); else renderDrill(); }
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
  if (d) html += `<p class="muted">${esc(d.started_at.slice(11, 19))} 開始・用 ${esc(d.day)} ${esc(d.start)} 之後的價格・${d.speed} 倍速・最多 ${d.minutes} 分鐘・${d.relay ? "中繼程式連 " + (d.relay === "TWQUANT_SUPABASE_DSN" ? "雲端" : "本機") + "資料庫" : "沒開中繼程式"}</p>`;
  html += `</section>`;
  if (!running) {
    const ticks = state.ticks.slice(0, 60).map((t) => `<option value="${esc(t.name)}">${esc(t.day)}</option>`).join("");
    const cfgs = sims.map((c) => `<option ${c.name === pick ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    html += `<section class="card"><h2>開一場演練</h2><div class="grid">
      <label class="f"><b>設定檔</b><select id="d-cfg">${cfgs}</select><small>要有遠端緊急停止那一段</small></label>
      <label class="f"><b>拿哪一天的價格</b><select id="d-ticks">${ticks}</select><small>期交所逐筆檔</small></label>
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
for (const name of ["settings", "drill"]) $("tab-" + name).onclick = () => {
  tab = name;
  $("tab-settings").classList.toggle("on", name === "settings");
  $("tab-drill").classList.toggle("on", name === "drill");
  $("view-settings").hidden = name !== "settings";
  $("view-drill").hidden = name !== "drill";
  flash();
  render();
};
load();
setInterval(() => { if (tab === "drill" && document.visibilityState === "visible") load(); }, 2000);
</script>
</body>
</html>
"""
