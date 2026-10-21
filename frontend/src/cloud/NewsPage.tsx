// 「新聞」分頁（T-400／D-200／SPEC-017 §7）：新聞流，國內外財經新聞由新到舊。
// 國外新聞顯示 AI 譯文標「譯」（T-401），點開附原文；標題下面列這則提到的台股／國外公司／族群（T-402，最多 3 個＋「+N」），
// 有提到的列多一顆「關係圖」：點了才向新聞服務要這則的圖（T-404），開心智圖。
// 搜尋框下面三張卡（T-417）：熱門主題、多家同報、股票熱度。「清單／全景」切換（T-418）：全景＝星雲圖，切過去才載入。
// 擁有者多一顆「待核可 N」（T-403）：點了換成核可頁（AI 草擬的關係線）。
// 頂端搜尋框（T-410）：照意思找，不必字面相同（「輝達財測」找得到英文的 Nvidia guidance）；展開一則時底下列「相似新聞」。
// 地鐵告示風格：每則＝國際／台灣色塊、標題、實體小標、來源與時間；點一下就地展開摘要與原文連結。
import { ChevronDown, ClipboardCheck, ExternalLink, Globe, MapPin, Network, RefreshCw, Search, X } from 'lucide-react'
import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react'
import { fetchNewsGraph, fetchNewsHealth, fetchNewsItems, fetchNewsReview, fetchNewsSearch } from './api'
import {
  KIND_LABEL,
  NEWS_PAGE,
  appendPage,
  dayTitle,
  groupByDay,
  healthLine,
  mentionChips,
  nextUntil,
  reviewLeft,
  searchLine,
  shown,
  taipeiDay,
  timeLabel,
  type NewsHealth,
  type NewsKind,
  type NewsSearch,
  type ReviewGroup,
  type NewsRow,
} from './news'
import { classOf, type NewsGraph as NewsGraphData } from './news-graph'
import { NewsDigest } from './NewsDigest'
import { galaxyFromUrl, withoutView } from './tabs'
import { NewsReview } from './NewsReview'
import { NewsSearchResults, SimilarNews } from './NewsSimilar'
import './news-graph.css'

type Filter = 'all' | NewsKind

// 關係圖（心智圖）自己一個檔案：第一次按「關係圖」才下載。
const NewsGraph = lazy(() => import('./NewsGraph').then((m) => ({ default: m.NewsGraph })))
// 全景星雲圖（T-418）：切到「全景」才下載
const NewsGalaxy = lazy(() => import('./NewsGalaxy').then((m) => ({ default: m.NewsGalaxy })))

const TAG_TITLE: Record<string, string> = {
  譯: 'AI 翻譯（標題與摘要），數字與公司名都核對過；點開看英文原文',
  未譯: '翻譯沒過檢查（數字或公司名對不上），先顯示英文原文',
  翻譯中: '剛抓到，下一輪（30 分鐘內）翻譯',
}

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'intl', label: '國際' },
  { key: 'tw', label: '台灣' },
]

export function NewsPage({ refresh, onStock }: { refresh: number; onStock: (sid: string, name: string) => void }) {
  const [filter, setFilter] = useState<Filter>('all')
  /** 清單（讀新聞）或全景（星雲圖，看全貌） */
  // 直接連結（?view=galaxy、舊網址 /api/prototype/galaxy）：一打開就是星雲圖，畫面捲到它（熱門主題卡在它上面）
  const [jumpToGalaxy] = useState(() => galaxyFromUrl(window.location.href))
  const [mode, setMode] = useState<'list' | 'galaxy'>(jumpToGalaxy ? 'galaxy' : 'list')
  const galaxyRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!jumpToGalaxy) return
    window.history.replaceState(null, '', withoutView(window.location.href))
    // 星雲圖是 lazy 載入、上面的卡片也要時間長出來：等它出現再捲
    const timer = window.setInterval(() => {
      const el = galaxyRef.current?.firstElementChild
      if (el && el.getBoundingClientRect().height > 100) {
        galaxyRef.current?.scrollIntoView({ block: 'start' })
        window.clearInterval(timer)
      }
    }, 200)
    const stop = window.setTimeout(() => window.clearInterval(timer), 8000)
    return () => {
      window.clearInterval(timer)
      window.clearTimeout(stop)
    }
  }, [jumpToGalaxy])
  const [rows, setRows] = useState<NewsRow[]>([])
  const [health, setHealth] = useState<NewsHealth | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [more, setMore] = useState<'idle' | 'loading' | 'done'>('idle')
  const [open, setOpen] = useState<string | null>(null)
  const [now, setNow] = useState(() => new Date())
  const [graph, setGraph] = useState<NewsGraphData | null>(null)
  /** 正在抓哪一則的關係圖；抓壞了是哪一則、什麼原因（顯示在那一列） */
  const [graphLoading, setGraphLoading] = useState<string | null>(null)
  const [graphErr, setGraphErr] = useState<{ id: string; msg: string } | null>(null)
  /** 搜尋框：輸入中的字、送出後的結果（null＝沒在搜尋，顯示新聞流） */
  const [q, setQ] = useState('')
  const [search, setSearch] = useState<NewsSearch | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchErr, setSearchErr] = useState('')
  /** 核可頁（T-403）：擁有者才有草稿清單（null＝不是擁有者或讀不到，不顯示按鈕） */
  const [review, setReview] = useState<ReviewGroup[] | null>(null)
  const [reviewOpen, setReviewOpen] = useState(false)

  useEffect(() => {
    let stale = false
    fetchNewsReview()
      .then((r) => !stale && setReview(r.can_review ? r.groups : null))
      .catch(() => !stale && setReview(null)) // 讀不到就不顯示按鈕，新聞照常
    return () => {
      stale = true
    }
  }, [refresh])

  const searchText = (raw: string) => {
    const text = raw.trim()
    if (!text || searching) return
    setSearching(true)
    setSearchErr('')
    fetchNewsSearch(text)
      .then(setSearch)
      .catch((x: Error) => setSearchErr(`搜尋失敗：${x.message}`))
      .finally(() => setSearching(false))
  }
  const runSearch = (e: React.FormEvent) => {
    e.preventDefault()
    searchText(q)
  }
  /** 熱門主題卡點了：把字放進搜尋框、搜尋、捲到頂端看結果 */
  const searchTopic = (text: string) => {
    setQ(text)
    searchText(text)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  const clearSearch = () => {
    setQ('')
    setSearch(null)
    setSearchErr('')
  }

  useEffect(() => {
    let stale = false
    setLoading(true)
    setErr('')
    setMore('idle')
    fetchNewsItems({ kind: filter === 'all' ? null : filter, limit: NEWS_PAGE })
      .then((r) => {
        if (stale) return
        setRows(r.items)
        if (r.items.length < NEWS_PAGE) setMore('done')
      })
      .catch((e: Error) => !stale && setErr(e.message))
      .finally(() => !stale && setLoading(false))
    fetchNewsHealth()
      .then((h) => !stale && setHealth(h))
      .catch(() => !stale && setHealth(null))
    setNow(new Date())
    return () => {
      stale = true
    }
  }, [filter, refresh])

  const loadMore = () => {
    const until = nextUntil(rows)
    if (!until || more !== 'idle') return
    setMore('loading')
    fetchNewsItems({ kind: filter === 'all' ? null : filter, limit: NEWS_PAGE, until })
      .then((r) => {
        const next = appendPage(rows, r.items)
        setRows(next)
        // 沒有新的（全是重複）或不滿一頁 → 到底了
        setMore(next.length === rows.length || r.items.length < NEWS_PAGE ? 'done' : 'idle')
      })
      .catch((e: Error) => {
        setErr(e.message)
        setMore('idle')
      })
  }

  const openGraph = (id: string) => {
    if (graphLoading) return
    setGraphLoading(id)
    setGraphErr(null)
    fetchNewsGraph(id)
      .then(setGraph)
      .catch((e: Error) => setGraphErr({ id, msg: `關係圖讀不到：${e.message}` }))
      .finally(() => setGraphLoading(null))
  }

  const groups = useMemo(() => groupByDay(rows), [rows])
  const today = taipeiDay(now.toISOString())
  const status = healthLine(health, now)

  return (
    <main className="cq-news">
      <div className="cq-news-bar">
        <div className="cq-seg cq-seg-small">
          {FILTERS.map((f) => (
            <button key={f.key} className={filter === f.key && mode === 'list' ? 'on' : ''} onClick={() => { setFilter(f.key); setMode('list') }}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="cq-seg cq-seg-small cq-news-mode" role="group" aria-label="看法">
          <button className={mode === 'list' ? 'on' : ''} onClick={() => setMode('list')} aria-pressed={mode === 'list'}>
            清單
          </button>
          <button className={mode === 'galaxy' ? 'on' : ''} onClick={() => setMode('galaxy')} aria-pressed={mode === 'galaxy'}>
            全景
          </button>
        </div>
        {review && reviewLeft(review) > 0 && (
          <button className="cq-news-review-btn" onClick={() => setReviewOpen(true)} title="AI 草擬的關係線，核可後才算數">
            <ClipboardCheck size={15} aria-hidden="true" />
            待核可 {reviewLeft(review)}
          </button>
        )}
        <span className={`cq-news-health ${status.tone}`} title={health?.sources.filter((s) => s.failing).map((s) => `${s.name}：${s.last_error ?? ''}`).join('\n') || undefined}>
          <RefreshCw size={14} aria-hidden="true" />
          {status.text}
        </span>
      </div>
      {reviewOpen && review && <NewsReview groups={review} onChange={setReview} onClose={() => setReviewOpen(false)} />}
      <form className="cq-news-search" role="search" onSubmit={runSearch} hidden={reviewOpen}>
        <Search size={16} aria-hidden="true" />
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="照意思找新聞，例：輝達財測、美債殖利率" maxLength={60} aria-label="搜尋新聞" enterKeyHint="search" />
        {(q || search) && (
          <button type="button" className="cq-news-search-x" onClick={clearSearch} aria-label="清除搜尋">
            <X size={16} aria-hidden="true" />
          </button>
        )}
        <button type="submit" disabled={!q.trim() || searching}>
          {searching ? '找…' : '找'}
        </button>
      </form>
      {searchErr && <div className="cq-err">{searchErr}</div>}
      {!search && !reviewOpen && <NewsDigest refresh={refresh} onSearch={searchTopic} onStock={onStock} />}
      {search && !reviewOpen && (
        <section className="cq-news-found">
          <h2>{searchLine(search)}</h2>
          <NewsSearchResults items={search.items} />
          <button className="cq-news-more" onClick={clearSearch}>
            回到全部新聞
          </button>
        </section>
      )}

      {graph && (
        <Suspense fallback={null}>
          <NewsGraph
            graph={graph}
            onClose={() => setGraph(null)}
            onStock={(sid, name) => {
              setGraph(null)
              onStock(sid, name)
            }}
          />
        </Suspense>
      )}
      {err && <div className="cq-err">{err}</div>}
      {loading && rows.length === 0 && !err && <p className="cq-war-mute cq-news-empty">讀取中…</p>}
      {!loading && !err && rows.length === 0 && <p className="cq-war-mute cq-news-empty">這段時間還沒有新聞。</p>}

      {!search && !reviewOpen && mode === 'galaxy' && (
        <div ref={galaxyRef} className="cq-news-galaxy-anchor">
          <Suspense fallback={<p className="cq-war-mute cq-news-empty">全景讀取中…</p>}>
            <NewsGalaxy onStock={onStock} />
          </Suspense>
        </div>
      )}
      {!search && !reviewOpen && mode === 'list' && groups.map((g) => (
        <section key={g.day} className="cq-news-day">
          <h2>{dayTitle(g.day, today)}</h2>
          <ul>
            {g.rows.map((r) => {
              const isOpen = open === r.id
              const v = shown(r)
              const lang = (l: 'en' | 'zh') => (l === 'en' ? 'en' : 'zh-Hant')
              const ents = mentionChips(r)
              const hasGraph = ents.chips.length > 0
              return (
                <li key={r.id} className={isOpen ? 'open' : ''}>
                  <div className="cq-news-head">
                    <button className={`cq-news-row${hasGraph ? ' has-graph' : ''}`} aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : r.id)}>
                      <span className={`cq-news-kind ${r.kind ?? 'tw'}`}>
                        {r.kind === 'intl' ? <Globe size={13} aria-hidden="true" /> : <MapPin size={13} aria-hidden="true" />}
                        {r.kind ? KIND_LABEL[r.kind] : '其他'}
                      </span>
                      <span className="cq-news-title" lang={lang(v.lang)}>
                        {v.tag && (
                          <em className={`cq-news-tag ${v.tag === '譯' ? 'tr' : 'raw'}`} title={TAG_TITLE[v.tag]}>
                            {v.tag}
                          </em>
                        )}
                        {v.title}
                      </span>
                      {hasGraph && (
                        <span className="cq-news-ents">
                          {ents.chips.map((m) => (
                            <span key={m.id} className={`cq-news-ent ${classOf(m.type)}`}>
                              {m.label}
                            </span>
                          ))}
                          {ents.more > 0 && <span className="cq-news-ent-more">+{ents.more}</span>}
                        </span>
                      )}
                      <span className="cq-news-meta">
                        <span className="cq-news-src">
                          {r.source_name}・{timeLabel(r, g.day)}
                        </span>
                        <ChevronDown size={15} className={isOpen ? 'flip' : ''} aria-hidden="true" />
                      </span>
                    </button>
                    {hasGraph && (
                      <button className="cq-news-graph" onClick={() => openGraph(r.id)} disabled={graphLoading !== null} title="這則新聞提到的公司、族群，往外牽動到哪些台股">
                        <Network size={14} aria-hidden="true" />
                        {graphLoading === r.id ? '讀取中…' : '關係圖'}
                      </button>
                    )}
                  </div>
                  {graphErr?.id === r.id && <p className="cq-news-gerr">{graphErr.msg}</p>}
                  {isOpen && (
                    <div className="cq-news-body">
                      {v.summary && <p lang={lang(v.lang)}>{v.summary}</p>}
                      {v.original && (
                        <div className="cq-news-orig" lang="en">
                          <span>原文</span>
                          <b>{v.original.title}</b>
                          {v.original.summary && <p>{v.original.summary}</p>}
                        </div>
                      )}
                      <a href={r.url} target="_blank" rel="noopener noreferrer">
                        看原文（{r.source_name}）<ExternalLink size={13} aria-hidden="true" />
                      </a>
                      <SimilarNews id={r.id} />
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      {!search && !reviewOpen && mode === 'list' && rows.length > 0 && more !== 'done' && (
        <button className="cq-news-more" onClick={loadMore} disabled={more === 'loading'}>
          {more === 'loading' ? '讀取中…' : '更多'}
        </button>
      )}
    </main>
  )
}
