// POST /functions/v1/ingest（T-400、T-401）：pg_cron 每 30 分鐘呼叫（supabase/setup/schedule.sql）——先進料、再翻譯。
// 先回 202、工作放 EdgeRuntime.waitUntil：pg_net 只等 5 秒，九個來源一起抓要十幾秒。
import config from '../../../config/news.json' with { type: 'json' }
import { runIngest, validateConfig } from '../../../src/ingest.ts'
import { translatePending } from '../../../src/translate.ts'
import { authorized, db, fetchFeed, json, pgStore, translateDeps } from '../_shared/io.ts'

declare const EdgeRuntime: { waitUntil(p: Promise<unknown>): void }

const cfg = validateConfig(config)

async function run(runId: string): Promise<void> {
  const sql = db()
  try {
    const runs = await runIngest(cfg, { now: () => new Date(), fetch: fetchFeed, store: pgStore(sql), runId })
    const bad = runs.filter((r) => !r.ok)
    console.log(`news ingest ${runId}：新增 ${runs.reduce((a, r) => a + r.new_items, 0)} 則；失敗 ${bad.map((r) => `${r.source}（${r.error}）`).join('、') || '無'}`)
    // 進料先寫好（first_seen_at 是唯一補不回來的東西），再翻譯；翻譯壞了、額度用完都不影響進料（T-401）
    const tr = await translatePending(cfg.translate, translateDeps(sql, cfg.translate.model))
    console.log(`news translate ${runId}：${JSON.stringify(tr)}`)
  } finally {
    await sql.end({ timeout: 5 })
  }
}

Deno.serve((req) => {
  if (req.method !== 'POST') return json({ detail: 'method not allowed' }, 405)
  if (!authorized(req, 'NEWS_INGEST_KEY')) return json({ detail: 'forbidden' }, 403)
  const runId = crypto.randomUUID()
  // 錯誤寫在 ingest_runs（每個來源自己記）；這裡只接資料庫連不上之類的整體錯誤，讓 Supabase 的函式紀錄看得到
  EdgeRuntime.waitUntil(run(runId).catch((e) => console.error(`news ingest ${runId} 整體失敗：`, e)))
  return json({ run_id: runId }, 202)
})
