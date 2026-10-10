// GET /api/picks/dates?limit=250：有名單的日期，新到舊。
import { guarded, json, type Env } from '../../_lib/db'

/** 日期清單（只有日期）；公開的 /pub/dates 也用這個，傳 publicDetail 讓錯誤不帶資料庫細節。 */
export function listDates(request: Request, env: Env, publicDetail?: string): Promise<Response> {
  const raw = Number(new URL(request.url).searchParams.get('limit') ?? 250)
  const limit = Number.isInteger(raw) && raw >= 1 && raw <= 2000 ? raw : 250
  return guarded(
    env,
    async (db) => {
      const rows = await db<{ d: string }[]>`
        SELECT DISTINCT date::text AS d FROM serving.daily_picks ORDER BY d DESC LIMIT ${limit}`
      return json({ dates: rows.map((r) => r.d) })
    },
    publicDetail,
  )
}

export const onRequestGet: PagesFunction<Env> = ({ request, env }) => listDates(request, env)
