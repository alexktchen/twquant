// GET /api/picks/dates?limit=250：有名單的日期，新到舊；附 counts[日期][線別]＝檔數、since[線別]＝第一天。只給 admin（T-429）。
import { guarded, json, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { denied } from '../../_lib/gate'
import { groupCounts } from '../../_lib/picks'

/** 日期清單（只有日期）；公開的 /pub/dates 也用這個，傳 publicDetail 讓錯誤不帶資料庫細節。 */
export function listDates(request: Request, env: Env, publicDetail?: string): Promise<Response> {
  const raw = Number(new URL(request.url).searchParams.get('limit') ?? 250)
  const limit = Number.isInteger(raw) && raw >= 1 && raw <= 2000 ? raw : 250
  return guarded(
    env,
    async (db) => {
      const rows = await db<{ d: string }[]>`
        SELECT DISTINCT date::text AS d FROM serving.daily_picks ORDER BY d DESC LIMIT ${limit}`
      const dates = rows.map((r) => r.d)
      // 公開版只給日期（不含任何名單資訊）；登入版附每天每條線的檔數，給日期選單直接看
      if (publicDetail !== undefined || dates.length === 0) return json({ dates })
      const n = await db<{ d: string; line: string; n: number }[]>`
        SELECT date::text AS d, line, count(*)::int AS n FROM serving.daily_picks
        WHERE date >= ${dates[dates.length - 1]} GROUP BY date, line`
      const first = await db<{ line: string; d: string }[]>`
        SELECT line, min(date)::text AS d FROM serving.daily_picks GROUP BY line`
      return json({ dates, ...groupCounts(n, first) })
    },
    publicDetail,
  )
}

// 60 秒暫存（_lib/cache.ts）；先擋權限再讀暫存
export const onRequestGet: PagesFunction<Env> = async ({ request, env }) =>
  (await denied(request, 'picks')) ?? cached(cacheKey(request), () => listDates(request, env))
