// 星雲圖「單獨看」（使用者 2026-10-21：「我可以針對這樣的選擇把它再單獨拎出來閱讀嗎? 可以快速看到關聯」）：
// 點一個亮點 → 檢視框的「單獨看」→ 跟新聞閱讀卡同一種卡（手機從底部升起、桌機置中）。
// 由上到下：這個亮點 → 常一起出現（點一個＝只看同時提到兩個的，再點一次取消；「改看」換成以它為中心）→ 提到它的新聞照主題分組。
// 同一件事只列最新一則、標幾家報；每組先列幾則，其餘按「再看」。手機的返回手勢＝關閉。
// 證據句（T-437；使用者：「如果我想要用在工作上我要怎麼定位那段字？」）：標題裡的名字標色；名字不在標題裡（在摘要、或英文原文對到別名）
// 就在那則底下多一行「比對到的那段字」（命中字前後各 20 字，向新聞服務問 /api/news/evidence）。只有標題與摘要比對過，不會是全文中段。
import { ExternalLink, Layers, Network, X } from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import { fetchNewsEvidence } from './api'
import { EVIDENCE_BATCH, FOCUS_GROUP_ROWS, FOCUS_ROW_HUBS, evidenceNeeded, evidenceParts, hubClass, hubFocus, hubTypeLabel, type GalaxyData, type TextPart } from './news-galaxy'
import { shortWhen, taipeiDay } from './news'

export function GalaxyFocus({
  data,
  hubId,
  onRecenter,
  onClose,
  onStock,
}: {
  data: GalaxyData
  hubId: string
  onRecenter: (id: string) => void
  onClose: () => void
  onStock: (sid: string, name: string) => void
}) {
  const [withId, setWithId] = useState<string | null>(null)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const cardRef = useRef<HTMLElement>(null)
  const today = taipeiDay(new Date().toISOString())
  const f = hubFocus(data, hubId, withId)
  // 證據句：ev＝{新聞 id: {實體 id: 那段字}}；fetched＝每個實體已經問過哪些新聞，換「兩個都提到」時只補問新的
  const [ev, setEv] = useState<Record<string, Record<string, string>>>({})
  const fetched = useRef(new Map<string, Set<string>>())
  const entities = withId ? [hubId, withId] : [hubId]
  const rowIds = f ? f.groups.flatMap((g) => g.rows.map((r) => r.item.id)) : []
  const idsKey = rowIds.join(','), entKey = entities.join(',')
  useEffect(() => {
    let stale = false
    const ents = entKey.split(','), ids = idsKey ? idsKey.split(',') : []
    const need = evidenceNeeded(ids, ents, fetched.current)
    if (!need.length) return
    for (const e of ents) {
      const s = fetched.current.get(e) ?? new Set<string>()
      for (const id of need) s.add(id)
      fetched.current.set(e, s)
    }
    const batches: string[][] = []
    for (let i = 0; i < need.length; i += EVIDENCE_BATCH) batches.push(need.slice(i, i + EVIDENCE_BATCH))
    Promise.all(batches.map((b) => fetchNewsEvidence(b, ents)))
      .then((rs) => {
        if (stale) return
        setEv((prev) => {
          const next = { ...prev }
          for (const r of rs) for (const [id, m] of Object.entries(r.items)) next[id] = { ...next[id], ...m }
          return next
        })
      })
      .catch(() => {
        // 證據句拿不到就不顯示（標題照常），不擋閱讀
      })
    return () => {
      stale = true
    }
  }, [idsKey, entKey])

  // 返回手勢／返回鍵＝關閉：打開時推一筆歷史，按返回就收到 popstate（同新聞閱讀卡）
  useEffect(() => {
    window.history.pushState({ galaxyFocus: true }, '')
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
  }, [])

  /** 自己按的關閉：把打開時推的那筆歷史退掉（popstate 會再呼叫 onClose） */
  function close() {
    if ((window.history.state as { galaxyFocus?: boolean } | null)?.galaxyFocus) window.history.back()
    else closeRef.current()
  }
  function pickWith(id: string) {
    setWithId(withId === id ? null : id)
    setOpen(new Set())
    cardRef.current?.scrollTo({ top: 0 })
  }
  function recenter(id: string) {
    onRecenter(id)
    setWithId(null)
    setOpen(new Set())
    cardRef.current?.scrollTo({ top: 0 })
  }

  if (!f) return null
  const withHub = withId ? data.hubs.find((h) => h.id === withId) : null
  const marks = [f.hub, ...(withHub ? [withHub] : [])].map((h) => ({ label: h.label, cls: hubClass(h.type) }))
  // 普通字用 Fragment 不用 span：標題那個 span 的 CSS（-webkit-box 兩行截斷）會套到裡面的 span，名字就被擠到下一行
  const render = (parts: TextPart[]) => parts.map((p, i) => (p.cls ? <mark key={i} className={`gxf-mk ${p.cls}`}>{p.t}</mark> : <Fragment key={i}>{p.t}</Fragment>))

  return (
    <div className="cq-sheet-backdrop cq-reader-backdrop" onClick={close}>
      <article ref={cardRef} className="cq-sheet cq-reader gxf" role="dialog" aria-modal="true" aria-label={`單獨看 ${f.hub.label}`} onClick={(e) => e.stopPropagation()}>
        <header className="cq-reader-top">
          <span className={`gxf-type ${hubClass(f.hub.type)}`}>
            {hubTypeLabel(f.hub.type)}
            {f.hub.type === 'tw' && f.hub.sub ? ` ${f.hub.sub}` : ''}
          </span>
          <span className="cq-reader-src">近 {data.days} 天</span>
          <button className="cq-icon-btn" aria-label="關閉" onClick={close}>
            <X size={20} aria-hidden="true" />
          </button>
        </header>

        <h2 className="cq-reader-title gxf-title">
          {f.hub.label}
          <small>{f.total} 則</small>
        </h2>
        {f.hub.type === 'tw' && f.hub.sub && (
          <div className="cq-reader-actions">
            <button
              onClick={() => {
                close()
                onStock(f.hub.sub!, f.hub.label)
              }}
            >
              開個股頁
            </button>
          </div>
        )}

        {f.related.length > 0 && (
          <section className="cq-reader-sec">
            <h3>
              <Network size={15} aria-hidden="true" />
              常一起出現<small>點一個只看兩個都提到的</small>
            </h3>
            <div className="gxf-rel">
              {f.related.map((r) => (
                <button
                  key={r.hub.id}
                  className={`cq-news-ent ${hubClass(r.hub.type)}${withId === r.hub.id ? ' on' : ''}`}
                  aria-pressed={withId === r.hub.id}
                  onClick={() => pickWith(r.hub.id)}
                >
                  {r.hub.label}
                  <small>{r.n}</small>
                </button>
              ))}
            </div>
          </section>
        )}

        {withHub && (
          <div className="gxf-with">
            <span>
              <b>{f.hub.label}</b>＋<b>{withHub.label}</b>　{f.shown} 則
            </span>
            <button onClick={() => recenter(withHub.id)}>改看{withHub.label}</button>
            <button className="cq-icon-btn" aria-label="看全部" onClick={() => pickWith(withHub.id)}>
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        )}

        {f.groups.map((g) => {
          const rows = open.has(g.k) ? g.rows : g.rows.slice(0, FOCUS_GROUP_ROWS)
          const count = (rs: typeof g.rows) => rs.reduce((s, r) => s + r.n, 0)
          return (
            <section key={g.k} className="cq-reader-sec">
              <h3>
                <Layers size={15} aria-hidden="true" />
                {g.name}
                <small>{count(g.rows)} 則</small>
              </h3>
              <ul className="cq-reader-list">
                {rows.map((r) => (
                  <li key={r.item.id}>
                    <a href={r.item.url} target="_blank" rel="noopener noreferrer">
                      <span>{render(evidenceParts(r.item.title, marks))}</span>
                      <small>
                        <i className={`cq-reader-dot ${r.item.kind}`} aria-hidden="true" />
                        {shortWhen(r.item.t, today)}・{r.srcs.join('・')}
                        {r.srcs.length > 1 && <b className="gxf-n">{r.srcs.length} 家</b>}
                        {r.item.tr && <em className="cq-news-tag tr">譯</em>}
                        <ExternalLink size={12} aria-hidden="true" />
                      </small>
                    </a>
                    {[f.hub, ...(withHub ? [withHub] : [])]
                      .filter((h) => !r.item.title.includes(h.label) && ev[r.item.id]?.[h.id])
                      .map((h) => (
                        <p key={h.id} className="gxf-ev" title="比對名字時存下的那段字（標題或摘要）">
                          <i className={`gxf-ev-dot ${hubClass(h.type)}`} aria-hidden="true" />
                          …{render(evidenceParts(ev[r.item.id][h.id], marks))}…
                        </p>
                      ))}
                    {r.others.length > 0 && (
                      <div className="gxf-ents">
                        {r.others.slice(0, FOCUS_ROW_HUBS).map((h) => (
                          <button key={h.id} className={`cq-news-ent ${hubClass(h.type)}`} onClick={() => pickWith(h.id)} title={`只看也提到${h.label}的`}>
                            {h.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              {!open.has(g.k) && g.rows.length > FOCUS_GROUP_ROWS && (
                <button className="cq-reader-more" onClick={() => setOpen(new Set([...open, g.k]))}>
                  再看 {count(g.rows.slice(FOCUS_GROUP_ROWS))} 則
                </button>
              )}
            </section>
          )
        })}
      </article>
    </div>
  )
}
