import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { ITEMS_DEFAULT, ITEMS_MAX, health, parseItemsQuery } from '../src/api.ts'
import { validateConfig } from '../src/ingest.ts'

const cfg = validateConfig(JSON.parse(readFileSync(new URL('../config/news.json', import.meta.url), 'utf8')))
const q = (s: string) => parseItemsQuery(new URLSearchParams(s), cfg)

test('T-400：/v1/items 參數——預設、上限、時間、來源與種類要合法', () => {
  assert.deepEqual(q(''), { since: null, until: null, kind: null, source: null, limit: ITEMS_DEFAULT })
  assert.deepEqual(q('since=2026-10-19T00:00:00%2B08:00&kind=intl&source=cnbc-top&limit=5'), {
    since: '2026-10-18T16:00:00.000Z',
    until: null,
    kind: 'intl',
    source: 'cnbc-top',
    limit: 5,
  })
  assert.throws(() => q(`limit=${ITEMS_MAX + 1}`), /limit/)
  assert.throws(() => q('limit=abc'), /limit/)
  assert.throws(() => q('kind=us'), /kind/)
  assert.throws(() => q('source=nope'), /來源/)
  assert.throws(() => q('since=yesterday'), /since/)
})

test('T-400：/v1/health——最後成功時間、壞掉的來源、超過 stale_hours 算停擺；沒跑過的來源也列出', () => {
  const now = new Date('2026-10-19T06:00:00Z')
  const runs = [
    { source: 'cnbc-top', last_run_at: '2026-10-19T05:30:00Z', last_ok_at: '2026-10-19T05:30:00Z', last_error: null },
    { source: 'ft-markets', last_run_at: '2026-10-19T05:30:00Z', last_ok_at: '2026-10-19T04:00:00Z', last_error: 'HTTP 403' },
  ]
  const h = health(cfg, runs, now)
  assert.equal(h.last_ok_at, '2026-10-19T05:30:00Z')
  assert.equal(h.stale, false)
  assert.equal(h.sources.length, cfg.sources.length)
  const by = Object.fromEntries(h.sources.map((s) => [s.source, s]))
  assert.equal(by['cnbc-top'].failing, false)
  assert.equal(by['ft-markets'].failing, true)
  assert.equal(by['fed-press'].last_run_at, null)
  assert.equal(health(cfg, runs, new Date('2026-10-19T08:00:01Z')).stale, true, `超過 ${cfg.stale_hours} 小時`)
  assert.equal(health(cfg, [], now).stale, true, '從沒成功過')
})
