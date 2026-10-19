// 個股頁「相關新聞」（反向看圖；使用者 2026-10-20：「那有反向看圖的地方嗎？像是每日推薦股有關的新聞」）。
// 近 7 天連得到這一檔的新聞：直接提到的在前，其次是二度（例：新聞講輝達 → 供應商 → 廣達），每則附上怎麼連過來的；
// 點「關係圖」打開那則的心智圖，直接選到這一檔、亮出路徑。新聞服務沒設定或連不上 → 這一塊不顯示（不擋個股頁）。
import { Network, Newspaper } from 'lucide-react'
import { Suspense, lazy, useEffect, useState } from 'react'
import { fetchNewsGraph, fetchStockNews } from './api'
import type { NewsGraph as NewsGraphData } from './news-graph'
import { STOCK_NEWS_SHOWN, pathText, stockNewsSummary, taipeiHm, taipeiDay, type StockNews as StockNewsData } from './news'
import { Section } from './Section'

const NewsGraph = lazy(() => import('./NewsGraph').then((m) => ({ default: m.NewsGraph })))

export function StockNews({ sid, refresh, onStock }: { sid: string; refresh: number; onStock?: (sid: string, name: string) => void }) {
  const [data, setData] = useState<StockNewsData | null>(null)
  const [all, setAll] = useState(false)
  const [graph, setGraph] = useState<NewsGraphData | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let stale = false
    setData(null)
    setAll(false)
    fetchStockNews(sid)
      .then((d) => !stale && setData(d))
      .catch(() => !stale && setData(null)) // 新聞服務沒設定或連不上：這一塊不顯示，個股頁照常
    return () => {
      stale = true
    }
  }, [sid, refresh])

  if (!data) return null
  const items = all ? data.items : data.items.slice(0, STOCK_NEWS_SHOWN)
  const today = taipeiDay(new Date().toISOString())

  const open = (id: string) => {
    setBusy(id)
    setErr('')
    fetchNewsGraph(id)
      .then(setGraph)
      .catch((e: Error) => setErr(`關係圖讀不到：${e.message}`))
      .finally(() => setBusy(null))
  }

  return (
    <Section icon={Newspaper} title="相關新聞" meta={stockNewsSummary(data)}>
      {data.items.length === 0 ? (
        <p className="cq-sn-empty">近 {data.days} 天沒有提到它、也沒有經過供應商或族群連到它的新聞。</p>
      ) : (
        <ul className="cq-sn">
          {items.map((it) => (
            <li key={it.id}>
              <span className={it.degree === 1 ? 'cq-sn-tag direct' : 'cq-sn-tag'} title={it.degree === 1 ? '新聞直接提到這一檔' : '新聞提到的公司或族群，再經一條線連到這一檔'}>
                {it.degree === 1 ? '直接' : '間接'}
              </span>
              <div className="cq-sn-body">
                <a href={it.url} target="_blank" rel="noopener noreferrer" className="cq-sn-title">
                  {it.title_zh ?? it.title}
                </a>
                <small>
                  {it.source_name}・{taipeiDay(it.first_seen_at) === today ? taipeiHm(it.first_seen_at) : `${taipeiDay(it.first_seen_at).slice(5).replace('-', '/')}`}
                  {it.degree > 1 && <span className={it.draft ? 'cq-sn-path draft' : 'cq-sn-path'}>{pathText(it)}</span>}
                </small>
              </div>
              <button className="cq-sn-graph" onClick={() => open(it.id)} disabled={busy !== null} title="打開這則的關係圖，直接標出這一檔">
                <Network size={14} aria-hidden="true" />
                {busy === it.id ? '讀取中…' : '關係圖'}
              </button>
            </li>
          ))}
        </ul>
      )}
      {data.items.length > STOCK_NEWS_SHOWN && (
        <button className="cq-hc-link cq-sn-more" onClick={() => setAll(!all)}>
          {all ? '收起' : `還有 ${data.items.length - STOCK_NEWS_SHOWN} 則`}
        </button>
      )}
      {err && <p className="cq-war-mute">{err}</p>}
      {graph && (
        <Suspense fallback={null}>
          <NewsGraph
            graph={graph}
            focus={`tw:${sid}`}
            onClose={() => setGraph(null)}
            onStock={(s, name) => {
              setGraph(null)
              onStock?.(s, name)
            }}
          />
        </Suspense>
      )}
    </Section>
  )
}
