// 新聞分頁（T-400／SPEC-017）的型別與顯示用純函式（不碰 DOM，供 node 單元測試）。後端形狀見 news-service/supabase/functions/api。

export type NewsKind = 'intl' | 'tw'
export type NewsRow = {
  id: string
  source: string
  source_name: string
  kind: NewsKind | null
  lang: 'en' | 'zh'
  title: string
  summary: string | null
  url: string
  /** 來源給的發布時間；Nikkei Asia 沒有 → null */
  published_at: string | null
  /** 我們第一次抓到的時間（列表依這個排） */
  first_seen_at: string
  /** 來源後來改過幾次標題或摘要 */
  versions: number
  /** 英文新聞的翻譯（T-401）：ok 有譯文、failed 沒過檢查、pending 還沒翻；中文新聞 null。舊的服務沒有這欄 → undefined */
  translation?: 'ok' | 'failed' | 'pending' | null
  title_zh?: string | null
  summary_zh?: string | null
  /** 這則提到的實體（T-402，一度；台股在前、再國外、再族群／主題）。舊的服務沒有這欄 → undefined */
  mentions?: NewsMention[]
}
export type NewsMention = { id: string; type: 'tw' | 'us' | 'company' | 'group' | 'theme'; label: string }

/** 列上最多顯示幾個實體小標（其餘收成「+N」） */
export const MENTION_CHIPS = 3
/** 列上的實體小標：前 k 個＋剩下幾個。 */
export function mentionChips(r: NewsRow, k: number = MENTION_CHIPS): { chips: NewsMention[]; more: number } {
  const all = r.mentions ?? []
  return { chips: all.slice(0, k), more: Math.max(0, all.length - k) }
}

export type Shown = {
  title: string
  summary: string | null
  lang: 'en' | 'zh'
  /** 標題旁的小標：譯＝AI 翻譯、未譯＝翻譯沒過檢查（顯示原文）、翻譯中＝還沒翻 */
  tag: '譯' | '未譯' | '翻譯中' | null
  /** 有譯文時，展開後附原文 */
  original: { title: string; summary: string | null } | null
}

/** 這則要顯示的樣子：有過檢查的譯文就用譯文（附原文），否則顯示原文。 */
export function shown(r: NewsRow): Shown {
  if (r.translation === 'ok' && r.title_zh) {
    return { title: r.title_zh, summary: r.summary_zh ?? null, lang: 'zh', tag: '譯', original: { title: r.title, summary: r.summary } }
  }
  const tag = r.translation === 'failed' ? '未譯' : r.translation === 'pending' ? '翻譯中' : null
  return { title: r.title, summary: r.summary, lang: r.lang, tag, original: null }
}
export type NewsHealth = {
  now: string
  last_ok_at: string | null
  stale: boolean
  sources: { source: string; name: string; failing: boolean; last_error: string | null }[]
}

export const KIND_LABEL: Record<NewsKind, string> = { intl: '國際', tw: '台灣' }
/** 一次抓幾則（「更多」再抓下一批） */
export const NEWS_PAGE = 60

const TAIPEI_MS = 8 * 3600_000
const pad = (n: number) => String(n).padStart(2, '0')
const taipei = (iso: string) => new Date(Date.parse(iso) + TAIPEI_MS)

/** 台北日期 YYYY-MM-DD。 */
export const taipeiDay = (iso: string): string => taipei(iso).toISOString().slice(0, 10)
/** 台北時間 HH:MM。 */
export const taipeiHm = (iso: string): string => {
  const d = taipei(iso)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}

/** 列表上的時間：顯示來源的發布時間（沒有就用我們抓到的時間）。 */
export const shownTime = (r: NewsRow): string => r.published_at ?? r.first_seen_at

/** 列表上那則的時間：跟分組那天同一天 → 「22:05」；不同天（例：昨晚發、今天才抓到）→ 「10/18 22:05」。 */
export function timeLabel(r: NewsRow, groupDay: string): string {
  const t = shownTime(r)
  if (taipeiDay(t) === groupDay) return taipeiHm(t)
  const d = taipei(t)
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()} ${taipeiHm(t)}`
}

/** 「今天」「昨天」「10/17（六）」。 */
export function dayTitle(day: string, today: string): string {
  if (day === today) return '今天'
  const y = new Date(Date.parse(`${today}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10)
  if (day === y) return '昨天'
  const d = new Date(`${day}T00:00:00Z`)
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}（${'日一二三四五六'[d.getUTCDay()]}）`
}

/** 依抓到的那天（台北）分組，保留原本由新到舊的順序。 */
export function groupByDay(rows: NewsRow[]): { day: string; rows: NewsRow[] }[] {
  const out: { day: string; rows: NewsRow[] }[] = []
  for (const r of rows) {
    const day = taipeiDay(r.first_seen_at)
    const last = out.at(-1)
    if (last && last.day === day) last.rows.push(r)
    else out.push({ day, rows: [r] })
  }
  return out
}

/** 「剛剛」「12 分鐘前」「3 小時前」「10/18 22:05」。 */
export function ageLabel(iso: string, now: Date): string {
  const mins = Math.floor((now.getTime() - Date.parse(iso)) / 60_000)
  if (mins < 1) return '剛剛'
  if (mins < 60) return `${mins} 分鐘前`
  if (mins < 24 * 60) return `${Math.floor(mins / 60)} 小時前`
  const d = taipei(iso)
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()} ${taipeiHm(iso)}`
}

/** 頂端狀態列：最後抓取時間、壞掉幾個來源、是否停擺。 */
export function healthLine(h: NewsHealth | null, now: Date): { text: string; tone: 'ok' | 'warn' | 'bad' } {
  if (!h) return { text: '新聞服務狀態取不到', tone: 'bad' }
  if (!h.last_ok_at) return { text: '還沒抓到任何新聞', tone: 'bad' }
  const failing = h.sources.filter((s) => s.failing)
  const base = `最後抓取 ${ageLabel(h.last_ok_at, now)}`
  if (h.stale) return { text: `${base}・已停擺`, tone: 'bad' }
  if (failing.length > 0) return { text: `${base}・${failing.map((s) => s.name).join('、')} 暫停`, tone: 'warn' }
  return { text: base, tone: 'ok' }
}

/** 「更多」要從哪裡接著抓：目前最舊那則的抓到時間（API 的 until 含這個時間——同一批同一個時間，不能跳過；重複的由 appendPage 去掉）。 */
export const nextUntil = (rows: NewsRow[]): string | null => rows.at(-1)?.first_seen_at ?? null

/** 新的一批接在後面；同一則（翻頁時剛好跨到同一秒）只留一次。 */
export function appendPage(rows: NewsRow[], more: NewsRow[]): NewsRow[] {
  const seen = new Set(rows.map((r) => r.id))
  return [...rows, ...more.filter((r) => !seen.has(r.id))]
}

// ── 反向看圖（使用者 2026-10-20：「那有反向看圖的地方嗎？像是每日推薦股有關的新聞」）────────────────
export type StockNewsStep = { id: string; label: string; via: string; via_label?: string }
export type StockNewsItem = {
  id: string
  source_name: string
  url: string
  first_seen_at: string
  title: string
  title_zh: string | null
  /** 1＝新聞直接提到；2＝新聞提到的公司或族群再走一條線到這一檔 */
  degree: number
  score: number
  /** 路徑上有 AI 草擬、還沒核可的線 */
  draft: boolean
  path: StockNewsStep[]
}
export type StockNews = { id: string; days: number; items: StockNewsItem[] }
export type NewsCount = { direct: number; indirect: number }
export type StockNewsCounts = { days: number; counts: Record<string, NewsCount> }

/** 個股頁一開始列幾則（其餘收起） */
export const STOCK_NEWS_SHOWN = 5

const VIA_SHORT: Record<string, string> = { supplier: '供應商', belongs: '屬於族群', member: '族群成員', peer: '同族群', theme: '相關主題', mention: '提到', cooccur: '新聞常一起提到' }

/** 間接那則怎麼連過來的：「輝達 › 供應商」「伺服器 › 族群成員」（最後一步就是這一檔，不重複寫）；草擬的加「・草擬」。 */
export function pathText(it: Pick<StockNewsItem, 'path' | 'draft'>): string {
  if (it.path.length < 2) return ''
  const first = it.path[0].label
  const last = it.path[it.path.length - 1]
  const via = last.via === 'peer' && last.via_label ? `同族群・${last.via_label}` : last.via === 'cooccur' && last.via_label ? last.via_label : (VIA_SHORT[last.via] ?? last.via)
  return `${first} › ${via}${it.draft ? '・草擬' : ''}`
}

/**
 * 名單上的「相關新聞」小標被點了（focus 加一）→ 這一檔的相關新聞讀到後要不要捲過去（使用者 2026-10-20：「推薦股有新聞，但是點了沒反應」）。
 * done＝上次已經捲過的 focus；loadedId＝目前讀到的是哪一檔（換股時舊的那檔還在，要等新的讀完）。
 */
export const shouldJumpToNews = (focus: number, done: number, loadedId: string | null, sid: string): boolean =>
  focus > 0 && focus !== done && loadedId === `tw:${sid}`

/** 個股頁標題右邊那一句：「近 7 天・直接 1・間接 19」。 */
export function stockNewsSummary(d: StockNews): string {
  const direct = d.items.filter((x) => x.degree === 1).length
  return `近 ${d.days} 天・直接 ${direct}・間接 ${d.items.length - direct}`
}
