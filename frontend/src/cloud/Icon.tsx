// 標記圖示（lucide，MIT 授權；線條一致、單色，隨顏色類別上色）。代號定義在 badges.ts。
import { ArrowDownToLine, Ban, ChevronsDown, Coins, FileClock, Flag, Flame, Mic, Newspaper, Rocket, Sparkles, Square, TrendingDown, TriangleAlert, UserMinus, Users, type LucideIcon } from 'lucide-react'
import type { Badge, BadgeIcon } from './badges'

const ICONS: Record<BadgeIcon, LucideIcon> = {
  ban: Ban,
  alert: TriangleAlert,
  trendDown: TrendingDown,
  userMinus: UserMinus,
  coins: Coins,
  mic: Mic,
  fileClock: FileClock,
  usersUp: Users,
  usersDown: Users,
  floor: ArrowDownToLine,
  arrowDown: ChevronsDown,
  sparkles: Sparkles,
  flame: Flame,
  rocket: Rocket,
  newspaper: Newspaper,
  box: Square,
  flag: Flag,
}

export function Icon({ name, size = 14 }: { name: BadgeIcon; size?: number }) {
  const C = ICONS[name]
  return <C size={size} strokeWidth={2.2} aria-hidden="true" />
}

/** 一個標記：compact＝名單列（圖示＋極短字）；否則個股頁（圖示＋名稱＋極短字）。 */
export function BadgeChip({ b, compact = false, onClick }: { b: Badge; compact?: boolean; onClick?: () => void }) {
  const dir = b.icon === 'usersUp' ? '↑' : b.icon === 'usersDown' ? '↓' : ''
  const cls = `cq-b cq-b-${b.tone}${compact ? ' cq-b-compact' : ''}`
  const label = `${b.label}${b.short ? ` ${b.short}` : ''}`
  const inner = (
    <>
      <Icon name={b.icon} size={compact ? 13 : 14} />
      {dir}
      {!compact && <span className="cq-b-label">{b.label}</span>}
      {b.short && <span className="cq-b-short">{b.short}</span>}
    </>
  )
  // 點得動的標記（目前只有「相關新聞」：跳到個股頁那一塊）。名單列整列可點，所以要擋住冒泡，不然點小標＝點整列
  if (onClick)
    return (
      <button
        type="button"
        className={`${cls} cq-b-tap`}
        title={b.title}
        aria-label={label}
        onClick={(e) => {
          e.stopPropagation()
          onClick()
        }}
      >
        {inner}
      </button>
    )
  return (
    <span className={cls} title={b.title} aria-label={label}>
      {inner}
    </span>
  )
}
