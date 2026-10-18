// 標記圖示（lucide，MIT 授權；線條一致、單色，隨顏色類別上色）。代號定義在 badges.ts。
import {
  ArrowDownToLine,
  Ban,
  ChevronsDown,
  Coins,
  FileClock,
  Flame,
  Mic,
  Rocket,
  Sparkles,
  TrendingDown,
  TriangleAlert,
  UserMinus,
  Users,
  type LucideIcon,
} from 'lucide-react'
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
}

export function Icon({ name, size = 14 }: { name: BadgeIcon; size?: number }) {
  const C = ICONS[name]
  return <C size={size} strokeWidth={2.2} aria-hidden="true" />
}

/** 一個標記：compact＝名單列（圖示＋極短字）；否則個股頁（圖示＋名稱＋極短字）。 */
export function BadgeChip({ b, compact = false }: { b: Badge; compact?: boolean }) {
  const dir = b.icon === 'usersUp' ? '↑' : b.icon === 'usersDown' ? '↓' : ''
  return (
    <span className={`cq-b cq-b-${b.tone}${compact ? ' cq-b-compact' : ''}`} title={b.title} aria-label={`${b.label}${b.short ? ` ${b.short}` : ''}`}>
      <Icon name={b.icon} size={compact ? 13 : 14} />
      {dir}
      {!compact && <span className="cq-b-label">{b.label}</span>}
      {b.short && <span className="cq-b-short">{b.short}</span>}
    </span>
  )
}
