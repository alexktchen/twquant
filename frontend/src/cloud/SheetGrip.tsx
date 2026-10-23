// 手機底部抽屜的把手（T-442）：拖它把整張卡往下推到「只露出頭」、再往下推就關掉、往上拉回全開；露頭時點一下也拉回全開。
// 卡本身固定高度（news-v2.css 的 --sheet-h），拖的是位置不是高度（使用者：「請用一個固定高度」）。
// 只有把手會接拖曳，卡裡面照常捲動。桌機（寬 >820）把手用 CSS 藏起來。
import { useRef } from 'react'
import { SHEET_TAP, clampSheetOffset, settleSheet, sheetOffset, sheetVelocity, type SheetPos } from './sheet'

export function SheetGrip({ sheetRef, onClose }: { sheetRef: React.RefObject<HTMLElement | null>; onClose: () => void }) {
  const pos = useRef<SheetPos>('open')
  const drag = useRef<{ id: number; y0: number; off0: number; lastY: number; lastT: number; vy: number } | null>(null)

  const apply = (offset: number, animate: boolean) => {
    const el = sheetRef.current
    if (!el) return
    el.style.transition = animate ? 'transform .22s ease-out' : 'none'
    el.style.transform = offset ? `translateY(${offset}px)` : ''
  }
  const settle = (next: SheetPos) => {
    const el = sheetRef.current
    if (!el) return
    if (next === 'closed') {
      apply(el.offsetHeight, true)
      onClose()
      return
    }
    pos.current = next
    el.classList.toggle('peek', next === 'peek')
    apply(sheetOffset(next, el.offsetHeight), true)
  }

  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = sheetRef.current
    if (!el) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { id: e.pointerId, y0: e.clientY, off0: sheetOffset(pos.current, el.offsetHeight), lastY: e.clientY, lastT: e.timeStamp, vy: 0 }
  }
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    d.vy = sheetVelocity(e.clientY - d.lastY, e.timeStamp - d.lastT)
    d.lastY = e.clientY
    d.lastT = e.timeStamp
    apply(clampSheetOffset(d.off0 + e.clientY - d.y0), false)
  }
  const onUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    const el = sheetRef.current
    if (!d || d.id !== e.pointerId || !el) return
    drag.current = null
    const moved = e.clientY - d.y0
    if (Math.abs(moved) < SHEET_TAP) {
      settle(pos.current === 'peek' ? 'open' : pos.current)
      return
    }
    settle(settleSheet(clampSheetOffset(d.off0 + moved), d.vy, pos.current, el.offsetHeight))
  }
  const onCancel = () => {
    if (!drag.current) return
    drag.current = null
    settle(pos.current)
  }

  return (
    <div className="cq-sheet-grip" role="presentation" aria-hidden="true" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onCancel}>
      <i />
    </div>
  )
}
