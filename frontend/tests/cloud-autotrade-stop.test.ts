// autotrade A-011（AC15～AC20）：遠端緊急停止（D-225）在網站這一側。網站只能叫停——沒有解除、沒有開始交易。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { COMMANDS_DDL, STOP_BODY_MAX, STOP_CONFIRM, STOP_MAX_PER_DAY, confirmOk, envelope, taipeiToday } from '../functions/_lib/autotrade.ts'
import {
  BROKER_HINT,
  LISTENERS,
  LISTENER_NAME,
  LISTEN_MIN_S,
  LISTEN_POLLS,
  NOBODY_LISTENING,
  POLL_MS,
  STOP_CONFIRM_LATE_S,
  STOP_FAST_FOR_S,
  STOP_FAST_MS,
  STOP_IN_PROGRESS,
  STOP_LATE_S,
  STOP_SCOPE,
  DATA_STALE_S,
  listening,
  nobodyListening,
  unknownLines,
  pollMs,
  stopLine,
  stopLines,
  type ListenerBeat,
  type StopAck,
  type StopCommand,
} from '../src/cloud/autotrade.ts'

const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')

// autotrade/src/autotrade/relay.py 的 COMMANDS_DDL（autotrade/tests/test_a011_relay.py 守著同樣八句）；改表要兩邊一起改
const PROGRAM_DDL = [
  'CREATE SCHEMA IF NOT EXISTS web',
  "CREATE TABLE IF NOT EXISTS web.autotrade_commands (id bigserial PRIMARY KEY, day date NOT NULL, action text NOT NULL CHECK (action = 'stop'), created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())",
  "CREATE TABLE IF NOT EXISTS web.autotrade_command_acks (command_id bigint NOT NULL REFERENCES web.autotrade_commands (id) ON DELETE CASCADE, consumer text NOT NULL CHECK (consumer IN ('sim', 'live', 'watchdog')), acked_at timestamptz NOT NULL DEFAULT now(), code text NOT NULL, note text NOT NULL, done_at timestamptz, done_code text, done_note text, PRIMARY KEY (command_id, consumer))",
  "CREATE TABLE IF NOT EXISTS web.autotrade_listeners (consumer text PRIMARY KEY CHECK (consumer IN ('sim', 'live', 'watchdog')), relay_seen_at timestamptz NOT NULL, engine_seen_at timestamptz, poll_s real NOT NULL)",
  'ALTER TABLE web.autotrade_commands ENABLE ROW LEVEL SECURITY',
  'ALTER TABLE web.autotrade_command_acks ENABLE ROW LEVEL SECURITY',
  'ALTER TABLE web.autotrade_listeners ENABLE ROW LEVEL SECURITY',
  'CREATE INDEX IF NOT EXISTS autotrade_commands_day ON web.autotrade_commands (day, id)',
]

const PRESSED = '2026-11-13T10:02:11+08:00'
const cmd = (age_s: number, acks: StopCommand['acks'] = {}): StopCommand => ({ id: '42', day: '2026-11-13', created_at: PRESSED, age_s, acks })
const ack = (code: string, note: string, more: Partial<StopAck> = {}): StopAck => ({
  acked_at: '2026-11-13T10:02:13+08:00',
  ack_age_s: 1,
  code,
  note,
  done_at: null,
  done_code: null,
  done_note: null,
  ...more,
})
const done = (code: string, note: string, done_code: string, done_note: string): StopAck =>
  ack(code, note, { done_at: '2026-11-13T10:02:20+08:00', done_code, done_note })
const beat = (more: Partial<ListenerBeat> = {}): ListenerBeat => ({ relay_age_s: 1, engine_age_s: 1.5, poll_s: 2, seen_today: true, ...more })
const SILENT = beat({ relay_age_s: 600, engine_age_s: 600 })
const view = (who: 'live' | 'sim', c: StopCommand | null, b: ListenerBeat | undefined, elapsed = 0) => {
  const l = stopLine(who, c, b, elapsed)
  return [l.tone, l.label] as const
}

test('AC15 指令表跟交易程式那一份一字不差；表裡只可能有「停止」；回報一支程式一列', () => {
  assert.deepEqual([...COMMANDS_DDL], PROGRAM_DDL)
  assert.match(COMMANDS_DDL[1], /CHECK \(action = 'stop'\)/)
  assert.match(COMMANDS_DDL[2], /PRIMARY KEY \(command_id, consumer\)/)
  assert.equal(COMMANDS_DDL.filter((s) => s.includes('ENABLE ROW LEVEL SECURITY')).length, 3)
})

test('AC15 請求內容一定要是 {"confirm":"stop"}，多一個欄位都不收；日期由伺服器決定', () => {
  assert.equal(STOP_CONFIRM, 'stop')
  assert.equal(confirmOk({ confirm: 'stop' }), true)
  for (const bad of [null, undefined, 'stop', 42, [], ['stop'], {}, { confirm: 'STOP' }, { confirm: true }, { confirm: 'stop', mode: 'live' }, { action: 'resume' }])
    assert.equal(confirmOk(bad), false, JSON.stringify(bad))
  assert.ok(STOP_BODY_MAX >= JSON.stringify({ confirm: 'stop' }).length && STOP_BODY_MAX <= 1000)
  assert.ok(STOP_MAX_PER_DAY >= 5 && STOP_MAX_PER_DAY <= 100)
  assert.equal(taipeiToday(Date.UTC(2026, 10, 13, 15, 59, 59)), '2026-11-13')
  assert.equal(taipeiToday(Date.UTC(2026, 10, 13, 16, 0, 0)), '2026-11-14')
})

test('AC15 端點：權限在動資料庫之前擋、只收本站請求、只會新增「停止」', () => {
  const src = read('../functions/api/autotrade/stop.ts')
  assert.match(src, /export const onRequestPut[^\n]*denied\(ctx\.request, 'futures', ctx\.env\)\) \?\? stop\(ctx\)/)
  assert.equal(/onRequest(Get|Post|Delete|Patch)\b/.test(src), false)
  const order = ['sameOrigin(request)', 'confirmOk(', 'guarded(', 'userEmail(', 'requestStop('].map((s) => src.indexOf(s))
  assert.ok(order.every((i) => i > 0), `缺少步驟：${order}`)
  assert.deepEqual([...order].sort((a, b) => a - b), order) // 照這個順序：先擋跨站、再驗內容、最後才碰資料庫
  assert.doesNotMatch(src, /db`|INSERT|UPDATE|DELETE/) // SQL 都在 _lib/autotrade.ts

  const lib = read('../functions/_lib/autotrade.ts')
  const inserts = lib.match(/INSERT INTO [\w.]+/g) ?? []
  assert.deepEqual(inserts, ['INSERT INTO web.autotrade_commands']) // 唯一的寫入；網站不替交易程式寫回報或心跳
  assert.match(lib, /INSERT INTO web\.autotrade_commands \(day, action, created_by\)\s+SELECT \$\{day\}::date, 'stop', \$\{email\}/)
  assert.doesNotMatch(lib, /DELETE FROM|UPDATE\s+web\.|TRUNCATE|DROP /) // 不改、不刪
  for (const word of ['release', 'resume', 'start']) assert.equal(new RegExp(`'${word}'`).test(lib + src), false, word) // 只能叫停
})

test('AC15 上一筆還沒有任何程式回報時不寫新的（連按、或以為沒送出去再按，都不會疊出一堆）', () => {
  const lib = read('../functions/_lib/autotrade.ts')
  assert.match(lib, /waiting AS \(SELECT id FROM last WHERE NOT EXISTS \(SELECT 1 FROM web\.autotrade_command_acks a WHERE a\.command_id = last\.id\)\)/)
  assert.match(lib, /WHERE NOT EXISTS \(SELECT 1 FROM waiting\)\s+AND \(SELECT count\(\*\) FROM web\.autotrade_commands WHERE day = \$\{day\}::date\) < \$\{STOP_MAX_PER_DAY\}/)
  assert.match(lib, /請直接用券商的 App 處理部位/) // 到上限時也要給下一步
})

test('AC16 讀取：一次回今天最新的停止、每支程式的回報與心跳；沒有就是空的；這支仍然只讀', () => {
  const control = { stop: '{"id":"42","acks":{"sim":{"code":"flat"}}}', listeners: '{"sim":{"poll_s":2}}' }
  assert.deepEqual(JSON.parse(envelope([], null, control)), {
    days: [],
    current: null,
    stop: { id: '42', acks: { sim: { code: 'flat' } } },
    listeners: { sim: { poll_s: 2 } },
  })
  assert.deepEqual(JSON.parse(envelope([], null)), { days: [], current: null, stop: null, listeners: {} })
  assert.deepEqual(JSON.parse(envelope([], null, { stop: null, listeners: null })), { days: [], current: null, stop: null, listeners: {} })
  const src = read('../functions/api/autotrade/index.ts')
  assert.match(src, /readRows\(\(\) => controlView\(db, taipeiToday\(Date\.now\(\)\)\)\)/) // 表還沒建＝沒有，不是錯誤
  assert.doesNotMatch(src, /INSERT|UPDATE|DELETE/i)
  assert.equal(/onRequest(Post|Put|Delete|Patch)\b/.test(src), false)
  const lib = read('../functions/_lib/autotrade.ts')
  assert.match(lib, /AT TIME ZONE 'Asia\/Taipei'[^\n]*\|\| '\+08:00'/) // 時間帶 +08:00
  // 「多久以前」由資料庫的時鐘算，不拿手機的時鐘去比
  for (const col of ['c.created_at', 'a.acked_at', 'l.relay_seen_at', 'l.engine_seen_at']) assert.ok(lib.includes(`EXTRACT(EPOCH FROM now() - ${col})`), col)
})

test('AC17 心跳：中繼程式與交易程式都夠新才算在聽', () => {
  assert.equal(listening(beat()), true)
  assert.equal(listening(undefined), false)
  assert.equal(listening(beat({ engine_age_s: null })), false) // 中繼程式活著，但交易程式沒讀過指令
  const limit = Math.max(LISTEN_MIN_S, 2 * LISTEN_POLLS)
  assert.equal(listening(beat({ relay_age_s: limit, engine_age_s: 1 })), true)
  assert.equal(listening(beat({ relay_age_s: limit + 0.1, engine_age_s: 1 })), false)
  assert.equal(listening(beat({ relay_age_s: 1, engine_age_s: limit + 0.1 })), false)
  assert.equal(listening(beat({ poll_s: 10, relay_age_s: 40, engine_age_s: 40 })), true) // 讀得慢的程式門檻跟著放寬
  // 頁面自己多久沒更新不算進「程式有沒有回應」：15 秒才更新一次的頁面，不會把在聽的程式畫成紅的
  assert.deepEqual(view('live', null, beat(), 15), ['ok', '在聽'])
  assert.deepEqual(view('live', null, beat(), 40), ['ok', '在聽'])
})

test('AC17 按之前：每支程式在不在聽', () => {
  assert.deepEqual(view('live', null, beat()), ['ok', '在聽'])
  assert.deepEqual(view('live', null, undefined), ['off', '今天沒有在跑'])
  assert.deepEqual(view('live', null, beat({ seen_today: false, relay_age_s: 70000, engine_age_s: 70000 })), ['off', '今天沒有在跑'])
  const gone = stopLine('live', null, SILENT, 0)
  assert.deepEqual([gone.tone, gone.label], ['bad', '沒有回應']) // 今天跑過、現在沒聲音：比「沒在跑」嚴重
  assert.match(gone.detail, /已經 10 分鐘沒有回應，按了可能到不了/)
})

test('AC17 按下之後、還沒收到：15 秒內等，超過就標紅；今天根本沒在跑的不嚇人', () => {
  assert.deepEqual(view('live', cmd(5), beat()), ['wait', '等它收到…'])
  assert.deepEqual(view('live', cmd(STOP_LATE_S), beat()), ['wait', '等它收到…'])
  const late = stopLine('live', cmd(STOP_LATE_S + 1), beat(), 0)
  assert.deepEqual([late.tone, late.label, late.detail], ['bad', '還沒收到', '已經 16 秒'])
  assert.match(stopLine('live', cmd(200), SILENT, 0).detail, /已經 3 分鐘，而且它現在沒有回應/)
  const idle = stopLine('live', cmd(200), undefined, 0)
  assert.deepEqual([idle.tone, idle.label, idle.detail], ['off', '今天沒有在跑', '今天之內一啟動就會先停下來'])
  // 秒數用伺服器算的 age_s 加上拿到資料後過的時間，不看手機的時鐘
  assert.deepEqual(view('live', cmd(10), beat(), 4), ['wait', '等它收到…'])
  assert.deepEqual(view('live', cmd(10), beat(), 6), ['bad', '還沒收到'])
})

test('AC17 模擬的回報不會讓真單那一行變成「好了」', () => {
  const flatSim = done('flat', '已停手；帳上沒有部位', 'flat', '已確認沒有部位、沒有委託；帳上沒有部位')
  const c = cmd(20, { sim: flatSim })
  assert.deepEqual(view('sim', c, beat()), ['done', '已停手，沒有部位'])
  assert.deepEqual(view('live', c, beat()), ['bad', '還沒收到']) // 真單自己沒回報，照樣標紅
  const lines = stopLines(c, { live: beat(), sim: beat() }, 0)
  assert.deepEqual(lines.map((l) => l.who), ['live', 'sim']) // 真單排前面
  assert.deepEqual(LISTENERS, ['live', 'sim'])
  assert.deepEqual(LISTENER_NAME, { live: '真單', sim: '模擬' })
})

test('AC17 收到之後要追到「確認平掉」；平不掉、不會自己平、看不懂的回報都標紅', () => {
  const going = ack('flattening', '已停手，已送出平倉；帳上多 1 口')
  const wait = stopLine('live', cmd(5, { live: going }), beat(), 0)
  assert.deepEqual([wait.tone, wait.label], ['wait', '已收到，還沒確認平掉'])
  assert.match(wait.detail, /10:02:13 收到；已停手，已送出平倉；帳上多 1 口/)
  for (const code of ['flat', 'cancelling', 'flattening', 'link_down']) {
    assert.ok(STOP_IN_PROGRESS.has(code))
    assert.equal(stopLine('live', cmd(5, { live: ack(code, 'x') }), beat(), 0).tone, 'wait', code)
  }
  // 收到 30 秒了還沒確認
  assert.equal(stopLine('live', cmd(40, { live: ack('flattening', 'x', { ack_age_s: STOP_CONFIRM_LATE_S }) }), beat(), 0).tone, 'wait')
  const slow = stopLine('live', cmd(40, { live: ack('flattening', 'x', { ack_age_s: STOP_CONFIRM_LATE_S }) }), beat(), 1)
  assert.deepEqual([slow.tone, slow.label], ['bad', '收到了，但還沒確認平掉'])

  const flat = stopLine('live', cmd(30, { live: done('flattening', 'x', 'flat', '已確認沒有部位、沒有委託；帳上沒有部位') }), beat(), 0)
  assert.deepEqual([flat.tone, flat.label], ['done', '已停手，沒有部位'])
  assert.match(flat.detail, /10:02:13 收到；已確認沒有部位、沒有委託/)
  const stuck = stopLine('live', cmd(30, { live: done('flattening', 'x', 'stuck', '過了 21 秒還沒確認平掉，請手動處理；帳上多 1 口') }), beat(), 0)
  assert.deepEqual([stuck.tone, stuck.label, stuck.detail], ['bad', '部位沒有平掉', '過了 21 秒還沒確認平掉，請手動處理；帳上多 1 口'])
  // 在電腦上解除之後，這筆停止對它就結束了：回到「在不在聽」，程式之後沒聲音一樣要標紅
  const releasedAck = done('flat', 'x', 'released', '已於 10:20:00 在電腦上解除，程式可以再交易')
  const released = stopLine('live', cmd(900, { live: releasedAck }), beat(), 0)
  assert.deepEqual([released.tone, released.label, released.detail], ['ok', '在聽', '已於 10:20:00 在電腦上解除，程式可以再交易'])
  const releasedThenDied = stopLine('live', cmd(4000, { live: releasedAck }), SILENT, 0)
  assert.deepEqual([releasedThenDied.tone, releasedThenDied.label], ['bad', '沒有回應'])
  assert.match(releasedThenDied.detail, /在電腦上解除/)
  assert.equal(stopLine('live', cmd(30, { live: done('flat', 'x', 'something_new', '?') }), beat(), 0).tone, 'bad') // 不認得的結果：保守

  assert.deepEqual(view('live', cmd(5, { live: ack('suspended', '程式在暫停送單，不會自動平倉，請手動處理；帳上多 1 口') }), beat()), ['bad', '不會自動平倉'])
  assert.deepEqual(view('live', cmd(5, { live: ack('ignored', '不認得的動作，沒有執行') }), beat()), ['bad', '沒有執行'])
  assert.equal(stopLine('live', cmd(5, { live: ack('brand_new_code', '?') }), beat(), 0).tone, 'bad')
})

test('AC18 要人處理時給的下一步：先看再平；一筆停止管多久；沒有程式在聽要先講', () => {
  assert.match(BROKER_HINT, /先看元大 App 的未平倉，有部位才平/)
  assert.match(BROKER_HINT, /程式之後連上也會自己平，不要重複下/) // 不然手動平完、程式又平一次，會變成反向部位
  assert.match(STOP_SCOPE, /只算今天，不會帶到明天/)
  assert.match(STOP_SCOPE, /解除要在跑交易程式的電腦前做/)
  assert.match(NOBODY_LISTENING, /現在沒有交易程式在聽遠端停止，按了不會平倉/)
  assert.match(NOBODY_LISTENING, /元大 App/)
})

test('AC18 「沒有程式在聽」只在真的沒有程式在聽、也沒有程式正在處理停止時講；頁面沒有新資料時只說不知道', () => {
  const idle = stopLines(null, {}, 0)
  assert.equal(nobodyListening(idle), true)
  assert.equal(nobodyListening(stopLines(null, { sim: beat() }, 0)), false)
  // 今天稍早停過、已經在電腦上解除，現在程式都沒聲音：照樣要講
  const releasedAck = done('flat', 'x', 'released', '已解除')
  assert.equal(nobodyListening(stopLines(cmd(4000, { live: releasedAck }), { live: SILENT }, 0)), true)
  assert.equal(nobodyListening(stopLines(cmd(5), { live: beat() }, 0)), false) // 正在等它收到
  const unknown = unknownLines('讀取中…')
  assert.deepEqual(unknown.map((l) => [l.who, l.tone, l.label]), [['live', 'off', '狀態不明'], ['sim', 'off', '狀態不明']])
  assert.equal(nobodyListening(unknown), false) // 不知道的時候不說「沒有程式在聽」
  assert.ok(DATA_STALE_S >= 30 && DATA_STALE_S < 120)
  const page = read('../src/cloud/AutotradeView.tsx')
  assert.match(page, /elapsed > DATA_STALE_S\s+\? unknownLines/)
  assert.match(page, /if \(fast \|\| visible \|\|/) // 分頁在前景就一定重抓
  assert.match(page, /addEventListener\('visibilitychange', onVisible\)/)
})

test('AC19 有停止還在等（等收到、等確認平掉）時更新變快；結束了或拖太久就回到平常', () => {
  const beats = { live: beat(), sim: beat() }
  const at = (c: StopCommand | null, elapsed = 0) => pollMs(c, stopLines(c, beats, elapsed), elapsed)
  assert.equal(at(null), POLL_MS)
  assert.equal(at(cmd(5)), STOP_FAST_MS) // 等收到
  assert.equal(at(cmd(5, { live: ack('flattening', 'x'), sim: ack('flat', 'x') })), STOP_FAST_MS) // 等確認平掉
  const allDone = { live: done('flattening', 'x', 'flat', 'y'), sim: done('flat', 'x', 'flat', 'y') }
  assert.equal(at(cmd(30, allDone)), POLL_MS)
  assert.equal(at(cmd(60)), POLL_MS) // 兩行都已經是紅的（沒收到）：不用再快，等人處理
  assert.equal(at(cmd(STOP_FAST_FOR_S + 1, { live: ack('flattening', 'x', { ack_age_s: 1 }) })), POLL_MS)
  assert.equal(at(cmd(5), STOP_FAST_FOR_S), POLL_MS)
  assert.ok(STOP_FAST_MS < POLL_MS && STOP_FAST_MS >= 1000)
})

test('AC20 頁面：要按兩次才送；只有一個地方會送出停止；沒有解除的按鈕；送失敗會先重讀再下結論', () => {
  const page = read('../src/cloud/AutotradeView.tsx')
  assert.match(page, /className="cq-at-stop-btn" onClick=\{\(\) => setStep\('confirm'\)\}/) // 第一下只打開確認
  assert.match(page, /className="cq-at-stop-yes" disabled=\{step === 'sending'\} onClick=\{send\}/) // 第二下才送
  assert.equal(page.split('sendAutotradeStop(').length - 1, 1)
  assert.match(page, /確定停止/)
  assert.match(page, /取消/)
  assert.match(page, /除了緊急停止以外，這一頁都只能看/)
  assert.doesNotMatch(page, /解除停止<|重新開始<|release|resume/) // 沒有解除或開始交易的按鈕
  assert.match(page, /\.catch\(\(e\) =>\s+\/\/[^\n]*\n\s+reread\(\)/) // 請求可能在寫入之後才斷掉
  assert.match(page, /lines\.map\(\(l\) => \(\s*<li key=\{l\.who\} className=\{l\.tone\}>/) // 每支程式一行
  assert.match(page, /\{nobody && <p className="cq-at-stop-nobody">\{NOBODY_LISTENING\}<\/p>\}/)
  assert.match(page, /\{hand && <p className="cq-at-stop-hand">\{BROKER_HINT\}<\/p>\}/)
  const api = read('../src/cloud/api.ts')
  const writes = api.split('\n').filter((l) => l.includes('/api/autotrade') && /'(PUT|POST|DELETE|PATCH)'/.test(l))
  assert.equal(writes.length, 1)
  assert.match(writes[0], /\('\/api\/autotrade\/stop', 'PUT', \{ confirm: 'stop' \}\)/)
})
