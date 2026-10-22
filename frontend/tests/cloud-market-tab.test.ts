// T-430／D-208（使用者 2026-10-22：「行事曆跟族群資金移到其他分頁」→「一個盤勢的分頁，法說那些整合到追蹤股跟推薦股」）：
// 族群資金熱力圖從首頁搬到「盤勢」分頁；首頁行事曆拿掉，法說會、除權息、營收待公布改標在追蹤股（新）與名單股（T-387 已有）上。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { CALENDAR_KINDS, readRisks } from '../functions/_lib/risks.ts'
import { tabAllowed } from '../src/cloud/access.ts'
import { eventBadges } from '../src/cloud/badges.ts'
import { HOME_CARDS } from '../src/cloud/layout.ts'
import { tabFromUrl } from '../src/cloud/tabs.ts'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')

test('T-430 AC1 盤勢分頁：每個人都看得到；?tab=group（族群資金通知、LINE 族群圖卡）直接開它；熱力圖在這裡、名單股照樣標', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=group'), 'group')
  assert.ok(tabAllowed('group', []))
  const app = read('src/cloud/CloudApp.tsx')
  assert.match(app, /<button className=\{tab === 'group' \? 'on' : ''\} onClick=\{\(\) => setTab\('group'\)\}>\s*盤勢\s*<\/button>/)
  assert.match(app, /<GroupsPage\s+refresh=\{refresh\}\s+listed=\{listedGroups\}\s+withMood=\{false\}/)
  // 導覽順序：首頁、新聞、盤勢在前
  assert.ok(app.indexOf(">\n            新聞\n") < app.indexOf(">\n            盤勢\n"))
})

test('T-430 AC2 首頁沒有行事曆與族群熱力圖；首頁 API 不再查行事曆', () => {
  const ids = HOME_CARDS.map((c) => c.id as string)
  assert.ok(!ids.includes('cal') && !ids.includes('heat'))
  const war = read('src/cloud/WarRoom.tsx')
  assert.doesNotMatch(war, /CalendarCard|HeatCard|GroupsPage/)
  const api = read('functions/api/warroom/index.ts')
  assert.doesNotMatch(api, /dividend_calendar|conference_calendar|calendar:/)
})

test('T-430 AC3 追蹤股的行事曆：只查除權息、法說會、營收三張表，只回這三種（處置、注意不算行事曆）', async () => {
  const asked: string[] = []
  const rows: Record<string, unknown[]> = {
    disposition_stocks: [{ stock_id: '2330', effective_start: '2026-11-01', effective_end: '2026-11-20', reason: 'x', match_minutes: 5 }],
    attention_stocks: [{ stock_id: '2330', clauses: '1', info: '注意' }],
    dividend_calendar: [{ stock_id: '2330', date: '2026-11-09', kind: 'cash', cash_dividend: 5, stock_ratio: 0 }],
    conference_calendar: [{ stock_id: '2317', date: '2026-11-06', conf_time: '14:00', location: '台北' }],
    monthly_revenue: [{ stock_id: '2330' }],
  }
  const db = ((strings: TemplateStringsArray) => {
    const q = strings.join('?')
    const table = Object.keys(rows).find((t) => q.includes(`serving.${t}`))
    asked.push(table ?? q)
    return Promise.resolve(table ? rows[table] : [])
  }) as never
  const got = await readRisks(db, '2026-11-06', ['2330', '2317'], CALENDAR_KINDS)
  assert.deepEqual(asked.sort(), ['conference_calendar', 'dividend_calendar', 'monthly_revenue'])
  assert.deepEqual(
    got.get('2330')?.map((r) => [r.kind, r.text]),
    [['dividend', '11/9 除息']], // 處置、注意被濾掉；10 月營收已公布
  )
  assert.deepEqual(
    got.get('2317')?.map((r) => [r.kind, r.text]),
    [['conference', '11/6 法說會'], ['revenue', '10 月營收待公布']], // 法說會含當天；11/10 截止前 4 天還沒公布
  )
  // 名單股照舊看全部種類（不傳 kinds）
  asked.length = 0
  const all = await readRisks(db, '2026-11-06', ['2330'])
  assert.equal(asked.length, 5)
  assert.deepEqual(all.get('2330')?.map((r) => r.kind), ['attention', 'dividend', 'disposition'])
})

test('T-430 AC3 追蹤清單 API 每檔附 events（今天起，台北日期）；追蹤列畫跟名單列同一種小標', () => {
  const api = read('functions/api/watchlist/index.ts')
  assert.match(api, /readRisks\(db, taipeiDay\(Date\.now\(\)\), ids, CALENDAR_KINDS\)/)
  assert.match(api, /events: events\.get\(w\.sid\) \?\? null/)
  const b = eventBadges([
    { kind: 'conference', date: '2026-11-06', text: '11/6 法說會', detail: '14:00 台北' },
    { kind: 'dividend', date: '2026-11-09', text: '11/9 除息', detail: '現金 5 元' },
  ])
  assert.deepEqual(b.map((x) => [x.icon, x.short, x.label, x.tone]), [['mic', '11/6', '法說會', 'event'], ['coins', '11/9', '除息', 'event']])
  assert.match(b[0].title, /14:00 台北/)
  assert.deepEqual(eventBadges(null), [])
  assert.deepEqual(eventBadges(undefined), [])
  const app = read('src/cloud/CloudApp.tsx')
  assert.match(app, /rowBadges\(eventBadges\(w\.events\), WATCH_EVENTS_MAX\)/)
})
