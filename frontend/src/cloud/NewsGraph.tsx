// 新聞關係圖（「心智爆炸圖」，使用者 2026-10-19：「有那種心智爆炸圖的方式嗎」；版面與顏色照 dataviz 方法：放射狀樹、
// 三類顏色＋圖示＋文字、線灰色越粗越相關、AI 草擬的線畫虛線、點選亮路徑、另有清單看法）。
// 全螢幕：打開時從新聞往外一圈一圈炸開；拖曳平移、雙指或滾輪縮放；點一個點亮出它跟新聞怎麼連上的，再點「看個股」。
// 外觀照使用者給的 Gemini 截圖（2026-10-19）：純黑底、底部紫藍點陣漸層、乾淨細線、膠囊按鈕，只有圓心一圈柔光。
import { CandlestickChart, ChevronLeft, List, Network, Newspaper, RotateCcw, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent, type WheelEvent as RWheelEvent } from 'react'
import { CLASS_LABEL, classOf, edgePath, hiddenKids, pathTo, radialLayout, showLabel, strokeOf, tableRows, topTw, viaText, type GClass, type GNode, type NewsGraph as Graph, type Placed } from './news-graph'

const RADII: [number, number, number] = [72, 128, 172]
const NODE_R: Record<number, number> = { 1: 15, 2: 11, 3: 6 }
const SCALE_MIN = 0.6
const SCALE_MAX = 3

const chgText = (c: number | undefined) => (c === undefined ? '' : `${c > 0 ? '▲' : c < 0 ? '▼' : ''}${Math.abs(c).toFixed(2)}%`)

export function NewsGraph({ graph, onClose, onStock }: { graph: Graph; onClose: () => void; onStock: (sid: string, name: string) => void }) {
  const [size, setSize] = useState({ w: 390, h: 560 })
  // 直的螢幕把圈拉成直的橢圓（上下空間才用得到）；橫的螢幕維持正圓
  const aspect = Math.max(1, Math.min(1.45, (size.h - 60) / size.w))
  const placed = useMemo(() => radialLayout(graph.nodes, RADII, aspect), [graph, aspect])
  const by = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed])
  const [selected, setSelected] = useState<string | null>(null)
  const [opened, setOpened] = useState<Set<string>>(new Set())
  const [view, setView] = useState<'graph' | 'list'>('graph')
  const [boom, setBoom] = useState(false)
  const [tf, setTf] = useState({ s: 1, x: 0, y: 0 })
  const box = useRef<HTMLDivElement>(null)

  // 量畫布大小、算一開始的縮放（三圈的點都放得下）
  useEffect(() => {
    const el = box.current
    if (!el) return
    const fit = () => {
      const w = el.clientWidth
      const h = el.clientHeight
      setSize({ w, h })
      const a = Math.max(1, Math.min(1.45, (h - 60) / w))
      // 手機約 1 倍（字維持 13px 以上）、大螢幕最多 2.2 倍
      const s = Math.max(0.9, Math.min(2.2, w / (2 * (RADII[2] + 22)), h / (2 * (RADII[2] * a + 40))))
      setTf({ s, x: 0, y: 0 })
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [view])

  // 打開時炸開（下一個畫格才加 class，才有過場）
  useEffect(() => {
    const id = requestAnimationFrame(() => setBoom(true))
    return () => cancelAnimationFrame(id)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const onPath = useMemo(() => new Set(selected ? pathTo(graph.nodes, selected).map((n) => n.id) : []), [graph, selected])
  const pick = (n: GNode) => {
    setSelected((cur) => (cur === n.id ? null : n.id))
    if (n.degree < 3) setOpened((o) => new Set(o).add(n.id))
  }

  // ── 平移與縮放（一指拖、兩指捏、滾輪）─────────────────────────────
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const pinch = useRef<{ d: number; s: number } | null>(null)
  const down = (e: RPointerEvent) => {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y), s: tf.s }
    }
  }
  const move = (e: RPointerEvent) => {
    const prev = pointers.current.get(e.pointerId)
    if (!prev) return
    const cur = { x: e.clientX, y: e.clientY }
    pointers.current.set(e.pointerId, cur)
    if (pointers.current.size === 2 && pinch.current) {
      const [a, b] = [...pointers.current.values()]
      const s = Math.max(SCALE_MIN, Math.min(SCALE_MAX, (pinch.current.s * Math.hypot(a.x - b.x, a.y - b.y)) / pinch.current.d))
      setTf((t) => ({ ...t, s }))
    } else if (pointers.current.size === 1 && e.buttons) {
      setTf((t) => ({ ...t, x: t.x + cur.x - prev.x, y: t.y + cur.y - prev.y }))
    }
  }
  const up = (e: RPointerEvent) => {
    pointers.current.delete(e.pointerId)
    if (pointers.current.size < 2) pinch.current = null
  }
  const wheel = (e: RWheelEvent) => {
    const s = Math.max(SCALE_MIN, Math.min(SCALE_MAX, tf.s * Math.exp(-e.deltaY * 0.0015)))
    setTf((t) => ({ ...t, s }))
  }

  const sel = selected ? by.get(selected) ?? null : null
  const chain = sel ? pathTo(graph.nodes, sel.id) : []
  const counts = [1, 2, 3].map((d) => graph.nodes.filter((n) => n.degree === d).length)
  const center = { x: 0, y: 0 }

  return (
    <div className={view === 'list' ? 'cq-ng is-list' : 'cq-ng'} role="dialog" aria-modal="true" aria-label="新聞關係圖">
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
        <button className="cq-ng-icon" onClick={() => setView(view === 'graph' ? 'list' : 'graph')} aria-label={view === 'graph' ? '看清單' : '看關係圖'}>
          {view === 'graph' ? <List size={20} /> : <Network size={20} />}
        </button>
      </header>

      <div className="cq-ng-legend" aria-hidden={view === 'list'}>
        {(['abroad', 'tw', 'cluster'] as GClass[]).map((c) => (
          <span key={c} className={`cq-ng-key ${c}`}>
            <i aria-hidden="true" />
            {CLASS_LABEL[c]}
          </span>
        ))}
        <span className="cq-ng-key line" title="線越粗＝跟這則新聞越相關">粗＝較相關</span>
        <span className="cq-ng-key draft" title="AI 依公開資訊草擬、還沒經過核可的關係">虛線＝草擬</span>
      </div>

      {view === 'graph' ? (
        <div
          ref={box}
          className={boom ? 'cq-ng-stage boom' : 'cq-ng-stage'}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
          onWheel={wheel}
          onClick={(e) => e.target === e.currentTarget && setSelected(null)}
        >
          <svg width={size.w} height={size.h} className={selected ? 'focus' : ''} onClick={(e) => e.target === e.currentTarget && setSelected(null)}>
            <g transform={`translate(${size.w / 2 + tf.x},${size.h / 2 + tf.y}) scale(${tf.s})`}>
              <defs>
                {/* 圓心細環的藍紫粉漸層，與全圖唯一一圈柔光 */}
                <linearGradient id="ng-accent" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="#4f8cff" />
                  <stop offset="0.55" stopColor="#9b72f2" />
                  <stop offset="1" stopColor="#d96570" />
                </linearGradient>
                <filter id="ng-soft" x="-100%" y="-100%" width="300%" height="300%">
                  <feGaussianBlur stdDeviation="9" />
                </filter>
              </defs>
              {RADII.map((r) => (
                <ellipse key={r} className="cq-ng-ring" rx={r} ry={r * aspect} />
              ))}
              {placed.map((p) => {
                const from = p.parent ? by.get(p.parent) ?? center : center
                const d = edgePath(from, p)
                return (
                  <g key={`e-${p.id}`} className={['cq-ng-link', `d${p.degree}`, onPath.has(p.id) ? 'hot' : ''].join(' ')}>
                    <path className={p.draft ? 'cq-ng-edge draft' : 'cq-ng-edge'} d={d} strokeWidth={strokeOf(p.score)} pathLength={p.draft ? undefined : 1} />
                    {/* 選到的路徑上有光點從新聞往外流 */}
                    {onPath.has(p.id) && <path className="cq-ng-flow" d={d} />}
                  </g>
                )
              })}
              <g className="cq-ng-center" onClick={() => setSelected(null)}>
                <circle className="halo" r={30} filter="url(#ng-soft)" />
                <circle className="shock" r={24} />
                <circle className="core" r={24} />
                <Newspaper x={-10} y={-10} width={20} height={20} strokeWidth={1.6} className="glyph" />
              </g>
              {placed.map((p) => (
                <Node key={p.id} p={p} label={showLabel(p, opened, selected)} more={hiddenKids(p, graph.nodes, opened)} hot={onPath.has(p.id)} on={selected === p.id} onPick={() => pick(p)} />
              ))}
            </g>
          </svg>
          <div className="cq-ng-zoom">
            <button onClick={() => setTf((t) => ({ ...t, s: Math.min(SCALE_MAX, t.s * 1.25) }))} aria-label="放大">
              ＋
            </button>
            <button onClick={() => setTf((t) => ({ ...t, s: Math.max(SCALE_MIN, t.s / 1.25) }))} aria-label="縮小">
              －
            </button>
            <button
              onClick={() => {
                setTf((t) => ({ ...t, x: 0, y: 0 }))
                setSelected(null)
              }}
              aria-label="回到中間"
            >
              <RotateCcw size={16} />
            </button>
          </div>
        </div>
      ) : (
        <ul className="cq-ng-list">
          {tableRows(graph.nodes).map((n) => {
            const c = classOf(n.type)
            return (
              <li key={n.id}>
                <span className="deg">{n.degree} 度</span>
                <span className={`dot ${c}`} aria-hidden="true" />
                <span className="name">
                  <b>{n.label}</b>
                  <small>
                    {CLASS_LABEL[c]}・{n.sub}
                    {n.chg !== undefined && <em className={n.chg >= 0 ? 'up' : 'down'}> {chgText(n.chg)}</em>}
                  </small>
                </span>
                <span className="via">
                  {viaText(n)}
                  {n.draft && <i>草擬</i>}
                </span>
                {n.type === 'tw' ? (
                  <button onClick={() => onStock(n.sub, n.label)} aria-label={`看 ${n.label} 個股頁`}>
                    ›
                  </button>
                ) : (
                  <span />
                )}
              </li>
            )
          })}
        </ul>
      )}

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
                {sel.chg !== undefined && <em className={sel.chg >= 0 ? 'up' : 'down'}> {sel.sub} {chgText(sel.chg)}</em>}
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

function Node({ p, label, more, hot, on, onPick }: { p: Placed; label: boolean; more: number; hot: boolean; on: boolean; onPick: () => void }) {
  const c = classOf(p.type)
  const r = NODE_R[Math.min(p.degree, 3)]
  // 下半圈名字放點下面、上半圈放上面（離圓心越遠越不擠）；一度的美股多一行昨晚漲跌（代號在底部面板，圖上放會壓到旁邊的點）
  const below = p.y >= -4
  const tag = p.degree === 1 && p.type === 'us' && p.chg !== undefined
  const nameY = below ? r + 17 : -r - (tag ? 25 : 10)
  const tagY = below ? r + 32 : -r - 10
  return (
    <g
      className={['cq-ng-node', c, `d${p.degree}`, hot ? 'hot' : '', on ? 'on' : ''].join(' ')}
      style={{ ['--x' as string]: `${p.x}px`, ['--y' as string]: `${p.y}px`, ['--delay' as string]: `${(p.degree - 1) * 170 + Math.round((p.angle + Math.PI) * 18)}ms` }}
      tabIndex={0}
      role="button"
      aria-label={`${p.label}，${CLASS_LABEL[c]}，${p.degree} 度，${viaText(p)}${p.draft ? '，AI 草擬待核可' : ''}`}
      onClick={(e) => {
        e.stopPropagation()
        onPick()
      }}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onPick()}
    >
      <circle className="hit" r={Math.max(r + 8, 14)} />
      {on && <circle className="sel" r={r + 5} />}
      <circle className="dot" r={r} />
      {/* 「+N」放在離圓心遠的那一側：靠內側會壓到一度美股的代號與漲跌 */}
      {more > 0 && (
        <text className="more" x={p.x < 0 ? -(r + 5) : r + 5} y={4} textAnchor={p.x < 0 ? 'end' : 'start'}>
          +{more}
        </text>
      )}
      {label && (
        <text className="lbl" y={nameY} textAnchor="middle">
          {p.label}
        </text>
      )}
      {label && tag && p.chg !== undefined && (
        <text className={`lbl tag ${p.chg >= 0 ? 'up' : 'down'}`} y={tagY} textAnchor="middle">
          {chgText(p.chg)}
        </text>
      )}
    </g>
  )
}
