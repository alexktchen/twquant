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
  /** 新聞類型（T-421）：財報展望／訂單產品／擴廠併購／政策事故／盤勢行情／其他；還沒分類或舊服務 → null／undefined */
  type?: NewsType | null
}
export type NewsType = 'earnings' | 'business' | 'capital' | 'policy' | 'market' | 'other'
/** 類型的中文與顏色類別（地鐵告示：顏色歸類、少字）；跟 news-service/config/news_types.json 的 labels 一致 */
export const NEWS_TYPES: { key: NewsType; label: string }[] = [
  { key: 'earnings', label: '財報展望' },
  { key: 'business', label: '訂單產品' },
  { key: 'capital', label: '擴廠併購' },
  { key: 'policy', label: '政策事故' },
  { key: 'market', label: '盤勢行情' },
  { key: 'other', label: '其他' },
]
export const NEWS_TYPE_LABEL: Record<NewsType, string> = Object.fromEntries(NEWS_TYPES.map((t) => [t.key, t.label])) as Record<NewsType, string>
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

/** 這則要顯示的樣子：有過檢查的譯文就用譯文（附原文），否則顯示原文。D-207 起排程只翻標題，摘要譯文多半是 null（閱讀卡另外翻，見 readerSummary）。 */
export function shown(r: NewsRow): Shown {
  if (r.translation === 'ok' && r.title_zh) {
    return { title: r.title_zh, summary: r.summary_zh ?? null, lang: 'zh', tag: '譯', original: { title: r.title, summary: r.summary } }
  }
  const tag = r.translation === 'failed' ? '未譯' : r.translation === 'pending' ? '翻譯中' : null
  return { title: r.title, summary: r.summary, lang: r.lang, tag, original: null }
}

/** 點開才翻的摘要（D-207）：/pub/news/summary 的回應。budget＝今天的翻譯額度用完、error＝模型暫時叫不到、none＝不用翻 */
/** 證據句（T-437）：{新聞 id: {實體 id: 比對名字時存下的那段字}}；沒有的就沒有鍵 */
export type NewsEvidence = { items: Record<string, Record<string, string>> }
export type NewsSummary = { id: string; status: 'ok' | 'failed' | 'budget' | 'error' | 'none'; summary_zh: string | null }

/** 打開閱讀卡時要不要另外翻摘要：英文、標題已翻好、有英文摘要、還沒有摘要譯文。 */
export function needsSummary(r: NewsRow): boolean {
  return r.lang === 'en' && r.translation === 'ok' && !!r.title_zh && !!r.summary && !r.summary_zh
}

/** 閱讀卡的摘要：不用另外翻的照 shown()；翻好前寫「摘要翻譯中…」；翻不成（沒過檢查、額度用完、服務壞了）顯示英文摘要並說明原因。 */
export function readerSummary(r: NewsRow, s: NewsSummary | 'loading' | 'error' | null): { text: string | null; lang: 'en' | 'zh'; note: string | null } {
  if (!needsSummary(r)) {
    const v = shown(r)
    return { text: v.summary, lang: v.lang, note: null }
  }
  if (s === null || s === 'loading') return { text: null, lang: 'zh', note: '摘要翻譯中…' }
  if (s !== 'error' && s.status === 'ok' && s.summary_zh) return { text: s.summary_zh, lang: 'zh', note: null }
  const why = s === 'error' ? null : s.status
  const note =
    why === 'budget' ? '今天的翻譯額度用完了，先顯示英文摘要' : why === 'failed' ? '摘要翻譯沒過檢查（數字或公司名對不上），先顯示英文摘要' : '摘要暫時翻不了，先顯示英文摘要'
  return { text: r.summary, lang: 'en', note }
}

export type NewsHealth = {
  now: string
  last_ok_at: string | null
  stale: boolean
  sources: { source: string; name: string; failing: boolean; last_error: string | null }[]
  /** 新聞庫用量（2026-10-20 起；免費方案 500 MB），warn＝超過七成 */
  storage?: { used_mb: number; limit_mb: number; warn: boolean }
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
  if (h.storage?.warn) return { text: `${base}・資料庫已用 ${Math.round((h.storage.used_mb / h.storage.limit_mb) * 100)}%`, tone: 'warn' }
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
export type StockNews = { id: string; days: number; items: StockNewsItem[]; quiet?: Quiet | null }
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

/** 個股頁標題右邊那一句：「近 7 天・直接 1・間接 19」；久違的消息再加一句。 */
export function stockNewsSummary(d: StockNews): string {
  const direct = d.items.filter((x) => x.degree === 1).length
  const q = quietNote(d.quiet)
  return `近 ${d.days} 天・直接 ${direct}・間接 ${d.items.length - direct}${q ? `・${q}` : ''}`
}

// ── 相似新聞與語意搜尋（T-410／SPEC-018）：新聞服務用 bge-m3 向量找出來的 ────────────────────────
export type SimilarItem = {
  id: string
  source_name: string
  kind: NewsKind | null
  url: string
  first_seen_at: string
  title: string
  title_zh: string | null
  score: number
  /** 同一事件（不同媒體報同一件事，或同一則被兩個頻道刊） */
  same_event: boolean
}
/** ready＝這則已經算好向量（英文新聞要翻譯完才算，通常 30 分鐘內）；expired＝超過雲端保留的天數，向量已刪 */
export type SimilarNews = { id: string; ready: boolean; expired?: boolean; retention_days?: number; items: SimilarItem[] }
export type NewsSearch = { q: string; budget_stop: boolean; items: SimilarItem[] }

/** 短時間：今天 → 「22:05」，其他天 → 「10/18」。 */
export function shortWhen(iso: string, today: string): string {
  if (taipeiDay(iso) === today) return taipeiHm(iso)
  const d = taipei(iso)
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
}

/** 展開那則底下「相似新聞」的說明（沒有清單時才顯示）。 */
export function similarNote(d: SimilarNews): string | null {
  if (!d.ready && d.expired) return `這則比較舊，相似新聞只找近 ${d.retention_days ?? '幾十'} 天的`
  if (!d.ready) return '這則還在算（英文新聞翻譯完才算，通常 30 分鐘內）'
  if (d.items.length === 0) return '沒有內容相近的新聞'
  return null
}

/** 閱讀卡「相似新聞」先列幾則（其餘按「再看 N 則」） */
export const READER_RELATED = 4

/** 比較用的標題：去掉空白與標點、英文小寫（「加高績攻AI高速光通訊 明年展望謹慎樂觀」≈「加高績攻 AI 高速光通訊，明年展望謹慎樂觀」） */
export const titleKey = (t: string): string => t.toLowerCase().replace(/[\s\p{P}\p{S}]/gu, '')

export type ReaderSimilar = SimilarItem & { sameTitle: boolean }
/** 閱讀卡用：同一事件與其他相似新聞分開；同標題只留一則（分數高的）；其他相似新聞不重複列同一事件已有的標題、也不列跟這則同標題的。
 *  同一事件裡跟這則同標題的照樣列（那是「另一家也報了」），只是不再寫標題（sameTitle）。exclude＝別處已列的標題（來龍去脈）。 */
export function splitSimilar(
  selfTitle: string,
  items: SimilarItem[],
  exclude: readonly string[] = [],
): { sameEvent: ReaderSimilar[]; related: ReaderSimilar[] } {
  const self = titleKey(selfTitle)
  const byScore = [...items].sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
  const seen = new Set<string>()
  const sameEvent: ReaderSimilar[] = []
  for (const it of byScore.filter((x) => x.same_event)) {
    const k = titleKey(it.title_zh ?? it.title)
    const key = `${it.source_name}|${k}`
    if (seen.has(key)) continue
    seen.add(key)
    sameEvent.push({ ...it, sameTitle: k === self })
  }
  const taken = new Set([self, ...exclude.map(titleKey), ...sameEvent.map((x) => titleKey(x.title_zh ?? x.title))])
  const related: ReaderSimilar[] = []
  for (const it of byScore.filter((x) => !x.same_event)) {
    const k = titleKey(it.title_zh ?? it.title)
    if (taken.has(k)) continue
    taken.add(k)
    related.push({ ...it, sameTitle: false })
  }
  return { sameEvent, related }
}

/** 搜尋結果上面那一句。 */
export function searchLine(d: NewsSearch): string {
  if (d.budget_stop) return '今天新聞的 AI 額度用完了，明天早上 8 點恢復'
  if (d.items.length === 0) return `找不到跟「${d.q}」相關的新聞`
  return `跟「${d.q}」相關的新聞 ${d.items.length} 則，越相關越前面`
}

// ── 核可頁（T-403）：AI 草擬的線，擁有者核可才算數 ──────────────────────────────────────────
export type ReviewType = 'supplier' | 'theme' | 'peer'
export type ReviewItem = { dst: string; dst_label: string; dst_type: NewsMention['type'] | null; dst_sub: string | null; label: string | null }
export type ReviewGroup = { src: string; src_label: string; src_type: NewsMention['type'] | null; type: ReviewType; items: ReviewItem[] }
export type NewsReview = { can_review: boolean; total: number; groups: ReviewGroup[] }
export type ReviewDecision = { src: string; dst: string; type: ReviewType; decision: 'approved' | 'rejected' }
export type ReviewResult = { approved: number; rejected: number; skipped: string[] }

/** 一組卡片的標題後半：「輝達 → 台灣供應商」 */
export const REVIEW_TYPE_LABEL: Record<ReviewType, string> = { supplier: '台灣供應商', theme: '相關族群與個股', peer: '同業' }

/** 送出後把處理掉的線拿掉（空掉的組也拿掉）；略過的（別處已處理）一樣拿掉。 */
export function dropDecided(groups: ReviewGroup[], done: { src: string; dst: string; type: ReviewType }[]): ReviewGroup[] {
  const k = (x: { src: string; dst: string; type: string }) => `${x.src}|${x.dst}|${x.type}`
  const gone = new Set(done.map(k))
  return groups
    .map((g) => ({ ...g, items: g.items.filter((i) => !gone.has(k({ src: g.src, dst: i.dst, type: g.type }))) }))
    .filter((g) => g.items.length > 0)
}

/** 一組全部核可或退回要送的決定。 */
export const groupDecisions = (g: ReviewGroup, decision: ReviewDecision['decision']): ReviewDecision[] =>
  g.items.map((i) => ({ src: g.src, dst: i.dst, type: g.type, decision }))

/** 還剩幾條。 */
export const reviewLeft = (groups: ReviewGroup[]): number => groups.reduce((a, g) => a + g.items.length, 0)

// ── 新聞三張卡（T-417；使用者 2026-10-20：「要快速閱讀可以三張卡片」）──────────────────────────
export type DigestTopic = { id: string; label: string; type: string; n: number; prev: number; stocks: { id: string; label: string; n: number }[] }
export type DigestMulti = { ev: string; outlets: string[]; items: number; title: string; url: string; first_seen_at: string; last_seen_at: string; stocks: string[] }
export type DigestHot = { id: string; label: string; sub: string | null; n: number; base: number | null; ratio: number | null; quiet?: Quiet | null }
export type NewsDigest = {
  as_of: string
  /** 平常值用了幾天的資料（0＝還不到一天，股票熱度只排則數、主題不比前一天） */
  base_days: number
  topics: { items: DigestTopic[]; everywhere: { label: string; share: number }[] }
  multi: DigestMulti[]
  hot: DigestHot[]
}

/** 主題比前一天多幾則：「+12」「−5」「持平」；還沒有前一天的資料 → null（不顯示）。 */
export function topicDelta(t: DigestTopic, baseDays: number): string | null {
  if (baseDays < 1) return null
  const d = t.n - t.prev
  return d === 0 ? '持平' : d > 0 ? `+${d}` : `−${-d}`
}

/** 卡片底下那句：「AI」到處都提（45%），不列。 */
export function everywhereNote(list: { label: string; share: number }[]): string | null {
  if (list.length === 0) return null
  return `${list.map((x) => `「${x.label}」`).join('')}幾乎每則都提（${list.map((x) => `${Math.round(x.share * 100)}%`).join('、')}），不列`
}

/** 股票熱度那列的小字：「今 12 則・平常 2／天」；還沒有平常值 → 「今 12 則」。 */
export function hotLine(h: DigestHot): string {
  return h.base === null ? `近 24 小時 ${h.n} 則` : `近 24 小時 ${h.n} 則・平常 ${h.base}／天`
}

/** 倍數小標：至少 2 倍才標（×2.5）；沒有平常值 → null。 */
export const hotBadge = (h: DigestHot): string | null => (h.ratio !== null && h.ratio >= 2 ? `×${h.ratio}` : null)

// ── 個股頁新聞時間軸與久違的消息（T-419；使用者 2026-10-20 選的 2、4）──────────────────────────
export type Quiet = { days: number; at_least: boolean }
export type StockNewsDay = { day: string; n: number; outlets: string[]; items: { id: string; title: string; src: string; url: string }[] }
export type StockNewsDays = { id: string; timeline_days: number; days: StockNewsDay[] }

/** K 線上的新聞標記：只標 K 線範圍內的日子，附當天幾則。 */
export function newsMarkers(days: StockNewsDay[], barDates: string[]): { time: string; n: number }[] {
  const inRange = new Set(barDates)
  return days.filter((d) => inRange.has(d.day)).map((d) => ({ time: d.day, n: d.n })).sort((a, b) => (a.time < b.time ? -1 : 1))
}

/** 「30 天沒消息後第一次」「至少 60 天沒消息後第一次」；null → 不顯示。 */
export function quietNote(q: Quiet | null | undefined): string | null {
  if (!q) return null
  return `${q.at_least ? '至少 ' : ''}${q.days} 天沒消息後第一次`
}

// ── 事件追蹤（T-420；使用者 2026-10-20 選「事件追蹤」）：同一件事連續幾天的報導串成一條線 ───────────
export type ThreadStep = { cluster_id: string; current: boolean; n: number; first_seen_at: string; last_seen_at: string; item_id: string; title: string; url: string; outlets: string[] }
export type NewsThread = { id: string; cluster_id: string | null; steps: ThreadStep[] }

/** 這條線值得顯示嗎：至少兩個事件群（只有自己那一群就不用畫） */
export const threadShown = (t: NewsThread | null): boolean => !!t && t.steps.length >= 2

/** 線上每一步的日期：「10/18」；同一天多步照樣寫日期（時間在 title 旁） */
export const stepDay = (s: ThreadStep): string => {
  const d = new Date(Date.parse(s.first_seen_at) + 8 * 3600_000)
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
}
