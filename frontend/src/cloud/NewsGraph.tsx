// 新聞關係圖（「心智爆炸圖」，使用者 2026-10-19：「有那種心智爆炸圖的方式嗎」；版面與顏色照 dataviz 方法：放射狀樹、
// 三類顏色＋文字、線越粗越相關、AI 草擬的線畫虛線、點選亮路徑、另有清單看法）。
// 外觀照使用者給的 Gemini 截圖：純黑底、底部紫藍點陣漸層、膠囊按鈕，只有圓心一圈柔光。
// 3D（使用者：「可以有 3D 感嗎」）：三圈放在斜著看的軌道盤上、整盤慢慢自轉；拖曳轉動與俯仰、雙指或滾輪縮放；近大遠小、
// 遠的變暗、轉到後面的名字收起來（字一律固定大小，不跟著縮）。點一個點就停止自轉、亮出它跟新聞怎麼連上的，再點「看個股」。
import { CandlestickChart, ChevronLeft, List, Network, Newspaper, RotateCcw, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent, type WheelEvent as RWheelEvent } from 'react'
import {
  CLASS_LABEL,
  classOf,
  edgePath3,
  hiddenKids,
  orbitBounds,
  orbitLayout,
  orbitPath,
  pathTo,
  project,
  radialLayout,
  showLabel,
  strokeOf,
  tableRows,
  topTw,
  viaText,
  type GClass,
  type GNode,
  type NewsGraph as Graph,
  type P3,
  type Placed,
  type Proj,
} from './news-graph'

// 一度到二度拉得比較開：靠近鏡頭那一側一度的名字＋漲跌有兩行，要放得下（2026-10-19 截圖超微壓到日月光）
const RADII: [number, number, number] = [72, 150, 200]
// 三圈的高度（y 往下為正）：最外圈略低，像淺盤；側看時看得出不是一條線
const HEIGHTS: [number, number, number] = [0, 0, 14]
const CENTER: P3 = { x: 0, y: 0, z: 0 }
const NODE_R: Record<number, number> = { 1: 15, 2: 11, 3: 6 }
const ZOOM_MIN = 0.6
const ZOOM_MAX = 3
// 鏡頭：距離；預設俯角依畫面高寬比（直的螢幕看得更俯、盤才夠高）與可調範圍；自轉約 55 秒一圈；手動過後停多久才再自轉
const CAM_DIST = 640
const YAW0 = 0.5
const PITCH_DEFAULT: [number, number] = [-0.95, -0.5]
const PITCH_MIN = -1.25
const PITCH_MAX = -0.15
const SPIN = (2 * Math.PI) / 55
const IDLE_MS = 3500
// 打開時一度、二度、三度依序從新聞炸開
const BURST_MS = 900
const BURST_STAGGER = 150
// 拖曳：每 px 轉多少弧度；移動超過幾 px 就不算點一下
const DRAG_YAW = 0.008
const DRAG_PITCH = 0.005
const TAP_SLOP = 6
// 畫面留白：左右給名字、上下給名字與漲跌
const PAD_X = 26
const PAD_Y = 46

const chgText = (c: number | undefined) => (c === undefined ? '' : `${c > 0 ? '▲' : c < 0 ? '▼' : ''}${Math.abs(c).toFixed(2)}%`)
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
const easeOutBack = (t: number) => {
  const c = 1.4
  const u = t - 1
  return 1 + (c + 1) * u * u * u + c * u * u
}

export function NewsGraph({ graph, onClose, onStock }: { graph: Graph; onClose: () => void; onStock: (sid: string, name: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [opened, setOpened] = useState<Set<string>>(new Set())
  const [view, setView] = useState<'graph' | 'list'>('graph')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const pick = (n: GNode) => {
    setSelected((cur) => (cur === n.id ? null : n.id))
    if (n.degree < 3) setOpened((o) => new Set(o).add(n.id))
  }

  const sel = selected ? graph.nodes.find((n) => n.id === selected) ?? null : null
  const chain = sel ? pathTo(graph.nodes, sel.id) : []
  const counts = [1, 2, 3].map((d) => graph.nodes.filter((n) => n.degree === d).length)

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
        <Scene graph={graph} selected={selected} opened={opened} onPick={pick} onClear={() => setSelected(null)} />
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

type Screen = Proj & { g: number }

/** 預設俯角：直的畫面看得更俯（盤才夠高）、寬的畫面看得平一點（更有立體感）。 */
const pitchFor = (w: number, h: number) => -clamp(Math.atan((h - 2 * PAD_Y) / w), -PITCH_DEFAULT[1], -PITCH_DEFAULT[0])
/** 整盤放得下畫面的縮放。 */
const fitScale = (w: number, h: number, pitch: number) => {
  const b = orbitBounds(RADII, HEIGHTS, YAW0, pitch, CAM_DIST)
  return clamp(Math.min((w - 2 * PAD_X) / b.w, (h - 2 * PAD_Y) / (b.bottom - b.top)), 0.8, 2.2)
}

/** 3D 畫布：自己管鏡頭與每一格的重畫，上層（標題、底部面板）不會每格跟著重畫。 */
function Scene({ graph, selected, opened, onPick, onClear }: { graph: Graph; selected: string | null; opened: Set<string>; onPick: (n: GNode) => void; onClear: () => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [frame, setFrame] = useState({ w: 390, h: 560, k: 1, pitch0: PITCH_DEFAULT[0] })
  const [zoom, setZoom] = useState(1)
  const [boom, setBoom] = useState(false)
  const [, setTick] = useState(0)
  const cam = useRef({ yaw: YAW0, pitch: PITCH_DEFAULT[0] })
  const t0 = useRef(0)
  const idleUntil = useRef(0)
  const focused = useRef(false)
  const reduce = useMemo(() => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true, [])

  const placed = useMemo(() => radialLayout(graph.nodes, RADII), [graph])
  const pos = useMemo(() => orbitLayout(placed, HEIGHTS), [placed])
  const onPath = useMemo(() => new Set(selected ? pathTo(graph.nodes, selected).map((n) => n.id) : []), [graph, selected])

  useEffect(() => {
    const el = box.current
    if (!el) return
    const fit = () => {
      const w = el.clientWidth
      const h = el.clientHeight
      const pitch0 = pitchFor(w, h)
      cam.current.pitch = pitch0
      setFrame({ w, h, k: fitScale(w, h, pitch0), pitch0 })
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 動畫：炸開、自轉；沒在動的時候不重畫
  useEffect(() => {
    t0.current = reduce ? -1e9 : performance.now()
    const id = requestAnimationFrame(() => setBoom(true))
    return () => cancelAnimationFrame(id)
  }, [reduce])
  useEffect(() => {
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const dt = Math.min(64, now - last)
      last = now
      const bursting = now - t0.current < BURST_MS + 2 * BURST_STAGGER + 200
      const spinning = !reduce && selected === null && !focused.current && now > idleUntil.current
      if (spinning) cam.current.yaw += (SPIN * dt) / 1000
      if (bursting || spinning) setTick((t) => (t + 1) % 1_000_000)
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [selected, reduce])

  // ── 拖曳轉動、兩指捏或滾輪縮放 ─────────────────────────────
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const pinch = useRef<{ d: number; z: number } | null>(null)
  const moved = useRef(0)
  const touch = () => {
    idleUntil.current = performance.now() + IDLE_MS
  }
  const down = (e: RPointerEvent) => {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pointers.current.size === 1) moved.current = 0
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y), z: zoom }
    }
    touch()
  }
  const move = (e: RPointerEvent) => {
    const prev = pointers.current.get(e.pointerId)
    if (!prev) return
    const cur = { x: e.clientX, y: e.clientY }
    pointers.current.set(e.pointerId, cur)
    if (pointers.current.size === 2 && pinch.current) {
      const [a, b] = [...pointers.current.values()]
      setZoom(clamp((pinch.current.z * Math.hypot(a.x - b.x, a.y - b.y)) / pinch.current.d, ZOOM_MIN, ZOOM_MAX))
      moved.current += TAP_SLOP
    } else if (pointers.current.size === 1 && e.buttons) {
      const dx = cur.x - prev.x
      const dy = cur.y - prev.y
      moved.current += Math.abs(dx) + Math.abs(dy)
      cam.current.yaw += dx * DRAG_YAW
      cam.current.pitch = clamp(cam.current.pitch - dy * DRAG_PITCH, PITCH_MIN, PITCH_MAX)
      setTick((t) => (t + 1) % 1_000_000)
    }
    touch()
  }
  const up = (e: RPointerEvent) => {
    pointers.current.delete(e.pointerId)
    if (pointers.current.size < 2) pinch.current = null
  }
  const wheel = (e: RWheelEvent) => {
    setZoom((z) => clamp(z * Math.exp(-e.deltaY * 0.0015), ZOOM_MIN, ZOOM_MAX))
    touch()
  }
  const tap = (fn: () => void) => () => {
    if (moved.current <= TAP_SLOP) fn()
  }

  // ── 這一格的投影 ─────────────────────────────────────────
  const now = performance.now()
  const k = frame.k * zoom
  const R = RADII[2]
  const { yaw, pitch } = cam.current
  const grow = (d: number) => (reduce ? 1 : easeOutBack(clamp((now - t0.current - (d - 1) * BURST_STAGGER) / BURST_MS, 0, 1)))
  const toScreen = (v: P3, g: number): Screen => {
    // 從新聞那一點長出來
    const q = project({ x: CENTER.x + (v.x - CENTER.x) * g, y: CENTER.y + (v.y - CENTER.y) * g, z: CENTER.z + (v.z - CENTER.z) * g }, yaw, pitch, CAM_DIST)
    return { x: q.x * k, y: q.y * k, z: q.z, s: q.s, g }
  }
  const scr = new Map<string, Screen>()
  for (const p of placed) {
    const v = pos.get(p.id)
    if (v) scr.set(p.id, toScreen(v, grow(p.degree)))
  }
  const origin = toScreen(CENTER, 1)
  // 越前面越亮：最後面 0.4、最前面 1
  const depth = (z: number) => clamp((z / R + 1) / 2, 0, 1)
  const order = [...placed].sort((a, b) => (scr.get(a.id)?.z ?? 0) - (scr.get(b.id)?.z ?? 0))
  const back = order.filter((p) => (scr.get(p.id)?.z ?? 0) < origin.z)
  const front = order.filter((p) => (scr.get(p.id)?.z ?? 0) >= origin.z)
  const node = (p: Placed) => {
    const q = scr.get(p.id)
    if (!q) return null
    const hot = onPath.has(p.id)
    const on = selected === p.id
    // 轉到後面的名字收起來（轉到前面才出現），被選到或在路徑上的一律顯示
    const labelAlpha = hot || on ? 1 : clamp((q.z / R + 0.55) / 0.35, 0, 1)
    return (
      <Node
        key={p.id}
        p={p}
        q={q}
        r={Math.max(3, NODE_R[Math.min(p.degree, 3)] * q.s * clamp(k, 0.9, 1.6))}
        alpha={Math.min(1, q.g * 2) * (0.4 + 0.6 * depth(q.z))}
        label={showLabel(p, opened, selected) && labelAlpha > 0 ? labelAlpha : 0}
        more={hiddenKids(p, graph.nodes, opened)}
        hot={hot}
        on={on}
        onPick={tap(() => onPick(p))}
        onFocus={(f) => {
          focused.current = f
        }}
      />
    )
  }

  return (
    <div
      ref={box}
      className={boom ? 'cq-ng-stage boom' : 'cq-ng-stage'}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onWheel={wheel}
    >
      <svg width={frame.w} height={frame.h} className={selected ? 'focus' : ''} onClick={(e) => e.target === e.currentTarget && tap(onClear)()}>
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
          {/* 球：左上亮、右下暗（中間那一段是驗證過的類別色） */}
          <radialGradient id="ng-ball-abroad" cx="0.38" cy="0.32" r="0.72">
            <stop offset="0" stopColor="#a9cdff" />
            <stop offset="0.5" stopColor="#3987e5" />
            <stop offset="1" stopColor="#163a72" />
          </radialGradient>
          <radialGradient id="ng-ball-tw" cx="0.38" cy="0.32" r="0.72">
            <stop offset="0" stopColor="#ffc2a1" />
            <stop offset="0.5" stopColor="#d95926" />
            <stop offset="1" stopColor="#6a2408" />
          </radialGradient>
          <radialGradient id="ng-ball-cluster" cx="0.38" cy="0.32" r="0.72">
            <stop offset="0" stopColor="#8ff0cb" />
            <stop offset="0.5" stopColor="#199e70" />
            <stop offset="1" stopColor="#0a4532" />
          </radialGradient>
        </defs>
        <g transform={`translate(${frame.w / 2},${frame.h / 2})`}>
          {/* 三圈軌道：繞著轉時形狀不變，看得出是一個斜著的盤 */}
          <g transform={`scale(${k})`}>
            {RADII.map((r, i) => (
              <path key={r} className="cq-ng-ring" d={orbitPath(r, HEIGHTS[i], yaw, pitch, CAM_DIST)} vectorEffect="non-scaling-stroke" />
            ))}
          </g>
          {order.map((p) => {
            const q = scr.get(p.id)
            const v = pos.get(p.id)
            if (!q || !v) return null
            const from = p.parent ? scr.get(p.parent) ?? origin : origin
            const c = toScreen({ x: v.x * 0.62, y: v.y * 0.62, z: v.z * 0.62 }, q.g)
            const d = edgePath3(from, c, q)
            const hot = onPath.has(p.id)
            return (
              <g key={`e-${p.id}`} className={['cq-ng-link', `d${p.degree}`, hot ? 'hot' : ''].join(' ')}>
                <path
                  className={p.draft ? 'cq-ng-edge draft' : 'cq-ng-edge'}
                  d={d}
                  strokeWidth={strokeOf(p.score) * Math.sqrt(q.s)}
                  pathLength={p.draft ? undefined : 1}
                  style={{ opacity: hot ? 1 : 0.3 + 0.7 * depth((q.z + from.z) / 2) }}
                />
                {/* 選到的路徑上有光點從新聞往外流 */}
                {hot && <path className="cq-ng-flow" d={d} />}
              </g>
            )
          })}
          {back.map(node)}
          <g className="cq-ng-center" transform={`translate(${origin.x.toFixed(1)},${origin.y.toFixed(1)}) scale(${Math.sqrt(origin.s).toFixed(3)})`} onClick={tap(onClear)}>
            <circle className="halo" r={30} filter="url(#ng-soft)" />
            <circle className="shock" r={24} />
            <circle className="core" r={24} />
            <Newspaper x={-10} y={-10} width={20} height={20} strokeWidth={1.6} className="glyph" />
          </g>
          {front.map(node)}
        </g>
      </svg>
      <div className="cq-ng-zoom">
        <button onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z * 1.25))} aria-label="放大">
          ＋
        </button>
        <button onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z / 1.25))} aria-label="縮小">
          －
        </button>
        <button
          onClick={() => {
            cam.current = { yaw: YAW0, pitch: frame.pitch0 }
            setZoom(1)
            onClear()
            if (!reduce) t0.current = performance.now()
            idleUntil.current = 0
          }}
          aria-label="回到原本的角度"
        >
          <RotateCcw size={16} />
        </button>
      </div>
    </div>
  )
}

function Node({
  p,
  q,
  r,
  alpha,
  label,
  more,
  hot,
  on,
  onPick,
  onFocus,
}: {
  p: Placed
  q: Screen
  r: number
  alpha: number
  /** 名字的透明度；0＝不顯示 */
  label: number
  more: number
  hot: boolean
  on: boolean
  onPick: () => void
  onFocus: (focused: boolean) => void
}) {
  const c = classOf(p.type)
  // 下半部名字放點下面、上半部放上面；一度的美股多一行昨晚漲跌（代號在底部面板，圖上放會壓到旁邊的點）
  const below = q.y >= -4
  const tag = p.degree === 1 && p.type === 'us' && p.chg !== undefined
  const nameY = below ? r + 17 : -r - (tag ? 25 : 10)
  const tagY = below ? r + 32 : -r - 10
  return (
    <g
      className={['cq-ng-node', c, `d${p.degree}`, hot ? 'hot' : '', on ? 'on' : ''].join(' ')}
      transform={`translate(${q.x.toFixed(1)},${q.y.toFixed(1)})`}
      tabIndex={0}
      role="button"
      aria-label={`${p.label}，${CLASS_LABEL[c]}，${p.degree} 度，${viaText(p)}${p.draft ? '，AI 草擬待核可' : ''}`}
      onClick={(e) => {
        e.stopPropagation()
        onPick()
      }}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onPick()}
      onFocus={() => onFocus(true)}
      onBlur={() => onFocus(false)}
    >
      <g style={{ opacity: alpha }}>
        <circle className="hit" r={Math.max(r + 8, 14)} />
        {on && <circle className="sel" r={r + 5} />}
        <circle className="dot" r={r} />
        {/* 「+N」放在離圓心遠的那一側：靠內側會壓到一度美股的漲跌 */}
        {more > 0 && label > 0 && (
          <text className="more" x={q.x < 0 ? -(r + 5) : r + 5} y={4} textAnchor={q.x < 0 ? 'end' : 'start'} style={{ opacity: label }}>
            +{more}
          </text>
        )}
      </g>
      {label > 0 && (
        <text className="lbl" y={nameY} textAnchor="middle" style={{ opacity: label }}>
          {p.label}
        </text>
      )}
      {label > 0 && tag && p.chg !== undefined && (
        <text className={`lbl tag ${p.chg >= 0 ? 'up' : 'down'}`} y={tagY} textAnchor="middle" style={{ opacity: label }}>
          {chgText(p.chg)}
        </text>
      )}
    </g>
  )
}
