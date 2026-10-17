// 首頁「編輯版面」（使用者 2026-10-17：「每張卡可以自由移動位子，依照使用者帳號儲存擺置位置」）：
// 每張卡一列——按住右邊 ≡ 拖曳到想要的位置（手機手指、電腦滑鼠都可以），或按 ▲ ▼ 一格一格移；「收起／展開」設定平常的樣子。
// 每次放開就存（登入後存在帳號）。拖曳的做法與追蹤清單相同（reorder.ts）。
import { useRef, useState } from 'react'
import { HOME_CARDS, type CardId, type Layout } from './layout'
import { edgeScroll, moveItem, rowAt } from './reorder'

export function LayoutEditor({
  layout,
  onOrder,
  onToggle,
  onReset,
  onDone,
  error,
}: {
  layout: Layout
  onOrder: (order: CardId[]) => void
  onToggle: (id: CardId) => void
  onReset: () => void
  onDone: () => void
  error: string
}) {
  const list = useRef<HTMLUListElement>(null)
  const [dragOrder, setDragOrder] = useState<CardId[] | null>(null)
  const [dragging, setDragging] = useState<CardId | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const order = dragOrder ?? layout.order
  const title = (id: CardId) => HOME_CARDS.find((c) => c.id === id)?.title ?? id

  const startDrag = (id: CardId) => (e: React.PointerEvent) => {
    const box = list.current
    if (!box || !e.isPrimary) return
    e.preventDefault()
    const scroller = box.closest('.cq-war') as HTMLElement | null
    let cur = layout.order
    let y = e.clientY
    let frame = 0
    setDragging(id)
    setDragOrder(cur)
    // 每一列高度相同，第 i 個格子永遠是第 i 列，畫面還沒重畫也算得對
    const place = () => {
      const to = rowAt([...box.children].map((li) => li.getBoundingClientRect()), y)
      const from = cur.indexOf(id)
      if (to >= 0 && to !== from) {
        cur = moveItem(cur, from, to)
        setDragOrder(cur)
      }
    }
    // 拖到畫面上下緣時自動捲動
    const tick = () => {
      if (scroller) {
        const r = scroller.getBoundingClientRect()
        const dy = edgeScroll(y, r.top, r.bottom)
        if (dy !== 0 && scroller.scrollHeight > scroller.clientHeight) {
          scroller.scrollTop += dy
          place()
        }
      }
      frame = requestAnimationFrame(tick)
    }
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== e.pointerId) return
      y = ev.clientY
      place()
    }
    const end = (ev: PointerEvent) => {
      if (ev.pointerId !== e.pointerId) return
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      cancelAnimationFrame(frame)
      setDragging(null)
      setDragOrder(null)
      if (cur.join() !== layout.order.join()) onOrder(cur)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
    frame = requestAnimationFrame(tick)
  }

  const shift = (i: number, dir: -1 | 1) => onOrder(moveItem(layout.order, i, i + dir))

  return (
    <section className="cq-war-card cq-layout-edit">
      <header>
        <h2>編輯版面</h2>
        <span className="cq-war-when">按住 ≡ 拖曳，或按 ▲ ▼ 移動；點「收起中／展開中」切換平常的樣子。登入後存在你的帳號，換裝置也一樣</span>
      </header>
      <ul ref={list} className="cq-editing cq-layout-list">
        {order.map((id, i) => {
          const folded = layout.collapsed.includes(id)
          return (
            <li key={id} className={id === dragging ? 'cq-layout-row dragging' : 'cq-layout-row'}>
              <b>{title(id)}</b>
              <button className="cq-row-btn" aria-pressed={folded} onClick={() => onToggle(id)} title="點一下切換：平常收起（輪播重點）或展開">
                {folded ? '收起中' : '展開中'}
              </button>
              <button className="cq-row-btn" aria-label={`${title(id)} 往上`} disabled={i === 0} onClick={() => shift(i, -1)}>
                ▲
              </button>
              <button className="cq-row-btn" aria-label={`${title(id)} 往下`} disabled={i === order.length - 1} onClick={() => shift(i, 1)}>
                ▼
              </button>
              <span className="cq-handle" role="button" aria-label={`拖曳 ${title(id)} 調整位置`} onPointerDown={startDrag(id)}>
                ≡
              </span>
            </li>
          )
        })}
      </ul>
      {error && <p className="cq-war-mute">{error}</p>}
      <div className="cq-layout-actions">
        {confirmReset ? (
          <button
            className="cq-row-btn danger"
            onClick={() => {
              setConfirmReset(false)
              onReset()
            }}
          >
            確定恢復預設
          </button>
        ) : (
          <button className="cq-row-btn" onClick={() => setConfirmReset(true)}>
            恢復預設
          </button>
        )}
        <button className="cq-edit-btn" onClick={onDone}>
          完成
        </button>
      </div>
    </section>
  )
}
