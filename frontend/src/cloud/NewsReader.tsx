// 新聞閱讀卡（使用者 2026-10-21：「展開可以用其他方式呈現嗎？會亂掉 很難閱讀」）：點一則新聞不再在清單裡往下展開，
// 改成蓋在畫面上的閱讀卡（手機從底部升起、桌機置中），清單留在原位、關掉回到原處。
// 由上到下：類型與出處 → 標題 → 提到的股票 → 摘要 → 看原文／關係圖 → 同一件事（幾家報導）→ 來龍去脈 → 相似新聞（先列 4 則）。
// 同標題的只留一則；跟這則同標題的不再列一次。手機的返回手勢＝關閉（不會離開網站）。
import { ExternalLink, Globe, Layers, MapPin, Network, Route, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchNewsThread, fetchSimilarNews } from './api'
import {
  KIND_LABEL,
  NEWS_TYPE_LABEL,
  READER_RELATED,
  mentionChips,
  shortWhen,
  shown,
  similarNote,
  splitSimilar,
  stepDay,
  taipeiDay,
  threadShown,
  type NewsRow,
  type NewsThread,
  type SimilarNews,
} from './news'
import { classOf } from './news-graph'

export function NewsReader({
  row,
  onClose,
  onGraph,
  onStock,
}: {
  row: NewsRow
  onClose: () => void
  onGraph: (id: string) => void
  onStock: (sid: string, name: string) => void
}) {
  const v = shown(row)
  const today = taipeiDay(new Date().toISOString())
  const lang = v.lang === 'en' ? 'en' : 'zh-Hant'
  const ents = mentionChips(row, 12)
  const [sim, setSim] = useState<SimilarNews | null>(null)
  const [simErr, setSimErr] = useState(false)
  const [thread, setThread] = useState<NewsThread | null>(null)
  const [moreRelated, setMoreRelated] = useState(false)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  // 返回手勢／返回鍵＝關閉閱讀卡：打開時推一筆歷史，按返回就收到 popstate
  useEffect(() => {
    window.history.pushState({ newsReader: row.id }, '')
    const onPop = () => closeRef.current()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    window.addEventListener('popstate', onPop)
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('popstate', onPop)
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.id])

  useEffect(() => {
    let stale = false
    fetchSimilarNews(row.id)
      .then((d) => !stale && setSim(d))
      .catch(() => !stale && setSimErr(true))
    fetchNewsThread(row.id)
      .then((t) => !stale && setThread(t))
      .catch(() => !stale && setThread(null)) // 來龍去脈是附帶的：讀不到就不顯示
    return () => {
      stale = true
    }
  }, [row.id])

  /** 自己按的關閉：把打開時推的那筆歷史退掉（popstate 會再呼叫 onClose） */
  function close() {
    if ((window.history.state as { newsReader?: string } | null)?.newsReader === row.id) window.history.back()
    else closeRef.current()
  }

  const threadTitles = threadShown(thread) ? thread!.steps.map((s) => s.title) : []
  const split = sim && sim.items.length > 0 ? splitSimilar(v.title, sim.items, threadTitles) : null
  const note = simErr ? null : sim ? similarNote(sim) : '讀取中…'
  const related = split ? (moreRelated ? split.related : split.related.slice(0, READER_RELATED)) : []

  return (
    <div className="cq-sheet-backdrop cq-reader-backdrop" onClick={close}>
      <article className="cq-sheet cq-reader" role="dialog" aria-modal="true" aria-label="新聞" onClick={(e) => e.stopPropagation()}>
        <header className="cq-reader-top">
          <span className={`cq-news-kind ${row.kind ?? 'tw'}`}>
            {row.kind === 'intl' ? <Globe size={13} aria-hidden="true" /> : <MapPin size={13} aria-hidden="true" />}
            {row.kind ? KIND_LABEL[row.kind] : '其他'}
          </span>
          {row.type && row.type !== 'other' && <em className={`cq-news-type ${row.type}`}>{NEWS_TYPE_LABEL[row.type]}</em>}
          <span className="cq-reader-src">
            {row.source_name}・{shortWhen(row.first_seen_at, today)}
            {v.tag && <em className={`cq-news-tag ${v.tag === '譯' ? 'tr' : 'raw'}`}>{v.tag}</em>}
          </span>
          <button className="cq-icon-btn" aria-label="關閉" onClick={close}>
            <X size={20} aria-hidden="true" />
          </button>
        </header>

        <h2 className="cq-reader-title" lang={lang}>
          {v.title}
        </h2>

        {ents.chips.length > 0 && (
          <div className="cq-reader-ents">
            {ents.chips.map((m) =>
              m.type === 'tw' ? (
                <button
                  key={m.id}
                  className={`cq-news-ent ${classOf(m.type)} go`}
                  onClick={() => {
                    close()
                    onStock(m.id.slice(3), m.label)
                  }}
                  title="開個股頁"
                >
                  {m.label}
                </button>
              ) : (
                <span key={m.id} className={`cq-news-ent ${classOf(m.type)}`}>
                  {m.label}
                </span>
              ),
            )}
          </div>
        )}

        {v.summary && (
          <p className="cq-reader-summary" lang={lang}>
            {v.summary}
          </p>
        )}
        {v.original && (
          <details className="cq-reader-orig" lang="en">
            <summary>英文原文</summary>
            <b>{v.original.title}</b>
            {v.original.summary && <p>{v.original.summary}</p>}
          </details>
        )}

        <div className="cq-reader-actions">
          <a href={row.url} target="_blank" rel="noopener noreferrer">
            看原文<ExternalLink size={15} aria-hidden="true" />
          </a>
          {ents.chips.length > 0 && (
            <button
              onClick={() => {
                close()
                onGraph(row.id)
              }}
            >
              <Network size={15} aria-hidden="true" />
              關係圖
            </button>
          )}
        </div>

        {split && split.sameEvent.length > 0 && (
          <section className="cq-reader-sec">
            <h3>
              <Layers size={15} aria-hidden="true" />
              同一件事<small>還有 {split.sameEvent.length} 家報導</small>
            </h3>
            <ul className="cq-reader-list">
              {split.sameEvent.map((it) => (
                <li key={it.id}>
                  <a href={it.url} target="_blank" rel="noopener noreferrer">
                    <b>{it.source_name}</b>
                    <small>{shortWhen(it.first_seen_at, today)}</small>
                    {!it.sameTitle && <span>{it.title_zh ?? it.title}</span>}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}

        {threadShown(thread) && (
          <section className="cq-reader-sec cq-thread">
            <h3>
              <Route size={15} aria-hidden="true" />
              來龍去脈<small>{thread!.steps.length} 步</small>
            </h3>
            <ol>
              {thread!.steps.map((s) => (
                <li key={s.cluster_id} className={s.current ? 'now' : ''}>
                  <i>{stepDay(s)}</i>
                  <div>
                    <a href={s.url} target="_blank" rel="noopener noreferrer">
                      {s.title}
                    </a>
                    <small>
                      {s.n} 則・{s.outlets.join('・')}
                      {s.current && <b>這一則</b>}
                    </small>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        )}

        {(note || related.length > 0) && (
          <section className="cq-reader-sec">
            <h3>
              <Sparkles size={15} aria-hidden="true" />
              相似新聞
            </h3>
            {note ? (
              <p className="cq-sim-note">{note}</p>
            ) : (
              <>
                <ul className="cq-reader-list">
                  {related.map((it) => (
                    <li key={it.id}>
                      <a href={it.url} target="_blank" rel="noopener noreferrer">
                        <span>{it.title_zh ?? it.title}</span>
                        <small>
                          <i className={`cq-reader-dot ${it.kind ?? 'tw'}`} aria-hidden="true" />
                          {it.source_name}・{shortWhen(it.first_seen_at, today)}
                        </small>
                      </a>
                    </li>
                  ))}
                </ul>
                {!moreRelated && split!.related.length > READER_RELATED && (
                  <button className="cq-reader-more" onClick={() => setMoreRelated(true)}>
                    再看 {split!.related.length - READER_RELATED} 則
                  </button>
                )}
              </>
            )}
          </section>
        )}
      </article>
    </div>
  )
}
