import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { TRADES_DDL, TRADE_MAX, boughtKeys, parseTrade, parseTradeKey } from '../functions/_lib/trades.ts'
import { tradeFormError, tradeText } from '../src/cloud/trades.ts'

const TODAY = '2026-10-18'

test('T-388 AC-1：請求內容驗證——代號、日期不能是未來、買或賣、價格與張數；來源預設手動；不合法回 null', () => {
  const t = parseTrade({ sid: '2330', date: '2026-10-16', side: 'buy', price: '1180', qty: 2, source: 'pick', line: 'CORE' }, TODAY)
  assert.deepEqual(t, { sid: '2330', date: '2026-10-16', side: 'buy', price: 1180, qty: 2, source: 'pick', line: 'CORE' })
  assert.deepEqual(parseTrade({ sid: '0050', date: TODAY, side: 'sell' }, TODAY), { sid: '0050', date: TODAY, side: 'sell', price: null, qty: null, source: 'manual', line: null })
  assert.equal(parseTrade({ sid: '2330', date: '2026-10-19', side: 'buy' }, TODAY), null, '未來日期')
  assert.equal(parseTrade({ sid: '233', date: TODAY, side: 'buy' }, TODAY), null)
  assert.equal(parseTrade({ sid: '2330', date: TODAY, side: 'hold' }, TODAY), null)
  assert.equal(parseTrade({ sid: '2330', date: TODAY, side: 'buy', price: -1 }, TODAY), null)
  assert.equal(parseTrade({ sid: '2330', date: TODAY, side: 'buy', qty: 1.5 }, TODAY), null)
  assert.equal(parseTrade({ sid: '2330', date: TODAY, side: 'buy', source: 'robot' }, TODAY), null)
  assert.equal(parseTrade(null, TODAY), null)
  assert.deepEqual(parseTradeKey({ sid: '2330', date: '2026-10-16', side: 'buy' }), { sid: '2330', date: '2026-10-16', side: 'buy' })
  assert.equal(parseTradeKey({ sid: '2330', date: '2026-10-16' }), null)
  assert.ok(TRADES_DDL.some((s) => s.includes('PRIMARY KEY (email, stock_id, trade_date, side)')))
  assert.ok(TRADE_MAX >= 1000)
})

test('T-388 AC-2：「我買了」的鍵＝名單日＋代號，只算來源為名單勾選的買進', () => {
  const keys = boughtKeys([
    { sid: '2330', date: '2026-10-16', side: 'buy', price: null, qty: null, source: 'pick', line: 'CORE' },
    { sid: '2317', date: '2026-10-16', side: 'buy', price: 100, qty: 1, source: 'manual', line: null },
    { sid: '2330', date: '2026-10-17', side: 'sell', price: null, qty: null, source: 'pick', line: 'CORE' },
  ])
  assert.deepEqual([...keys], ['2026-10-16|2330'])
})

test('T-388 AC-3：一筆的文字與表單檢查', () => {
  const name = (c: string | null) => (c === 'CORE' ? '大盤精選' : null)
  assert.equal(tradeText({ sid: '2330', date: '2026-10-16', side: 'buy', price: 1180, qty: 2, source: 'pick', line: 'CORE' }, name), '買 2330 10/16 @1,180 × 2 張（名單勾選・大盤精選）')
  assert.equal(tradeText({ sid: '0050', date: '2026-10-01', side: 'sell', price: null, qty: null, source: 'manual', line: null }, name), '賣 0050 10/1（手動）')
  assert.equal(tradeFormError({ sid: '2330', date: '2026-10-16', price: '', qty: '' }, TODAY), null)
  assert.equal(tradeFormError({ sid: '23', date: '2026-10-16', price: '', qty: '' }, TODAY), '代號要 4～6 碼')
  assert.equal(tradeFormError({ sid: '2330', date: '2026-10-19', price: '', qty: '' }, TODAY), '日期不對或在未來')
  assert.equal(tradeFormError({ sid: '2330', date: '2026-10-16', price: 'x', qty: '' }, TODAY), '價格要是 0 以上的數字')
  assert.equal(tradeFormError({ sid: '2330', date: '2026-10-16', price: '', qty: '0' }, TODAY), '張數要是正整數')
})

test('T-388 AC-4（靜態）：端點 GET／PUT／DELETE 都驗身分、寫入只收本站；名單列有「我買了」、追蹤分頁有我的下單', () => {
  const api = readFileSync(new URL('../functions/api/trades/index.ts', import.meta.url), 'utf8')
  for (const fn of ['onRequestGet', 'onRequestPut', 'onRequestDelete']) assert.ok(api.includes(fn), fn)
  assert.equal(api.match(/accessEmail\(request\)/g)?.length, 3)
  assert.equal(api.match(/sameOrigin\(request\)/g)?.length, 2)
  assert.ok(api.includes('ON CONFLICT (email, stock_id, trade_date, side)'))
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  assert.ok(app.includes('我買了') && app.includes('<TradesPanel'))
})
