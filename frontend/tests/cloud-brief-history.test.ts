import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { briefDates, selectActual, selectBrief } from '../functions/_lib/brief-db.ts'
import { briefNeighbors } from '../src/cloud/war.ts'

function fakeDb(results: (() => Promise<unknown[]>)[], calls: string[]) {
  return ((strings: TemplateStringsArray, ...values: unknown[]) => {
    calls.push(strings.join('?') + JSON.stringify(values))
    const next = results.shift()
    return next ? next() : Promise.resolve([])
  }) as unknown as Parameters<typeof selectBrief>[0]
}

const missing = () => Promise.reject(Object.assign(new Error('relation does not exist'), { code: '42P01' }))

test('使用者 2026-10-17：今日一句話選日期——前一天（較舊）與後一天（較新），頭尾是 null', () => {
  const dates = ['2026-10-17', '2026-10-16', '2026-10-15']
  assert.deepEqual(briefNeighbors(dates, '2026-10-17'), { older: '2026-10-16', newer: null })
  assert.deepEqual(briefNeighbors(dates, '2026-10-16'), { older: '2026-10-15', newer: '2026-10-17' })
  assert.deepEqual(briefNeighbors(dates, '2026-10-15'), { older: null, newer: '2026-10-16' })
  assert.deepEqual(briefNeighbors(dates, '2026-01-01'), { older: null, newer: null })
})

test('使用者 2026-10-17：歷史晨報從存好的表讀——指定那天、最新一天、日期清單、當天實際收盤', async () => {
  const calls: string[] = []
  const row = { date: '2026-10-16', headline: '外資賣壓未歇' }
  assert.deepEqual(await selectBrief(fakeDb([() => Promise.resolve([row])], calls), '2026-10-16'), row)
  assert.ok(calls[0].includes('serving.morning_brief') && calls[0].includes('"2026-10-16"'))
  assert.ok(calls[0].includes("inputs->'headlines'"), '歷史那天的新聞頭條一起回')
  assert.equal(await selectBrief(fakeDb([], calls), null), null)
  assert.ok(calls[1].includes('[null,null]'), '不帶日期＝最新一天')

  const days = await briefDates(fakeDb([() => Promise.resolve([{ d: '2026-10-17' }, { d: '2026-10-16' }])], calls), 400)
  assert.deepEqual(days, ['2026-10-17', '2026-10-16'])
  assert.ok(calls[2].includes('ORDER BY date DESC') && calls[2].includes('[400]'))

  const j = { date: '2026-10-16', taiex_close: 47242, taiex_chg: 0.0066, otc_close: null, otc_chg: null, foreign_net: -4.5e10 }
  assert.deepEqual(await selectActual(fakeDb([() => Promise.resolve([{ j }])], calls), '2026-10-16'), j)
  assert.ok(calls[3].includes('serving.market_daily'))
  assert.equal(await selectActual(fakeDb([], calls), '2026-10-18'), null, '假日沒有收盤')
})

test('使用者 2026-10-17：表還沒建 → 沒有晨報，不讓首頁壞掉', async () => {
  const calls: string[] = []
  assert.equal(await selectBrief(fakeDb([missing], calls), '2026-10-16'), null)
  assert.deepEqual(await briefDates(fakeDb([missing], calls), 400), [])
  assert.equal(await selectActual(fakeDb([missing], calls), '2026-10-16'), null)
})

test('使用者 2026-10-17：首頁回日期清單、歷史端點驗日期格式且不呼叫模型', () => {
  const home = readFileSync(new URL('../functions/api/warroom/index.ts', import.meta.url), 'utf8')
  assert.ok(home.includes('selectBrief(db, null)') && home.includes('brief_dates: briefDays'))
  const api = readFileSync(new URL('../functions/api/warroom/brief.ts', import.meta.url), 'utf8')
  assert.ok(api.includes('DATE_RE.test(date)') && api.includes('fail(400'))
  assert.ok(api.includes('selectBrief(db, date)') && api.includes('selectActual(db, date)'))
  assert.ok(!/ai\/run|workers-ai|insight/i.test(api), '歷史只讀存好的晨報')
})
