// 「新聞」分頁（T-400／D-200／SPEC-017 §7）：新聞流，國內外財經新聞由新到舊。
// 國外新聞顯示 AI 譯文標「譯」（T-401），點開附原文；關聯的美股／台股／族群、關係圖在後面的任務接上。
// 地鐵告示風格：每則＝國際／台灣色塊、標題、來源與時間；點一下就地展開摘要與原文連結。
import { ChevronDown, ExternalLink, Globe, MapPin, Network, RefreshCw } from 'lucide-react'
import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import { fetchNewsHealth, fetchNewsItems } from './api'
import {
  KIND_LABEL,
  NEWS_PAGE,
  appendPage,
  dayTitle,
  groupByDay,
  healthLine,
  nextUntil,
  shown,
  taipeiDay,
  timeLabel,
  type NewsHealth,
  type NewsKind,
  type NewsRow,
} from './news'
import type { NewsGraph as NewsGraphData } from './news-graph'
import './news-graph.css'

type Filter = 'all' | NewsKind

// 關係圖（心智圖）：示範區預設就顯示（使用者 2026-10-19：「心智圖在哪」——藏在 ?demo=graph 後面找不到）；圖與示範資料各自一個檔案，進分頁才下載。
// 關聯資料（T-402、T-404）上線後改成每則新聞點開就有，這段示範拿掉。
const NewsGraph = lazy(() => import('./NewsGraph').then((m) => ({ default: m.NewsGraph })))

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
  const [rows, setRows] = useState<NewsRow[]>([])
  const [health, setHealth] = useState<NewsHealth | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [more, setMore] = useState<'idle' | 'loading' | 'done'>('idle')
  const [open, setOpen] = useState<string | null>(null)
  const [now, setNow] = useState(() => new Date())
  const [demo, setDemo] = useState<NewsGraphData[] | null>(null)
  const [graph, setGraph] = useState<NewsGraphData | null>(null)

  useEffect(() => {
    import('./news-graph-demo.json').then((m) => setDemo(m.default as unknown as NewsGraphData[]))
  }, [])

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

  const groups = useMemo(() => groupByDay(rows), [rows])
  const today = taipeiDay(now.toISOString())
  const status = healthLine(health, now)

  return (
    <main className="cq-news">
      <div className="cq-news-bar">
        <div className="cq-seg cq-seg-small">
          {FILTERS.map((f) => (
            <button key={f.key} className={filter === f.key ? 'on' : ''} onClick={() => setFilter(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
        <span className={`cq-news-health ${status.tone}`} title={health?.sources.filter((s) => s.failing).map((s) => `${s.name}：${s.last_error ?? ''}`).join('\n') || undefined}>
          <RefreshCw size={14} aria-hidden="true" />
          {status.text}
        </span>
      </div>

      {demo && (
        <section className="cq-ng-demo">
          <h2>
            <Network size={18} aria-hidden="true" /> 關係圖（示範）
          </h2>
          <p className="cq-ng-demo-key">
            <span className="abroad">國外</span>
            <span className="tw">台股</span>
            <span className="cluster">族群</span>
            新聞怎麼牽動美股、台股、族群
          </p>
          {demo.map((g) => (
            <button key={g.item.id} onClick={() => setGraph(g)}>
              <b>{g.item.title_zh ?? g.item.title}</b>
              <small>{g.nodes.length} 個關聯</small>
            </button>
          ))}
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

      {groups.map((g) => (
        <section key={g.day} className="cq-news-day">
          <h2>{dayTitle(g.day, today)}</h2>
          <ul>
            {g.rows.map((r) => {
              const isOpen = open === r.id
              const v = shown(r)
              const lang = (l: 'en' | 'zh') => (l === 'en' ? 'en' : 'zh-Hant')
              return (
                <li key={r.id} className={isOpen ? 'open' : ''}>
                  <button className="cq-news-row" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : r.id)}>
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
                    <span className="cq-news-meta">
                      {r.source_name}・{timeLabel(r, g.day)}
                      <ChevronDown size={15} className={isOpen ? 'flip' : ''} aria-hidden="true" />
                    </span>
                  </button>
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
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      {rows.length > 0 && more !== 'done' && (
        <button className="cq-news-more" onClick={loadMore} disabled={more === 'loading'}>
          {more === 'loading' ? '讀取中…' : '更多'}
        </button>
      )}
    </main>
  )
}
