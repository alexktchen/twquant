// RSS 2.0／RSS 1.0（RDF）／Atom 的解析（T-400／SPEC-017 §3）。不用第三方 XML 套件（Edge Function 與 node 測試共用、
// Cloudflare／Deno 沒有 DOMParser），只取要用的五個欄位：guid、網址、標題、摘要、發布時間。
// 只存標題與摘要（D-200 §三：不存全文、不轉載）。格式不對 → 丟錯，由進料記在 ingest_runs。

export type FeedItem = {
  /** 來源給的唯一代號；沒有就用網址 */
  guid: string
  url: string
  title: string
  /** 純文字摘要（去掉 HTML 標籤）；沒有 → null */
  summary: string | null
  /** 來源給的發布時間（ISO）；沒有（例：Nikkei Asia 的 RDF）→ null，只用我們第一次抓到的時間 */
  publishedAt: string | null
}

const NAMED: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }

/** XML／HTML 實體 → 字元（&amp; &#x2019; &#8217; &nbsp;）。 */
export function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z]+);/g, (m, e: string) => {
    if (e[0] === '#') {
      const code = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : m
    }
    return NAMED[e] ?? m
  })
}

/** 一段文字：拿掉 CDATA 外殼、HTML 標籤，解實體，空白收成一格。 */
export function cleanText(raw: string): string {
  const unwrapped = raw.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
  // 摘要裡常有 <p>、<img>：先解實體（&lt;p&gt; 也算標籤），再去標籤
  const decoded = decodeEntities(unwrapped)
  return decodeEntities(decoded.replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim()
}

/** 第一個 <tag>…</tag> 的內容（tag 可帶命名空間，例 dc:date）；沒有 → null。 */
function inner(block: string, tag: string): string | null {
  const re = new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)</${tag}>`)
  const m = re.exec(block)
  return m ? m[1] : null
}

/** Atom 的 <link href="…">（rel 是 alternate 或沒寫的那個）。 */
function atomLink(block: string): string | null {
  for (const m of block.matchAll(/<link\b([^>]*?)\/?>/g)) {
    const attrs = m[1]
    const href = /\bhref="([^"]+)"/.exec(attrs)?.[1]
    const rel = /\brel="([^"]+)"/.exec(attrs)?.[1]
    if (href && (!rel || rel === 'alternate')) return decodeEntities(href)
  }
  return null
}

/** 時間字串 → ISO（UTC）；空的 → null；有字但看不懂 → 丟錯（不靜默當成沒有時間）。 */
export function parseTime(raw: string | null): string | null {
  if (raw === null) return null
  const s = cleanText(raw)
  if (s === '') return null
  const t = Date.parse(s)
  if (!Number.isFinite(t)) throw new Error(`看不懂的時間：${s}`)
  return new Date(t).toISOString()
}

/** 一份 RSS／RDF／Atom → 項目；看不出是哪種格式、或某則沒有標題與網址 → 丟錯。 */
export function parseFeed(xml: string): FeedItem[] {
  const blocks = [...xml.matchAll(/<item\b[^>]*>[\s\S]*?<\/item>/g)].map((m) => m[0])
  const atom = blocks.length === 0 ? [...xml.matchAll(/<entry\b[^>]*>[\s\S]*?<\/entry>/g)].map((m) => m[0]) : []
  if (blocks.length === 0 && atom.length === 0) {
    if (/<(rss|rdf:RDF|feed)\b/.test(xml)) return []
    throw new Error('不是 RSS／Atom（找不到 <rss>、<rdf:RDF> 或 <feed>）')
  }
  const out: FeedItem[] = []
  for (const b of blocks.length > 0 ? blocks : atom) {
    const title = cleanText(inner(b, 'title') ?? '')
    // RSS 2.0：<link>網址</link>；RDF：<item rdf:about="網址">；Atom：<link href=…>
    const link =
      cleanText(inner(b, 'link') ?? '') || atomLink(b) || decodeEntities(/rdf:about="([^"]+)"/.exec(b)?.[1] ?? '')
    if (!title || !link) throw new Error(`有一則沒有標題或網址：${b.slice(0, 120)}`)
    const guid = cleanText(inner(b, 'guid') ?? inner(b, 'id') ?? '') || link
    const summaryRaw = inner(b, 'description') ?? inner(b, 'summary') ?? inner(b, 'content')
    const summary = summaryRaw === null ? null : cleanText(summaryRaw) || null
    const when = inner(b, 'pubDate') ?? inner(b, 'dc:date') ?? inner(b, 'published') ?? inner(b, 'updated')
    out.push({ guid, url: link, title, summary, publishedAt: parseTime(when) })
  }
  return out
}

/** 鉅亨網新聞列表 API（同 src/twquant/data/sources/news_headlines.py）→ 項目。statusCode 不是 200 或格式不對 → 丟錯。 */
export function parseCnyes(body: unknown): FeedItem[] {
  const b = body as { statusCode?: unknown; items?: { data?: unknown } } | null
  if (!b || b.statusCode !== 200 || !Array.isArray(b.items?.data)) throw new Error(`鉅亨網回應異常（statusCode=${String(b?.statusCode)}）`)
  return (b.items.data as Record<string, unknown>[]).map((it) => {
    const id = it.newsId
    const title = cleanText(String(it.title ?? ''))
    const ts = it.publishAt
    if (typeof id !== 'number' || !title || typeof ts !== 'number') throw new Error(`鉅亨網有一則缺代號、標題或時間：${JSON.stringify(it).slice(0, 120)}`)
    const summary = cleanText(String(it.summary ?? '')) || null
    return { guid: String(id), url: `https://news.cnyes.com/news/id/${id}`, title, summary, publishedAt: new Date(ts * 1000).toISOString() }
  })
}
