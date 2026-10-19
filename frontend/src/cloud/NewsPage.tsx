// 「新聞」分頁（T-400／D-200／SPEC-017 §7）：新聞流，國內外財經新聞由新到舊。
// 第一版只有原文；翻譯（「譯」）、關聯的美股／台股／族群、關係圖在後面的任務接上。
// 地鐵告示風格：每則＝國際／台灣色塊、標題、來源與時間；點一下就地展開摘要與原文連結。
import { ChevronDown, ExternalLink, Globe, MapPin, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { fetchNewsHealth, fetchNewsItems } from './api'
import {
  KIND_LABEL,
  NEWS_PAGE,
  appendPage,
  dayTitle,
  groupByDay,
  healthLine,
  nextUntil,
  taipeiDay,
  timeLabel,
  type NewsHealth,
  type NewsKind,
  type NewsRow,
} from './news'

type Filter = 'all' | NewsKind

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'intl', label: '國際' },
  { key: 'tw', label: '台灣' },
]

export function NewsPage({ refresh }: { refresh: number }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [rows, setRows] = useState<NewsRow[]>([])
  const [health, setHealth] = useState<NewsHealth | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [more, setMore] = useState<'idle' | 'loading' | 'done'>('idle')
  const [open, setOpen] = useState<string | null>(null)
  const [now, setNow] = useState(() => new Date())

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

      {err && <div className="cq-err">{err}</div>}
      {loading && rows.length === 0 && !err && <p className="cq-war-mute cq-news-empty">讀取中…</p>}
      {!loading && !err && rows.length === 0 && <p className="cq-war-mute cq-news-empty">這段時間還沒有新聞。</p>}

      {groups.map((g) => (
        <section key={g.day} className="cq-news-day">
          <h2>{dayTitle(g.day, today)}</h2>
          <ul>
            {g.rows.map((r) => {
              const isOpen = open === r.id
              return (
                <li key={r.id} className={isOpen ? 'open' : ''}>
                  <button className="cq-news-row" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : r.id)}>
                    <span className={`cq-news-kind ${r.kind ?? 'tw'}`}>
                      {r.kind === 'intl' ? <Globe size={13} aria-hidden="true" /> : <MapPin size={13} aria-hidden="true" />}
                      {r.kind ? KIND_LABEL[r.kind] : '其他'}
                    </span>
                    <span className="cq-news-title" lang={r.lang === 'en' ? 'en' : 'zh-Hant'}>
                      {r.title}
                    </span>
                    <span className="cq-news-meta">
                      {r.source_name}・{timeLabel(r, g.day)}
                      <ChevronDown size={15} className={isOpen ? 'flip' : ''} aria-hidden="true" />
                    </span>
                  </button>
                  {isOpen && (
                    <div className="cq-news-body">
                      {r.summary && <p lang={r.lang === 'en' ? 'en' : 'zh-Hant'}>{r.summary}</p>}
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
