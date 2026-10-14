import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import {
  COOLDOWN_MS,
  FRESH_MS,
  FUGLE_COOLDOWN_MS,
  FUGLE_FRESH_MS,
  FUGLE_PER_MIN,
  FUGLE_PER_REQUEST,
  MAX_COOLDOWN_MS,
  MIN_GAP_MS,
  QuoteHub,
  STALE_MAX_MS,
  parseFugle,
  readFugle,
  readMis,
  type Quote,
} from '../functions/_lib/quote.ts'
import { liveBackoff } from '../src/cloud/live.ts'

const q = (sid: string, last = 100): Quote => ({
  sid,
  date: '2026-10-14',
  time: '12:40:00',
  open: 100,
  high: 101,
  low: 99,
  last,
  prev: 100,
  volume: 1000,
  limit_up: 110,
  limit_down: 90,
  auction: false,
})

function setup() {
  let t = 1_000_000
  const calls: string[][] = []
  let fail: Error | null = null
  const hub = new QuoteHub(
    async (sids) => {
      calls.push(sids)
      if (fail) throw fail
      return sids.filter((s) => s !== '9999').map((s) => q(s, t))
    },
    () => t,
  )
  return {
    hub,
    calls,
    tick: (ms: number) => (t += ms),
    failWith: (e: Error | null) => (fail = e),
  }
}

test('2026-10-14 證交所限流：名單、個股頁問到同一檔共用快取，不再各問一次', async () => {
  const { hub, calls, tick } = setup()
  const list = await hub.get(['2330', '3090', '9999'])
  assert.deepEqual(
    list.quotes.map((x) => x.sid),
    ['2330', '3090'],
  )
  assert.equal(list.stale, false)
  tick(MIN_GAP_MS)
  // 個股頁問 3090：還新鮮，直接給；證交所沒回的 9999 也記著，不再問
  assert.deepEqual((await hub.get(['3090'])).quotes.map((x) => x.sid), ['3090'])
  assert.deepEqual((await hub.get(['9999'])).quotes, [])
  assert.equal(calls.length, 1)
  // 過了新鮮期才再問，而且只問過期的那些
  tick(FRESH_MS)
  await hub.get(['2330'])
  assert.deepEqual(calls[1], ['2330'])
})

test('請求太密：離上一次問證交所不到 2 秒，先給舊一點的快取、標 stale', async () => {
  const { hub, calls, tick } = setup()
  await hub.get(['2330'])
  tick(FRESH_MS + 1)
  await hub.get(['3090']) // 新的一檔：離上次已超過 2 秒，照問
  tick(500)
  const got = await hub.get(['2330', '3090'])
  assert.equal(calls.length, 2) // 2330 過期了，但離上一次問不到 2 秒 → 不問
  assert.equal(got.stale, true)
  assert.deepEqual(got.quotes.map((x) => x.sid), ['2330', '3090'])
  // 太密又沒有任何快取 → 丟錯（網站顯示「暫時取不到」）
  await assert.rejects(hub.get(['2317']), /太密/)
})

test('被證交所擋下：暫停 1 分鐘，連續被擋加倍到 10 分鐘；期間只給快取，恢復後回到 1 分鐘', async () => {
  const { hub, calls, tick, failWith } = setup()
  await hub.get(['2330'])
  tick(FRESH_MS)
  failWith(new Error('證交所防火牆暫時擋下即時報價'))
  const blocked = await hub.get(['2330'])
  assert.equal(blocked.stale, true) // 還有舊的就先給
  assert.equal(blocked.retryAfter, COOLDOWN_MS / 1000)
  await assert.rejects(
    hub.get(['3090']),
    (e: Error & { retryAfter?: number }) => e.message.includes('擋下') && e.retryAfter === COOLDOWN_MS / 1000,
  )
  assert.equal(calls.length, 2) // 暫停期間不再打證交所
  // 暫停結束再試、又被擋：暫停加倍
  tick(COOLDOWN_MS)
  await hub.get(['2330'])
  assert.equal(calls.length, 3)
  tick(1000)
  assert.equal((await hub.get(['2330'])).retryAfter, (2 * COOLDOWN_MS) / 1000 - 1)
  for (let i = 0; i < 6; i += 1) {
    tick(MAX_COOLDOWN_MS)
    await hub.get(['2330']).catch(() => null)
  }
  tick(1)
  assert.ok(((await hub.get(['2330']).catch((e: { retryAfter: number }) => e)) as { retryAfter: number }).retryAfter <= MAX_COOLDOWN_MS / 1000)
  // 恢復
  failWith(null)
  tick(MAX_COOLDOWN_MS)
  const ok = await hub.get(['2330'])
  assert.equal(ok.stale, false)
  assert.equal(ok.retryAfter, null)
  failWith(new Error('x'))
  tick(FRESH_MS)
  assert.equal((await hub.get(['2330'])).retryAfter, COOLDOWN_MS / 1000) // 回到 1 分鐘
})

test('太舊的快取（15 分鐘）不給', async () => {
  const { hub, tick, failWith } = setup()
  await hub.get(['2330'])
  failWith(new Error('擋'))
  tick(STALE_MAX_MS)
  await assert.rejects(hub.get(['2330']))
})

test('證交所防火牆回的是一頁 HTML：當成被擋，不是解析錯就算了', async () => {
  const html = '<html><body>因為安全性考量，您所執行的頁面無法呈現。</body></html>'
  const res = (status: number, body: string) => ({ ok: status === 200, status, text: async () => body })
  await assert.rejects(readMis(res(200, html)), /防火牆/)
  await assert.rejects(readMis(res(502, html)), /HTTP 502/)
  await assert.rejects(readMis(res(200, '<html>維護中</html>')), /不是 JSON/)
  const ok = { rtcode: '0000', msgArray: [{ c: '2330', d: '20261014', t: '12:49:25', z: '2520.0000', y: '2500.0000', o: '2505', h: '2535', l: '2500', v: '1000', u: '2750', w: '2250' }] }
  assert.equal((await readMis(res(200, JSON.stringify(ok))))[0].last, 2520)
})

test('網站端：取不到時 30 秒起加倍等、最多 5 分鐘；證交所說要等更久就照它', () => {
  assert.equal(liveBackoff(1, null), 30_000)
  assert.equal(liveBackoff(2, null), 60_000)
  assert.equal(liveBackoff(4, null), 240_000)
  assert.equal(liveBackoff(9, null), 300_000)
  assert.equal(liveBackoff(1, 120), 120_000)
})

// ── 富果備援（2026-10-14）────────────────────────────────────────────────

const fugleBody = JSON.parse(readFileSync(new URL('./fixtures/fugle-quote-5289.json', import.meta.url), 'utf8'))

test('富果的回應（2026-10-14 13:10 實抓宜鼎）換成與證交所同形狀的報價：量從張換成股、時間換成台北', () => {
  assert.deepEqual(parseFugle(fugleBody), {
    sid: '5289',
    date: '2026-10-14',
    time: '13:10:35',
    open: 1200,
    high: 1200,
    low: 1160,
    last: 1175,
    prev: 1190,
    volume: 991_000,
    limit_up: null, // 網站由昨收照升降單位推
    limit_down: null,
    auction: false,
  })
  assert.equal(parseFugle({ ...fugleBody, lastPrice: undefined }).last, 1175) // 沒有最新成交用收盤
  assert.equal(parseFugle({ ...fugleBody, isTrial: true }).auction, true)
  assert.throws(() => parseFugle({ ...fugleBody, date: '20261014' }), /異常/)
  assert.throws(() => parseFugle(null), /異常/)
})

test('富果的錯誤：代號不存在回 null；額度用完（429）、金鑰不對（401）帶 status 丟出去', async () => {
  const res = (status: number, body: unknown = fugleBody) => ({ ok: status === 200, status, json: async () => body })
  assert.equal((await readFugle(res(200)))?.sid, '5289')
  assert.equal(await readFugle(res(404)), null)
  await assert.rejects(readFugle(res(429)), (e: Error & { status?: number }) => e.status === 429)
  await assert.rejects(readFugle(res(401)), (e: Error & { status?: number }) => e.status === 401)
})

function fallbackSetup() {
  const s = setup()
  const asked: string[] = []
  let status: number | null = null
  const fallback = async (sid: string) => {
    asked.push(sid)
    if (status !== null) throw Object.assign(new Error(`HTTP ${status}`), { status })
    return sid === '9999' ? null : q(sid, 200)
  }
  return { ...s, asked, fallback, failFugle: (st: number | null) => (status = st) }
}

test('證交所擋下時用富果補：補到的不算舊、網站不用停；證交所正常時完全不用富果', async () => {
  const { hub, calls, asked, fallback, tick, failWith } = fallbackSetup()
  await hub.get(['2330'], fallback)
  assert.deepEqual(asked, []) // 證交所正常
  tick(FRESH_MS)
  failWith(new Error('證交所防火牆暫時擋下即時報價'))
  const got = await hub.get(['2330', '3090', '9999'], fallback)
  assert.deepEqual(asked, ['3090', '9999', '2330']) // 沒有的先補，再補最舊的
  assert.deepEqual(got.quotes.map((x) => [x.sid, x.last]), [['2330', 200], ['3090', 200]])
  assert.equal(got.stale, false)
  assert.equal(got.retryAfter, null) // 富果補得上：網站照常輪詢
  assert.equal(calls.length, 2)
  // 富果補的 30 秒內不重問，也不再去敲證交所（還在暫停）
  tick(FUGLE_FRESH_MS - 1)
  await hub.get(['2330', '3090'], fallback)
  assert.equal(asked.length, 3)
  assert.equal(calls.length, 2)
})

test('富果額度：每次最多補 15 檔、每分鐘不超過 40 檔，其餘先給舊的', async () => {
  const { hub, asked, fallback, tick, failWith } = fallbackSetup()
  const many = Array.from({ length: 30 }, (_, i) => String(1101 + i))
  failWith(new Error('擋'))
  const first = await hub.get(many, fallback)
  assert.equal(asked.length, FUGLE_PER_REQUEST)
  assert.equal(first.quotes.length, FUGLE_PER_REQUEST)
  assert.equal(first.stale, true) // 還有沒補到的
  // 同一時刻再問（證交所暫停中，不受 2 秒間隔限制）：額度隨時間連續回補，這裡時間沒走、不回補
  await hub.get(many, fallback)
  assert.equal(asked.length, 2 * FUGLE_PER_REQUEST)
  await hub.get(['2330', '2317', '2454', '2412', '2882', '2881', '1301', '1303', '2002', '2603', '2609', '2615'], fallback)
  assert.equal(asked.length, FUGLE_PER_MIN) // 這分鐘的額度用完了（30 + 10）
  tick(30_000) // 半分鐘回補一半額度
  await hub.get(['3008', '3034', '3037', '3045', '3231', '3443', '3661', '3711', '4938', '5871', '6415', '6505', '6669', '1216', '2207', '2303', '2308', '2327', '2345', '2356', '2357', '2379', '2382', '2395', '2408'], fallback)
  assert.equal(asked.length, FUGLE_PER_MIN + FUGLE_PER_REQUEST)
})

test('富果也擋（429）或金鑰不對：富果停 1 分鐘，期間跟以前一樣只給證交所的舊價格、叫網站晚點再來', async () => {
  const { hub, asked, fallback, tick, failWith, failFugle } = fallbackSetup()
  await hub.get(['2330'], fallback)
  tick(FRESH_MS)
  failWith(new Error('擋'))
  failFugle(429)
  const got = await hub.get(['2330'], fallback)
  assert.equal(got.stale, true)
  assert.ok(got.retryAfter !== null) // 兩個都不行 → 網站暫停
  tick(MIN_GAP_MS)
  await hub.get(['2330'], fallback)
  assert.equal(asked.length, 1) // 富果停用中
  failFugle(null)
  tick(FUGLE_COOLDOWN_MS)
  assert.equal((await hub.get(['2330'], fallback)).quotes[0].last, 200)
  // 沒設金鑰（沒有 fallback）：照舊只用證交所
  assert.ok((await hub.get(['2330'])).quotes.length === 1)
})
