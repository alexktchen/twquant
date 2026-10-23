// /pub/galaxy-search：星雲圖「大家都在搜」（T-446／D-217）。公開、不用登入、不記身分與打的字。
// 日期一律用台北日期（now() AT TIME ZONE 'Asia/Taipei'）。
// POST { id, hubs }＝這個分頁搜了這幾個亮點；GET＝近 7 天被最多分頁搜過的亮點（同一執行個體暫存 60 秒、瀏覽器也留 60 秒）。
import { cached, cacheKey } from '../_lib/cache'
import { fail, guarded, json, type Env } from '../_lib/db'
import { PUBLIC_MAX_AGE_S } from '../_lib/news-proxy'
import { TREND_DAYS, TREND_DDL, TREND_PURGE_DAYS, TREND_TOP, parseSearch } from '../_lib/search-trend'
import { sameOrigin } from '../_lib/watchlist'

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的搜尋')
  const p = parseSearch(await request.text())
  if (p === null) return fail(400, '搜尋紀錄的格式不對')
  return guarded(
    env,
    async (db) => {
      const [{ ok }] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.galaxy_searches') IS NOT NULL AS ok`
      if (!ok) for (const stmt of TREND_DDL) await db.unsafe(stmt)
      for (const hub of p.hubs)
        await db`INSERT INTO web.galaxy_searches (day, viewer, hub) VALUES ((now() AT TIME ZONE 'Asia/Taipei')::date, ${p.id}, ${hub}) ON CONFLICT DO NOTHING`
      await db`DELETE FROM web.galaxy_searches WHERE day < (now() AT TIME ZONE 'Asia/Taipei')::date - ${TREND_PURGE_DAYS}::int`
      return json({ ok: true })
    },
    '搜尋紀錄暫時寫不進去',
  )
}

export const onRequestGet: PagesFunction<Env> = ({ request, env }) =>
  cached(cacheKey(request), () =>
    guarded(
      env,
      async (db) => {
        const [{ ok }] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.galaxy_searches') IS NOT NULL AS ok`
        const items = ok
          ? await db<{ hub: string; n: number }[]>`
              SELECT hub, count(*)::int AS n FROM web.galaxy_searches
              WHERE day > (now() AT TIME ZONE 'Asia/Taipei')::date - ${TREND_DAYS}::int
              GROUP BY hub ORDER BY n DESC, hub LIMIT ${TREND_TOP}`
          : []
        return new Response(JSON.stringify({ days: TREND_DAYS, items: items.map((r) => ({ hub: r.hub, n: r.n })) }), {
          headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': `public, max-age=${PUBLIC_MAX_AGE_S}` },
        })
      },
      '大家都在搜暫時讀不到',
    ),
  )
