// GET /api/stocks/:sid/scores：三個模型最近幾個打分日的分數與名次，加上最新一天在哪條線的名單上。
import { fail, guarded, json, type Env } from '../../../_lib/db'
import { LINES, SID_RE } from '../../../_lib/picks'
import { modelScores } from '../../../_lib/scores'
import { loadScores } from '../../../_lib/scores-db'

export const onRequestGet: PagesFunction<Env, 'sid'> = async ({ params, env }) => {
  const sid = String(params.sid)
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  return guarded(env, async (db) => {
    const meta = await db<{ name: string | null; industry: string | null }[]>`
      SELECT name, industry FROM serving.stock_meta WHERE stock_id = ${sid}`
    if (meta.length === 0) return fail(404, `查無股票 ${sid}`)
    const { rows, days } = await loadScores(db, [sid])
    const lists = await db<{ date: string; line: string; rank: number }[]>`
      SELECT date::text AS date, line, rank FROM serving.daily_picks
      WHERE stock_id = ${sid} AND date = (SELECT max(date) FROM serving.daily_picks) ORDER BY line`
    return json({
      sid,
      name: meta[0].name,
      industry: meta[0].industry,
      models: modelScores(sid, rows, days),
      lists: lists.map((l) => ({ ...l, name: LINES.find((x) => x.code === l.line)?.name ?? l.line })),
    })
  })
}
