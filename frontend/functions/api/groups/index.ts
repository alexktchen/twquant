// GET /api/groups?date=YYYY-MM-DD（空＝最新）：族群排行——每個族群那天的資金倍數、成交比重、漲跌家數與成交金額最大的幾檔；
// inst＝同一天每個族群的法人象限圖資料（serving.group_inst_daily，D-177 修訂三），還沒算出來是 null。
// 每天收盤後排程算好存在 serving.group_flow_daily（D-177 修訂）；JSON 在資料庫組好，這裡原樣回（免費方案每請求 10ms CPU）。
import { fail, guarded, type Env } from '../../_lib/db'
import { DATE_RE } from '../../_lib/picks'
import { readInst, readRanking } from '../../_lib/groups'
import { cached, cacheKey } from '../../_lib/cache'

const HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' }

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const want = new URL(request.url).searchParams.get('date')
  if (want !== null && !DATE_RE.test(want)) return fail(400, `日期格式錯誤：${want}`)
  return guarded(env, async (db) => {
    const got = await readRanking(db, want)
    if (!got) return fail(404, '族群資金還沒算出來：每個交易日收盤後的排程（約 17:40）跑完就會出現。')
    const inst = await readInst(db, got.date)
    return new Response(`{"date":${JSON.stringify(got.date)},"rows":${got.body},"inst":${inst ?? 'null'}}`, { headers: HEADERS })
  })
}

// 每個人看到都一樣、一天只更新兩次：同一執行個體 60 秒暫存，省下連資料庫的 CPU（_lib/cache.ts）
export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
