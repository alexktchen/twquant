// 新聞全景（T-418；使用者 2026-10-20：「星雲圖當成可以切換的一個視角就好」）：新聞頁「清單／全景」切到全景時才載入（React.lazy）。
// 光點＝一則新聞（台灣橘、國際藍），靠在一起＝內容相近；亮點＝被很多則提到的股票、公司、主題；線＝這則提到它。
// 3D、預設慢慢自轉（使用者：「可以旋轉嗎」）；一指拖曳旋轉、兩指或滾輪縮放、⟳ 開關自轉；點光點或亮點浮出檢視框（點了就停住）。
// 亮度跟著畫面密度調（使用者：「手機看好像太亮」）。資料是排程算好的快照（一天幾次）。
import { ExternalLink, Minus, Plus, RotateCw, Undo2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchNewsGalaxy } from './api'
import {
  REF_S,
  clusterLabelAt,
  density,
  depthAlpha,
  galaxySummary,
  hubClass,
  hubRadius,
  hubTypeLabel,
  pickAt,
  placeLabels,
  project,
  type GalaxyData,
  type Proj,
  type Rot,
  type View,
} from './news-galaxy'
import { shortWhen, taipeiDay } from './news'
import './news-galaxy.css'

type Sel = { kind: 'item' | 'hub'; id: string } | null
const COL: Record<string, string> = { tw: '255,122,89', intl: '127,178,255', hubtw: '255,150,110', abroad: '140,185,255', cluster: '45,212,191', sel: '255,210,63' }
const HOME_ROT: Rot = { yaw: 0.5, pitch: -0.3 }

/** 發光圓點：先畫好一張放射漸層，每顆用 drawImage（手機也順） */
function sprite(rgb: string): HTMLCanvasElement {
  const s = document.createElement('canvas')
  s.width = s.height = 64
  const g = s.getContext('2d')!
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32)
  gr.addColorStop(0, 'rgba(255,255,255,0.85)')
  gr.addColorStop(0.14, `rgba(${rgb},0.9)`)
  gr.addColorStop(0.45, `rgba(${rgb},0.28)`)
  gr.addColorStop(1, `rgba(${rgb},0)`)
  g.fillStyle = gr
  g.fillRect(0, 0, 64, 64)
  return s
}

export function NewsGalaxy({ onStock }: { onStock: (sid: string, name: string) => void }) {
  const [data, setData] = useState<GalaxyData | null>(null)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState<Sel>(null)
  const [auto, setAuto] = useState(true)
  const stageRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const labelsRef = useRef<HTMLDivElement>(null)
  const inspRef = useRef<HTMLDivElement>(null)
  const live = useRef({ sel: null as Sel, auto: true, reset: 0, zoom: 1 })
  live.current.sel = sel
  live.current.auto = auto

  useEffect(() => {
    let stale = false
    fetchNewsGalaxy()
      .then((d) => !stale && setData(d))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [])

  useEffect(() => {
    if (!data || !stageRef.current || !canvasRef.current || !labelsRef.current) return
    const stage = stageRef.current, cv = canvasRef.current, ctx = cv.getContext('2d')!, labelsEl = labelsRef.current
    const SP = Object.fromEntries(Object.entries(COL).map(([k, v]) => [k, sprite(v)]))
    const items = data.items, hubs = data.hubs, clusters = data.clusters
    const hubIdx = new Map(hubs.map((h, i) => [h.id, i]))
    const itemHubs = items.map((it) => it.m.map((m) => hubIdx.get(m)).filter((i): i is number => i !== undefined))
    const seed = items.map((_, i) => [Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1, Math.abs(Math.sin(i * 78.233) * 12345.678) % 1])
    let W = 0, H = 0, dpr = 1
    let view: View = { s: 1, x: 0, y: 0 }, home: View = { s: 1, x: 0, y: 0 }
    const rot: Rot = { ...HOME_ROT }
    let raf = 0, last = 0, t0 = performance.now(), resetSeen = 0

    // 標籤（DOM 比較好排版）：主題依則數、亮點台股優先；大小量一次
    const bigK = new Set([...clusters].sort((a, b) => b.n - a.n).slice(0, 3).map((c) => c.k))
    const rank = (t: string) => (t === 'tw' ? 0 : t === 'us' || t === 'company' ? 1 : 2)
    const hubLabels = [...hubs].sort((a, b) => rank(a.type) - rank(b.type) || b.n - a.n).slice(0, 18)
    labelsEl.replaceChildren()
    const mk = (cls: string, text: string, small: string) => {
      const e = document.createElement('div')
      e.className = cls
      e.append(text)
      const s = document.createElement('small')
      s.textContent = small
      e.append(s)
      labelsEl.append(e)
      return e
    }
    const cEls = clusters.map((c) => ({ c, e: mk(`gx-lbl${bigK.has(c.k) ? ' hot' : ''}`, c.name, `/ ${c.n}`) }))
    const hEls = hubLabels.map((h) => ({ h, e: mk(`gx-lbl hub ${hubClass(h.type)}`, h.label, String(h.n)) }))
    const sizes = new Map<HTMLElement, [number, number]>()
    for (const { e } of [...cEls, ...hEls]) sizes.set(e, [e.offsetWidth, e.offsetHeight])

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1)
      W = stage.clientWidth
      H = stage.clientHeight
      cv.width = W * dpr
      cv.height = H * dpr
      const s = Math.min(W, H * 1.15) / 2.45
      const first = home.s === 1
      home = { s, x: W / 2, y: H / 2 }
      if (first) view = { ...home }
      else view = { ...view, x: W / 2, y: H / 2 }
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(stage)

    const P = { items: [] as Proj[], hubs: [] as Proj[], clusters: [] as Proj[] }
    const hubSize = (n: number) => hubRadius(n) * Math.min(1.4, Math.max(0.65, Math.sqrt(view.s / REF_S)))

    const draw = (now: number) => {
      const lv = live.current
      if (lv.reset !== resetSeen) {
        resetSeen = lv.reset
        view = { ...home }
        rot.yaw = HOME_ROT.yaw
        rot.pitch = HOME_ROT.pitch
      }
      if (lv.zoom !== 1) {
        view.s = Math.max(home.s * 0.6, Math.min(home.s * 9, view.s * lv.zoom))
        lv.zoom = 1
      }
      const dt = Math.min(0.05, (now - (last || now)) / 1000)
      last = now
      if (lv.auto && !lv.sel && !drag) rot.yaw += dt * 0.12
      const tt = (now - t0) / 1000
      P.items = items.map((it) => project(it, rot, view))
      P.hubs = hubs.map((h) => project(h, rot, view))
      P.clusters = clusters.map((c) => project(c, rot, view))
      const sel = lv.sel
      let relItems: Set<string> | null = null, relHubs: Set<string> | null = null
      if (sel?.kind === 'item') {
        const it = items.find((x) => x.id === sel.id)
        if (it) {
          relItems = new Set(items.filter((x) => (it.ev ? x.ev === it.ev : x.id === it.id)).map((x) => x.id))
          relHubs = new Set(it.m)
        }
      } else if (sel?.kind === 'hub') {
        relItems = new Set(items.filter((x) => x.m.includes(sel.id)).map((x) => x.id))
        relHubs = new Set([sel.id])
      }
      const k = density(view.s)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, W, H)
      ctx.globalCompositeOperation = 'lighter'
      const pos = P.items.map((p, i) => [p[0] + Math.sin(tt * 0.5 + seed[i][0] * 6.28) * 1.6, p[1] + Math.cos(tt * 0.4 + seed[i][1] * 6.28) * 1.6])
      // 線
      ctx.lineWidth = 0.6
      ctx.strokeStyle = `rgba(255,122,89,${(relItems ? 0.035 : 0.075) * k})`
      ctx.beginPath()
      itemHubs.forEach((hs, i) => hs.forEach((h) => {
        ctx.moveTo(pos[i][0], pos[i][1])
        ctx.lineTo(P.hubs[h][0], P.hubs[h][1])
      }))
      ctx.stroke()
      if (relItems) {
        ctx.strokeStyle = 'rgba(255,210,63,0.55)'
        ctx.lineWidth = 1
        ctx.beginPath()
        itemHubs.forEach((hs, i) => {
          if (!relItems!.has(items[i].id)) return
          hs.forEach((h) => {
            if (sel?.kind === 'hub' && hubs[h].id !== sel.id) return
            ctx.moveTo(pos[i][0], pos[i][1])
            ctx.lineTo(P.hubs[h][0], P.hubs[h][1])
          })
        })
        ctx.stroke()
      }
      // 新聞光點
      const r0 = Math.min(9, Math.max(3.4, 5.5 * Math.sqrt(view.s / REF_S)))
      items.forEach((it, i) => {
        const p = P.items[i], on = !relItems || relItems.has(it.id)
        const tw = 0.75 + 0.25 * Math.sin(tt * 1.3 + seed[i][0] * 40)
        ctx.globalAlpha = (on ? k : 0.12 * k) * tw * depthAlpha(p[2])
        const r = r0 * p[3] * (it.m.length ? 1.15 : 0.9)
        ctx.drawImage(relItems?.has(it.id) ? SP.sel : SP[it.kind === 'intl' ? 'intl' : 'tw'], pos[i][0] - r, pos[i][1] - r, r * 2, r * 2)
      })
      // 亮點
      hubs.forEach((h, i) => {
        const p = P.hubs[i], on = !relHubs || relHubs.has(h.id)
        const r = hubSize(h.n) * p[3] * (1 + 0.06 * Math.sin(tt * 2 + i))
        ctx.globalAlpha = (on ? 0.75 : 0.12) * Math.max(0.45, k) * depthAlpha(p[2])
        const c = hubClass(h.type)
        ctx.drawImage(c === 'tw' ? SP.hubtw : c === 'abroad' ? SP.abroad : SP.cluster, p[0] - r, p[1] - r, r * 2, r * 2)
        ctx.drawImage(SP[c === 'abroad' ? 'intl' : 'tw'], p[0] - r * 0.35, p[1] - r * 0.35, r * 0.7, r * 0.7)
      })
      ctx.globalAlpha = 1
      ctx.globalCompositeOperation = 'source-over'
      // 標籤避讓：主題（手機少放）→ 亮點；放大後放得下更多
      const phone = W < 600, zf = Math.sqrt(view.s / home.s)
      const center = { x: view.x, y: view.y }
      const cWant = [...cEls].sort((a, b) => b.c.n - a.c.n).slice(0, phone ? Math.round(8 * zf) : 16)
      const hWant = hEls.slice(0, phone ? Math.round(7 * zf) : 16)
      const want = [
        ...cWant.map(({ c, e }) => {
          const at = clusterLabelAt(P.clusters[clusters.indexOf(c)], center)
          const [w, h] = sizes.get(e)!
          return { key: `c${c.k}`, x: at.x, y: at.y, w, h, e }
        }),
        ...hWant.map(({ h, e }) => {
          const p = P.hubs[hubIdx.get(h.id)!]
          const [w, hh] = sizes.get(e)!
          return { key: `h${h.id}`, x: p[0], y: p[1] + hubSize(h.n) * p[3] + 10, w, h: hh, e }
        }),
      ]
      const placed = placeLabels(want, W, H)
      for (const { e } of [...cEls, ...hEls]) e.style.visibility = 'hidden'
      ctx.strokeStyle = 'rgba(230,220,215,0.35)'
      ctx.lineWidth = 0.8
      ctx.beginPath()
      for (const w of want) {
        const at = placed.get(w.key)
        if (!at) continue
        w.e.style.visibility = 'visible'
        w.e.style.left = `${at.x}px`
        w.e.style.top = `${at.y}px`
        w.e.classList.toggle('dim', !!relItems && !(w.key.startsWith('h') && relHubs?.has(w.key.slice(1))))
        if (w.key.startsWith('c')) {
          const p = P.clusters[clusters.findIndex((c) => `c${c.k}` === w.key)]
          ctx.moveTo(p[0], p[1])
          ctx.lineTo(at.x, at.y)
        }
      }
      ctx.stroke()
      // 選取的那顆畫框；檢視框跟著它
      const insp = inspRef.current
      if (sel && insp) {
        const p = sel.kind === 'item' ? P.items[items.findIndex((x) => x.id === sel.id)] : P.hubs[hubIdx.get(sel.id) ?? -1]
        if (p) {
          if (sel.kind === 'item') {
            ctx.strokeStyle = '#ffd23f'
            ctx.lineWidth = 1.2
            ctx.strokeRect(p[0] - 9, p[1] - 9, 18, 18)
          }
          const w = insp.offsetWidth, h = insp.offsetHeight
          let x = p[0] + 18, y = p[1] - h - 14
          if (x + w > W - 8) x = p[0] - w - 18
          if (x < 8) x = 8
          if (y < 8) y = p[1] + 18
          if (y + h > H - 50) y = Math.max(8, H - 50 - h)
          insp.style.left = `${x}px`
          insp.style.top = `${y}px`
        }
      }
      raf = requestAnimationFrame(draw)
    }

    // 拖曳＝旋轉；兩指＝縮放；點一下＝選
    const pts = new Map<number, [number, number]>()
    let drag: { yaw: number; pitch: number; sx: number; sy: number; d: number; s: number } | null = null
    let moved = 0
    const xy = (e: PointerEvent): [number, number] => {
      const r = stage.getBoundingClientRect()
      return [e.clientX - r.left, e.clientY - r.top]
    }
    const pinch = () => {
      const a = [...pts.values()]
      return a.length === 2 ? Math.hypot(a[0][0] - a[1][0], a[0][1] - a[1][1]) : 0
    }
    const onDown = (e: PointerEvent) => {
      if ((e.target as HTMLElement).closest('.gx-insp,.gx-ctl')) return
      stage.setPointerCapture(e.pointerId)
      pts.set(e.pointerId, xy(e))
      const [x, y] = xy(e)
      drag = { yaw: rot.yaw, pitch: rot.pitch, sx: x, sy: y, d: pinch(), s: view.s }
      moved = 0
    }
    const onMove = (e: PointerEvent) => {
      if (!pts.has(e.pointerId) || !drag) return
      const [x, y] = xy(e)
      pts.set(e.pointerId, [x, y])
      if (pts.size === 2 && drag.d) {
        view.s = Math.max(home.s * 0.6, Math.min(home.s * 9, drag.s * (pinch() / drag.d)))
        moved = 99
        return
      }
      rot.yaw = drag.yaw + (x - drag.sx) * 0.008
      rot.pitch = Math.max(-1.3, Math.min(1.3, drag.pitch + (y - drag.sy) * 0.008))
      moved = Math.max(moved, Math.hypot(x - drag.sx, y - drag.sy))
    }
    const onUp = (e: PointerEvent) => {
      const was = pts.size
      pts.delete(e.pointerId)
      if (pts.size) {
        const [p] = pts.values()
        drag = { yaw: rot.yaw, pitch: rot.pitch, sx: p[0], sy: p[1], d: 0, s: view.s }
        return
      }
      drag = null
      if (was === 1 && moved < 6 && !(e.target as HTMLElement).closest('.gx-insp,.gx-ctl')) {
        const [x, y] = xy(e)
        setSel(
          pickAt(
            x,
            y,
            hubs.map((h, i) => ({ id: h.id, p: P.hubs[i], r: hubSize(h.n) * P.hubs[i][3] })),
            items.map((it, i) => ({ id: it.id, p: P.items[i] })),
          ),
        )
      }
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      view.s = Math.max(home.s * 0.6, Math.min(home.s * 9, view.s * Math.exp(-e.deltaY * 0.0015)))
    }
    stage.addEventListener('pointerdown', onDown)
    stage.addEventListener('pointermove', onMove)
    stage.addEventListener('pointerup', onUp)
    stage.addEventListener('pointercancel', onUp)
    stage.addEventListener('wheel', onWheel, { passive: false })
    raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      stage.removeEventListener('pointerdown', onDown)
      stage.removeEventListener('pointermove', onMove)
      stage.removeEventListener('pointerup', onUp)
      stage.removeEventListener('pointercancel', onUp)
      stage.removeEventListener('wheel', onWheel)
    }
  }, [data])

  if (err) return <p className="cq-war-mute cq-news-empty">全景讀不到：{err}</p>
  if (!data) return <p className="cq-war-mute cq-news-empty">全景讀取中…</p>
  const today = taipeiDay(new Date().toISOString())
  const item = sel?.kind === 'item' ? data.items.find((x) => x.id === sel.id) : null
  const hub = sel?.kind === 'hub' ? data.hubs.find((h) => h.id === sel.id) : null
  const hubById = new Map(data.hubs.map((h) => [h.id, h]))
  return (
    <section className="gx">
      <p className="gx-sum">{galaxySummary(data)}</p>
      <div className="gx-stage" ref={stageRef}>
        <canvas ref={canvasRef} />
        <div ref={labelsRef} />
        {(item || hub) && (
          <div className="gx-insp" ref={inspRef}>
            <button className="gx-x" onClick={() => setSel(null)} aria-label="關閉">
              <X size={16} aria-hidden="true" />
            </button>
            {item && (
              <>
                <div className="gx-h">
                  <span>
                    {item.src}
                    {item.tr ? '・譯' : ''}・{shortWhen(item.t, today)}
                  </span>
                  <b>{data.clusters.find((c) => c.k === item.k)?.name}</b>
                </div>
                <p className="gx-t">{item.title}</p>
                {item.m.length + item.ml.length > 0 && (
                  <div className="gx-chips">
                    {item.m
                      .map((m) => hubById.get(m))
                      .filter((h) => h?.type === 'tw' && h.sub)
                      .map((h) => (
                        <button key={h!.id} className="gx-chip tw" onClick={() => onStock(h!.sub!, h!.label)} title="開個股頁">
                          {h!.label}
                        </button>
                      ))}
                    {item.ml
                      .filter((l) => !item.m.some((m) => hubById.get(m)?.type === 'tw' && hubById.get(m)?.label === l))
                      .map((l) => (
                        <span key={l} className="gx-chip">
                          {l}
                        </span>
                      ))}
                  </div>
                )}
                {item.ev && data.items.filter((x) => x.ev === item.ev).length > 1 && (
                  <p className="gx-row">同一件事 {data.items.filter((x) => x.ev === item.ev).length} 則・{[...new Set(data.items.filter((x) => x.ev === item.ev).map((x) => x.src))].join('、')}</p>
                )}
                <a href={item.url} target="_blank" rel="noopener noreferrer">
                  看原文 <ExternalLink size={13} aria-hidden="true" />
                </a>
              </>
            )}
            {hub && (
              <>
                <div className="gx-h">
                  <span>
                    {hubTypeLabel(hub.type)}
                    {hub.type === 'tw' && hub.sub ? ` ${hub.sub}` : ''}
                  </span>
                  <b>{hub.n} 則</b>
                </div>
                <p className="gx-t">{hub.label}</p>
                {data.items
                  .filter((x) => x.m.includes(hub.id))
                  .sort((a, b) => (a.t < b.t ? 1 : -1))
                  .slice(0, 4)
                  .map((x) => (
                    <p key={x.id} className="gx-row">
                      {shortWhen(x.t, today)}　{x.title}
                    </p>
                  ))}
                {hub.type === 'tw' && hub.sub && (
                  <button className="gx-go" onClick={() => onStock(hub.sub!, hub.label)}>
                    開個股頁
                  </button>
                )}
              </>
            )}
          </div>
        )}
        <div className="gx-legend">
          <span><i className="tw" />台灣</span>
          <span><i className="intl" />國際</span>
          <span><i className="hub" />股票與主題</span>
        </div>
        <div className="gx-ctl">
          <button className={auto ? 'on' : ''} onClick={() => setAuto(!auto)} aria-pressed={auto} aria-label="自動旋轉" title="自動旋轉（開／關）">
            <RotateCw size={17} aria-hidden="true" />
          </button>
          <button onClick={() => (live.current.zoom = 1.5)} aria-label="放大">
            <Plus size={17} aria-hidden="true" />
          </button>
          <button onClick={() => (live.current.zoom = 1 / 1.5)} aria-label="縮小">
            <Minus size={17} aria-hidden="true" />
          </button>
          <button onClick={() => { live.current.reset += 1; setSel(null) }} aria-label="回到一開始" title="回到一開始">
            <Undo2 size={17} aria-hidden="true" />
          </button>
        </div>
      </div>
      <p className="gx-help">光點＝一則新聞，靠在一起＝內容相近；亮點＝被很多則提到的股票、主題。拖曳旋轉、兩指縮放，點一顆看細節。要快速讀新聞用上面三張卡或「清單」。</p>
    </section>
  )
}
