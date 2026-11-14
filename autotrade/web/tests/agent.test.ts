// autotrade A-019（D-231）AC8：網站的「啟動／停止」——交易 agent 在待命與運作之間切換，指令走訊息匯流排。
// 使用者 2026-11-14：「這個交易agent也要可以被啟動、停止」「指令也是透過db mq溝通」，兩個網站都要做。
// 緊急停止（stop.test.ts）與原本的遙控指令（remote.test.ts）都沒有動。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { BUS_ACTIONS, MESSAGES_DDL, busCommandOk, mergeRequests, withBus } from '../functions/_lib/autotrade_bus.ts'
import { REQUESTS_SHOWN } from '../functions/_lib/autotrade_remote.ts'
import {
  ACTION_CONFIRM,
  ACTION_NAME,
  armedOf,
  armedText,
  linkLight,
  remoteButtons,
  requestLine,
  type AutoStatus,
  type ListenerBeat,
  type RemoteBeat,
  type RemoteRequest,
} from '../src/autotrade.ts'

const PAGE_DIR = ''
const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')

// autotrade/src/autotrade/bus.py 的 MESSAGES_DDL（autotrade/tests/test_a019_agent.py 守著兩邊一字不差）
const PROGRAM_DDL = [
  "CREATE TABLE IF NOT EXISTS web.autotrade_messages (id bigint PRIMARY KEY DEFAULT nextval('web.autotrade_commands_id_seq'), day date NOT NULL, topic text NOT NULL CHECK (topic IN ('command', 'report', 'signal', 'fill', 'status')), target text NOT NULL CHECK (target IN ('sim', 'live')), action text NOT NULL, body jsonb NOT NULL DEFAULT '{}'::jsonb, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), ttl_s real, reply_to bigint, CHECK (topic <> 'command' OR action IN ('arm', 'standby')))",
  'ALTER TABLE web.autotrade_messages ENABLE ROW LEVEL SECURITY',
  'CREATE INDEX IF NOT EXISTS autotrade_messages_day ON web.autotrade_messages (day, topic, id)',
  'CREATE INDEX IF NOT EXISTS autotrade_messages_reply ON web.autotrade_messages (reply_to) WHERE reply_to IS NOT NULL',
]

const beat = (more: Partial<ListenerBeat> = {}): ListenerBeat => ({ relay_age_s: 1, engine_age_s: 1.5, poll_s: 2, seen_today: true, ...more })
const admin = (more: Partial<RemoteBeat> = {}): RemoteBeat => ({ age_s: 1, reader_age_s: 1, poll_s: 2, seen_today: true, ...more })
const req = (more: Partial<RemoteRequest> = {}): RemoteRequest => ({
  id: '61',
  action: 'arm',
  target: 'sim',
  created_at: '2026-11-16T08:50:05+08:00',
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

test('A-019 AC1 訊息表跟交易程式那一份一字不差；匯流排上的指令只可能是啟動或停止', () => {
  assert.deepEqual([...MESSAGES_DDL], PROGRAM_DDL)
  assert.match(MESSAGES_DDL[0], /CHECK \(topic <> 'command' OR action IN \('arm', 'standby'\)\)/)
  assert.match(MESSAGES_DDL[0], /nextval\('web\.autotrade_commands_id_seq'\)/) // 編號跟另外兩張表共用，不會撞號
  assert.deepEqual([...BUS_ACTIONS], ['arm', 'standby']) // 沒有緊急停止、沒有解除、沒有買賣
})

test('A-019 AC2 請求內容一定要是 {"confirm": "arm" 或 "standby", "target": 模式}', () => {
  assert.deepEqual(busCommandOk({ confirm: 'arm', target: 'sim' }), { action: 'arm', target: 'sim' })
  assert.deepEqual(busCommandOk({ confirm: 'standby', target: 'live' }), { action: 'standby', target: 'live' })
  for (const bad of [
    null,
    'arm',
    [],
    {},
    { confirm: 'arm' },
    { confirm: 'stop', target: 'sim' }, // 緊急停止不走這裡
    { confirm: 'start', target: 'sim' }, // 舊的「把程式開起來」也不走這裡
    { confirm: 'release', target: 'sim' },
    { confirm: 'buy', target: 'sim' },
    { confirm: 'arm', target: 'all' },
    { confirm: 'arm', target: 'sim', qty: 1 },
  ])
    assert.equal(busCommandOk(bad), null, JSON.stringify(bad))
})

test('A-019 AC2 網站只會新增「指令」這一種訊息；回報是 agent 寫的；同一種還在等就不疊', () => {
  const lib = read('../functions/_lib/autotrade_bus.ts')
  assert.deepEqual(lib.match(/INSERT INTO web\.\w+/g), ['INSERT INTO web.autotrade_messages'])
  assert.match(lib, /INSERT INTO web\.autotrade_messages \(day, topic, target, action, created_by, ttl_s\)\s+SELECT \$\{day\}::date, 'command',/)
  assert.doesNotMatch(lib, /UPDATE |DELETE FROM|DROP |TRUNCATE/) // 只新增，不改、不刪
  assert.match(lib, /NOT EXISTS \(SELECT 1 FROM web\.autotrade_messages r WHERE r\.reply_to = m\.id AND r\.topic = 'report'\) LIMIT 1/)
  assert.match(lib, /< \$\{COMMAND_MAX_PER_DAY\}/)
  const put = read('../functions/api/autotrade/command.ts')
  assert.ok(put.indexOf('busCommandOk(') < put.indexOf('guarded(env')) // 內容先驗，才碰資料庫
  assert.ok(put.indexOf('sameOrigin(request)') < put.indexOf('busCommandOk('))
  assert.doesNotMatch(put, /INSERT|UPDATE|DELETE/)
  const get = read('../functions/api/autotrade/index.ts')
  assert.match(get, /readRows\(\(\) => busView\(db, taipeiToday\(Date\.now\(\)\)\)\)/) // 表還沒建就當成沒有
  assert.doesNotMatch(get, /INSERT|UPDATE|DELETE/)
})

test('A-019 AC8 兩張表的指令併成一份清單：新的在前、留最近幾筆；都沒有就是沒有', () => {
  assert.equal(withBus(null, null), null)
  const merged = withBus({ requests: '[{"id":"9"},{"id":"4"}]', remote: '{"age_s":1}' }, { requests: '[{"id":"12"},{"id":"7"}]' })
  assert.deepEqual(JSON.parse(merged!.requests!), [{ id: '12' }, { id: '9' }, { id: '7' }, { id: '4' }])
  assert.equal(merged!.remote, '{"age_s":1}')
  assert.deepEqual(JSON.parse(withBus(null, { requests: '[{"id":"3"}]' })!.requests!), [{ id: '3' }])
  const many = JSON.stringify(Array.from({ length: REQUESTS_SHOWN + 3 }, (_, i) => ({ id: String(100 - i) })))
  assert.equal(JSON.parse(mergeRequests(many, null)).length, REQUESTS_SHOWN)
})

test('A-019 AC8 按鈕：待命給「啟動」、運作中給「停止」；要那支交易程式在聽；頁面上只有三顆', () => {
  const old = remoteButtons('sim', { sim: beat() }, admin(), false, true)
  assert.deepEqual(old.map((b) => b.action), ['flatten', 'pause', 'quit']) // 沒給待命／運作：跟以前一樣三顆
  const standby = remoteButtons('sim', { sim: beat() }, admin(), false, true, false)
  assert.deepEqual(standby.map((b) => b.action), ['arm', 'flatten', 'pause']) // 舊的「啟動／結束交易程式」不放了
  assert.ok(standby.every((b) => !/後台/.test(b.why)))
  assert.deepEqual(standby[0], { action: 'arm', label: '開始自動交易', enabled: true, why: '' })
  const running = remoteButtons('sim', { sim: beat() }, admin(), false, true, true)
  assert.deepEqual(running[0], { action: 'standby', label: '停止自動交易', enabled: true, why: '' })
  // 今天還沒有狀態（開盤前）：給「啟動」，可以先按
  assert.equal(remoteButtons('sim', { sim: beat() }, admin(), false, true, null)[0].action, 'arm')
  // 交易程式沒在聽：不能按，寫原因
  const nobody = remoteButtons('sim', {}, admin(), false, true, false)[0]
  assert.equal(nobody.enabled, false)
  assert.match(nobody.why, /模擬的交易程式沒在聽/)
  // 只看這個分頁的那一支
  assert.equal(remoteButtons('live', { sim: beat() }, admin(), false, true, false)[0].enabled, false)
  // 畫面上的狀態不夠新：不猜
  assert.equal(remoteButtons('sim', { sim: beat() }, admin(), false, false, false)[0].enabled, false)
  assert.equal(ACTION_NAME.arm, '開始自動交易')
  assert.equal(ACTION_NAME.standby, '停止自動交易')
  assert.match(ACTION_CONFIRM.pause, /不會平倉/) // 暫停與停止差在哪，確認時講清楚
  assert.match(ACTION_CONFIRM.standby, /只想不進新單、部位留著，請按「暫停自動交易」/)
  assert.match(ACTION_CONFIRM.arm, /不會解除緊急停止/)
  assert.match(ACTION_CONFIRM.standby, /先把部位平掉，再回待命/)
})

test('A-019 AC8 待命還是運作：只有今天、還在跑的那一份狀態才算數', () => {
  const live = { status: 'live', armed: false } as AutoStatus
  assert.equal(armedOf(live, true), false)
  assert.equal(armedOf({ ...live, armed: true }, true), true)
  assert.equal(armedOf(live, false), null) // 在看別天
  assert.equal(armedOf({ ...live, status: 'closed' } as AutoStatus, true), null) // 收盤了
  assert.equal(armedOf({ status: 'live' } as AutoStatus, true), null) // 舊的狀態沒有這一欄
  assert.equal(armedOf(null, true), null)
  assert.match(armedText(false)!, /^待命：不會進新單/)
  assert.match(armedText(true)!, /^運作中/)
  assert.equal(armedText(null), null)
})

test('連線燈：心跳夠新是綠燈並寫幾秒前；沒有連線寫是哪一段；畫面太舊不猜', () => {
  const on = linkLight('sim', { sim: beat() }, true, 2)
  assert.deepEqual(on, { tone: 'on', label: '電腦上的模擬交易程式連線中', detail: '心跳 3 秒前' })
  const none = linkLight('sim', {}, true, 0)
  assert.equal(none.tone, 'off')
  assert.match(none.label, /沒有連線/)
  assert.match(none.detail, /今天沒有收到過它的心跳/)
  const gone = linkLight('sim', { sim: beat({ relay_age_s: 600, engine_age_s: 600 }) }, true, 0)
  assert.equal(gone.tone, 'off')
  assert.match(gone.detail, /最後一次心跳是 10 分鐘前/)
  const half = linkLight('sim', { sim: beat({ engine_age_s: 90 }) }, true, 0)
  assert.equal(half.tone, 'off')
  assert.match(half.detail, /收發程式連著，但交易的那一半已經/)
  assert.match(linkLight('sim', { sim: beat({ engine_age_s: null }) }, true, 0).detail, /還沒回應過/)
  assert.equal(linkLight('live', { sim: beat() }, true, 0).tone, 'off') // 只看這個分頁的那一支
  assert.equal(linkLight('sim', { sim: beat() }, false, 0).tone, 'unknown') // 畫面太舊：黃燈，不說有也不說沒有
  const view = read('../src/cloud/AutotradeView.tsx'.replace('cloud/', PAGE_DIR))
  assert.match(view, /linkLight\(mode, data\?\.listeners \?\? \{\}, known, elapsed\)/)
  assert.match(view, /className=\{`cq-at-link \$\{light\.tone\}`\} role="status"/)
})

test('A-019 AC6 進度：啟動做完是「完成」；停止要等確認沒有部位；沒人接手 30 秒作廢', () => {
  assert.equal(requestLine(req(), 0).label, '開始自動交易：等它收到…')
  const armed = requestLine(req({ acked_at: '2026-11-16T08:50:06+08:00', ack_age_s: 1, code: 'armed', note: '已啟動', done_code: 'armed', done_note: '已啟動：開始自動交易' }), 0)
  assert.deepEqual([armed.tone, armed.label], ['done', '開始自動交易：完成'])
  const stopping = requestLine(req({ action: 'standby', acked_at: '2026-11-16T09:30:06+08:00', ack_age_s: 1, code: 'flattening', note: '已切到待命，不再進新單；已送出平倉' }), 0)
  assert.deepEqual([stopping.tone, stopping.label], ['wait', '停止自動交易：已收到，處理中'])
  const flat = requestLine(req({ action: 'standby', acked_at: 'x', ack_age_s: 3, code: 'flattening', note: '', done_code: 'flat', done_note: '已確認沒有部位' }), 0)
  assert.deepEqual([flat.tone, flat.label], ['done', '停止自動交易：完成'])
  assert.equal(requestLine(req({ age_s: 40 }), 0).label, '開始自動交易：沒有送到，已作廢')
})

test('A-019 AC8 頁面：按鈕上方寫待命或運作', () => {
  const bar = read('../src/RemoteBar.tsx')
  assert.match(bar, /\{state && <p className=\{`cq-at-remote-state/)
  assert.match(bar, /arm: Rocket, standby: Square/)
  const view = read('../src/AutotradeView.tsx')
  assert.match(view, /const armed = known \? armedOf\(current, day === null\) : null/)
  assert.match(view, /remoteButtons\(mode, data\?\.listeners \?\? \{\}, data\?\.remote, paused, known, armed\)/)
  assert.match(view, /state=\{armedText\(armed\)\}/)
  const css = read('../src/autotrade.css')
  assert.match(css, /\.cq-at-remote-btns\.n4 \{ grid-template-columns: repeat\(2,/)
})
