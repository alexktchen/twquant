// 收起的卡片的輪播（使用者 2026-10-17：「卡也要可以縮起來，縮起來後內容要可以輪播」）：每 6 秒換下一則，
// 滑鼠移上去、鍵盤停在裡面或呼叫端要求（例：打開了某則內文）時暫停；‹ › 手動切換，手機左右滑也可以。
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { CAROUSEL_MS, carouselStep } from './war'

export type Slide = { key: string; node: ReactNode }

/** 左右滑多少像素算換一則。 */
const SWIPE_PX = 40

export function Reel({ slides, paused = false, label }: { slides: Slide[]; paused?: boolean; label?: string }) {
  const [i, setI] = useState(0)
  const [hold, setHold] = useState(false)
  const startX = useRef<number | null>(null)
  const n = slides.length
  const at = n > 0 ? Math.min(i, n - 1) : 0

  useEffect(() => {
    if (n < 2 || hold || paused) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') setI((x) => carouselStep(x, n, 1))
    }, CAROUSEL_MS)
    return () => clearInterval(t)
  }, [n, hold, paused])

  if (n === 0) return null
  const step = (dir: 1 | -1) => setI((x) => carouselStep(Math.min(x, n - 1), n, dir))

  return (
    <div
      className={n > 1 ? 'cq-news-reel cq-reel' : 'cq-reel cq-reel-one'}
      onMouseEnter={() => setHold(true)}
      onMouseLeave={() => setHold(false)}
      onFocus={() => setHold(true)}
      onBlur={() => setHold(false)}
      onPointerDown={(e) => {
        if (e.pointerType !== 'mouse') startX.current = e.clientX
      }}
      onPointerUp={(e) => {
        const x0 = startX.current
        startX.current = null
        if (x0 === null || n < 2) return
        const dx = e.clientX - x0
        if (Math.abs(dx) >= SWIPE_PX) step(dx < 0 ? 1 : -1)
      }}
    >
      {n > 1 && (
        <button className="cq-news-nav" aria-label="上一則" onClick={() => step(-1)}>
          ‹
        </button>
      )}
      <div className="cq-reel-slide" aria-live="polite">
        {slides[at].node}
      </div>
      {n > 1 && (
        <button className="cq-news-nav" aria-label="下一則" onClick={() => step(1)}>
          ›
        </button>
      )}
      {n > 1 && (
        <span className="cq-news-count">
          {label ? `${label} ` : ''}
          {at + 1} / {n}
        </span>
      )}
    </div>
  )
}
