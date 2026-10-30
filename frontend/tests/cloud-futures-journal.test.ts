// T-467：台指期對照策略與檢討單（使用者 2026-10-30：「那把策略也整合進去我的系統」）。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { JOURNAL_DDL, JOURNAL_MAX, JOURNAL_SETUPS, parseJournal } from '../functions/_lib/futures-journal.ts'
import { EXTRA_GROUPS, JOURNAL_GOAL, extrasAt, journalPoints, journalR, journalStats, journalYuan, statOf, type FuturesExtras, type JournalRow } from '../src/cloud/futures-extras.ts'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const good = { date: '2026-10-30', product: 'MTX', side: 1, time: '09:05', entry: 47000, exit: 47050, stop: 46950, qty: 1, setup: '回測確認', followed: true, note: '等回測' }
const row = (over: Partial<JournalRow> = {}): JournalRow => ({ id: '1', date: '2026-10-30', product: 'MTX', side: 1, time: '09:05', entry: 100, exit: 110, stop: 95, qty: 1, setup: '回測確認', followed: true, note: '', ...over })

test('T-467 AC1 檢討單驗內容：合法才收；未來日期、壞時間、壞價格、未知理由都擋', () => {
  assert.deepEqual(parseJournal(good, '2026-10-30'), good)
  assert.equal(parseJournal({ ...good, date: '2026-10-31' }, '2026-10-30'), null)
  assert.equal(parseJournal({ ...good, time: '25:00' }, '2026-10-30'), null)
  assert.equal(parseJournal({ ...good, entry: 0 }, '2026-10-30'), null)
  assert.equal(parseJournal({ ...good, side: 0 }, '2026-10-30'), null)
  assert.equal(parseJournal({ ...good, setup: '通靈' }, '2026-10-30'), null)
  assert.equal(parseJournal({ ...good, qty: 1.5 }, '2026-10-30'), null)
  assert.equal(parseJournal({ ...good, stop: 'abc' }, '2026-10-30'), null)
  assert.equal(parseJournal({ ...good, stop: '' }, '2026-10-30')?.stop, null)
  assert.equal(parseJournal({ ...good, note: 'x'.repeat(201) }, '2026-10-30'), null)
  assert.ok(JOURNAL_SETUPS.includes('回測確認') && JOURNAL_MAX > 1000)
})

test('T-467 AC2 點數、金額、風險倍數：放空方向相反；大台每點 200 元；沒停損沒有 R', () => {
  assert.equal(journalPoints(row()), 10)
  assert.equal(journalPoints(row({ side: -1 })), -10)
  assert.equal(journalYuan(row({ product: 'TX', qty: 2 })), 4000)
  assert.equal(journalYuan(row({ product: 'TMF' })), 100)
  assert.equal(journalR(row()), 2)
  assert.equal(journalR(row({ stop: null })), null)
  assert.equal(journalR(row({ stop: 105 })), null, '停損在進場價錯的一邊')
})

test('T-467 AC3 統計：勝率、打平勝率照平均賺賠；依理由與有沒有照計畫分組', () => {
  const rows = [row({ exit: 120 }), row({ exit: 90, setup: '突破', followed: false }), row({ exit: 110 })]
  const s = statOf('x', rows)
  assert.equal(s.n, 3)
  assert.ok(Math.abs(s.win - 2 / 3) < 1e-9)
  assert.equal(s.avgWin, 15)
  assert.equal(s.avgLoss, 10)
  assert.ok(Math.abs((s.breakeven ?? 0) - 0.4) < 1e-9)
  const j = journalStats(rows)
  assert.deepEqual(j.setups.map((x) => [x.name, x.n]), [['回測確認', 2], ['突破', 1]])
  assert.equal(j.followed.n, 2)
  assert.equal(j.broke.n, 1)
  assert.equal(statOf('空', []).breakeven, null)
  assert.equal(JOURNAL_GOAL, 100)
})

test('T-467 AC4 對照策略：復盤不劇透；分組合計；空字串看全部', () => {
  const t = (sleeve: FuturesExtras['trades'][number]['sleeve'], a: string, b: string, net: number) => ({ sleeve, side: 1 as const, entry_time: a, entry_price: 1, exit_time: b, exit_price: 2, exit_reason: 'EOD', points: 1, net_pnl: net })
  const blank = { name: '', position: 0, entry_price: null, realized: 0, open_pnl: null, closed: 0 }
  const x: FuturesExtras = {
    version: 1,
    noise_ready: true,
    events: [{ time: '09:00:00', kind: 'signal', sleeve: 'NOISE_LONG', text: 'a' }, { time: '12:00:00', kind: 'exit', sleeve: 'NOISE_LONG', text: 'b' }],
    sleeves: { NOISE_LONG: blank, NOISE_SHORT: blank, BOX_LONG: blank, BOX_SHORT: blank, TAIL_SHORT: { ...blank, position: -1 } },
    trades: [t('NOISE_LONG', '09:00:00', '10:00:00', 500), t('BOX_SHORT', '10:30:00', '12:00:00', -200)],
  }
  const early = extrasAt(x, '11:00')
  assert.equal(early.trades.length, 1)
  assert.equal(early.events.length, 1)
  assert.equal(early.groups[1].holding, 1, '回測確認那筆 11:00 還抱著')
  const all = extrasAt(x, '')
  assert.deepEqual(all.groups.map((g) => g.realized), [500, -200, 0])
  assert.equal(all.groups[2].holding, 1)
  assert.deepEqual(EXTRA_GROUPS.map((g) => g.name), ['區間突圍', '回測確認', '尾盤順勢空'])
})

test('T-467 AC5 端點：權限在讀資料前擋、變更只收本站、只動自己的資料；畫面標明未通過驗證、不併主帳', () => {
  const api = read('functions/api/futures/journal.ts')
  assert.equal(api.match(/denied\(ctx\.request, 'futures', ctx\.env\)/g)?.length, 3)
  assert.match(api, /WHERE id = \$\{id\}::bigint AND email = \$\{email\}/)
  assert.equal(api.match(/sameOrigin\(request\)/g)?.length, 2)
  assert.match(JOURNAL_DDL[1], /web\.futures_journal/)
  const ui = read('src/cloud/FuturesExtras.tsx')
  assert.match(ui, /對照策略（未通過驗證）/)
  assert.match(ui, /不併進上面的合計/)
  assert.match(read('src/cloud/FuturesPage.tsx'), /data\.extras && <ExtrasSection/)
  // 手機上 .cq-fu-trades 會藏第 4 欄；這裡第 4 欄是損益與刪除鈕，一定要看得到
  assert.equal(ui.match(/cq-fu-trades cq-fu-trades-all/g)?.length, 3)
  assert.match(read('src/cloud/futures.css'), /\.cq-fu-trades-all td:nth-child\(4\) \{ display: table-cell; \}/)
})
