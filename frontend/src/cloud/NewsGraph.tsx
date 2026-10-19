// 新聞關係圖＝地鐵路線圖（使用者 2026-10-19 在三個方向裡選的，呼應「像地鐵告示一樣」；之前的放射圖、HUD、Gemini 風、3D 都被退回）。
// 全螢幕、往下捲：起點站是這則新聞，主幹往下一站一站是新聞直接提到的公司（一度）；每一站往右岔出台灣供應商或同業（二度），
// 點二度的站再岔出同族群的其他台股（三度）。站＝白圓＋類別色邊，線＝類別色、越粗越相關、AI 草擬畫虛線。
// 點一站：亮出它跟新聞的路徑（其他變淡）、底部出現「看個股」。純深色底，沒有光暈、漸層、透明。
import { CandlestickChart, ChevronLeft, ChevronsDownUp, ChevronsUpDown, Newspaper, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { CLASS_LABEL, classOf, pathTo, routeLines, routeRows, topTw, viaText, type Depth, type GClass, type GNode, type NewsGraph as Graph, type RowH } from './news-graph'

/** 三欄的 x（一度在主幹、二度、三度各往右一欄）、線圖的寬 */
const COLS: [number, number, number] = [30, 74, 118]
const LINES_W = 150
const ROW_H: RowH = { 1: 56, 2: 48, 3: 40 }
const ORIGIN_H = 62
const STATION_R: Record<Depth, number> = { 1: 11, 2: 8, 3: 5 }

const chgText = (c: number | undefined) => (c === undefined ? '' : `${c > 0 ? '▲' : c < 0 ? '▼' : ''}${Math.abs(c).toFixed(2)}%`)

export function NewsGraph({ graph, onClose, onStock }: { graph: Graph; onClose: () => void; onStock: (sid: string, name: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [opened, setOpened] = useState<Set<string>>(new Set())
  const map = useRef<HTMLDivElement>(null)

  const rows = useMemo(() => routeRows(graph.nodes, opened, ROW_H, ORIGIN_H), [graph, opened])
  const segs = useMemo(() => routeLines(rows, COLS, ORIGIN_H / 2), [rows])
  const total = (rows.length ? rows[rows.length - 1].y + rows[rows.length - 1].h : ORIGIN_H) + 12
  const onPath = useMemo(() => new Set(selected ? pathTo(graph.nodes, selected).map((n) => n.id) : []), [graph, selected])
  // 有三度可展開的二度站
  const expandable = useMemo(() => graph.nodes.filter((n) => n.degree === 2 && graph.nodes.some((x) => x.parent === n.id)).map((n) => n.id), [graph])
  const allOpen = expandable.every((id) => opened.has(id))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 從底部面板點的站可能在畫面外：捲到看得到
  useEffect(() => {
    if (!selected || !map.current) return
    const el = map.current.querySelector<HTMLElement>(`[data-id="${CSS.escape(selected)}"]`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selected])

  const pick = (n: GNode) => {
    setSelected((cur) => (cur === n.id ? null : n.id))
    if (n.degree < 3) setOpened((o) => new Set(o).add(n.id))
  }
  const toggleAll = () => setOpened(allOpen ? new Set() : new Set(expandable))

  const sel = selected ? graph.nodes.find((n) => n.id === selected) ?? null : null
  const chain = sel ? pathTo(graph.nodes, sel.id) : []
  const counts = [1, 2, 3].map((d) => graph.nodes.filter((n) => n.degree === d).length)

  return (
    <div className="cq-ng" role="dialog" aria-modal="true" aria-label="新聞關係圖">
      <header className="cq-ng-head">
        <button className="cq-ng-icon" onClick={onClose} aria-label="返回">
          <ChevronLeft size={22} />
        </button>
        <div className="cq-ng-title">
          <b>{graph.item.title_zh ?? graph.item.title}</b>
          <small>
            {graph.item.source_name}・一度 {counts[0]}・二度 {counts[1]}・三度 {counts[2]}
          </small>
        </div>
        {expandable.length > 0 && (
          <button className="cq-ng-icon" onClick={toggleAll} aria-label={allOpen ? '全部收合' : '全部展開'} title={allOpen ? '收起所有三度' : '展開所有三度'}>
            {allOpen ? <ChevronsDownUp size={20} /> : <ChevronsUpDown size={20} />}
          </button>
        )}
      </header>

      <div className="cq-ng-legend">
        {(['abroad', 'tw', 'cluster'] as GClass[]).map((c) => (
          <span key={c} className={`cq-ng-key ${c}`}>
            <i aria-hidden="true" />
            {CLASS_LABEL[c]}
          </span>
        ))}
        <span className="cq-ng-key line" title="線越粗＝跟這則新聞越相關">
          粗＝較相關
        </span>
        <span className="cq-ng-key draft" title="AI 依公開資訊草擬、還沒經過核可的關係">
          虛線＝草擬
        </span>
      </div>

      <div className={selected ? 'cq-ng-map focus' : 'cq-ng-map'} ref={map} onClick={(e) => e.target === e.currentTarget && setSelected(null)}>
        <div className="cq-ng-route" style={{ height: total }}>
          <svg className="cq-ng-lines" width={LINES_W} height={total} aria-hidden="true">
            {segs.map((s, i) => (
              <path
                key={s.to}
                className={['seg', s.cls, s.draft ? 'draft' : '', onPath.has(s.to) ? 'hot' : ''].join(' ')}
                d={s.d}
                strokeWidth={s.w}
                pathLength={s.draft ? undefined : 1}
                style={{ ['--i' as string]: i }}
              />
            ))}
            <g className="origin" transform={`translate(${COLS[0]},${ORIGIN_H / 2})`}>
              <circle r={14} />
              <Newspaper x={-8} y={-8} width={16} height={16} strokeWidth={2} />
            </g>
            {rows.map((r, i) => (
              <g
                key={r.n.id}
                className={['st', `d${r.depth}`, r.cls, onPath.has(r.n.id) ? 'hot' : '', selected === r.n.id ? 'on' : ''].join(' ')}
                transform={`translate(${COLS[r.depth - 1]},${r.cy})`}
                style={{ ['--i' as string]: i }}
              >
                {selected === r.n.id && <circle className="sel" r={STATION_R[r.depth] + 5} />}
                <circle className="dot" r={STATION_R[r.depth]} />
              </g>
            ))}
          </svg>
          <div className="cq-ng-origin" style={{ height: ORIGIN_H, paddingLeft: COLS[0] + 26 }}>
            <b>這則新聞</b>
            <small>{graph.item.source_name}</small>
          </div>
          {rows.map((r, i) => {
            const n = r.n
            const c = classOf(n.type)
            return (
              <button
                key={n.id}
                data-id={n.id}
                className={['cq-ng-row', `d${r.depth}`, c, onPath.has(n.id) ? 'hot' : '', selected === n.id ? 'on' : ''].join(' ')}
                style={{ top: r.y, height: r.h, paddingLeft: COLS[r.depth - 1] + 26, ['--i' as string]: i }}
                onClick={() => pick(n)}
                aria-label={`${n.label}，${CLASS_LABEL[c]}，${n.degree} 度，${viaText(n)}${n.draft ? '，AI 草擬待核可' : ''}`}
                aria-pressed={selected === n.id}
              >
                <span className="name">
                  {n.label}
                  {r.depth === 1 && n.chg !== undefined && <em className={n.chg >= 0 ? 'up' : 'down'}>{chgText(n.chg)}</em>}
                </span>
                <span className="sub">
                  {r.depth === 1 ? n.sub : `${viaText(n)}${n.draft && n.via !== 'mention' ? '・草擬' : ''}`}
                  {r.more > 0 && <i className="more">＋{r.more} 站</i>}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <footer className="cq-ng-sheet" aria-live="polite">
        {sel ? (
          <>
            <ol className="cq-ng-path">
              <li className="news">
                <Newspaper size={14} /> 新聞
              </li>
              {chain.map((n) => (
                <li key={n.id} className={classOf(n.type)}>
                  <small>
                    {viaText(n)}
                    {n.draft && n.via !== 'mention' ? '・草擬' : ''}
                  </small>
                  <b>{n.label}</b>
                </li>
              ))}
            </ol>
            <div className="cq-ng-act">
              <span>
                {sel.degree} 度・關聯 {sel.score.toFixed(2)}
                {sel.chg !== undefined && (
                  <em className={sel.chg >= 0 ? 'up' : 'down'}>
                    {' '}
                    {sel.sub} {chgText(sel.chg)}
                  </em>
                )}
              </span>
              {sel.type === 'tw' && (
                <button onClick={() => onStock(sel.sub, sel.label)}>
                  看 {sel.label} <CandlestickChart size={15} />
                </button>
              )}
              <button className="ghost" onClick={() => setSelected(null)} aria-label="取消選取">
                <X size={16} />
              </button>
            </div>
          </>
        ) : (
          <div className="cq-ng-top">
            <span className="cap">最相關台股</span>
            <div>
              {topTw(graph.nodes).map((n, i) => (
                <button key={n.id} onClick={() => pick(n)}>
                  <i>{i + 1}</i>
                  <b>{n.label}</b>
                  <small>
                    {n.degree} 度・{n.score.toFixed(2)}
                  </small>
                </button>
              ))}
            </div>
          </div>
        )}
      </footer>
    </div>
  )
}
