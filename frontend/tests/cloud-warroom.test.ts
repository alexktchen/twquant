import assert from 'node:assert/strict'
import test from 'node:test'
import { SID_RE } from '../functions/_lib/picks.ts'
import { addDays, marketWide, newsTags, parseSids, pickNews, revenueDeadline, taipeiDay } from '../functions/_lib/warroom.ts'
import { breadth, signedYi, streakText, tone } from '../src/cloud/war.ts'

const row = (stock_id: string, subject: string, at: string) => ({ stock_id, name: null, subject, at })

test('T-379：昨日消息——名單股的全留、其他只留全市場重要類別；新到舊、有上限', () => {
  const rows = [
    row('1234', '公告本公司115年8月自結財務比率', '2026-10-16 15:00:00+08'),
    row('8084', '本公司接獲通知列為變更交易方法', '2026-10-16 23:04:00+08'),
    row('2330', '公告本公司9月營收', '2026-10-16 14:00:00+08'),
    row('9999', '本公司董事會決議辦理現金增資', '2026-10-16 18:00:00+08'),
  ]
  const out = pickNews(rows, new Set(['2330']))
  assert.deepEqual(out.map((n) => [n.stock_id, n.listed]), [['8084', false], ['9999', false], ['2330', true]])
  assert.deepEqual(out[0].tags, ['變更交易／下市'])
  assert.deepEqual(out[2].tags, ['營收／財報／股利'])
  assert.equal(pickNews(rows, new Set(['2330']), 1).length, 1)
  assert.equal(marketWide('本公司董事會決議庫藏股買回'), true)
  assert.equal(marketWide('公告本公司9月營收'), false)
  assert.deepEqual(newsTags('今天天氣很好'), [])
})

test('T-379：台北日期、加天數、月營收截止', () => {
  assert.equal(taipeiDay(Date.parse('2026-10-16T16:30:00Z')), '2026-10-17')
  assert.equal(addDays('2026-12-31', 1), '2027-01-01')
  assert.deepEqual(revenueDeadline('2026-10-08'), { date: '2026-10-10', days: 2 })
  assert.deepEqual(revenueDeadline('2026-10-10'), { date: '2026-10-10', days: 0 })
  assert.deepEqual(revenueDeadline('2026-12-17'), { date: '2027-01-10', days: 24 })
})

test('T-379：追蹤股代號只收合法的、去重、有上限', () => {
  assert.deepEqual(parseSids('2330,2317,2330,bad;x,0050', SID_RE), ['2330', '2317', '0050'])
  assert.deepEqual(parseSids(null, SID_RE), [])
  assert.equal(parseSids(Array.from({ length: 150 }, (_, i) => String(1000 + i)).join(','), SID_RE).length, 100)
})

test('T-379：首頁的數字格式——億帶正負號、連續天數白話、漲跌家數比例、顏色', () => {
  assert.equal(signedYi(-1.4097e11), '−1,410 億')
  assert.equal(signedYi(2.443e10), '+244 億')
  assert.equal(signedYi(null), '—')
  assert.equal(streakText(-5), '連賣 5 天')
  assert.equal(streakText(3), '連買 3 天')
  assert.equal(streakText(1), '')
  assert.equal(streakText(null), '')
  const b = breadth(442, 1334, 100)
  assert.ok(Math.abs(b.up + b.down + b.flat - 100) < 1e-9 && b.down > b.up)
  assert.deepEqual(breadth(0, 0, 0), { up: 0, down: 0, flat: 0 })
  assert.equal(tone(0.01), 'up')
  assert.equal(tone(-0.01), 'down')
  assert.equal(tone(null), 'flat')
})

test('使用者 2026-10-17：昨日消息的輪播頭尾相接；內文接回挑好的那幾則', async () => {
  const { carouselStep, newsId } = await import('../src/cloud/war.ts')
  const { attachContent } = await import('../functions/_lib/warroom.ts')
  assert.equal(carouselStep(0, 3, 1), 1)
  assert.equal(carouselStep(2, 3, 1), 0)
  assert.equal(carouselStep(0, 3, -1), 2)
  assert.equal(carouselStep(5, 0, 1), 0)
  const item = { stock_id: '8084', name: null, subject: '變更交易方法', at: '2026-10-16 23:04', ts: 1792170240, tags: [], listed: true }
  assert.equal(newsId(item), '8084|1792170240|變更交易方法')
  const out = attachContent([item, { ...item, ts: 1 }], [{ stock_id: '8084', ts: 1792170240, subject: '變更交易方法', content: '1.事實發生日' }])
  assert.deepEqual(out.map((n) => n.content), ['1.事實發生日', null])
})

test('內文欄還沒建（42703）或表還沒建（42P01）都當沒有資料；其他錯誤照丟', async () => {
  const { readNewColumn } = await import('../functions/_lib/scorecard.ts')
  const fail = (code: string) => async () => {
    throw Object.assign(new Error(code), { code })
  }
  assert.deepEqual(await readNewColumn(fail('42703')), [])
  assert.deepEqual(await readNewColumn(fail('42P01')), [])
  await assert.rejects(readNewColumn(fail('57014')), /57014/)
})
