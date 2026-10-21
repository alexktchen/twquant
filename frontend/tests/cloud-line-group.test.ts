// T-423：「LINE 群組」分頁——角色權限、輸入檢查、顯示小函式；憑證永遠不出資料庫。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { CARD_TOKEN_RE, HOME_ID_RE, LOGIN_WORKFLOW, POST_KINDS, dispatchRequest, loginBusy, parsePost, parseToggle } from '../functions/_lib/line-admin.ts'
import { FEATURE_ROLES, ROLE_MEMBERS, can, featuresOf, rolesOf } from '../functions/_lib/roles.ts'
import { KIND_LABEL, findGroups, groupsMeta, latestPostId, latestSync, taipeiToday, loginActive, loginStep, shortTime, statusView, type LineLogin } from '../src/cloud/linegroup.ts'
import { tabFromUrl } from '../src/cloud/tabs.ts'

test('T-423 AC9 權限：只有 line_admin（目前 cktalex@gmail.com）能用 LINE 群組，大小寫不分', () => {
  assert.deepEqual(ROLE_MEMBERS.line_admin, ['cktalex@gmail.com'])
  assert.deepEqual(FEATURE_ROLES.line, ['line_admin'])
  assert.deepEqual(rolesOf('CKTalex@gmail.com '), ['line_admin'])
  assert.deepEqual(featuresOf('cktalex@gmail.com'), ['line'])
  assert.ok(can('cktalex@gmail.com', 'line'))
  assert.ok(!can('someone@example.com', 'line'))
  assert.deepEqual(featuresOf(null), [])
  assert.deepEqual(featuresOf(''), [])
  for (const members of Object.values(ROLE_MEMBERS)) {
    for (const e of members) assert.equal(e, e.trim().toLowerCase()) // 名單一律小寫，比對才準
  }
})

test('T-423 AC9 權限：每個 LINE 端點都自己擋（不只靠前端藏分頁）', () => {
  for (const f of ['../functions/api/line/index.ts', '../functions/api/line/groups/[id].ts', '../functions/api/line/login.ts', '../functions/api/line/sync.ts', '../functions/api/line/post.ts']) {
    const src = readFileSync(new URL(f, import.meta.url), 'utf8')
    assert.match(src, /accessEmail\(request\)/, f) // 身分來自驗過的權杖，不信標頭
    assert.match(src, /can\(email, 'line'\)/, f)
    assert.match(src, /fail\(403, '沒有權限'\)/, f)
  }
  for (const f of ['../functions/api/line/groups/[id].ts', '../functions/api/line/login.ts', '../functions/api/line/sync.ts', '../functions/api/line/post.ts']) {
    assert.match(readFileSync(new URL(f, import.meta.url), 'utf8'), /sameOrigin\(request\)/, f) // 會改東西的只收本站請求
  }
})

test('T-423 AC10 安全：網站讀帳號表只選要顯示的欄位，登入憑證不出資料庫', () => {
  const src = readFileSync(new URL('../functions/_lib/line-admin.ts', import.meta.url), 'utf8')
  const sql = src.replace(/\/\/.*$/gm, '') // 註解裡提到「憑證」不算
  assert.doesNotMatch(sql, /access_token|refresh_token|token_expire|SELECT \*/)
})

test('T-423 AC10 輸入檢查：群組 id、圖卡 token、勾選內容', () => {
  assert.ok(HOME_ID_RE.test(`c${'0'.repeat(32)}`))
  assert.ok(!HOME_ID_RE.test(`C${'0'.repeat(32)}`) && !HOME_ID_RE.test("c1';--") && !HOME_ID_RE.test(`s${'0'.repeat(32)}`))
  assert.ok(CARD_TOKEN_RE.test('ab'.repeat(16)) && !CARD_TOKEN_RE.test('../etc/passwd') && !CARD_TOKEN_RE.test('ab'.repeat(15)))
  assert.deepEqual(parseToggle('{"enabled":true}'), { enabled: true })
  assert.deepEqual(parseToggle('{"enabled":false}'), { enabled: false })
  for (const bad of ['', 'null', '[]', '{"enabled":"true"}', '{"enabled":true,"name":"x"}', '{', ' '.repeat(300)]) {
    assert.equal(parseToggle(bad), null, bad)
  }
})

test('T-423 分頁與顯示：?tab=line 認得；狀態、時間、群組摘要', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=line'), 'line')
  assert.deepEqual(statusView({ status: 'sent', via: 'flex' }), { text: '已發', tone: 'ok' })
  assert.deepEqual(statusView({ status: 'sent', via: 'text' }), { text: '已發（改發文字）', tone: 'wait' })
  assert.equal(statusView({ status: 'unknown', via: null }).tone, 'bad')
  assert.equal(statusView({ status: 'queued', via: null }).text, '排隊中')
  assert.equal(shortTime('2026-10-21 17:40'), '10/21 17:40')
  assert.equal(shortTime(null), '')
  const g = (home_id: string, enabled: boolean, present: boolean) => ({ home_id, name: home_id, enabled, present, synced_at: '' })
  assert.equal(groupsMeta([g('a', true, true), g('b', false, true), g('c', true, false)]), '發 1／在 2 個群組')
  assert.equal(groupsMeta([]), '還沒有群組')
  // 同步好沒有：看清單最後一次同步的時間有沒有變
  assert.equal(latestSync([{ ...g('a', true, true), synced_at: '2026-10-21 09:00' }, { ...g('b', true, true), synced_at: '2026-10-21 22:05' }]), '2026-10-21 22:05')
  assert.equal(latestSync([]), '')
  // 找群組：名字含關鍵字（不分大小寫），勾了的永遠留著
  const list = [{ ...g('c1', false, true), name: 'Family' }, { ...g('c2', true, true), name: '股友會' }, { ...g('c3', false, true), name: '家長群' }]
  assert.deepEqual(findGroups(list, ' fam ').map((x) => x.home_id), ['c1', 'c2'])
  assert.deepEqual(findGroups(list, '').map((x) => x.home_id), ['c1', 'c2', 'c3'])
})

const login = (status: string, fresh = true): LineLogin => ({
  status,
  requested_by: 'cktalex@gmail.com',
  requested_at: '2026-10-21 21:00',
  updated_at: '2026-10-21 21:01',
  run_url: null,
  qr_url: null,
  qr_png: null,
  pin: status === 'pin' ? '4321' : null,
  display_name: status === 'done' ? '小幫手' : null,
  error: status === 'failed' ? '等太久沒掃 QR' : null,
  fresh,
})

test('T-423 AC11 從網站登入：進行中不再觸發、10 分鐘沒動靜可以重按', () => {
  for (const st of ['requested', 'starting', 'scan', 'pin']) {
    assert.ok(loginBusy(login(st)), st)
    assert.ok(loginActive(login(st)), st)
    assert.ok(!loginBusy(login(st, false)), `${st} 過期`)
  }
  for (const st of ['done', 'failed']) assert.ok(!loginBusy(login(st)) && !loginActive(login(st)), st)
  assert.ok(!loginBusy(null) && !loginActive(null))
  assert.match(loginStep(login('scan')), /備用帳號/)
  assert.match(loginStep(login('done')), /已登入「小幫手」/)
  assert.match(loginStep(login('failed')), /等太久沒掃 QR/)
  assert.match(loginStep(login('scan', false)), /可以重按/)
})

test('T-423 AC11 觸發 GitHub Actions：只打 line-login.yml、main、沒 token 就大聲失敗', () => {
  const r = dispatchRequest('tok')
  assert.equal(r.url, `https://api.github.com/repos/alexktchen/twquant/actions/workflows/${LOGIN_WORKFLOW}/dispatches`)
  assert.equal(LOGIN_WORKFLOW, 'line-login.yml')
  assert.deepEqual(JSON.parse(String(r.init.body)), { ref: 'main', inputs: { task: 'login' } })
  assert.deepEqual(JSON.parse(String(dispatchRequest('tok', 'groups').init.body)), { ref: 'main', inputs: { task: 'groups' } })
  assert.equal((r.init.headers as Record<string, string>).Authorization, 'Bearer tok')
  assert.throws(() => dispatchRequest(undefined), /TWQUANT_DISPATCH_TOKEN/)
  assert.throws(() => dispatchRequest(''), /TWQUANT_DISPATCH_TOKEN/)
  // 工作流程檔真的在
  const wf = readFileSync(new URL(`../../.github/workflows/${LOGIN_WORKFLOW}`, import.meta.url), 'utf8')
  assert.ok(wf.includes('line-group login') && wf.includes('line-group groups'))
  assert.match(wf, /options: \[login, groups, post\]/)
})

test('T-423 AC12 手動發送：只收四種與合法日期、輸入走環境變數不直接展開進指令', () => {
  assert.deepEqual(parsePost('{"kind":"picks","date":"2026-10-21"}'), { kind: 'picks', date: '2026-10-21' })
  for (const bad of ['', '{"kind":"picks"}', '{"kind":"x","date":"2026-10-21"}', '{"kind":"picks","date":"10/21"}',
    '{"kind":"picks","date":"2026-10-21; rm -rf /"}', '{"kind":"picks","date":"2026-13-45"}', '{"kind":"picks","date":"2026-10-21","x":1}']) {
    assert.equal(parsePost(bad), null, bad)
  }
  assert.deepEqual([...POST_KINDS].sort(), Object.keys(KIND_LABEL).sort()) // 下拉選單跟後端認得的一樣
  const body = JSON.parse(String(dispatchRequest('tok', 'post', { kind: 'morning', date: '2026-10-22' }).init.body))
  assert.deepEqual(body, { ref: 'main', inputs: { task: 'post', kind: 'morning', date: '2026-10-22' } })
  const wf = readFileSync(new URL(`../../.github/workflows/${LOGIN_WORKFLOW}`, import.meta.url), 'utf8')
  assert.match(wf, /options: \[login, groups, post\]/)
  assert.match(wf, /DAY: \$\{\{ inputs\.date \}\}/)
  assert.doesNotMatch(wf, /run:[^\n]*\$\{\{ inputs\.(date|kind) \}\}/) // 不能直接展開進 run
  assert.equal(taipeiToday(Date.parse('2026-10-21T16:30:00Z')), '2026-10-22') // 台北已經過午夜
  assert.equal(latestPostId([]), 0)
})
