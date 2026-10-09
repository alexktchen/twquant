// GET /api/stocks/:sid/ohlc?days=500：個股日 K（舊到新），欄位同 Python 版。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { SID_RE } from '../../../_lib/picks'

export const onRequestGet: PagesFunction<Env, 'sid'> = async ({ request, params, env }) => {
  const sid = String(params.sid)
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  const raw = Number(new URL(request.url).searchParams.get('days') ?? 500)
  const days = Number.isInteger(raw) && raw >= 1 && raw <= 5000 ? raw : 500
  return guarded(env, async (db) => {
    const rows = await db<{ date: string; open: number; high: number; low: number; close: number; volume: number }[]>`
      SELECT date::text AS date, open, high, low, close, volume::float8 AS volume
      FROM serving.prices_daily WHERE stock_id = ${sid} ORDER BY date DESC LIMIT ${days}`
    rows.reverse()
    return json({ sid, as_of: rows.at(-1)?.date ?? null, source: 'pg', rows })
  })
}
