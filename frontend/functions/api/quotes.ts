// GET /api/quotes?sids=2330,5340：盤中即時量價（證交所，約 5 秒更新）。只給登入的人（在 /api 底下）。
// 證交所對同一個 IP 有頻率限制、超過會整個擋一段時間（2026-10-14 實際發生）：同一個 Cloudflare 執行個體裡
// 以每一檔為單位共用快取、請求之間至少隔 2 秒、被擋就暫停並加倍（QuoteHub，_lib/quote.ts）。行情是公開資料，不分使用者。
// 證交所擋下時，有富果金鑰（Pages 祕密 FUGLE_API_KEY）就用富果補，額度內一檔一檔問；沒設金鑰就只用證交所。
import { fail, json } from '../_lib/db'
import { FUGLE_URL, parseSids, readFugle } from '../_lib/quote'
import { hub } from '../_lib/quote-live'

type QuoteEnv = { FUGLE_API_KEY?: string }

/** 富果一檔一次（金鑰放在 header，不進網址、不進紀錄）。 */
const fugle = (key: string) => async (sid: string) =>
  readFugle(await fetch(FUGLE_URL + encodeURIComponent(sid), { headers: { accept: 'application/json', 'X-API-KEY': key } }))

export const onRequestGet: PagesFunction<QuoteEnv> = async ({ request, env }) => {
  const sids = parseSids(new URL(request.url).searchParams.get('sids'))
  if (sids === null) return fail(400, '代號要是 1～100 個、用逗號分隔')
  try {
    const got = await hub.get(sids, env.FUGLE_API_KEY ? fugle(env.FUGLE_API_KEY) : undefined)
    return json({ quotes: got.quotes, stale: got.stale, retry_after: got.retryAfter })
  } catch (e) {
    const retry = (e as { retryAfter?: number | null }).retryAfter ?? null
    const res = fail(503, `即時報價暫時取不到：${e instanceof Error ? e.message : String(e)}`)
    if (retry !== null) res.headers.set('retry-after', String(retry))
    return res
  }
}
