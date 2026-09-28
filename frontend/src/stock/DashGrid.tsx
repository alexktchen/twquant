// 個股儀表板的可拖拉格（T-322）：右上 ⠿ 拖曳換位置、右下角拖曳調寬（對齊 12 欄）與高度，版面存 localStorage。
// 瀑布流（T-324）：每張卡量自己的實際高度換算占幾個細列，矮卡會疊進高卡旁的空位，同一欄可直疊多張。
import { createContext, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { GRID, colsFromWidth, moveCard, parseLayout, resolveLayout, rowSpan, type Layout } from './layout.ts'

/** 使用者設定的卡片高度（px，放開把手後才更新；null＝跟內容走）。K 線卡用它把圖撐高。 */
export const CardHeightContext = createContext<number | null>(null)

export type DashItem = { id: string; w: number; node: ReactNode }

/** 瀑布流的一格：量內層實際高度 → grid-row: span N；拖曳把手與縮放角都放在內層，貼著卡片本體。 */
function Cell({ id, half, cls, style, h, children, handlers }: {
  id: string
  half: boolean
  cls: string
  style: CSSProperties
  h: number | null
  children: ReactNode
  handlers: React.HTMLAttributes<HTMLDivElement>
}) {
  const inner = useRef<HTMLDivElement>(null)
  const [span, setSpan] = useState(() => rowSpan(h ?? GRID.minHeight))
  useLayoutEffect(() => {
    const el = inner.current
    if (!el) return
    const measure = () => setSpan(rowSpan(el.offsetHeight))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return (
    <div data-card={id} data-half={half ? '1' : '0'} className={cls} style={{ ...style, gridRowEnd: `span ${span}` }} {...handlers}>
      <div ref={inner} className="dash-card-in" style={{ height: h ?? undefined }}>
        {children}
      </div>
    </div>
  )
}

function loadLayout(key: string): Layout | null {
  try {
    return parseLayout(localStorage.getItem(key))
  } catch {
    return null // 隱私模式等讀不到：用預設版面
  }
}

export function DashGrid({ storageKey, items, toolbar }: { storageKey: string; items: DashItem[]; toolbar?: ReactNode }) {
  const [saved, setSaved] = useState<Layout | null>(() => loadLayout(storageKey))
  const defaultsKey = items.map((i) => `${i.id}:${i.w}`).join(',')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const layout = useMemo(() => resolveLayout(items.map(({ id, w }) => ({ id, w })), saved), [defaultsKey, saved])
  const [dragId, setDragId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const [live, setLive] = useState<{ id: string; w: number; h: number } | null>(null)
  const grid = useRef<HTMLDivElement>(null)
  const byId = new Map(items.map((i) => [i.id, i]))

  const save = (l: Layout | null) => {
    setSaved(l)
    try {
      if (l) localStorage.setItem(storageKey, JSON.stringify(l))
      else localStorage.removeItem(storageKey)
    } catch {
      /* 寫不進去：版面只在本次會話有效 */
    }
  }

  const startResize = (id: string) => (e: React.PointerEvent<HTMLSpanElement>) => {
    const card = e.currentTarget.parentElement
    const g = grid.current
    if (!card || !g) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const rect = card.getBoundingClientRect()
    const cs = getComputedStyle(g)
    const gap = parseFloat(cs.columnGap) || 0
    // 窄螢幕格子不是 12 欄（CSS 會把卡片改排），這時只調高度、不改寬度
    const full = cs.gridTemplateColumns.split(' ').length === GRID.cols
    const w0 = layout.sizes[id].w
    let cur = { id, w: w0, h: rect.height }
    const move = (ev: PointerEvent) => {
      cur = {
        id,
        w: full ? colsFromWidth(ev.clientX - rect.left, g.clientWidth, gap) : w0,
        h: Math.max(GRID.minHeight, Math.round(ev.clientY - rect.top)),
      }
      setLive(cur)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setLive(null)
      save({ ...layout, sizes: { ...layout.sizes, [id]: { w: cur.w, h: cur.h } } })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const customized = saved !== null
  return (
    <>
      <div className="dash-layout-bar">
        {toolbar}
        <span className="dash-layout-hint">拖曳卡片右上角 ⠿ 可移動位置，拖曳右下角可調整大小（雙擊右下角恢復該卡預設大小）</span>
        {customized && (
          <button type="button" className="dash-btn" onClick={() => save(null)}>
            重設版面
          </button>
        )}
      </div>
      <div className="dash-grid dash-masonry" ref={grid}>
        {layout.order.map((id) => {
          const it = byId.get(id)
          if (!it) return null
          const sz = live?.id === id ? live : layout.sizes[id]
          const style = { '--w': sz.w } as CSSProperties
          return (
            <Cell
              key={id}
              id={id}
              half={sz.w > 3}
              h={sz.h}
              cls={`dash-card${sz.h !== null ? ' has-h' : ''}${dragId === id ? ' is-dragging' : ''}${overId === id && dragId !== id ? ' is-over' : ''}`}
              style={style}
              handlers={{
                onDragOver: (e) => {
                  if (!dragId) return
                  e.preventDefault()
                  e.dataTransfer.dropEffect = 'move'
                  if (overId !== id) setOverId(id)
                },
                onDragLeave: () => overId === id && setOverId(null),
                onDrop: (e) => {
                  e.preventDefault()
                  if (dragId && dragId !== id) save({ ...layout, order: moveCard(layout.order, dragId, id) })
                  setDragId(null)
                  setOverId(null)
                },
              }}
            >
              <button
                type="button"
                className="dash-grip"
                draggable
                title="拖曳移動這張卡"
                aria-label="拖曳移動這張卡"
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = 'move'
                  e.dataTransfer.setData('text/plain', id)
                  const card = e.currentTarget.parentElement
                  if (card) e.dataTransfer.setDragImage(card, 24, 16)
                  setDragId(id)
                }}
                onDragEnd={() => {
                  setDragId(null)
                  setOverId(null)
                }}
              >
                ⠿
              </button>
              <CardHeightContext.Provider value={layout.sizes[id].h}>{it.node}</CardHeightContext.Provider>
              <span
                className="dash-resize"
                title="拖曳調整大小；雙擊恢復預設"
                onPointerDown={startResize(id)}
                onDoubleClick={() => save({ ...layout, sizes: { ...layout.sizes, [id]: { w: it.w, h: null } } })}
              />
            </Cell>
          )
        })}
      </div>
    </>
  )
}
