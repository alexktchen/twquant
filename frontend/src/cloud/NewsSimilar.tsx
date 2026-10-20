// 相似新聞與語意搜尋（T-410／SPEC-018）：新聞服務用 bge-m3 向量找出來的。相似≠因果，只是「內容相近」。
// SimilarNews：展開一則時才向服務要（不展開不花查詢）；同一事件的標「同一事件」、排前面。
// NewsSearchResults：新聞頁搜尋框的結果，越相關越前面。兩者共用同一種列（色塊、標題連原文、出處・時間）。
import { Globe, Layers, MapPin } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchSimilarNews } from './api'
import { KIND_LABEL, shortWhen, similarNote, taipeiDay, type SimilarItem, type SimilarNews as SimilarData } from './news'

function SimilarList({ items }: { items: SimilarItem[] }) {
  const today = taipeiDay(new Date().toISOString())
  return (
    <ul className="cq-sim">
      {items.map((it) => (
        <li key={it.id}>
          {it.same_event ? (
            <span className="cq-sim-tag same" title="不同媒體報同一件事，或同一則被兩個頻道刊出">
              <Layers size={12} aria-hidden="true" />
              同一事件
            </span>
          ) : (
            <span className={`cq-sim-tag ${it.kind ?? 'tw'}`}>
              {it.kind === 'intl' ? <Globe size={12} aria-hidden="true" /> : <MapPin size={12} aria-hidden="true" />}
              {it.kind ? KIND_LABEL[it.kind] : '其他'}
            </span>
          )}
          <div className="cq-sim-body">
            <a href={it.url} target="_blank" rel="noopener noreferrer">
              {it.title_zh ?? it.title}
            </a>
            <small>
              {it.source_name}・{shortWhen(it.first_seen_at, today)}
            </small>
          </div>
        </li>
      ))}
    </ul>
  )
}

export function SimilarNews({ id }: { id: string }) {
  const [data, setData] = useState<SimilarData | null>(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let stale = false
    setData(null)
    setErr(false)
    fetchSimilarNews(id)
      .then((d) => !stale && setData(d))
      .catch(() => !stale && setErr(true))
    return () => {
      stale = true
    }
  }, [id])
  if (err) return null // 相似新聞是附帶的：讀不到就不顯示，不擋這則
  const note = data ? similarNote(data) : '讀取中…'
  return (
    <div className="cq-sim-box">
      <h3>相似新聞</h3>
      {note ? <p className="cq-sim-note">{note}</p> : <SimilarList items={data!.items} />}
    </div>
  )
}

export function NewsSearchResults({ items }: { items: SimilarItem[] }) {
  return items.length > 0 ? <SimilarList items={items} /> : null
}
