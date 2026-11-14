// autotrade A-016、A-017（D-229）：網站的遙控按鈕——平倉、暫停／恢復自動交易、啟動／結束交易程式。
// 使用者 2026-11-14：「雲端也要有各式按鈕 賣出」。緊急停止那一條（cloud-autotrade-stop.test.ts）沒有動。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { COMMANDS_DDL, envelope } from '../functions/_lib/autotrade.ts'
import { COMMAND_MAX_PER_DAY, REMOTE_ACTIONS, REQUESTS_DDL, REQUESTS_SHOWN, REQUEST_TTL_S as SERVER_TTL_S, commandOk } from '../functions/_lib/autotrade_remote.ts'
import {
  ACTION_CONFIRM,
  ACTION_NAME,
  REQUESTS_PER_TAB,
  REQUEST_LATE_S,
  REQUEST_TTL_S,
  remoteButtons,
  remoteListening,
  requestLine,
  requestLines,
  requestsWaiting,
  type ListenerBeat,
  type RemoteBeat,
  type RemoteRequest,
} from '../src/cloud/autotrade.ts'

const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')

// autotrade/src/autotrade/relay.py 的 REQUESTS_DDL（autotrade/tests/test_a016_remote.py 守著那一邊）；改表要兩邊一起改
const PROGRAM_DDL = [
  "CREATE TABLE IF NOT EXISTS web.autotrade_requests (id bigint PRIMARY KEY DEFAULT nextval('web.autotrade_commands_id_seq'), day date NOT NULL, action text NOT NULL CHECK (action IN ('flatten', 'pause', 'resume', 'start', 'quit')), target text NOT NULL CHECK (target IN ('sim', 'live')), created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), acked_at timestamptz, code text, note text, done_at timestamptz, done_code text, done_note text)",
  "CREATE TABLE IF NOT EXISTS web.autotrade_remote (id text PRIMARY KEY CHECK (id = 'admin'), seen_at timestamptz NOT NULL, reader_seen_at timestamptz, poll_s real NOT NULL)",
  'ALTER TABLE web.autotrade_requests ENABLE ROW LEVEL SECURITY',
  'ALTER TABLE web.autotrade_remote ENABLE ROW LEVEL SECURITY',
  'CREATE INDEX IF NOT EXISTS autotrade_requests_day ON web.autotrade_requests (day, id)',
]

const req = (more: Partial<RemoteRequest> = {}): RemoteRequest => ({
  id: '51',
  action: 'flatten',
  target: 'sim',
  created_at: '2026-11-16T09:30:05+08:00',
  age_s: 2,
  acked_at: null,
  ack_age_s: null,
  code: null,
  note: null,
  done_at: null,
  done_code: null,
  done_note: null,
  ...more,
})
const acked = (more: Partial<RemoteRequest> = {}) => req({ acked_at: '2026-11-16T09:30:06+08:00', ack_age_s: 1, code: 'flattening', note: '已送出平倉；帳上多 1 口', ...more })
const beat = (more: Partial<ListenerBeat> = {}): ListenerBeat => ({ relay_age_s: 1, engine_age_s: 1.5, poll_s: 2, seen_today: true, ...more })
const admin = (more: Partial<RemoteBeat> = {}): RemoteBeat => ({ age_s: 1, reader_age_s: 1, poll_s: 2, seen_today: true, ...more })

test('A-016 AC1 遙控指令另開一張表，跟交易程式那一份一字不差；停止那張表沒有動', () => {
  assert.deepEqual([...REQUESTS_DDL], PROGRAM_DDL)
  assert.match(REQUESTS_DDL[0], /CHECK \(action IN \('flatten', 'pause', 'resume', 'start', 'quit'\)\)/)
  assert.doesNotMatch(REQUESTS_DDL[0], /'stop'/) // 停止不走這張表
  assert.match(REQUESTS_DDL[0], /nextval\('web\.autotrade_commands_id_seq'\)/) // 編號跟停止共用，不會撞號
  assert.equal(REQUESTS_DDL.filter((s) => s.includes('ENABLE ROW LEVEL SECURITY')).length, 2)
  assert.equal(COMMANDS_DDL.length, 8)
  assert.match(COMMANDS_DDL[1], /CHECK \(action = 'stop'\)/)
  assert.deepEqual([...REMOTE_ACTIONS], ['flatten', 'pause', 'resume', 'start', 'quit']) // 沒有買、賣、改設定、解除
})

test('A-016 AC8 請求內容一定要是 {"confirm": 動作, "target": 模式}，多一個欄位都不收', () => {
  assert.deepEqual(commandOk({ confirm: 'flatten', target: 'sim' }), { action: 'flatten', target: 'sim' })
  assert.deepEqual(commandOk({ confirm: 'start', target: 'live' }), { action: 'start', target: 'live' })
  for (const bad of [
    null,
    'flatten',
    [],
    {},
    { confirm: 'flatten' },
    { target: 'sim' },
    { confirm: 'stop', target: 'sim' }, // 停止不走這一支
    { confirm: 'buy', target: 'sim' },
    { confirm: 'release', target: 'sim' },
    { confirm: 'flatten', target: 'all' },
    { confirm: 'flatten', target: 'sim', qty: 2 },
    { confirm: ['flatten'], target: 'sim' },
  ])
    assert.equal(commandOk(bad), null, JSON.stringify(bad))
  assert.equal(SERVER_TTL_S, REQUEST_TTL_S) // 伺服器與頁面講的是同一個秒數
  assert.ok(COMMAND_MAX_PER_DAY > 0 && REQUESTS_SHOWN >= REQUESTS_PER_TAB)
})

test('A-016 AC8 端點：門檻跟停止一樣；只會在遙控那張表新增一列；讀取多回 requests 與 remote', () => {
  const put = read('../functions/api/autotrade/command.ts')
  assert.match(put, /export const onRequestPut[^\n]*\(await denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? command\(ctx\)/) // 權限在動資料庫之前擋
  assert.ok(put.indexOf('sameOrigin(request)') < put.indexOf('guarded(env')) // 先看是不是本站送的
  assert.ok(put.indexOf('commandOk(') < put.indexOf('guarded(env'))
  assert.doesNotMatch(put, /onRequest(Get|Post|Delete|Patch)\b/)
  const lib = read('../functions/_lib/autotrade_remote.ts')
  assert.deepEqual(lib.match(/INSERT INTO web\.\w+/g), ['INSERT INTO web.autotrade_requests'])
  assert.doesNotMatch(lib, /UPDATE |DELETE FROM|DROP |TRUNCATE/) // 網站不改、不刪任何一列
  assert.match(lib, /acked_at IS NULL\s+AND created_at > now\(\) - make_interval\(secs => \$\{REQUEST_TTL_S\}/) // 同一種還在等就不疊
  assert.match(lib, /< \$\{COMMAND_MAX_PER_DAY\}/)
  const stopLib = read('../functions/_lib/autotrade.ts')
  assert.deepEqual(stopLib.match(/INSERT INTO web\.\w+/g), ['INSERT INTO web.autotrade_commands']) // 停止那一份沒有多別的
  const get = read('../functions/api/autotrade/index.ts')
  assert.match(get, /readRows\(\(\) => requestsView\(db, taipeiToday\(Date\.now\(\)\)\)\)/) // 表還沒建就當成沒有
  assert.doesNotMatch(get, /INSERT|UPDATE|DELETE/)
  assert.equal(envelope([], null, null), '{"days":[],"current":null,"stop":null,"listeners":{}}') // 沒有遙控那一段時跟以前一樣
  const both = JSON.parse(envelope([], null, null, { requests: '[{"id":"5"}]', remote: null }))
  assert.deepEqual(both.requests, [{ id: '5' }])
  assert.equal(both.remote, null)
})

test('A-016 AC9 按鈕：平倉、暫停或恢復要那支交易程式在聽；啟動或結束要後台在聽；不能按就寫原因', () => {
  const on = remoteButtons('sim', { sim: beat() }, admin(), false, true)
  assert.deepEqual(
    on.map((b) => [b.action, b.label, b.enabled]),
    [
      ['flatten', '平倉', true],
      ['pause', '暫停自動交易', true],
      ['quit', '結束交易程式', true], // 在跑的時候第三顆是「結束」
    ],
  )
  assert.equal(remoteButtons('sim', { sim: beat() }, admin(), true, true)[1].action, 'resume') // 暫停中：第二顆變「恢復」
  // 模擬在跑、看的是真單分頁：真單沒在聽，前兩顆不能按；後台在聽，可以啟動
  const live = remoteButtons('live', { sim: beat() }, admin(), false, true)
  assert.deepEqual(
    live.map((b) => [b.action, b.enabled]),
    [
      ['flatten', false],
      ['pause', false],
      ['start', true],
    ],
  )
  assert.equal(live[0].why, '真單的交易程式沒在聽')
  // 後台沒開：啟動不能按，並講原因
  const off = remoteButtons('sim', {}, null, false, true)
  assert.deepEqual(off.map((b) => b.enabled), [false, false, false])
  assert.equal(off[2].action, 'start')
  assert.match(off[2].why, /電腦或本機後台沒開/)
  assert.equal(remoteButtons('sim', {}, admin({ age_s: 600 }), false, true)[2].enabled, false) // 後台的心跳太舊
  assert.equal(remoteButtons('sim', {}, admin({ reader_age_s: null }), false, true)[2].enabled, false) // 後台還沒讀過指令
  // 畫面上的狀態不夠新：全部不能按，不猜
  const unsure = remoteButtons('sim', { sim: beat() }, admin(), false, false)
  assert.deepEqual(unsure.map((b) => b.enabled), [false, false, false])
  assert.ok(unsure.every((b) => b.why === '狀態不明，先等畫面更新'))
  assert.equal(remoteListening(admin()), true)
  assert.equal(remoteListening(undefined), false)
  for (const action of ['flatten', 'pause', 'resume', 'start', 'quit'] as const) assert.ok(ACTION_NAME[action] && ACTION_CONFIRM[action])
  assert.match(ACTION_CONFIRM.resume, /不會解除緊急停止/)
  assert.match(ACTION_CONFIRM.flatten, /自動交易不會停/)
})

test('A-016 AC9 進度：等它收到 → 已收到 → 完成；沒人接手就作廢；做不到要講', () => {
  const view = (r: RemoteRequest, elapsed = 0) => {
    const l = requestLine(r, elapsed)
    return [l.tone, l.label] as const
  }
  assert.deepEqual(view(req()), ['wait', '平倉：等它收到…'])
  assert.deepEqual(view(req(), REQUEST_TTL_S - 2), ['wait', '平倉：等它收到…'])
  const dead = requestLine(req(), REQUEST_TTL_S) // 2 + 30 秒：超過了
  assert.deepEqual([dead.tone, dead.label], ['bad', '平倉：沒有送到，已作廢'])
  assert.match(dead.detail, /09:30:05 按下，30 秒內沒有程式接手/)
  assert.deepEqual(view(acked()), ['wait', '平倉：已收到，處理中'])
  assert.deepEqual(view(acked(), REQUEST_LATE_S), ['bad', '平倉：收到了，但還沒做完'])
  assert.deepEqual(view(acked({ code: 'ignored', note: '不認得的動作' })), ['bad', '平倉：沒有執行'])
  const done = requestLine(acked({ done_at: '2026-11-16T09:30:08+08:00', done_code: 'flat', done_note: '已確認沒有部位、沒有委託' }), 500)
  assert.deepEqual([done.tone, done.label], ['done', '平倉：完成']) // 做完了就不再隨時間變
  assert.match(done.detail, /09:30:05 按下；已確認沒有部位/)
  assert.deepEqual(view(acked({ done_code: 'stuck', done_note: '過了 21 秒還沒確認平掉' })), ['bad', '平倉：要人處理'])
  assert.deepEqual(view(acked({ action: 'start', done_code: 'refused', done_note: '交易程式已經在跑' })), ['bad', '啟動交易程式：沒有執行'])
  assert.deepEqual(view(acked({ action: 'pause', code: 'paused', done_code: 'paused', done_note: '已暫停' })), ['done', '暫停自動交易：完成'])
  assert.deepEqual(view(acked({ action: 'start', code: 'accepted', done_code: 'started', done_note: '已啟動' })), ['done', '啟動交易程式：完成'])
  // 每個分頁只看給自己的，最多 REQUESTS_PER_TAB 筆，新的在前
  const many = [req({ id: '9' }), req({ id: '8', target: 'live' }), req({ id: '7' }), req({ id: '6' }), req({ id: '5' })]
  assert.deepEqual(requestLines(many, 'sim', 0).map((l) => l.id), ['9', '7', '6'])
  assert.deepEqual(requestLines(many, 'live', 0).map((l) => l.id), ['8'])
  assert.equal(requestsWaiting([req()], 0), true)
  assert.equal(requestsWaiting([req()], 60), false) // 作廢了就不用再快
  assert.equal(requestsWaiting([], 0), false)
})

test('A-016 AC9 頁面：要按兩次才送；只對現在看的分頁；沒有買賣、改設定、解除停止的按鈕', () => {
  const bar = read('../src/cloud/RemoteBar.tsx')
  assert.match(bar, /disabled=\{!b\.enabled\} title=\{b\.why\} onClick=\{\(\) => setAsking\(b\)\}/) // 第一下只打開確認
  assert.match(bar, /className="cq-at-remote-yes" disabled=\{sending\} onClick=\{\(\) => send\(asking\)\}/) // 第二下才送
  assert.equal(bar.split('sendAutotradeCommand(').length - 1, 1)
  assert.match(bar, /sendAutotradeCommand\(b\.action, mode\)/)
  assert.match(bar, /確定要\{asking\.label\}？（\{MODE_NAME\[mode\]\}）/) // 確認時講清楚是模擬還是真單
  assert.doesNotMatch(bar, /buy|sell|買進|賣出|release|解除停止</i)
  const page = read('../src/cloud/AutotradeView.tsx')
  assert.match(page, /<RemoteBar key=\{mode\} mode=\{mode\} buttons=\{buttons\} lines=\{known \? requestLines\(requests, mode, elapsed\) : \[\]\}/)
  assert.match(page, /const known = data !== null && elapsed <= DATA_STALE_S/) // 畫面太舊就不讓人按
})
