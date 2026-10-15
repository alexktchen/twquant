// 證交所即時報價的共用 QuoteHub：/api/quotes（個股）與 /api/mood/live（指數）用同一個，
// 「兩次請求至少隔 2 秒」「被擋就暫停」才算得準（分開兩個就會各算各的，一起打太密又被擋）。
import { QuoteHub, misUrls, readMis, type Quote } from './quote.ts'

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

export const hub = new QuoteHub(fetchQuotes)
