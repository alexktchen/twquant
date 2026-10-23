// 新聞全景（T-418；使用者 2026-10-20：「星雲圖當成可以切換的一個視角就好」）：新聞頁「清單／全景」切到全景時才載入（React.lazy）。
// 光點＝一則新聞（台灣橘、國際藍），靠在一起＝內容相近；亮點＝被很多則提到的股票、公司、主題；線＝這則提到它。
// 3D、預設慢慢自轉（使用者：「可以旋轉嗎」）；一指拖曳旋轉、兩指或滾輪縮放、⟳ 開關自轉；點光點或亮點浮出檢視框（點了就停住）。
// 不迷航（使用者 2026-10-21：「有時候再旋轉很容易迷航」）：點了的那顆慢慢移到中央、之後繞著它轉、縮放也朝它；
// 一動手（拖、縮放）就關掉自轉；拖曳轉得慢一點；「回到一開始」滑回去而不是跳回去。
// 上下左右都能一直轉（使用者：「應該要可以上下左右一直旋轉 現在好像會卡住180度」）：轉向存成四元數、照畫面方向轉，
// 翻過頭頂也不會左右顛倒；往下拖＝前面的往下（跟往右拖＝前面的往右同一個手感）。
// 亮度跟著畫面密度調（使用者：「手機看好像太亮」）。資料是排程算好的快照（一天幾次）。
// 亮點的檢視框有「單獨看」：把提到它的新聞拎出來照主題讀、看常一起出現的（GalaxyFocus.tsx）。
// 搜尋與牽連（T-436；使用者 2026-10-22：「想要在那頁下一個字來搜尋就出現他是怎麼牽連在一起的」，像 graphify）：
// 左上角搜尋框，打字就列符合的股票、主題、新聞；選一個＝飛到它、畫出它跟誰牽連（同一則新聞一起被提到＝一條線，越粗則數越多）；
// 打兩個詞（例：「台積電 輝達」）＝找兩個之間怎麼牽在一起，路徑畫成粗黃線、上面列出每一度幾則（「幾度」＝隔幾層關係，跟新聞關係圖的講法一樣）。
import { ExternalLink, Maximize2, Minus, Plus, RotateCw, Search, Undo2, X } from 'lucide-react'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { fetchNewsGalaxy } from './api'
import { GalaxyFocus } from './GalaxyFocus'
import {
  DRAG_RAD,
  ORIGIN,
  REF_S,
  clusterLabelAt,
  density,
  depthAlpha,
  dockInspector,
  dragRotate,
  ease,
  bestHub,
  galaxySummary,
  hubClass,
  hubGraph,
  hubPath,
  hubRadius,
  hubTypeLabel,
  hubWeb,
  parseQuery,
  pickAt,
  pivotScreenY,
  placeLabels,
  project,
  qFromYawPitch,
  qSame,
  qSlerp,
  searchGalaxy,
  type GalaxyData,
  type HubPath,
  type HubWeb,
  type Proj,
  type Quat,
  type SearchHit,
  type Vec3,
  type View,
} from './news-galaxy'
import { shortWhen, taipeiDay } from './news'
import './news-galaxy.css'

type Sel = { kind: 'item' | 'hub'; id: string } | null
const COL: Record<string, string> = { tw: '255,122,89', intl: '127,178,255', hubtw: '255,150,110', abroad: '140,185,255', cluster: '45,212,191', sel: '255,210,63' }
const HOME_ROT: Quat = qFromYawPitch(0.5, -0.3)

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
  const [focus, setFocus] = useState<string | null>(null)
  // 搜尋（T-436）：q＝打的字、open＝下拉列著；path＝兩個詞找到的路徑、pathMsg＝找不到時的一句話
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [path, setPath] = useState<HubPath | null>(null)
  const [pathMsg, setPathMsg] = useState('')
  const stageRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const labelsRef = useRef<HTMLDivElement>(null)
  const inspRef = useRef<HTMLDivElement>(null)
  // fit：搜尋選了東西就拉近到看得清牽連的比例（數字變了就做一次）；webAt／pathAt：牽連線從什麼時候開始畫（像 graphify 的「路徑逐段點亮」）
  const live = useRef({ sel: null as Sel, auto: true, reset: 0, zoom: 1, fit: 0, web: null as HubWeb | null, path: null as { ids: string[]; n: number[] } | null, webAt: 0, pathAt: 0 })
  const graph = useMemo(() => (data ? hubGraph(data) : null), [data])
  const web = useMemo(() => (data && graph && sel?.kind === 'hub' ? hubWeb(data, graph, sel.id) : null), [data, graph, sel])
  const hits = useMemo(() => (data ? searchGalaxy(data, q) : []), [data, q])
  live.current.sel = sel
  live.current.auto = auto
  live.current.web = web
  live.current.path = path ? { ids: path.hubs.map((h) => h.id), n: path.n } : null
  useEffect(() => {
    live.current.webAt = performance.now()
  }, [web])
  useEffect(() => {
    live.current.pathAt = performance.now()
  }, [path])

  /** 搜尋列裡選一個：亮點＝飛到它、畫牽連；新聞＝選那顆 */
  const pick = (hit: SearchHit) => {
    setPath(null)
    setPathMsg('')
    setOpen(false)
    setAuto(false)
    live.current.fit += 1
    setSel(hit.kind === 'hub' ? { kind: 'hub', id: hit.hub.id } : { kind: 'item', id: hit.item.id })
  }
  /** 按 Enter：兩個詞＝找兩個之間怎麼牽；一個詞＝選第一個符合的 */
  const submit = () => {
    if (!data || !graph) return
    const terms = parseQuery(q)
    if (terms.length === 2) {
      const a = bestHub(data, terms[0]), b = bestHub(data, terms[1])
      const missing = !a ? terms[0] : !b ? terms[1] : null
      if (missing) {
        setPath(null)
        setPathMsg(`近 ${data.days} 天的新聞裡沒有被多次提到的「${missing}」`)
        setOpen(false)
        return
      }
      const p = hubPath(data, graph, a!.id, b!.id)
      setPath(p)
      setPathMsg(p ? '' : `近 ${data.days} 天沒有新聞把「${a!.label}」和「${b!.label}」連在一起`)
      setOpen(false)
      setAuto(false)
      live.current.fit += 1
      setSel({ kind: 'hub', id: a!.id })
      return
    }
    if (hits[0]) pick(hits[0])
  }
  const clearSearch = () => {
    setQ('')
    setOpen(false)
    setPath(null)
    setPathMsg('')
  }

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
    const itemIdx = new Map(items.map((it, i) => [it.id, i]))
    const itemHubs = items.map((it) => it.m.map((m) => hubIdx.get(m)).filter((i): i is number => i !== undefined))
    const seed = items.map((_, i) => [Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1, Math.abs(Math.sin(i * 78.233) * 12345.678) % 1])
    let W = 0, H = 0, dpr = 1
    let view: View = { s: 1, x: 0, y: 0 }, home: View = { s: 1, x: 0, y: 0 }
    let rot: Quat = HOME_ROT
    // 轉動中心：選了東西就慢慢移到它身上；取消選取後留在原地，回到一開始才回整團的中心
    const pivot: Vec3 = { ...ORIGIN }
    let pivotGoal: Vec3 = ORIGIN
    let goal: { q: Quat; s: number } | null = null
    let raf = 0, last = 0, t0 = performance.now(), resetSeen = 0, fitSeen = 0
    /** 搜尋選了東西：拉近到這個比例（一開始的 1.8 倍），牽連的線與名字才拉得開 */
    const FIT_S = 1.8
    /** 手機（檢視框貼下緣、看得到的區域只剩上半）拉近少一點，牽連的亮點才留在框上方（使用者 2026-10-23：「擋到了」） */
    const FIT_S_PHONE = 1.3
    /** 搜尋框佔掉圖的上緣：轉動中心最上只到這裡（手機檢視框貼下緣時） */
    const TOP_PAD = 96
    const INSP_W = 320
    /** 路徑逐段點亮（像 graphify 的 demo）：每一步隔 HOP 秒開始、花 DRAW 秒畫到底；一個亮點的牽連線 WEB_DRAW 秒從中心射出去 */
    const HOP = 0.45, DRAW = 0.35, WEB_DRAW = 0.5
    const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
    /** 亮點被點亮那一下彈一下（半徑倍率），之後縮回 */
    const pop = (since: number) => (since < 0 ? 1 : 1 + 0.7 * Math.exp(-since * 4))

    // 標籤（DOM 比較好排版）：主題依則數、亮點台股優先；大小量一次。亮點的標籤全部先做好，牽連到的才有名字可以亮出來（T-436）
    const bigK = new Set([...clusters].sort((a, b) => b.n - a.n).slice(0, 3).map((c) => c.k))
    const rank = (t: string) => (t === 'tw' ? 0 : t === 'us' || t === 'company' ? 1 : 2)
    const hubLabels = [...hubs].sort((a, b) => rank(a.type) - rank(b.type) || b.n - a.n)
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
    const cEls = clusters.map((c) => ({ c, e: mk(`gx-lbl${bigK.has(c.k) ? ' hot' : ''}`, c.name, String(c.n)) }))
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
        goal = { q: HOME_ROT, s: home.s }
        pivotGoal = ORIGIN
      }
      if (lv.zoom !== 1) {
        view.s = Math.max(home.s * 0.6, Math.min(home.s * 9, view.s * lv.zoom))
        lv.zoom = 1
        goal = null
      }
      if (lv.fit !== fitSeen) {
        fitSeen = lv.fit
        const fitS = W < 600 ? FIT_S_PHONE : FIT_S
        if (view.s < home.s * fitS) goal = { q: rot, s: home.s * fitS }
      }
      const dt = Math.min(0.05, (now - (last || now)) / 1000)
      last = now
      if (lv.auto && !lv.sel && !drag && !goal) rot = dragRotate(rot, dt * 0.12, 0)
      if (goal) {
        rot = qSlerp(rot, goal.q, 1 - Math.exp(-8 * dt))
        view.s = ease(view.s, goal.s, dt)
        if (qSame(rot, goal.q) > 1 - 1e-7 && Math.abs(view.s - goal.s) < 0.5) goal = null
      }
      if (lv.sel) {
        const o = lv.sel.kind === 'item' ? items[itemIdx.get(lv.sel.id) ?? -1] : hubs[hubIdx.get(lv.sel.id) ?? -1]
        if (o) pivotGoal = o
      }
      pivot.x = ease(pivot.x, pivotGoal.x, dt, 6)
      pivot.y = ease(pivot.y, pivotGoal.y, dt, 6)
      pivot.z = ease(pivot.z, pivotGoal.z, dt, 6)
      const insp = inspRef.current
      // 用固定寬度判斷（CSS 的 min(320px, …)）：手機上亮點的檢視框整個不顯示，量不到寬度
      const dock = !!(lv.sel && dockInspector(W, INSP_W))
      insp?.classList.toggle('dock', dock) // 貼下緣的版本矮一點：新聞標題收起來、超過就捲
      stage.parentElement?.classList.toggle('dock', dock) // 整個區塊也記著：按鈕搬到圖的外面
      view.y = ease(view.y, pivotScreenY(H, dock && insp?.offsetHeight ? insp.offsetHeight : null, 50, TOP_PAD), dt, 6)
      const tt = (now - t0) / 1000
      P.items = items.map((it) => project(it, rot, view, pivot))
      P.hubs = hubs.map((h) => project(h, rot, view, pivot))
      P.clusters = clusters.map((c) => project(c, rot, view, pivot))
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
      // 牽連（T-436）：兩個詞的路徑 → 只亮路徑上的亮點與把相鄰兩個連起來的新聞；否則選了亮點就連同它牽連的亮點一起亮
      const pathIds = lv.path && lv.path.ids.every((id) => hubIdx.has(id)) ? lv.path.ids : null
      const web = !pathIds && sel?.kind === 'hub' && lv.web?.hub.id === sel.id ? lv.web : null
      if (pathIds) {
        relHubs = new Set(pathIds)
        relItems = new Set(items.filter((x) => pathIds.some((id, i) => i > 0 && x.m.includes(id) && x.m.includes(pathIds[i - 1]))).map((x) => x.id))
      } else if (web) {
        relHubs = new Set([web.hub.id, ...web.first.map((f) => f.hub.id)])
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
      // 亮點之間的牽連線：粗細與亮度照同時提到的則數；路徑一律粗黃線
      // prog＝畫到幾分（0～1），線從 a 往 b 長出來；畫到一半時前端放一顆亮點
      const hubLine = (a: string, b: string, prog = 1) => {
        const pa = P.hubs[hubIdx.get(a)!], pb = P.hubs[hubIdx.get(b)!]
        if (prog <= 0) return
        const ex = pa[0] + (pb[0] - pa[0]) * prog, ey = pa[1] + (pb[1] - pa[1]) * prog
        ctx.beginPath()
        ctx.moveTo(pa[0], pa[1])
        ctx.lineTo(ex, ey)
        ctx.stroke()
        if (prog < 1) ctx.drawImage(SP.sel, ex - 7, ey - 7, 14, 14)
      }
      const pathEl = (now - lv.pathAt) / 1000, webEl = (now - lv.webAt) / 1000
      /** 路徑上第 i 個亮點被點亮了幾秒（還沒到 → 負數） */
      const litFor = (i: number) => pathEl - i * HOP
      if (pathIds) {
        ctx.strokeStyle = 'rgba(255,210,63,0.9)'
        ctx.lineWidth = 2.4
        for (let i = 1; i < pathIds.length; i++) hubLine(pathIds[i - 1], pathIds[i], clamp01(litFor(i - 1) / DRAW))
      } else if (web) {
        const mx = web.first[0]?.n ?? 1
        for (const e of web.edges) {
          const f = e.n / mx
          const fade = e.deg === 1 ? 1 : clamp01((webEl - WEB_DRAW) / 0.3) // 彼此的線等中心的射完再浮出來
          ctx.strokeStyle = `rgba(255,210,63,${(e.deg === 1 ? 0.3 + 0.6 * f : 0.1 + 0.25 * f) * fade})`
          ctx.lineWidth = e.deg === 1 ? 0.8 + 2.2 * f : 0.6
          hubLine(e.a, e.b, e.deg === 1 ? clamp01(webEl / WEB_DRAW) : fade)
        }
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
        const p = P.hubs[i]
        // 路徑上的亮點：輪到它才亮、亮的那一下彈一下；一個亮點的牽連：中心先亮、其餘等線射到才亮
        const pi = pathIds ? pathIds.indexOf(h.id) : -1
        const since = pi >= 0 ? litFor(pi) : web && h.id !== web.hub.id && relHubs?.has(h.id) ? webEl - WEB_DRAW : 1
        const on = (!relHubs || relHubs.has(h.id)) && since >= 0
        const r = hubSize(h.n) * p[3] * (1 + 0.06 * Math.sin(tt * 2 + i)) * pop(since)
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
      // 牽連到的亮點名字優先放（中心那顆最先），其餘照平常的順序
      const hot = relHubs ? hEls.filter((x) => relHubs!.has(x.h.id)).sort((a, b) => Number(b.h.id === sel?.id) - Number(a.h.id === sel?.id)) : []
      const hWant = hEls.filter((x) => !relHubs?.has(x.h.id)).slice(0, phone ? Math.round(7 * zf) : 16)
      const hubWant = ({ h, e }: { h: (typeof hubs)[number]; e: HTMLElement }) => {
        const p = P.hubs[hubIdx.get(h.id)!]
        const [w, hh] = sizes.get(e)!
        return { key: `h${h.id}`, x: p[0], y: p[1] + hubSize(h.n) * p[3] + 10, w, h: hh, e }
      }
      // 牽連到的亮點名字最優先（主題名也讓路），再來主題名，最後其餘亮點
      const want = [
        ...hot.map(hubWant),
        ...cWant.map(({ c, e }) => {
          const at = clusterLabelAt(P.clusters[clusters.indexOf(c)], center)
          const [w, h] = sizes.get(e)!
          return { key: `c${c.k}`, x: at.x, y: at.y, w, h, e }
        }),
        ...hWant.map(hubWant),
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
        w.e.classList.toggle('on', sel?.kind === 'hub' && w.key === `h${sel.id}`)
        if (w.key.startsWith('c')) {
          const p = P.clusters[clusters.findIndex((c) => `c${c.k}` === w.key)]
          ctx.moveTo(p[0], p[1])
          ctx.lineTo(at.x, at.y)
        }
      }
      ctx.stroke()
      // 牽連線中點寫則數（路徑每一步；一個亮點的牽連只寫跟中心的那幾條）
      const counts: [string, string, number][] = pathIds ? pathIds.slice(1).map((id, i) => [pathIds[i], id, lv.path!.n[i]]) : web ? web.edges.filter((e) => e.deg === 1).map((e) => [e.a, e.b, e.n]) : []
      if (counts.length) {
        ctx.font = '11px ui-monospace, Menlo, Consolas, monospace'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        counts.forEach(([a, b, n], i) => {
          const pa = P.hubs[hubIdx.get(a)!], pb = P.hubs[hubIdx.get(b)!]
          if (Math.hypot(pa[0] - pb[0], pa[1] - pb[1]) < 48) return // 線太短，數字會疊成一團；放大就看得到
          if ((pathIds ? litFor(i) / DRAW : webEl / WEB_DRAW) < 1) return // 線畫到底才寫數字
          const x = (pa[0] + pb[0]) / 2, y = (pa[1] + pb[1]) / 2
          const text = String(n), w = ctx.measureText(text).width + 8
          ctx.fillStyle = 'rgba(5,5,6,0.85)'
          ctx.fillRect(x - w / 2, y - 8, w, 16)
          ctx.fillStyle = '#ffd23f'
          ctx.fillText(text, x, y)
        })
      }
      // 選取的那顆畫框；檢視框跟著它（窄畫面貼在下緣）
      if (sel && insp) {
        const p = sel.kind === 'item' ? P.items[items.findIndex((x) => x.id === sel.id)] : P.hubs[hubIdx.get(sel.id) ?? -1]
        if (p) {
          if (sel.kind === 'item') {
            ctx.strokeStyle = '#ffd23f'
            ctx.lineWidth = 1.2
            ctx.strokeRect(p[0] - 9, p[1] - 9, 18, 18)
          }
          const w = insp.offsetWidth, h = insp.offsetHeight
          let x = dock ? (W - w) / 2 : p[0] + 18, y = dock ? H - 50 - h : p[1] - h - 14
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
    let drag: { sx: number; sy: number; lx: number; ly: number; d: number; s: number } | null = null
    let moved = 0
    const xy = (e: PointerEvent): [number, number] => {
      const r = stage.getBoundingClientRect()
      return [e.clientX - r.left, e.clientY - r.top]
    }
    const pinch = () => {
      const a = [...pts.values()]
      return a.length === 2 ? Math.hypot(a[0][0] - a[1][0], a[0][1] - a[1][1]) : 0
    }
    // 一動手就不再自己轉（轉到想看的角度，放開後不會又轉走）
    const stopAuto = () => {
      if (!live.current.auto) return
      live.current.auto = false
      setAuto(false)
    }
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement
      if (!t.closest('.gx-search')) setOpen(false)
      if (t.closest('.gx-insp,.gx-ctl,.gx-search,.gx-path')) return
      goal = null
      stage.setPointerCapture(e.pointerId)
      pts.set(e.pointerId, xy(e))
      const [x, y] = xy(e)
      drag = { sx: x, sy: y, lx: x, ly: y, d: pinch(), s: view.s }
      moved = 0
    }
    const onMove = (e: PointerEvent) => {
      if (!pts.has(e.pointerId) || !drag) return
      const [x, y] = xy(e)
      pts.set(e.pointerId, [x, y])
      if (pts.size === 2 && drag.d) {
        view.s = Math.max(home.s * 0.6, Math.min(home.s * 9, drag.s * (pinch() / drag.d)))
        moved = 99
        stopAuto()
        return
      }
      rot = dragRotate(rot, (x - drag.lx) * DRAG_RAD, (y - drag.ly) * DRAG_RAD)
      drag.lx = x
      drag.ly = y
      moved = Math.max(moved, Math.hypot(x - drag.sx, y - drag.sy))
      if (moved >= 6) stopAuto()
    }
    const onUp = (e: PointerEvent) => {
      const was = pts.size
      pts.delete(e.pointerId)
      if (pts.size) {
        const [p] = pts.values()
        drag = { sx: p[0], sy: p[1], lx: p[0], ly: p[1], d: 0, s: view.s }
        return
      }
      drag = null
      if (was === 1 && moved < 6 && !(e.target as HTMLElement).closest('.gx-insp,.gx-ctl,.gx-search,.gx-path')) {
        const [x, y] = xy(e)
        const hit = pickAt(
          x,
          y,
          hubs.map((h, i) => ({ id: h.id, p: P.hubs[i], r: hubSize(h.n) * P.hubs[i][3] })),
          items.map((it, i) => ({ id: it.id, p: P.items[i] })),
        )
        setSel(hit)
        if (hit) setPath(null) // 點了別的，就不再畫兩個詞的路徑
      }
    }
    const onWheel = (e: WheelEvent) => {
      if ((e.target as HTMLElement).closest('.gx-search')) return // 下拉列表自己捲
      e.preventDefault()
      stopAuto()
      goal = null
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
  const terms = parseQuery(q)
  // 新聞光點的內容：桌機放在圖裡的檢視框，手機放在圖外面那排（使用者 2026-10-23：「有時候會擋到星雲圖？全都改成第一張」）
  const itemBody = item ? (
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
  ) : null
  return (
    <section className="gx">
      <p className="gx-sum">{galaxySummary(data)}</p>
      <div className="gx-stage" ref={stageRef}>
        <canvas ref={canvasRef} />
        <div ref={labelsRef} />
        <form
          className="gx-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <Search size={16} aria-hidden="true" />
          <input
            type="search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => e.key === 'Escape' && clearSearch()}
            placeholder="找股名、代號或主題；打兩個詞看怎麼牽"
            aria-label="在星雲圖找"
            enterKeyHint="search"
            maxLength={40}
            autoComplete="off"
          />
          {q && (
            <button type="button" className="gx-search-x" onClick={clearSearch} aria-label="清除">
              <X size={16} aria-hidden="true" />
            </button>
          )}
          {open && terms.length > 0 && (
            <ul className="gx-hits">
              {terms.length === 2 && (
                <li className="gx-hit-tip">
                  Enter ＝ 找「{terms[0]}」和「{terms[1]}」怎麼牽在一起
                </li>
              )}
              {hits.map((h) => (
                <li key={h.kind === 'hub' ? h.hub.id : h.item.id}>
                  {h.kind === 'hub' ? (
                    <button type="button" className={`gx-hit ${hubClass(h.hub.type)}`} onClick={() => pick(h)}>
                      <b>{h.hub.label}</b>
                      <small>
                        {hubTypeLabel(h.hub.type)}
                        {h.hub.type === 'tw' && h.hub.sub ? ` ${h.hub.sub}` : ''}・{h.hub.n} 則
                      </small>
                    </button>
                  ) : (
                    <button type="button" className={`gx-hit news ${h.item.kind}`} onClick={() => pick(h)}>
                      <b>{h.item.title}</b>
                      <small>
                        {h.item.src}・{shortWhen(h.item.t, today)}
                      </small>
                    </button>
                  )}
                </li>
              ))}
              {hits.length === 0 && terms.length === 1 && <li className="gx-hit-tip">近 {data.days} 天的新聞裡沒有「{terms[0]}」</li>}
            </ul>
          )}
        </form>
        {(path || pathMsg) && (
          <div className="gx-path" role="status">
            {path ? (
              <>
                {path.hubs.map((h, i) => (
                  <Fragment key={h.id}>
                    {i > 0 && <i>{path.n[i - 1]} 則</i>}
                    <button type="button" className={`gx-chip ${hubClass(h.type)}${sel?.id === h.id ? ' on' : ''}`} onClick={() => setSel({ kind: 'hub', id: h.id })}>
                      {h.label}
                    </button>
                  </Fragment>
                ))}
                <small>{path.hubs.length === 1 ? '同一個' : `${path.hubs.length - 1} 度`}</small>
              </>
            ) : (
              <span>{pathMsg}</span>
            )}
            <button
              type="button"
              className="gx-x"
              onClick={() => {
                setPath(null)
                setPathMsg('')
              }}
              aria-label="不看路徑"
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        )}
        {(item || hub) && (
          <div className={`gx-insp${hub ? ' hub' : ''}`} ref={inspRef}>
            <button className="gx-x" onClick={() => setSel(null)} aria-label="關閉">
              <X size={16} aria-hidden="true" />
            </button>
            {item && itemBody}
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
                {web && web.first.length > 0 && (
                  <div className="gx-chips gx-web">
                    <span className="gx-web-h">一起被提到</span>
                    {web.first.slice(0, 8).map((f) => (
                      <button
                        key={f.hub.id}
                        type="button"
                        className={`gx-chip ${hubClass(f.hub.type)}`}
                        title={`${f.n} 則同時提到${hub.label}和${f.hub.label}；點了換看${f.hub.label}`}
                        onClick={() => {
                          setPath(null)
                          setSel({ kind: 'hub', id: f.hub.id })
                        }}
                      >
                        {f.hub.label}
                        <small>{f.n}</small>
                      </button>
                    ))}
                  </div>
                )}
                <div className="gx-news">
                  {data.items
                    .filter((x) => x.m.includes(hub.id))
                    .sort((a, b) => (a.t < b.t ? 1 : -1))
                    .slice(0, 3)
                    .map((x) => (
                      <p key={x.id} className="gx-row">
                        {shortWhen(x.t, today)}　{x.title}
                      </p>
                    ))}
                </div>
                <div className="gx-acts">
                  <button className="gx-focus" onClick={() => setFocus(hub.id)}>
                    <Maximize2 size={14} aria-hidden="true" />
                    單獨看 {hub.n} 則
                  </button>
                  {hub.type === 'tw' && hub.sub && (
                    <button className="gx-go" onClick={() => onStock(hub.sub!, hub.label)}>
                      開個股頁
                    </button>
                  )}
                </div>
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
          <button onClick={() => { live.current.zoom = 1.5; setAuto(false) }} aria-label="放大">
            <Plus size={17} aria-hidden="true" />
          </button>
          <button onClick={() => { live.current.zoom = 1 / 1.5; setAuto(false) }} aria-label="縮小">
            <Minus size={17} aria-hidden="true" />
          </button>
          <button onClick={() => { live.current.reset += 1; live.current.sel = null; setSel(null) }} aria-label="回到一開始" title="回到一開始">
            <Undo2 size={17} aria-hidden="true" />
          </button>
        </div>
      </div>
      {(hub || item) && (
        <div className={`gx-bar${item ? ' news' : ''}`}>
          <button className="gx-x" onClick={() => setSel(null)} aria-label="關閉">
            <X size={16} aria-hidden="true" />
          </button>
          {item && itemBody}
          {hub && (
            <>
              <span className={`gx-bar-name ${hubClass(hub.type)}`}>
                {hub.label}
                <small>{hub.n} 則</small>
              </span>
              <button className="gx-focus" onClick={() => setFocus(hub.id)}>
                <Maximize2 size={14} aria-hidden="true" />
                單獨看 {hub.n} 則
              </button>
              {hub.type === 'tw' && hub.sub && (
                <button className="gx-go" onClick={() => onStock(hub.sub!, hub.label)}>
                  開個股頁
                </button>
              )}
            </>
          )}
        </div>
      )}
      {focus && (
        <GalaxyFocus
          data={data}
          hubId={focus}
          onRecenter={(id) => {
            setFocus(id)
            setSel({ kind: 'hub', id })
          }}
          onClose={() => setFocus(null)}
          onStock={onStock}
        />
      )}
      <p className="gx-help">光點＝一則新聞，靠在一起＝內容相近；亮點＝被很多則提到的股票、主題。拖曳旋轉、兩指縮放；點一顆看細節，它會移到中間、之後繞著它轉。要快速讀新聞用上面三張卡或「清單」。</p>
    </section>
  )
}
