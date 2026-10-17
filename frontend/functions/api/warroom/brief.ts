// GET /api/warroom/brief?date=YYYY-MM-DD：過去某天的「今日一句話」與那天大盤實際收盤（使用者 2026-10-17：「做一個可以選日期看歷史的頁面」）。
// 讀存好的晨報，不重叫模型；每個人看到都一樣，同一執行個體 60 秒暫存。日期清單隨首頁 /api/warroom 一起回。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { DATE_RE } from '../../_lib/picks'
import { selectActual, selectBrief } from '../../_lib/brief-db'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const date = new URL(request.url).searchParams.get('date')
  if (date === null || !DATE_RE.test(date)) return fail(400, `date 要是 YYYY-MM-DD：${date}`)
  return guarded(env, async (db) => {
    const brief = await selectBrief(db, date)
    const actual = await selectActual(db, date)
    return json({ date, brief, actual })
  })
}

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
