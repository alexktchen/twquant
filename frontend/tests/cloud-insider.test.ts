import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { INSIDER_LOOKBACK_DAYS, insiderFlag, insiderText, sharesText, type InsiderNotice } from '../functions/_lib/insider.ts'
import { stockCalendar } from '../functions/_lib/stock-calendar.ts'
import { INSIDER_DAYS, insiderTag } from '../src/cloud/insider.ts'
import { STOCK_CAL_KIND, calDetail } from '../src/cloud/stockcal.ts'

const notice = (over: Partial<InsiderNotice> = {}): InsiderNotice => ({
  date: '2026-10-16',
  role: '董事本人',
  person: '兆淞投資有限公司',
  method: '鉅額逐筆交易',
  planned_shares: 2_000_000,
  period_start: '2026-10-19',
  period_end: '2026-11-18',
  transferee: null,
  ...over,
})

test('T-384 AC-1：一筆申報的一句話——身分、姓名、方式、股數、期間；贈與加受讓人；算不出股數就不寫', () => {
  assert.equal(insiderText(notice()), '董事本人 兆淞投資有限公司 鉅額逐筆交易 200 萬股（10/19～11/18）')
  assert.equal(
    insiderText(notice({ role: '董事配偶', person: '鄭雅文之配偶', method: '贈與', planned_shares: 2_348_885, period_start: '2026-10-16', period_end: '2026-10-18', transferee: '鄭雅文' })),
    '董事配偶 鄭雅文之配偶 贈與 234.9 萬股（10/16～10/18），受讓人 鄭雅文',
  )
  assert.equal(insiderText(notice({ planned_shares: null, period_start: null, period_end: null, person: null })), '董事本人 鉅額逐筆交易')
  assert.equal(sharesText(5000), '5,000 股')
  assert.equal(sharesText(12_345_678), '1,234.6 萬股')
  assert.equal(sharesText(null), '')
})

test('T-384 AC-2：名單小標摘要——幾筆、合計（只加算得出來的）、最近一筆、身分不重複；沒有申報是 null', () => {
  assert.equal(insiderFlag([]), null)
  const f = insiderFlag([
    notice({ date: '2026-10-01', planned_shares: 100_000 }),
    notice({ date: '2026-10-16', role: '經理人本人', planned_shares: null }),
    notice({ date: '2026-10-10', role: '董事本人', planned_shares: 250_000 }),
  ])
  assert.deepEqual(f, { n: 3, shares: 350_000, latest: '2026-10-16', roles: ['董事本人', '經理人本人'] })
  assert.equal(insiderFlag([notice({ planned_shares: null })])?.shares, null, '全部算不出來 → null，不寫 0')
  assert.ok(INSIDER_LOOKBACK_DAYS >= 20 && INSIDER_LOOKBACK_DAYS === INSIDER_DAYS, '前後端的「近幾天」同一個數')
})

test('T-384 AC-3：名單列的小標文字——一筆寫「內部人申讓」、多筆加次數；title 說明不等於一定會賣、名單不刪減', () => {
  assert.equal(insiderTag(null), null)
  assert.equal(insiderTag(undefined), null)
  assert.equal(insiderTag({ n: 0, shares: null, latest: '2026-10-16', roles: [] }), null)
  const one = insiderTag({ n: 1, shares: 2_000_000, latest: '2026-10-16', roles: ['董事本人'] })
  assert.equal(one?.text, '內部人申讓')
  assert.match(one!.title, /近 30 天有 1 筆.*（董事本人）.*合計約 200 萬股.*最近一筆 10\/16/)
  assert.match(one!.title, /不等於一定會賣完/)
  assert.match(one!.title, /名單不因此刪減/)
  const many = insiderTag({ n: 3, shares: null, latest: '2026-10-02', roles: ['董事本人', '經理人本人', '監察人本人', '大股東'] })
  assert.equal(many?.text, '內部人申讓×3')
  assert.ok(!many!.title.includes('合計'), '算不出合計就不寫')
  assert.ok(many!.title.includes('董事本人、經理人本人、監察人本人') && !many!.title.includes('大股東'), '身分最多列三個')
})

test('T-384 AC-4：個股頁行事曆多一種「內部人申讓」——記在申報公開那天、說明是那一句話、同一天排在法說會之後；舊呼叫（三個參數）不變', () => {
  const items = stockCalendar(
    [{ date: '2026-10-16', kind: 'cash', cash_dividend: 1, stock_ratio: 0, rights_ratio: 0 }],
    [{ date: '2026-10-16', conf_time: '14:00', location: null, summary: null }],
    [],
    [notice(), notice({ date: '2026-09-01', role: '經理人本人', planned_shares: 50_000 })],
  )
  assert.deepEqual(
    items.map((x) => `${x.date} ${x.kind}`),
    ['2026-10-16 insider', '2026-10-16 conf', '2026-10-16 cash', '2026-09-01 insider'],
  )
  assert.equal(items[0].summary, '董事本人 兆淞投資有限公司 鉅額逐筆交易 200 萬股（10/19～11/18）')
  assert.equal(calDetail({ ...items[0], kind: 'insider' }), items[0].summary)
  assert.equal(STOCK_CAL_KIND.insider, '內部人申讓')
  assert.equal(stockCalendar([], [], []).length, 0)
})

test('T-384 AC-5（靜態）：名單 API 與個股行事曆 API 都讀 serving.insider_transfer_notices，而且只讀到名單那天（不偷看之後的申報）', () => {
  const picks = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.ok(picks.includes('readInsider(db, target, sids)') && picks.includes('insider: insider.get(r.stock_id) ?? null'))
  const lib = readFileSync(new URL('../functions/_lib/insider.ts', import.meta.url), 'utf8')
  assert.ok(lib.includes('serving.insider_transfer_notices'))
  assert.match(lib, /date <= \$\{date\}::date/)
  const cal = readFileSync(new URL('../functions/api/stocks/[sid]/calendar.ts', import.meta.url), 'utf8')
  assert.ok(cal.includes('serving.insider_transfer_notices') && cal.includes('BETWEEN ${since}::date AND ${today}::date'))
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  assert.ok(app.includes('insiderTag(p.insider)'))
})
