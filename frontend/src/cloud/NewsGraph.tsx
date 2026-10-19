// 新聞關係圖＝心智圖（使用者原話「心智爆炸圖」；2026-10-19 放射圖、HUD、Gemini 風、3D、地鐵路線圖都被退回後「你幫我想一個」）。
// 新聞在正中間，分支像扇子往外張開：一度、二度、三度一層一層往外，字直接寫在分支末端、底下一條類別色的底線——沒有點、沒有球、
// 沒有光暈。分支是由粗到細的彩帶，顏色＝到站的類別，越相關越粗，AI 草擬的畫虛線。手機只往右長，每一度一欄、一直往右延伸
// （使用者 2026-10-19：「這個畫面還不錯，就保留這個吧，只是要一直往右邊延伸才對」），超出螢幕就左右、上下捲；桌機左右都長。
// 點一個名字：亮出它跟新聞的路徑（其他變淡）、底部出現「看個股」；二度點了會展開它的三度，右上角可一次全部展開。
import { CandlestickChart, ChevronLeft, ChevronsDownUp, ChevronsUpDown, Newspaper, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { CLASS_LABEL, classOf, mindBranches, mindLayout, pathTo, topTw, viaText, type GClass, type GNode, type MindOpts, type NewsGraph as Graph } from './news-graph'

/** 手機（窄）：只往右長、每一度一欄（三度不再縮排在二度底下），比螢幕寬就往右捲；桌機：左右都長 */
const PHONE: MindOpts = { rowH: { 1: 42, 2: 44, 3: 40 }, slotW: { 1: 98, 2: 104, 3: 108 }, gap: 26, indent3: null, twoSided: false, centerW: 104, centerH: 66, pad: 12 }
const DESK: MindOpts = { rowH: { 1: 44, 2: 44, 3: 40 }, slotW: { 1: 112, 2: 116, 3: 128 }, gap: 30, indent3: null, twoSided: true, centerW: 150, centerH: 72, pad: 16 }
const DESK_MIN_W = 900

const chgText = (c: number | undefined) => (c === undefined ? '' : `${c > 0 ? '▲' : c < 0 ? '▼' : ''}${Math.abs(c).toFixed(2)}%`)

export function NewsGraph({ graph, onClose, onStock }: { graph: Graph; onClose: () => void; onStock: (sid: string, name: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [opened, setOpened] = useState<Set<string>>(new Set())
  const [viewW, setViewW] = useState(390)
  const map = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = map.current
    if (!el) return
    const fit = () => setViewW(el.clientWidth)
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const opts = viewW >= DESK_MIN_W ? DESK : PHONE
  const lay = useMemo(() => mindLayout(graph.nodes, opened, opts, viewW), [graph, opened, opts, viewW])
  const branches = useMemo(() => mindBranches(lay, opts), [lay, opts])
  const onPath = useMemo(() => new Set(selected ? pathTo(graph.nodes, selected).map((n) => n.id) : []), [graph, selected])
  // 有三度可展開的二度
  const expandable = useMemo(() => graph.nodes.filter((n) => n.degree === 2 && graph.nodes.some((x) => x.parent === n.id)).map((n) => n.id), [graph])
  const allOpen = expandable.every((id) => opened.has(id))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 從底部面板點的名字可能在畫面外：捲到看得到
  useEffect(() => {
    if (!selected || !map.current) return
    map.current.querySelector<HTMLElement>(`[data-id="${CSS.escape(selected)}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' })
  }, [selected])

  const pick = (n: GNode) => {
    setSelected((cur) => (cur === n.id ? null : n.id))
    if (n.degree < 3) setOpened((o) => new Set(o).add(n.id))
  }
  const toggleAll = () => setOpened(allOpen ? new Set() : new Set(expandable))

  const sel = selected ? graph.nodes.find((n) => n.id === selected) ?? null : null
  const chain = sel ? pathTo(graph.nodes, sel.id) : []
  const counts = [1, 2, 3].map((d) => graph.nodes.filter((n) => n.degree === d).length)
  // 進場順序：一層一層往外、同層由上到下
  const order = new Map(lay.items.map((it, i) => [it.n.id, (it.depth - 1) * 160 + i * 18]))

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
        <span className="cq-ng-key line" title="分支越粗＝跟這則新聞越相關">
          粗＝較相關
        </span>
        <span className="cq-ng-key draft" title="AI 依公開資訊草擬、還沒經過核可的關係">
          虛線＝草擬
        </span>
      </div>

      <div className={selected ? 'cq-ng-map focus' : 'cq-ng-map'} ref={map} onClick={(e) => e.target === e.currentTarget && setSelected(null)}>
        <div className="cq-ng-canvas" style={{ width: lay.width, height: lay.height }} onClick={(e) => e.target === e.currentTarget && setSelected(null)}>
          <svg className="cq-ng-branches" width={lay.width} height={lay.height} aria-hidden="true">
            {branches.map((b) =>
              b.draft ? (
                <path key={b.to} className={['br', 'draft', b.cls, onPath.has(b.to) ? 'hot' : ''].join(' ')} d={b.d} strokeWidth={b.w} style={{ ['--d' as string]: order.get(b.to) ?? 0 }} />
              ) : (
                <path key={b.to} className={['br', b.cls, onPath.has(b.to) ? 'hot' : ''].join(' ')} d={b.d} style={{ ['--d' as string]: order.get(b.to) ?? 0 }} />
              ),
            )}
          </svg>
          <div className="cq-ng-center" style={{ left: lay.cx - opts.centerW / 2, top: lay.cy - opts.centerH / 2, width: opts.centerW, height: opts.centerH }}>
            <Newspaper size={18} aria-hidden="true" />
            <b>這則新聞</b>
            <small>{graph.item.source_name}</small>
          </div>
          {lay.items.map((it) => {
            const n = it.n
            const c = it.cls
            return (
              <button
                key={n.id}
                data-id={n.id}
                className={['cq-ng-node', `d${it.depth}`, c, it.side === 1 ? 'r' : 'l', onPath.has(n.id) ? 'hot' : '', selected === n.id ? 'on' : ''].join(' ')}
                style={{ left: it.x, top: it.top, width: it.w, height: it.h, ['--d' as string]: order.get(n.id) ?? 0 }}
                onClick={() => pick(n)}
                aria-label={`${n.label}，${CLASS_LABEL[c]}，${n.degree} 度，${viaText(n)}${n.draft ? '，AI 草擬待核可' : ''}`}
                aria-pressed={selected === n.id}
              >
                <span className="name">
                  {n.label}
                  {it.depth === 1 && n.chg !== undefined && <em className={n.chg >= 0 ? 'up' : 'down'}>{chgText(n.chg)}</em>}
                </span>
                <span className="sub">
                  <span>{it.depth === 1 ? n.sub : viaText(n)}</span>
                  {it.more > 0 && <i className="more">＋{it.more}</i>}
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
