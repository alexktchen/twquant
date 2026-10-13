// GET /api/watchlist：登入者的追蹤股，新加的在前；每檔附最新收盤、漲跌與三個模型最新一天的分數。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { accessEmail } from '../../_lib/identity'
import { loadScores } from '../../_lib/scores-db'
import { WATCH_MAX, latestDate, watchItems } from '../../_lib/watchlist'

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) =>
  guarded(env, async (db) => {
    const email = await accessEmail(request)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    const [{ ok }] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.watchlist') IS NOT NULL AS ok`
    // 還沒有人加過追蹤股時表還沒建（第一次加入才建，見 watchlist/[sid].ts）
    const list = ok
      ? await db<{ sid: string; name: string | null; added_at: string }[]>`
          SELECT w.stock_id AS sid, m.name, w.added_at::text AS added_at
          FROM web.watchlist w LEFT JOIN serving.stock_meta m ON m.stock_id = w.stock_id
          WHERE w.email = ${email} ORDER BY w.added_at DESC, w.stock_id`
      : []
    const ids = list.map((w) => w.sid)
    const px = ids.length
      ? await db<{ sid: string; date: string; close: number | null }[]>`
          SELECT s.sid, p.date::text AS date, p.close
          FROM unnest(string_to_array(${ids.join(',')}, ',')) AS s(sid)
          CROSS JOIN LATERAL (
            SELECT date, close FROM serving.prices_daily q WHERE q.stock_id = s.sid ORDER BY date DESC LIMIT 2
          ) p`
      : []
    const scores = await loadScores(db, ids)
    return json({ max: WATCH_MAX, date: latestDate(scores.days), items: watchItems(list, px, scores) })
  })
