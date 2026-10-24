// POST /functions/v1/ingest（T-400、T-401、T-402、T-410）：pg_cron 每 30 分鐘呼叫（supabase/setup/schedule.sql）——先進料、再翻譯、再比對提到哪些實體、再從新聞學關係（cooccur）、再算向量與同事件合併。
// 先回 202、工作放 EdgeRuntime.waitUntil：pg_net 只等 5 秒，九個來源一起抓要十幾秒。
import config from '../../../config/news.json' with { type: 'json' }
import typeWeights from '../../../config/news_types.json' with { type: 'json' }
import { runIngest, validateConfig } from '../../../src/ingest.ts'
import { learnCooccur } from '../../../src/cooccur.ts'
import { detectPending } from '../../../src/mention.ts'
import { embedPending } from '../../../src/embed.ts'
import { translatePending } from '../../../src/translate.ts'
import { typePending, validateTypeWeights } from '../../../src/types.ts'
import { sentimentPending } from '../../../src/sentiment.ts'
import { authorized, cooccurDeps, db, embedDeps, fetchFeed, json, mentionDeps, pgStore, sentimentDeps, translateDeps, typeDeps } from '../_shared/io.ts'

declare const EdgeRuntime: { waitUntil(p: Promise<unknown>): void }

const cfg = validateConfig(config)

async function run(runId: string): Promise<void> {
  const sql = db()
  try {
    const runs = await runIngest(cfg, { now: () => new Date(), fetch: fetchFeed, store: pgStore(sql), runId })
    const bad = runs.filter((r) => !r.ok)
    console.log(`news ingest ${runId}：新增 ${runs.reduce((a, r) => a + r.new_items, 0)} 則；失敗 ${bad.map((r) => `${r.source}（${r.error}）`).join('、') || '無'}`)
    // 進料先寫好（first_seen_at 是唯一補不回來的東西），再翻譯；翻譯壞了、額度用完都不影響進料（T-401）
    // 翻譯壞了也照樣比對（已翻過的英文、中文新聞不必等）；翻譯的錯誤比對完再丟，函式紀錄照樣看得到
    let trError: unknown = null
    try {
      const tr = await translatePending(cfg.translate, translateDeps(sql, cfg.translate.model))
      console.log(`news translate ${runId}：${JSON.stringify(tr)}`)
    } catch (e) {
      trError = e
    }
    // 第三步（T-402）：新聞提到哪些實體（一度）；英文新聞等翻過才比（見 mentionDeps 的 pending）
    const mn = await detectPending(cfg.mention, mentionDeps(sql, cfg.mention.rule_version))
    console.log(`news mention ${runId}：${JSON.stringify(mn)}`)
    // 第四步（2026-10-20）：從新聞學關係——一起被提到夠多次的兩方長出「新聞常一起提到」的線
    const co = await learnCooccur(cfg.cooccur, cooccurDeps(sql, cfg.mention.rule_version))
    console.log(`news cooccur ${runId}：${JSON.stringify(co)}`)
    // 第五步（T-410）：向量與同事件合併；英文等翻譯完才算（見 embedDeps 的 pending），額度跟翻譯共用，翻譯會留 translate.reserve_for_embed 給這一步
    const em = await embedPending(cfg.embed, cfg.translate.daily_neurons, embedDeps(sql, cfg.embed))
    console.log(`news embed ${runId}：${JSON.stringify(em)}`)
    // 第六步（T-421）：新聞類型——本機訓好的探針對向量算一次，不花額度；舊新聞第一次部署後幾輪補完
    const ty = await typePending(validateTypeWeights(typeWeights), cfg.embed.per_run_limit * 5, typeDeps(sql, typeWeights.version))
    console.log(`news types ${runId}：${JSON.stringify(ty)}`)
    // 第七步（D-205 走 A／D-218）：情緒——雲端 qwen3 判斷這則新聞對它提到的每檔台股是好、壞還是中性；自己的額度上限
    const se = await sentimentPending(cfg.sentiment, sentimentDeps(sql, cfg.sentiment, cfg.mention.rule_version))
    console.log(`news sentiment ${runId}：${JSON.stringify(se)}`)
    if (trError !== null) throw trError
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
