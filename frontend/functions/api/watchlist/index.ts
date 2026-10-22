// GET /api/watchlist：登入者的追蹤股，照使用者排的順序；每檔附最新收盤、漲跌與三個模型最新一天的分數，
// 與今天起 10 天內的除權息、法說會、月營收待公布（events；T-431／D-209，從首頁行事曆搬來，跟名單列同一套規則 _lib/risks.ts）。
// PUT /api/watchlist：存順序，內容 { order: [代號…] }（由上到下）。不在清單上的代號略過；
// 清單上卻沒列到的（例：另一台裝置剛加入的）保留原位置——新加入的本來就排最上面。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { accessEmail } from '../../_lib/identity'
import { CALENDAR_KINDS, readRisks } from '../../_lib/risks'
import { loadScores } from '../../_lib/scores-db'
import { taipeiDay } from '../../_lib/warroom'
import { ORDER_BODY_MAX, WATCH_MAX, latestDate, parseOrder, sameOrigin, watchItems } from '../../_lib/watchlist'
import { ensureWatchlist } from '../../_lib/watchlist-db'

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) =>
  guarded(env, async (db) => {
    const email = await accessEmail(request)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureWatchlist(db)
    const list = await db<{ sid: string; name: string | null; added_at: string }[]>`
      SELECT w.stock_id AS sid, m.name, w.added_at::text AS added_at
      FROM web.watchlist w LEFT JOIN serving.stock_meta m ON m.stock_id = w.stock_id
      WHERE w.email = ${email} ORDER BY w.position, w.added_at DESC, w.stock_id`
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
    const events = await readRisks(db, taipeiDay(Date.now()), ids, CALENDAR_KINDS)
    const items = watchItems(list, px, scores).map((w) => ({ ...w, events: events.get(w.sid) ?? null }))
    return json({ max: WATCH_MAX, date: latestDate(scores.days), items })
  })

export const onRequestPut: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const text = await request.text()
  if (text.length > ORDER_BODY_MAX) return fail(413, '順序內容太長')
  let body: unknown = null
  try {
    body = JSON.parse(text)
  } catch {
    return fail(400, '順序內容不是 JSON')
  }
  const order = parseOrder(body)
  if (order === null) return fail(400, `順序要是 1～${WATCH_MAX} 個不重複的股票代號`)
  return guarded(env, async (db) => {
    const email = await accessEmail(request)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureWatchlist(db)
    await db`
      UPDATE web.watchlist w SET position = o.k::int
      FROM unnest(string_to_array(${order.join(',')}, ',')) WITH ORDINALITY AS o(sid, k)
      WHERE w.email = ${email} AND w.stock_id = o.sid`
    return json({ ok: true })
  })
}
