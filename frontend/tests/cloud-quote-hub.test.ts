import assert from 'node:assert/strict'
import test from 'node:test'
import { COOLDOWN_MS, FRESH_MS, MAX_COOLDOWN_MS, MIN_GAP_MS, QuoteHub, STALE_MAX_MS, readMis, type Quote } from '../functions/_lib/quote.ts'
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
