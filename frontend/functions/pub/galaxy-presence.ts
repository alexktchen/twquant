// POST /pub/galaxy-presence：星雲圖心跳，回「同時幾個人在看」（T-445／D-216）。公開、不用登入、不記身分（只有分頁自己產生的亂數 id）。
// { id } ＝我還在看；{ id, leave: true } ＝我走了。回 { n }（含自己）。
import { fail, guarded, json, type Env } from '../_lib/db'
import { PRESENCE_DDL, PRESENCE_PURGE_S, PRESENCE_WINDOW_S, parsePresence } from '../_lib/presence'
import { sameOrigin } from '../_lib/watchlist'

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的心跳')
  const p = parsePresence(await request.text())
  if (p === null) return fail(400, '心跳的格式不對')
  return guarded(
    env,
    async (db) => {
      const [{ ok }] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.galaxy_presence') IS NOT NULL AS ok`
      if (!ok) for (const stmt of PRESENCE_DDL) await db.unsafe(stmt)
      if (p.leave) await db`DELETE FROM web.galaxy_presence WHERE id = ${p.id}`
      else await db`INSERT INTO web.galaxy_presence (id, seen_at) VALUES (${p.id}, now()) ON CONFLICT (id) DO UPDATE SET seen_at = now()`
      await db`DELETE FROM web.galaxy_presence WHERE seen_at < now() - make_interval(secs => ${PRESENCE_PURGE_S})`
      const [{ n }] = await db<{ n: number }[]>`
        SELECT count(*)::int AS n FROM web.galaxy_presence WHERE seen_at > now() - make_interval(secs => ${PRESENCE_WINDOW_S})`
      return json({ n })
    },
    '在看人數暫時讀不到',
  )
}
