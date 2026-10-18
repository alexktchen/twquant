import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { WARRANT_DAYS, putShare, warrantDays } from '../functions/_lib/warrants.ts'
import { ivText, moneyText, putShareText, ratioText, warrantLine } from '../src/cloud/warrants.ts'

const day = (date: string, call: number, put: number, over: Partial<Record<string, unknown>> = {}) => ({
  date,
  n_call: 12,
  n_put: 3,
  call_value: call,
  put_value: put,
  underlying_amount: 1_000_000_000,
  value_ratio: (call + put) / 1_000_000_000,
  iv_call: 0.38,
  iv_put: 0.41,
  iv_call_days: 60,
  iv_put_days: 45,
  ...over,
})

test('T-385 AC-1：後端列 → 數字型別、新到舊；認售占比；都是 0 → null', () => {
  const rows = warrantDays([day('2026-10-15', '100', '50', { iv_put: null, iv_put_days: null }) as never, day('2026-10-16', 120_000_000, 35_000_000) as never])
  assert.deepEqual(rows.map((r) => r.date), ['2026-10-16', '2026-10-15'])
  assert.equal(rows[1].call_value, 100)
  assert.equal(rows[1].iv_put, null)
  assert.ok(Math.abs(putShare(rows[0])! - 35 / 155) < 1e-12)
  assert.equal(putShare({ call_value: 0, put_value: 0 }), null)
  assert.ok(WARRANT_DAYS >= 3)
})

test('T-385 AC-2：文字——金額單位、隱含波動、認售占比、對標的成交金額；一句摘要', () => {
  assert.equal(moneyText(120_000_000), '1.2 億')
  assert.equal(moneyText(3_500_000_000), '35 億')
  assert.equal(moneyText(35_000_000), '3,500 萬')
  assert.equal(moneyText(850_000), '85 萬')
  assert.equal(moneyText(0), '0')
  assert.equal(ivText(0.384), '38%')
  assert.equal(ivText(null), '—')
  assert.equal(putShareText({ call_value: 120_000_000, put_value: 35_000_000 }), '認售 23%')
  assert.equal(putShareText({ call_value: 0, put_value: 0 }), '')
  assert.equal(ratioText(0.123), '權證成交＝標的的 12%')
  assert.equal(ratioText(0.0234), '權證成交＝標的的 2.3%')
  assert.equal(ratioText(null), '')
  const d = day('2026-10-16', 120_000_000, 35_000_000, { value_ratio: 0.155 })
  assert.equal(warrantLine(d as never), '認購 1.2 億、認售 3,500 萬（認售 23%）；權證成交＝標的的 16%；價平隱含波動 認購 38%／認售 41%')
  const none = day('2026-10-16', 0, 0, { value_ratio: null, iv_call: null, iv_put: null })
  assert.equal(warrantLine(none as never), '認購 0、認售 0')
})

test('T-385 AC-3（靜態）：個股頁端點讀 warrant_summary_daily、只到今天；個股頁有放', () => {
  const lib = readFileSync(new URL('../functions/_lib/warrants.ts', import.meta.url), 'utf8')
  assert.ok(lib.includes('serving.warrant_summary_daily') && lib.includes('date <= ${today}::date'))
  const api = readFileSync(new URL('../functions/api/stocks/[sid]/warrants.ts', import.meta.url), 'utf8')
  assert.ok(api.includes('SID_RE.test(sid)') && api.includes('readWarrantDays(db, sid, today)'))
  const app = readFileSync(new URL('../src/cloud/CloudApp.tsx', import.meta.url), 'utf8')
  // 2026-10-18 使用者嫌個股頁太長：權證、千張大戶、停損基礎率併成一張「籌碼與風險」卡
  assert.ok(app.includes('<StockFacts sid={sid} refresh={refresh} />'))
})
