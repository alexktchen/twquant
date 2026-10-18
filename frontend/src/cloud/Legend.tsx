// 「圖示說明」：名單表頭的按鈕打開，列出每種標記的圖示、名稱、一句話（使用者 2026-10-18：「指引明顯」）。
// 蓋在畫面上的彈出框（手機從底部升起、桌機置中），點背景或 ✕ 關閉；不擠進名單欄裡。
import { X } from 'lucide-react'
import { useEffect } from 'react'
import { BADGE_LEGEND } from './badges'
import { Icon } from './Icon'

export function BadgeLegend({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="cq-sheet-backdrop" onClick={onClose}>
      <div className="cq-sheet" role="dialog" aria-modal="true" aria-label="圖示說明" onClick={(e) => e.stopPropagation()}>
        <div className="cq-legend-head">
          <b>圖示說明</b>
          <button className="cq-icon-btn" aria-label="關閉" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="cq-legend-dots">
          <span className="cq-dots">
            <i className="in" />
            <i className="in" />
            <i />
            <i className="in" />
            <i className="na" />
            <i className="in" />
          </span>
          <span>近 6 個交易日有沒有上榜（舊→新，空框＝那天沒打分）</span>
        </div>
        <ul>
          {BADGE_LEGEND.map((l) => (
            <li key={l.label}>
              <span className={`cq-b cq-b-${l.tone}`}>
                <Icon name={l.icon} />
                <span className="cq-b-label">{l.label}</span>
              </span>
              <span>{l.meaning}</span>
            </li>
          ))}
        </ul>
        <p>紅＝交易受限、黃＝留意、藍＝日程、灰＝籌碼走勢、橘＝正面。都只是提醒，名單不因此刪減。</p>
      </div>
    </div>
  )
}
