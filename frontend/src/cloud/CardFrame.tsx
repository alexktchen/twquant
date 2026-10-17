// 首頁每張卡的外框（使用者 2026-10-17）：標題列點一下收起／展開；收起時輪播這張卡的重點（slides），展開時顯示完整內容。
// 收起與否由首頁版面決定（存在帳號），這裡不自己記。
import type { ReactNode } from 'react'
import { Reel, type Slide } from './Reel'

export function CardFrame({
  title,
  when,
  extra,
  top,
  collapsed,
  onToggle,
  slides,
  reelLabel,
  reelPaused,
  empty,
  className,
  children,
}: {
  title: string
  /** 標題旁的灰字（日期、來源） */
  when?: ReactNode
  /** 標題列右邊的東西（日期切換、分頁）；收起時也顯示 */
  extra?: ReactNode
  /** 標題列下面、收起展開都顯示的東西（例：行事曆的搜尋框） */
  top?: ReactNode
  collapsed: boolean
  onToggle: () => void
  /** 收起時輪播的重點，一則一張 */
  slides: Slide[]
  reelLabel?: string
  reelPaused?: boolean
  /** 收起時沒有可輪播的內容要說什麼 */
  empty?: string
  className?: string
  children: ReactNode
}) {
  return (
    <section className={['cq-war-card', className, collapsed ? 'cq-card-folded' : ''].filter(Boolean).join(' ')}>
      <header>
        <button className="cq-news-head" aria-expanded={!collapsed} onClick={onToggle}>
          <h2>{title}</h2>
          {when && <span className="cq-war-when">{when}</span>}
          <span className="cq-mood-caret">{collapsed ? '展開 ▼' : '收起 ▲'}</span>
        </button>
        {extra}
      </header>
      {top}
      {collapsed ? (
        slides.length > 0 ? (
          <Reel slides={slides} label={reelLabel} paused={reelPaused} />
        ) : (
          <p className="cq-war-mute">{empty ?? '還沒有資料。'}</p>
        )
      ) : (
        children
      )}
    </section>
  )
}
