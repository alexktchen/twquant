import assert from 'node:assert/strict'
import test from 'node:test'
import { BADGE_LEGEND, ROW_BADGES_MAX, pickBadges, rowBadges } from '../src/cloud/badges.ts'

const base = { new: false, streak: 1, recent: [true, false, null, true, true, true], score: 0.23, line: 'QUIET' }

test('使用者 2026-10-18「多用符號、一目瞭然」：每個提醒一個標記——圖示、極短字、名稱、完整說明、顏色類別；依嚴重度排', () => {
  const b = pickBadges({
    ...base,
    new: true,
    streak: 9,
    weak: { low20: true, down: 4 },
    insider: { n: 2, shares: 1_000_000, latest: '2026-10-16', roles: ['董事本人'] },
    holders: { latest: '2026-09-18', pct: 0.4, change: 0.01, streak: 3, weeks: 5 },
    risks: [
      { kind: 'dividend', date: '2026-10-22', text: '10/22 除息', detail: '現金 1.5 元' },
      { kind: 'disposition', date: '2026-10-23', text: '處置中到 10/23', detail: '連續3個營業日' },
      { kind: 'attention', date: null, text: '注意股', detail: null },
    ],
    down_risk: { p: 0.49, flagged: true },
  })
  assert.deepEqual(
    b.map((x) => [x.tone, x.icon, x.label, x.short]),
    [
      ['risk', 'ban', '處置中', '~10/23'],
      ['warn', 'alert', '注意股', null],
      ['warn', 'trendDown', '模型易跌', '49%'],
      ['warn', 'userMinus', '內部人申讓', '×2'],
      ['event', 'coins', '除息', '10/22'],
      ['chip', 'usersUp', '大戶連增', '3週'],
      ['chip', 'floor', '20日新低', null],
      ['chip', 'arrowDown', '連跌', '4天'],
      ['good', 'sparkles', '新上榜', null],
      ['good', 'flame', '連續上榜', '9天'],
      ['good', 'rocket', '飆漲機率', '23%'],
    ],
  )
  assert.ok(b.every((x) => x.title.length > 0), '每個標記都有完整說明（滑過或長按看）')
})

test('標記：大盤精選不標飆漲機率；沒有任何提醒 → 空；沒被 flagged 的易跌不標', () => {
  assert.deepEqual(pickBadges({ ...base, line: 'CORE', down_risk: { p: 0.1, flagged: false } }), [])
  assert.equal(pickBadges({ ...base, score: null }).length, 0)
  assert.deepEqual(
    pickBadges({ ...base, risks: [{ kind: 'disposition_soon', date: '2026-10-19', text: '10/19 起處置', detail: null }] }).map((x) => [x.label, x.short]),
    [
      ['即將處置', '10/19起'],
      ['飆漲機率', '23%'],
    ],
  )
})

test('名單列最多放 4 個標記，其餘收成 +N；圖示說明涵蓋每一種圖示', () => {
  const many = pickBadges({ ...base, new: true, streak: 9, weak: { low20: true, down: 4 }, down_risk: { p: 0.4, flagged: true } })
  const { shown, more } = rowBadges(many)
  assert.equal(ROW_BADGES_MAX, 4)
  assert.equal(shown.length, 4)
  assert.equal(more, many.length - 4)
  assert.deepEqual(rowBadges(many.slice(0, 2)), { shown: many.slice(0, 2), more: 0 })
  const legendIcons = new Set(BADGE_LEGEND.map((l) => l.icon))
  for (const b of many) assert.ok(legendIcons.has(b.icon), `圖示說明要有 ${b.icon}`)
})

test('相關新聞小標（反向看圖，2026-10-20）：有幾則就顯示幾、藍色日程類；0 則或新聞服務沒回就沒有；圖示說明有這一條', () => {
  const b = pickBadges({ news: { direct: 1, indirect: 4 } }).find((x) => x.key === 'news')
  assert.ok(b)
  assert.equal(b.short, '5')
  assert.equal(b.tone, 'event')
  assert.match(b.title, /直接提到 1 則.*連到 4 則/)
  assert.equal(pickBadges({ news: { direct: 0, indirect: 0 } }).find((x) => x.key === 'news'), undefined)
  assert.equal(pickBadges({ news: null }).find((x) => x.key === 'news'), undefined)
  assert.ok(BADGE_LEGEND.some((x) => x.icon === 'newspaper' && x.label === '相關新聞'))
})
