// GET /api/mood/global：市場氛圍的期貨與美股版（D-185）——台指期日盤、夜盤（期交所近月）、加權現貨與期現價差、
// 美股四大指數最近一個交易日（盤中為即時），合成「夜盤大跌・美股大跌・偏空」這種一句。
// 每個人看到都一樣：同一執行個體 60 秒暫存（_lib/cache.ts）；暫存過期後由 GlobalHub（_lib/global-live.ts）重抓，
// 哪個來源沒拿到就給上一份並標 stale；全部都沒有才 503。
import { fail, json } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { globalHub } from '../../_lib/global-live'

const handle = async (): Promise<Response> => {
  try {
    const got = await globalHub.get()
    return json({ global: got.global, stale: got.stale, errors: got.errors })
  } catch (e) {
    return fail(503, `期貨與美股行情暫時取不到：${e instanceof Error ? e.message : String(e)}`)
  }
}

export const onRequestGet: PagesFunction = (ctx) => cached(cacheKey(ctx.request), handle)
