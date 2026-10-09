// GET /api/picks/dates?limit=250：有名單的日期，新到舊。
import { guarded, json, type Env } from '../../_lib/db'

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const raw = Number(new URL(request.url).searchParams.get('limit') ?? 250)
  const limit = Number.isInteger(raw) && raw >= 1 && raw <= 2000 ? raw : 250
  return guarded(env, async (db) => {
    const rows = await db<{ d: string }[]>`
      SELECT DISTINCT date::text AS d FROM serving.daily_picks ORDER BY d DESC LIMIT ${limit}`
    return json({ dates: rows.map((r) => r.d) })
  })
}
