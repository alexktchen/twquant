// 離線提及比對（T-412）：本機歷史語料用跟線上 ingest 同一套 src/mention.ts 規則。
// 用法：node --experimental-strip-types scripts/mentions_offline.ts <entities.json> <config/news.json>
//   stdin 每行 {"id": "...", "texts": ["標題", "摘要", "內文"]} → stdout 每行 {"id": "...", "hits": [{entity, alias, evidence}]}
// entities.json＝雲端 news.entities（valid_to is null）的 [{id, label, aliases}]；由 `twquant news mentions-backfill` 匯出。
import { readFileSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { buildIndex, detectMentions, validateMentionConfig } from '../src/mention.ts'
import type { EntityAliases } from '../src/mention.ts'

const [entitiesPath, configPath] = process.argv.slice(2)
if (!entitiesPath || !configPath) {
  console.error('用法：node --experimental-strip-types scripts/mentions_offline.ts <entities.json> <config/news.json>')
  process.exit(2)
}
const cfg = validateMentionConfig(JSON.parse(readFileSync(configPath, 'utf8')).mention)
const index = buildIndex(JSON.parse(readFileSync(entitiesPath, 'utf8')) as EntityAliases[], cfg)

const out: string[] = []
const flush = () => {
  if (out.length > 0) process.stdout.write(out.join('\n') + '\n')
  out.length = 0
}
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity })
rl.on('line', (line) => {
  if (!line.trim()) return
  const it = JSON.parse(line) as { id: string; texts: Array<string | null> }
  out.push(JSON.stringify({ id: it.id, hits: detectMentions(it.texts, index) }))
  if (out.length >= 500) flush()
})
rl.on('close', flush)
