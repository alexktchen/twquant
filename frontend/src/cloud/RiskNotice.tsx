// 第一次進網站的投資風險警語：勾選同意後「同意並進入」才能按（每台裝置一次；警語改版會再問）。
import { useEffect, useRef, useState } from 'react'
import { RISK_AGREE, RISK_POINTS, RISK_TITLE } from './risk'

export function RiskNotice({ onAgree }: { onAgree: () => void }) {
  const [checked, setChecked] = useState(false)
  const card = useRef<HTMLDivElement>(null)
  useEffect(() => {
    card.current?.focus()
    // 彈窗開著時後面的頁面不跟著捲（手機整頁捲動）
    const before = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = before
    }
  }, [])
  return (
    <div className="cq-risk" role="dialog" aria-modal="true" aria-labelledby="cq-risk-title">
      <div className="cq-risk-card" ref={card} tabIndex={-1}>
        <h2 id="cq-risk-title">{RISK_TITLE}</h2>
        <ol className="cq-risk-list">
          {RISK_POINTS.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ol>
        <label className="cq-risk-check">
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          <span>{RISK_AGREE}</span>
        </label>
        <button className="cq-risk-go" disabled={!checked} onClick={onAgree}>
          同意並進入
        </button>
      </div>
    </div>
  )
}
