import assert from 'node:assert/strict'
import test from 'node:test'
import { groupOf, readGroups, type GroupRow } from '../functions/_lib/groups.ts'
import { groupLine, groupTitle, segName, type Group } from '../src/cloud/group.ts'

const dayi: GroupRow = { stock_id: '2478', chain: '被動元件', segments: '["電阻器"]', also: '["通信網路","電腦及週邊設備","平面顯示器"]' }

test('D-177：有產業鏈就用鏈（大毅 → 被動元件），沒有就用官方產業別，兩個都沒有不顯示', () => {
  assert.deepEqual(groupOf(dayi, '電子零組件業'), {
    name: '被動元件',
    segs: ['電阻器'],
    also: ['通信網路', '電腦及週邊設備', '平面顯示器'],
    official: false,
  })
  assert.deepEqual(groupOf(undefined, '玻璃陶瓷'), { name: '玻璃陶瓷', segs: [], also: [], official: true })
  assert.equal(groupOf(undefined, null), null)
  assert.equal(groupOf(undefined, '  '), null)
  // 陣列欄位是空的（資料庫 NULL）也不壞
  assert.deepEqual(groupOf({ ...dayi, segments: null, also: null }, null)?.segs, [])
})

// 假的 postgres.js：tagged template 呼叫，記下查詢、回指定結果
function fakeDb(result: () => Promise<GroupRow[]>, calls: string[]) {
  return ((strings: TemplateStringsArray, ...values: unknown[]) => {
    calls.push(strings.join('?') + JSON.stringify(values))
    return result()
  }) as unknown as Parameters<typeof readGroups>[0]
}

test('D-177：族群表還沒建（第一次排程跑完之前）當成都沒有，名單照樣出來；其他錯誤照丟', async () => {
  const calls: string[] = []
  const missing = Object.assign(new Error('relation "serving.stock_group" does not exist'), { code: '42P01' })
  assert.equal((await readGroups(fakeDb(() => Promise.reject(missing), calls), ['2478'])).size, 0)
  const got = await readGroups(fakeDb(() => Promise.resolve([dayi]), calls), ['2478', '3090'])
  assert.equal(got.get('2478')?.chain, '被動元件')
  assert.ok(calls[1].includes('array_to_json(segments)') && calls[1].includes('2478,3090'))
  assert.equal((await readGroups(fakeDb(() => Promise.resolve([]), calls), [])).size, 0)
  assert.equal(calls.length, 2) // 沒有股票就不查
  await assert.rejects(readGroups(fakeDb(() => Promise.reject(Object.assign(new Error('x'), { code: '28P01' })), calls), ['2478']))
})

test('D-177：段名去掉括號裡的舉例', () => {
  assert.equal(segName('網路設備(如數據機、網路卡、閘道器、路由器、網路電話)'), '網路設備')
  assert.equal(segName('棒鋼盤元(捲狀條鋼)'), '棒鋼盤元')
  assert.equal(segName('散熱片、風扇馬達、散熱模組'), '散熱片、風扇馬達、散熱模組')
  assert.equal(segName('(全部都是括號)'), '(全部都是括號)')
})

const g = (over: Partial<Group>): Group => ({ name: '被動元件', segs: ['電阻器'], also: [], official: false, ...over })

test('D-177：個股頁那一行——族群 › 段（最多 3 段）、也在哪些鏈（最多 4 條）', () => {
  assert.deepEqual(groupLine(g({})), { main: '被動元件 › 電阻器', also: null })
  assert.deepEqual(groupLine(g({ also: ['通信網路', '電腦及週邊設備'] })).also, '也在 通信網路、電腦及週邊設備')
  const delta = g({
    name: '電腦及週邊設備',
    segs: ['電源供應器', '散熱片、風扇馬達、散熱模組', '筆記型電腦', '桌上型電腦'],
    also: ['半導體', '醫療器材', '電動車輛產業', 'LED照明產業', '太陽能產業'],
  })
  assert.deepEqual(groupLine(delta), {
    main: '電腦及週邊設備 › 電源供應器、散熱片、風扇馬達、散熱模組、筆記型電腦 等 4 段',
    also: '也在 半導體、醫療器材、電動車輛產業、LED照明產業 等 5 條鏈',
  })
  // 去掉括號後同名的段只列一次
  assert.equal(groupLine(g({ segs: ['生產設備(上游)', '生產設備(下游)'] })).main, '被動元件 › 生產設備')
  assert.deepEqual(groupLine(g({ name: '玻璃陶瓷', segs: [], official: true })), { main: '玻璃陶瓷', also: null })
})

test('D-177：名單小字滑過去的說明', () => {
  assert.equal(groupTitle(g({ segs: ['電阻器', '電容器'] })), '被動元件：電阻器、電容器')
  assert.equal(groupTitle(g({ segs: [] })), '被動元件')
  assert.ok(groupTitle(g({ name: '玻璃陶瓷', segs: [], official: true })).startsWith('官方產業別：玻璃陶瓷'))
})
