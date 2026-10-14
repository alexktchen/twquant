import assert from 'node:assert/strict'
import test from 'node:test'
import { readReasons } from '../functions/_lib/reasons.ts'
import { cardReasons, factorsNote, mark, primaryModel, whyLine, type Reason } from '../src/cloud/reasons.ts'

const R = (t: string, s: number, f = t): Reason => ({ f, t, s })

test('T-360：名單那一行只放加分的、最多兩個；沒有加分的就不顯示', () => {
  const rs = [R('投信連買 4 天', 1), R('成交量是前 5 日平均的 2.1 倍', 1), R('月營收年增 35%', 1), R('每日真實波幅約 3.2%', -1)]
  assert.equal(whyLine(rs), '投信連買 4 天・成交量是前 5 日平均的 2.1 倍')
  assert.equal(whyLine(rs, 3), '投信連買 4 天・成交量是前 5 日平均的 2.1 倍・月營收年增 35%')
  assert.equal(whyLine([R('每日真實波幅約 3.2%', -1), R('排序模型分數在全市場前 7%', 0)]), null)
  assert.equal(whyLine(null), null)
  assert.equal(whyLine(undefined), null)
})

test('T-360：個股頁的標記——正向＋、負向－、篩選門檻・', () => {
  assert.deepEqual(mark(1), { sym: '＋', label: '正向因子', tone: 'plus' })
  assert.deepEqual(mark(-1), { sym: '－', label: '負向因子', tone: 'minus' })
  assert.deepEqual(mark(0), { sym: '・', label: '篩選門檻', tone: 'base' })
})

test('T-360：選股依據掛在選中它的那張模型卡——大盤精選＝漲幅排名、潛伏飆股／高分爆量＝飆漲機率；沒有理由維持三卡並列', () => {
  const rs = [R('近 60 日漲幅 42%，全市場前 3%', 1), R('排序模型分數在全市場前 7%', 0)]
  assert.equal(primaryModel('CORE', rs), 'rank')
  assert.equal(primaryModel('QUIET', rs), 'quiet')
  assert.equal(primaryModel('QUIETV', rs), 'quiet')
  assert.equal(primaryModel('QUIET', []), null)
  assert.equal(primaryModel('QUIET', null), null)
  assert.equal(primaryModel(null, rs), null)
  assert.equal(primaryModel('OTHER', rs), null)
})

test('T-360：掛在漲幅排名卡時不重列「排序模型分數」那條門檻（卡片上已有名次）；飆漲機率卡全列', () => {
  const rs = [R('近 60 日漲幅 42%，全市場前 3%', 1), R('排序模型分數在全市場前 7%', 0), R('每日真實波幅約 3.2%', -1)]
  assert.deepEqual(cardReasons(rs, 'rank'), [rs[0], rs[2]])
  assert.deepEqual(cardReasons(rs, 'quiet'), rs)
})

test('T-360：排序說明照實寫——大盤精選是規則權重，潛伏飆股／高分爆量是模型貢獻度', () => {
  assert.equal(factorsNote('rank'), '依規則權重與全市場排名排序')
  assert.equal(factorsNote('quiet'), '依模型貢獻度排序')
})

function fakeDb(results: (() => Promise<unknown[]>)[], calls: string[]) {
  return ((strings: TemplateStringsArray, ...values: unknown[]) => {
    calls.push(strings.join('?') + JSON.stringify(values))
    const next = results.shift()
    return next ? next() : Promise.resolve([])
  }) as unknown as Parameters<typeof readReasons>[0]
}

test('T-360：/api 讀當天的理由、以「線|代號」對；表還沒建當成沒有；壞掉的列略過', async () => {
  const calls: string[] = []
  const missing = () => Promise.reject(Object.assign(new Error('relation "serving.pick_reasons" does not exist'), { code: '42P01' }))
  assert.equal((await readReasons(fakeDb([missing], calls), '2026-10-14')).size, 0)
  const got = await readReasons(
    fakeDb(
      [
        () =>
          Promise.resolve([
            { line: 'QUIET', stock_id: '1101', reasons: JSON.stringify([R('投信連買 4 天', 1, 'chip.trust_streak'), { t: 'x' }]) },
            { line: 'CORE', stock_id: '1101', reasons: '[]' },
            { line: 'CORE', stock_id: '2454', reasons: null },
          ]),
      ],
      calls,
    ),
    '2026-10-14',
  )
  assert.deepEqual([...got.keys()], ['QUIET|1101']) // 空的、null 的不放
  assert.deepEqual(got.get('QUIET|1101'), [R('投信連買 4 天', 1, 'chip.trust_streak')])
  assert.ok(calls[1].includes('pick_reasons') && calls[1].includes('"2026-10-14"'))
  await assert.rejects(readReasons(fakeDb([() => Promise.reject(Object.assign(new Error('x'), { code: '28P01' }))], calls), '2026-10-14'))
})
