// GET /api/quotes?sids=2330,5340：盤中即時量價（證交所，約 5 秒更新）。只給登入的人（在 /api 底下）。
// 證交所對同一個 IP 有頻率限制、超過會整個擋一段時間（2026-10-14 實際發生）：同一個 Cloudflare 執行個體裡
// 以每一檔為單位共用快取、請求之間至少隔 2 秒、被擋就暫停並加倍（QuoteHub，_lib/quote.ts）。行情是公開資料，不分使用者。
import { fail, json } from '../_lib/db'
import { QuoteHub, misUrls, parseSids, readMis, type Quote } from '../_lib/quote'

async function fetchQuotes(sids: string[]): Promise<Quote[]> {
  const out: Quote[] = []
  // 一批一批依序問（不同時打好幾個請求，免得被證交所當成太頻繁）
  for (const url of misUrls(sids)) {
    const res = await fetch(url, {
      headers: { accept: 'application/json', 'user-agent': 'Mozilla/5.0', referer: 'https://mis.twse.com.tw/stock/index.jsp' },
    })
    out.push(...(await readMis(res)))
  }
  return out
}

const hub = new QuoteHub(fetchQuotes)

export const onRequestGet: PagesFunction = async ({ request }) => {
  const sids = parseSids(new URL(request.url).searchParams.get('sids'))
  if (sids === null) return fail(400, '代號要是 1～100 個、用逗號分隔')
  try {
    const got = await hub.get(sids)
    return json({ quotes: got.quotes, stale: got.stale, retry_after: got.retryAfter })
  } catch (e) {
    const retry = (e as { retryAfter?: number | null }).retryAfter ?? null
    const res = fail(503, `即時報價暫時取不到：${e instanceof Error ? e.message : String(e)}`)
    if (retry !== null) res.headers.set('retry-after', String(retry))
    return res
  }
}
