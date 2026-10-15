import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  DEF_GROUPS,
  INDEX_CODES,
  SECTORS,
  closeLabel,
  closeMood,
  dayReturn,
  liveLabel,
  liveMood,
  readClose,
  type FlowRow,
} from '../functions/_lib/mood.ts'
import { misUrls, parseMis, type Quote } from '../functions/_lib/quote.ts'
import { moodTone, moodView, pp, sectorWords, type CloseMood as WebClose, type LiveMood as WebLive } from '../src/cloud/mood.ts'

const Q = (sid: string, last: number | null, prev: number | null, date = '2026-10-15', time = '10:32:05'): Quote => ({
  sid,
  date,
  time,
  open: null,
  high: null,
  low: null,
  last,
  prev,
  volume: null,
  limit_up: null,
  limit_down: null,
  auction: false,
})

/** 大盤跌 2%；每個類股的漲跌（小數）照 chg 表，沒列的跟大盤一樣。 */
function quotes(chg: Record<string, number>, taiex = -0.02): Quote[] {
  const out = [Q('t00', 100 * (1 + taiex), 100), Q('o00', 97, 100)]
  for (const s of SECTORS) out.push(Q(s.code, 100 * (1 + (chg[s.code] ?? taiex)), 100))
  return out
}

test('T-363 AC1：加權、櫃買與 28 個類股一次請求問完（跟個股共用的 QuoteHub 每次一個網址）', () => {
  assert.equal(INDEX_CODES.length, 30)
  const urls = misUrls(INDEX_CODES)
  assert.equal(urls.length, 1)
  const ch = decodeURIComponent(urls[0])
  assert.ok(ch.includes('tse_t00.tw') && ch.includes('otc_o00.tw') && ch.includes('tse_t17.tw'))
  // 合計類（電子工業、化學生技醫療）不列，免得排行重複
  assert.ok(!INDEX_CODES.includes('t13') && !INDEX_CODES.includes('t07'))
})

test('T-363 AC2：開盤前證交所只給昨收（實際回應 2026-10-15 07:50）→ 盤中氛圍是 null，不顯示 0%', () => {
  const body = JSON.parse(readFileSync(new URL('./fixtures/mis-index-2026-10-15-0750-premarket.json', import.meta.url), 'utf8'))
  const got = parseMis(body)
  const t00 = got.find((q) => q.sid === 't00')
  assert.equal(t00?.prev, 48625.83)
  assert.equal(t00?.last, null)
  assert.ok(got.some((q) => q.sid === 'o00') && got.some((q) => q.sid === 't17'))
  assert.equal(liveMood(got), null)
})

test('T-363 AC3：盤中——相對大盤最強／最弱的類股、攻守差、標籤', () => {
  const live = liveMood(quotes({ t17: -0.002, t02: -0.005, t23: -0.006, t24: -0.031, t28: -0.028, t25: -0.026, t26: -0.025 }))
  assert.ok(live)
  assert.equal(live.date, '2026-10-15')
  assert.equal(live.time, '10:32:05')
  assert.ok(Math.abs(live.taiex - -0.02) < 1e-12)
  assert.ok(Math.abs((live.otc ?? 0) - -0.03) < 1e-12)
  assert.deepEqual(
    live.strong.map((m) => m.name),
    ['金融保險', '食品', '油電燃氣'],
  )
  assert.deepEqual(
    live.weak.map((m) => m.name),
    ['半導體', '電子零組件', '電腦週邊'],
  )
  assert.ok(Math.abs(live.strong[0].rel - 0.018) < 1e-12) // 金融 −0.2% 比大盤 −2% 強 1.8 個百分點
  // 防禦平均 −0.433%、攻擊平均 −2.833% → 差 2.4 個百分點
  assert.ok(Math.abs((live.tilt ?? 0) - 0.024) < 1e-12)
  assert.equal(live.label, '大跌・偏防守')
})

test('T-363 AC4：缺一個攻守類股就不算攻守（平均會偏）；跟加權不同天的類股不算', () => {
  const qs = quotes({}).filter((q) => q.sid !== 't23')
  const live = liveMood(qs)
  assert.equal(live?.tilt, null)
  assert.equal(live?.label, '大跌')
  const old = quotes({ t17: 0.05 }).map((q) => (q.sid === 't17' ? { ...q, date: '2026-10-14' } : q))
  const got = liveMood(old)
  assert.ok(got && !got.strong.some((m) => m.code === 't17'))
  assert.equal(got?.tilt, null)
})

test('T-363 AC5：標籤門檻——大盤 ±1% 算大漲大跌；攻守差 ±1 個百分點；資金比重 ±1 個百分點', () => {
  assert.equal(liveLabel(-0.015, 0.012), '大跌・偏防守')
  assert.equal(liveLabel(0.012, -0.02), '大漲・偏攻擊')
  assert.equal(liveLabel(0.002, 0.004), '攻守均衡')
  assert.equal(liveLabel(-0.0099, 0.0099), '攻守均衡')
  assert.equal(liveLabel(0.003, null), '類股強弱')
  assert.equal(closeLabel(-0.021, 0.013), '大跌・資金轉向防禦')
  assert.equal(closeLabel(0.004, -0.011), '資金轉向攻擊')
  assert.equal(closeLabel(0.004, 0.002), '資金分布如常')
  assert.equal(closeLabel(null, null), null)
  assert.equal(closeLabel(-0.02, null), '大跌')
})

const F = (grp: string, share: number, share_avg: number | null, up = 1, down = 2, ret: number | null = -0.01): FlowRow => ({ grp, share, share_avg, ret, up, down })

test('T-363 AC6：收盤後——防禦族群合計成交比重 vs 平均、流入流出前三、漲跌家數', () => {
  const rows = [
    F('金融', 0.061, 0.042, 30, 10, 0.004),
    F('電信', 0.012, 0.01),
    F('食品', 0.011, 0.01),
    F('油電燃氣', 0.006, 0.005),
    F('交通運輸及航運', 0.05, 0.042),
    F('晶圓製造', 0.2, 0.24, 2, 40, -0.03),
    F('IC設計', 0.08, 0.085),
    F('散熱', 0.02, 0.03),
    F('新族群', 0.01, null), // 沒有 20 日平均的不排
  ]
  const c = closeMood('2026-10-14', rows, { taiex: -0.021, otc: -0.025 })
  assert.ok(Math.abs(c.def_share - 0.09) < 1e-12)
  assert.ok(Math.abs((c.def_avg ?? 0) - 0.067) < 1e-12)
  assert.ok(Math.abs((c.shift ?? 0) - 0.023) < 1e-12)
  assert.equal(c.label, '大跌・資金轉向防禦')
  assert.deepEqual(
    c.inflow.map((m) => m.grp),
    ['金融', '交通運輸及航運', '電信'],
  )
  assert.deepEqual(
    c.outflow.map((m) => m.grp),
    ['晶圓製造', '散熱', 'IC設計'],
  )
  assert.equal(c.up, 30 + 2 + 1 * 7) // 金融 30、晶圓製造 2，其他七個族群各 1
  assert.equal(c.down, 10 + 40 + 2 * 7)
  assert.deepEqual(DEF_GROUPS, ['金融', '電信', '食品', '油電燃氣'])
  // 防禦族群有一個沒有 20 日平均 → 不算轉向，只寫大盤
  const partial = closeMood('2026-10-14', [F('金融', 0.06, null), F('食品', 0.01, 0.01)], { taiex: -0.02, otc: null })
  assert.equal(partial.def_avg, null)
  assert.equal(partial.shift, null)
  assert.equal(partial.label, '大跌')
})

test('T-363 AC7：指數當天漲跌——要有那天和前一個交易日；那天沒有收盤 → null', () => {
  const rows = [
    { index_id: 'TAIEX', date: '2026-10-14', close: 47600 },
    { index_id: 'TAIEX', date: '2026-10-13', close: 48625.83 },
    { index_id: 'TAIEX', date: '2026-10-08', close: 48000 },
    { index_id: 'TPEX', date: '2026-10-13', close: 420 },
  ]
  assert.ok(Math.abs((dayReturn(rows, 'TAIEX', '2026-10-14') ?? 0) - (47600 / 48625.83 - 1)) < 1e-12)
  assert.equal(dayReturn(rows, 'TPEX', '2026-10-14'), null)
  assert.equal(dayReturn(rows, 'TAIEX', '2026-10-15'), null)
})

function fakeDb(results: (() => Promise<unknown[]>)[], calls: string[]) {
  return ((strings: TemplateStringsArray, ...values: unknown[]) => {
    calls.push(strings.join('?') + JSON.stringify(values))
    const next = results.shift()
    return next ? next() : Promise.resolve([])
  }) as unknown as Parameters<typeof readClose>[0]
}

test('T-363 AC8：收盤版讀最近一天的族群資金與指數；表還沒建或還沒算 → null；其他錯誤照丟', async () => {
  const calls: string[] = []
  const missing = () => Promise.reject(Object.assign(new Error('relation "serving.group_flow_daily" does not exist'), { code: '42P01' }))
  assert.equal(await readClose(fakeDb([missing], calls)), null)
  assert.equal(await readClose(fakeDb([() => Promise.resolve([])], calls)), null)
  const got = await readClose(
    fakeDb(
      [
        () => Promise.resolve([{ date: '2026-10-14', ...F('金融', 0.061, 0.042) }]),
        () =>
          Promise.resolve([
            { index_id: 'TAIEX', date: '2026-10-14', close: 98 },
            { index_id: 'TAIEX', date: '2026-10-13', close: 100 },
          ]),
      ],
      calls,
    ),
    )
  assert.equal(got?.date, '2026-10-14')
  assert.ok(Math.abs((got?.taiex ?? 0) - -0.02) < 1e-12)
  assert.equal(got?.otc, null)
  assert.ok(calls.at(-2)?.includes('group_flow_daily'))
  assert.ok(calls.at(-1)?.includes('index_daily') && calls.at(-1)?.includes('"2026-10-14"'))
  await assert.rejects(readClose(fakeDb([() => Promise.reject(Object.assign(new Error('x'), { code: '28P01' }))], calls)))
})

test('T-363 AC9：盤中版不進共用暫存（QuoteHub 自己有 8 秒快取）、跟個股共用同一個 QuoteHub；收盤版進 60 秒暫存', () => {
  const read = (p: string) => readFileSync(new URL(`../functions/${p}`, import.meta.url), 'utf8')
  const live = read('api/mood/live.ts')
  assert.ok(!live.includes('_lib/cache'))
  assert.ok(live.includes("from '../../_lib/quote-live'"))
  assert.ok(read('api/quotes.ts').includes("from '../_lib/quote-live'"))
  assert.ok(!read('api/quotes.ts').includes('new QuoteHub'))
  assert.ok(read('api/mood/close.ts').includes('cached(cacheKey('))
})

// ── 網站卡片（src/cloud/mood.ts） ──────────────────────────────────────────

const LIVE: WebLive = {
  date: '2026-10-15',
  time: '10:32:05',
  taiex: -0.02,
  otc: -0.03,
  tilt: 0.024,
  label: '大跌・偏防守',
  strong: [{ code: 't17', name: '金融保險', chg: -0.002, rel: 0.018 }],
  weak: [{ code: 't24', name: '半導體', chg: -0.031, rel: -0.011 }],
}
const CLOSE: WebClose = {
  date: '2026-10-14',
  taiex: 0.004,
  otc: null,
  up: 900,
  down: 700,
  def_share: 0.09,
  def_avg: 0.067,
  shift: 0.023,
  label: '資金轉向防禦',
  inflow: [{ grp: '金融', diff: 0.019, share: 0.061, ret: 0.004 }],
  outflow: [{ grp: '晶圓製造', diff: -0.04, share: 0.2, ret: -0.03 }],
}

test('T-363 AC10：卡片——盤中比收盤新顯示類股強弱（資金傍晚才更新）；同一天以資金版為主、類股留著；只有收盤版就顯示資金', () => {
  const intraday = moodView(LIVE, CLOSE, { date: '2026-10-15', time: '10:33:00' })
  assert.equal(intraday?.when, '盤中 10:32')
  assert.equal(intraday?.label, '大跌・偏防守')
  assert.equal(intraday?.tone, 'def')
  assert.deepEqual(intraday?.sectors?.words, ['抗跌', '領跌'])
  assert.equal(intraday?.flow, null)
  assert.ok(intraday?.flowNote)
  // 收盤後、排程還沒跑完：寫「10/15 收盤」
  assert.equal(moodView(LIVE, CLOSE, { date: '2026-10-15', time: '15:00:00' })?.when, '10/15 收盤')
  // 排程跑完：同一天
  const evening = moodView(LIVE, { ...CLOSE, date: '2026-10-15', taiex: null }, { date: '2026-10-15', time: '18:00:00' })
  assert.equal(evening?.when, '10/15 收盤')
  assert.equal(evening?.label, '資金轉向防禦')
  assert.equal(evening?.taiex, -0.02) // 指數表還沒有當天 → 用證交所的收盤
  assert.ok(evening?.sectors && evening.flow)
  // 隔天開盤前：證交所只給昨收（live 是 null）→ 只有資金版
  const morning = moodView(null, CLOSE, { date: '2026-10-15', time: '08:30:00' })
  assert.equal(morning?.when, '10/14 收盤')
  assert.equal(morning?.sectors, null)
  assert.deepEqual(morning?.sectors ?? null, null)
  assert.equal(morning?.flow?.inflow[0].grp, '金融')
  assert.equal(moodView(null, null, { date: '2026-10-15', time: '08:30:00' }), null)
})

test('T-363 AC11：卡片用字——漲跌日換字、標籤顏色、百分點', () => {
  assert.deepEqual(sectorWords(-0.01), ['抗跌', '領跌'])
  assert.deepEqual(sectorWords(0.01), ['領漲', '落後'])
  assert.deepEqual(sectorWords(0), ['強勢', '弱勢'])
  assert.equal(moodTone('大跌・資金轉向防禦'), 'def')
  assert.equal(moodTone('偏攻擊'), 'off')
  assert.equal(moodTone('攻守均衡'), 'flat')
  assert.equal(moodTone(null), 'flat')
  assert.equal(pp(0.019), '+1.9')
  assert.equal(pp(-0.04), '-4.0')
})
