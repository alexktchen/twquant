// GET /api/quotes?sids=2330,5340：盤中即時量價（證交所，約 5 秒更新）。只給登入的人（在 /api 底下）。
// 同一組股票 5 秒內只問證交所一次：同一個 Cloudflare 執行個體裡暫存（行情是公開資料，不分使用者）。
import { fail, json } from '../_lib/db'
import { misUrls, parseMis, parseSids, type Quote } from '../_lib/quote'

const CACHE_MS = 5000
const CACHE_KEEP = 200
const memo = new Map<string, { at: number; quotes: Quote[] }>()

async function fetchQuotes(sids: string[]): Promise<Quote[]> {
  const out: Quote[] = []
  // 一批一批依序問（不同時打好幾個請求，免得被證交所當成太頻繁）
  for (const url of misUrls(sids)) {
    const res = await fetch(url, { headers: { accept: 'application/json', 'user-agent': 'Mozilla/5.0' } })
    if (!res.ok) throw new Error(`證交所即時報價 HTTP ${res.status}`)
    out.push(...parseMis(await res.json()))
  }
  return out
}

export const onRequestGet: PagesFunction = async ({ request }) => {
  const sids = parseSids(new URL(request.url).searchParams.get('sids'))
  if (sids === null) return fail(400, '代號要是 1～100 個、用逗號分隔')
  const key = [...sids].sort().join(',')
  const now = Date.now()
  const hit = memo.get(key)
  if (hit && now - hit.at < CACHE_MS) return json({ quotes: hit.quotes })
  try {
    const quotes = await fetchQuotes(sids)
    if (memo.size >= CACHE_KEEP) for (const [k, v] of memo) if (now - v.at >= CACHE_MS) memo.delete(k)
    if (memo.size < CACHE_KEEP) memo.set(key, { at: now, quotes })
    return json({ quotes })
  } catch (e) {
    return fail(503, `即時報價暫時取不到：${e instanceof Error ? e.message : String(e)}`)
  }
}
