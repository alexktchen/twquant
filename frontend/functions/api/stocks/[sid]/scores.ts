// GET /api/stocks/:sid/scores：三個模型最近幾個打分日的分數與名次，加上最新一天在哪條線的名單上、族群。
// 「在哪條線的名單上」只給 admin（T-429／D-207）；其他人 lists 是空的。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { allowed, gatedKey } from '../../../_lib/gate'
import { groupsFor } from '../../../_lib/groups'
import { LINES, SID_RE } from '../../../_lib/picks'
import { modelScores } from '../../../_lib/scores'
import { loadScores } from '../../../_lib/scores-db'
import { cached, cacheKey } from '../../../_lib/cache'

const handle = async (sid: string, env: Env, withLists: boolean): Promise<Response> => {
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  return guarded(env, async (db) => {
    const meta = await db<{ name: string | null; industry: string | null }[]>`
      SELECT name, industry FROM serving.stock_meta WHERE stock_id = ${sid}`
    if (meta.length === 0) return fail(404, `查無股票 ${sid}`)
    const { rows, days } = await loadScores(db, [sid])
    const groups = await groupsFor(db, null, [{ stock_id: sid, industry: meta[0].industry }])
    const lists = withLists
      ? await db<{ date: string; line: string; rank: number }[]>`
          SELECT date::text AS date, line, rank FROM serving.daily_picks
          WHERE stock_id = ${sid} AND date = (SELECT max(date) FROM serving.daily_picks) ORDER BY line`
      : []
    return json({
      sid,
      name: meta[0].name,
      industry: meta[0].industry,
      group: groups.get(sid) ?? null,
      models: modelScores(sid, rows, days),
      lists: lists.map((l) => ({ ...l, name: LINES.find((x) => x.code === l.line)?.name ?? l.line })),
    })
  })
}

// 同一種權限看到都一樣、一天只更新兩次：同一執行個體 60 秒暫存，省下連資料庫的 CPU（_lib/cache.ts）；有沒有名單分開存
export const onRequestGet: PagesFunction<Env, 'sid'> = async (ctx) => {
  const full = await allowed(ctx.request, 'picks')
  return cached(gatedKey(cacheKey(ctx.request), full), async () => handle(String(ctx.params.sid), ctx.env, full))
}
