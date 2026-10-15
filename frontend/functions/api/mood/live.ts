// GET /api/mood/live：市場氛圍的盤中版（D-181）——加權、櫃買與 28 個類股指數的漲跌，算出最強／最弱類股與攻守。
// 跟個股即時報價共用同一個 QuoteHub（_lib/quote-live.ts）：一次請求問完、每 8 秒最多問一次、被擋就暫停。
// 指數沒有富果備援（富果的指數代號不同）；證交所擋下時給還不太舊的快取，太舊就 503。
import { fail, json } from '../../_lib/db'
import { INDEX_CODES, liveMood } from '../../_lib/mood'
import { hub } from '../../_lib/quote-live'

export const onRequestGet: PagesFunction = async () => {
  try {
    const got = await hub.get(INDEX_CODES)
    return json({ live: liveMood(got.quotes), stale: got.stale, retry_after: got.retryAfter })
  } catch (e) {
    const retry = (e as { retryAfter?: number | null }).retryAfter ?? null
    const res = fail(503, `類股指數暫時取不到：${e instanceof Error ? e.message : String(e)}`)
    if (retry !== null) res.headers.set('retry-after', String(retry))
    return res
  }
}
