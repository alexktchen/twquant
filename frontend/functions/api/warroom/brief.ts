// GET /api/warroom/brief?date=YYYY-MM-DD&edition=morning|evening：過去某天的「今日一句話」與對照的實際收盤
// （盤前對照當天收盤；盤後快訊講的是下一個交易日，對照下一個交易日收盤）。edition 預設 morning（使用者 2026-10-17：「做一個可以選日期看歷史的頁面」）。
// 讀存好的晨報，不重叫模型；同一種權限看到都一樣，同一執行個體 60 秒暫存。日期清單隨首頁 /api/warroom 一起回。
// 沒有名單權限的人：舊晨報裡的名單痕跡拿掉（T-435／D-212，_lib/brief-scrub.ts），兩種內容分開暫存。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { scrubBrief } from '../../_lib/brief-scrub'
import { allowed, gatedKey } from '../../_lib/gate'
import { DATE_RE } from '../../_lib/picks'
import { selectActual, selectActualAfter, selectEdition } from '../../_lib/brief-db'

const handle = async (request: Request, env: Env, full: boolean): Promise<Response> => {
  const q = new URL(request.url).searchParams
  const date = q.get('date')
  if (date === null || !DATE_RE.test(date)) return fail(400, `date 要是 YYYY-MM-DD：${date}`)
  const ed = q.get('edition') ?? 'morning'
  if (ed !== 'morning' && ed !== 'evening') return fail(400, `edition 要是 morning 或 evening：${ed}`)
  return guarded(env, async (db) => {
    const stored = await selectEdition(db, ed, date)
    const brief = full ? stored : scrubBrief(stored)
    const actual = ed === 'evening' ? await selectActualAfter(db, date) : await selectActual(db, date)
    return json({ date, edition: ed, brief, actual })
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const full = await allowed(ctx.request, 'picks')
  return cached(gatedKey(cacheKey(ctx.request), full), async () => handle(ctx.request, ctx.env, full))
}
