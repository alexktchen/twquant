import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

test('T-412：離線提及比對（stdin JSONL → stdout JSONL）用線上同一套規則，0 命中也回一行', () => {
  const dir = mkdtempSync(join(tmpdir(), 'mentions-offline-'))
  const entities = join(dir, 'entities.json')
  writeFileSync(
    entities,
    JSON.stringify([
      { id: 'tw:2330', label: '台積電', aliases: ['TSMC'] },
      { id: 'tw:2454', label: '聯發科', aliases: ['MediaTek'] },
    ]),
  )
  const script = fileURLToPath(new URL('../scripts/mentions_offline.ts', import.meta.url))
  const config = fileURLToPath(new URL('../config/news.json', import.meta.url))
  const input = ['{"id":"a","texts":["台積電法說會","TSMC said",null]}', '{"id":"b","texts":["跟股票無關的新聞"]}', ''].join('\n')
  const r = spawnSync(process.execPath, ['--experimental-strip-types', script, entities, config], { input, encoding: 'utf8' })
  assert.equal(r.status, 0, r.stderr)
  const rows = r.stdout.trim().split('\n').map((l) => JSON.parse(l) as { id: string; hits: { entity: string }[] })
  assert.deepEqual(
    rows.map((o) => [o.id, o.hits.map((h) => h.entity)]),
    [
      ['a', ['tw:2330']],
      ['b', []],
    ],
  )
})
