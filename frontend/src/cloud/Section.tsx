// 個股頁的區塊外框（2026-10-18 改版）：每塊同一種卡片——左上圖示＋標題、右上一行小字，內容在下面。
// fold＝預設收起，只露標題與一句摘要（漸進展開：次要資訊點了才出來）。
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

export function Section({
  icon: I,
  title,
  meta,
  fold = false,
  summary,
  id,
  onToggle,
  children,
}: {
  icon: LucideIcon
  title: string
  meta?: ReactNode
  fold?: boolean
  /** 收起時標題右邊那一句 */
  summary?: ReactNode
  /** 錨點（例：名單上的「相關新聞」小標點了跳到這塊） */
  id?: string
  /** fold 時展開／收起會呼叫（T-455：盤口資料展開了才抓，省流量） */
  onToggle?: (open: boolean) => void
  children: ReactNode
}) {
  const head = (
    <>
      <I size={16} strokeWidth={2.2} className="cq-sec-icon" aria-hidden="true" />
      <b>{title}</b>
      {(fold ? summary : meta) && <span className="cq-sec-meta">{fold ? summary : meta}</span>}
    </>
  )
  if (fold)
    return (
      <details className="cq-sec cq-sec-fold" id={id} onToggle={onToggle ? (e) => onToggle(e.currentTarget.open) : undefined}>
        <summary className="cq-sec-head">{head}</summary>
        <div className="cq-sec-body">{children}</div>
      </details>
    )
  return (
    <section className="cq-sec" id={id}>
      <div className="cq-sec-head">{head}</div>
      <div className="cq-sec-body">{children}</div>
    </section>
  )
}

/** 數字卡：標籤、大數字、一行小字；tone 決定大數字顏色。onClick 有給就能點（點了展開細節）。 */
export function Kpi({
  label,
  value,
  sub,
  tone = 'plain',
  side,
  on = false,
  onClick,
  title,
}: {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  tone?: 'plain' | 'hot' | 'warn' | 'na' | 'up' | 'down'
  side?: ReactNode
  on?: boolean
  onClick?: () => void
  title?: string
}) {
  return (
    <div
      className={`cq-kpi${on ? ' on' : ''}${onClick ? ' tap' : ''}`}
      onClick={onClick}
      title={title}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => (e.key === 'Enter' || e.key === ' ') && onClick() : undefined}
    >
      <span className="cq-kpi-label">{label}</span>
      <span className="cq-kpi-row">
        <b className={`cq-kpi-value ${tone}`}>{value}</b>
        {side}
      </span>
      {sub && <span className="cq-kpi-sub">{sub}</span>}
    </div>
  )
}
