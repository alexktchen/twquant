import assert from 'node:assert/strict'
import test from 'node:test'
import { groupOf, groupsFor, readFlows, readGroups, readRanking, type Flow, type GroupRow } from '../functions/_lib/groups.ts'
import { flowText, groupLine, groupTitle, rankRows, ratioText, sameGroups, segName, tagText, yi, type Group, type RankRow } from '../src/cloud/group.ts'

const dayi: GroupRow = { stock_id: '2478', grp: '被動元件', chain: '被動元件', segments: '["電阻器"]', also: '["通信網路","電腦及週邊設備","平面顯示器"]' }
const flow: Flow = { date: '2026-10-13', ratio: 2.1, share: 0.032, share_avg: 0.015, n: 12, up: 9, down: 2, ret: 0.023, hot: true }

test('D-177：族群表有就用表上的（大毅 → 被動元件、帶當天族群資金），沒有就用官方產業別，兩個都沒有不顯示', () => {
  assert.deepEqual(groupOf(dayi, '電子零組件業', new Map([['被動元件', flow]])), {
    name: '被動元件',
    chain: '被動元件',
    segs: ['電阻器'],
    also: ['通信網路', '電腦及週邊設備', '平面顯示器'],
    official: false,
    flow,
  })
  assert.deepEqual(groupOf(undefined, '玻璃陶瓷', new Map()), { name: '玻璃陶瓷', chain: null, segs: [], also: [], official: true, flow: null })
  // 族群表上本來就是官方產業別的（chain 是 null）
  assert.equal(groupOf({ ...dayi, grp: '玻璃陶瓷', chain: null, segments: '[]', also: '[]' }, '玻璃陶瓷', new Map())?.official, true)
  assert.equal(groupOf(undefined, null, new Map()), null)
  assert.equal(groupOf(undefined, '  ', new Map()), null)
  assert.deepEqual(groupOf({ ...dayi, segments: null, also: null }, null, new Map())?.segs, [])
})

// 假的 postgres.js：tagged template 呼叫，記下查詢、照順序回結果
function fakeDb(results: (() => Promise<unknown[]>)[], calls: string[]) {
  return ((strings: TemplateStringsArray, ...values: unknown[]) => {
    calls.push(strings.join('?') + JSON.stringify(values))
    const next = results.shift()
    return next ? next() : Promise.resolve([])
  }) as unknown as Parameters<typeof readGroups>[0]
}
const missing = () => Promise.reject(Object.assign(new Error('relation "serving.stock_group" does not exist'), { code: '42P01' }))

test('D-177：族群表、族群資金表還沒建（第一次排程跑完之前）當成都沒有，名單照樣出來；其他錯誤照丟', async () => {
  const calls: string[] = []
  assert.equal((await readGroups(fakeDb([missing], calls), ['2478'])).size, 0)
  assert.equal((await readFlows(fakeDb([missing], calls), '2026-10-13', ['被動元件'])).size, 0)
  assert.equal(await readRanking(fakeDb([missing], calls), null), null)
  const got = await readGroups(fakeDb([() => Promise.resolve([dayi])], calls), ['2478', '3090'])
  assert.equal(got.get('2478')?.grp, '被動元件')
  assert.ok(calls[3].includes('array_to_json(segments)') && calls[3].includes('2478,3090'))
  assert.equal((await readGroups(fakeDb([], calls), [])).size, 0)
  assert.equal(calls.length, 4) // 沒有股票就不查
  await assert.rejects(readGroups(fakeDb([() => Promise.reject(Object.assign(new Error('x'), { code: '28P01' }))], calls), ['2478']))
})

test('D-177 修訂：名單上每一檔的族群連同那天的族群資金；表上沒有的用官方產業別的資金', async () => {
  const calls: string[] = []
  const db = fakeDb(
    [() => Promise.resolve([dayi]), () => Promise.resolve([{ grp: '被動元件', ...flow }, { grp: '玻璃陶瓷', ...flow, hot: false }])],
    calls,
  )
  const got = await groupsFor(db, '2026-10-13', [
    { stock_id: '2478', industry: '電子零組件業' },
    { stock_id: '1802', industry: '玻璃陶瓷' },
    { stock_id: '9999', industry: null },
  ])
  assert.equal(got.get('2478')?.flow?.ratio, 2.1)
  assert.equal(got.get('1802')?.name, '玻璃陶瓷')
  assert.equal(got.get('1802')?.flow?.hot, false)
  assert.equal(got.get('9999'), null)
  // 族群名用不會出現的分隔字元串起來（族群名可能有逗號）；date 給 null＝最近一天
  assert.ok(calls[1].includes('被動元件\\u001f玻璃陶瓷') && calls[1].includes('"2026-10-13"'))
  await readFlows(fakeDb([], calls), null, ['被動元件'])
  assert.ok(calls[2].includes('coalesce(') && calls[2].includes('[null,'))
})

test('D-177：段名去掉括號裡的舉例', () => {
  assert.equal(segName('網路設備(如數據機、網路卡、閘道器、路由器、網路電話)'), '網路設備')
  assert.equal(segName('棒鋼盤元(捲狀條鋼)'), '棒鋼盤元')
  assert.equal(segName('散熱片、風扇馬達、散熱模組'), '散熱片、風扇馬達、散熱模組')
  assert.equal(segName('(全部都是括號)'), '(全部都是括號)')
})

const g = (over: Partial<Group>): Group => ({ name: '被動元件', chain: '被動元件', segs: ['電阻器'], also: [], official: false, flow: null, ...over })

test('D-177：個股頁的族群行——族群 › 段、拆到段的前面帶鏈名、也在哪些鏈（最多 4 條）', () => {
  assert.deepEqual(groupLine(g({})), { main: '被動元件 › 電阻器', also: null })
  assert.deepEqual(groupLine(g({ name: '散熱', chain: '電腦及週邊設備', segs: ['散熱片、風扇馬達、散熱模組'] })).main, '電腦及週邊設備 › 散熱')
  assert.deepEqual(groupLine(g({ also: ['通信網路', '電腦及週邊設備'] })).also, '也在 通信網路、電腦及週邊設備')
  const delta = g({
    name: '電腦及週邊設備',
    chain: '電腦及週邊設備',
    segs: ['電源供應器', '散熱片、風扇馬達、散熱模組', '筆記型電腦', '桌上型電腦'],
    also: ['半導體', '醫療器材', '電動車輛產業', 'LED照明產業', '太陽能產業'],
  })
  assert.deepEqual(groupLine(delta), {
    main: '電腦及週邊設備 › 電源供應器、散熱片、風扇馬達、散熱模組、筆記型電腦 等 4 段',
    also: '也在 半導體、醫療器材、電動車輛產業、LED照明產業 等 5 條鏈',
  })
  assert.equal(groupLine(g({ segs: ['生產設備(上游)', '生產設備(下游)'] })).main, '被動元件 › 生產設備')
  assert.deepEqual(groupLine(g({ name: '玻璃陶瓷', chain: null, segs: [], official: true })), { main: '玻璃陶瓷', also: null })
})

test('D-177 修訂：資金流入的族群小框寫倍數；說明寫成交比重與漲跌家數', () => {
  assert.equal(tagText(g({})), '被動元件')
  assert.equal(tagText(g({ flow })), '被動元件 2.1×')
  assert.equal(tagText(g({ flow: { ...flow, hot: false } })), '被動元件')
  assert.equal(flowText(flow), '資金 2.1 倍（成交比重 3.2%，前 20 日平均 1.5%）・12 檔 9 漲 2 跌・平均 +2.3%')
  assert.equal(flowText({ ...flow, ratio: null, share_avg: null, ret: null }), '成交比重 3.2%（歷史不夠，還算不出倍數）・12 檔 9 漲 2 跌')
  assert.equal(ratioText(1.83), '1.8 倍')
  assert.equal(ratioText(null), null)
  assert.equal(groupTitle(g({ segs: ['電阻器', '電容器'] })), '被動元件：電阻器、電容器')
  assert.equal(groupTitle(g({ flow })), `被動元件：電阻器\n資金流入：${flowText(flow)}`)
  assert.ok(groupTitle(g({ name: '玻璃陶瓷', segs: [], official: true })).startsWith('官方產業別：玻璃陶瓷'))
  assert.equal(yi(12.34e8), '12.3 億')
  assert.equal(yi(45_000_000), '4,500 萬')
})

test('D-177 修訂：名單上方的「同族群」——兩檔以上才列，檔數多的在前，一樣多看資金倍數', () => {
  const p = (name: string, ratio: number | null = null) => ({ group: g({ name, flow: ratio === null ? null : { ...flow, ratio } }) })
  const picks = [p('IC設計', 1.0), p('被動元件', 2.1), p('散熱'), p('被動元件', 2.1), p('IC設計', 1.0), p('被動元件', 2.1), { group: null }, p('晶圓製造', 3), p('晶圓製造', 3)]
  assert.deepEqual(
    sameGroups(picks).map((x) => [x.name, x.count]),
    [
      ['被動元件', 3],
      ['晶圓製造', 2],
      ['IC設計', 2],
    ],
  )
  assert.deepEqual(sameGroups([p('散熱'), {}]), [])
})

test('D-177 修訂：族群排行可以只看資金流入、太小的族群不列', () => {
  const r = (grp: string, n: number, hot: boolean): RankRow => ({ grp, n, hot, ratio: 1, share: 0.01, share_avg: 0.01, up: 0, down: 0, ret: 0, members: [] })
  const rows = [r('A', 12, true), r('B', 2, true), r('C', 5, false)]
  assert.deepEqual(
    rankRows(rows, false, 3).map((x) => x.grp),
    ['A', 'C'],
  )
  assert.deepEqual(
    rankRows(rows, true, 3).map((x) => x.grp),
    ['A'],
  )
})
