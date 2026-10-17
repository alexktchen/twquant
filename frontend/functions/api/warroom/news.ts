// GET /api/warroom/news?sids=2330,2317&since=YYYY-MM-DD：指定股票（網站傳追蹤股）自 since 起的重大訊息（T-379）。
// 只回公開資料、不讀身分：代號由網站帶上來，暫存鍵含代號，不同人的清單不會混在一起。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { DATE_RE, SID_RE } from '../../_lib/picks'
import { readNewColumn, readRows } from '../../_lib/scorecard'
import { WATCH_NEWS_LIMIT, attachContent, newsTags, parseSids, type NewsRow } from '../../_lib/warroom'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url)
  const sids = parseSids(url.searchParams.get('sids'), SID_RE)
  const since = url.searchParams.get('since')
  if (since === null || !DATE_RE.test(since)) return fail(400, `since 要是 YYYY-MM-DD：${since}`)
  if (sids.length === 0) return json({ since, items: [] })
  return guarded(env, async (db) => {
    const rows = await readRows(
      () => db<NewsRow[]>`
        SELECT stock_id, name, subject, to_char(available_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS at,
               extract(epoch FROM available_at)::bigint::int AS ts
        FROM serving.mops_announcements
        WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ','))
          AND available_at >= (${since}::date)::timestamp AT TIME ZONE 'Asia/Taipei'
        ORDER BY available_at DESC LIMIT ${WATCH_NEWS_LIMIT}`,
    )
    // 內文另外查：欄位還沒建（排程第一次跑到之前）就當沒有
    const bodies = rows.length
      ? await readNewColumn(
          () => db<{ stock_id: string; ts: number; subject: string; content: string | null }[]>`
            SELECT stock_id, extract(epoch FROM available_at)::bigint::int AS ts, subject, content
            FROM serving.mops_announcements
            WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ','))
              AND available_at >= (${since}::date)::timestamp AT TIME ZONE 'Asia/Taipei'`,
        )
      : []
    const items = rows.map((r) => ({ ...r, tags: newsTags(r.subject), listed: false }))
    return json({ since, items: attachContent(items, bodies) })
  })
}

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
