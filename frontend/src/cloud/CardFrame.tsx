// 首頁每張卡的外框（使用者 2026-10-17：點標題收起／展開，收起時輪播重點）。
// 2026-10-18 改版（地鐵告示）：每張卡左邊一條路線色、一個圖示、短標題；標題列右邊只放日期與操作；說明不放卡上。
import {
  Activity,
  BarChart3,
  CalendarDays,
  ChevronDown,
  Compass,
  Eye,
  Flag,
  Globe,
  Landmark,
  LayoutGrid,
  ListChecks,
  Megaphone,
  Navigation,
  Scale,
  Sunrise,
  Trophy,
  Wallet,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { CARD_META, type HomeIcon } from './home'
import type { CardId } from './layout'
import { Reel, type Slide } from './Reel'

const ICONS: Record<HomeIcon, LucideIcon> = {
  sunrise: Sunrise,
  globe: Globe,
  activity: Activity,
  scale: Scale,
  barChart: BarChart3,
  landmark: Landmark,
  megaphone: Megaphone,
  listChecks: ListChecks,
  trophy: Trophy,
  calendar: CalendarDays,
  grid: LayoutGrid,
  compass: Compass,
  wallet: Wallet,
  eye: Eye,
  flag: Flag,
  zap: Zap,
  navigation: Navigation,
}

export function HomeIconSvg({ name, size = 18 }: { name: HomeIcon; size?: number }) {
  const C = ICONS[name]
  return <C size={size} strokeWidth={2.2} aria-hidden="true" />
}

export function CardFrame({
  id,
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
  /** 哪張卡：決定路線色與圖示（home.ts CARD_META） */
  id: CardId
  /** 標題（預設用 CARD_META 的短標題） */
  title?: string
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
  const meta = CARD_META[id]
  return (
    <section className={['cq-war-card', 'cq-hc', `cq-hc-${meta.tone}`, className, collapsed ? 'cq-card-folded' : ''].filter(Boolean).join(' ')}>
      <header className="cq-hc-head">
        <button className="cq-hc-title" aria-expanded={!collapsed} onClick={onToggle}>
          <span className="cq-hc-icon">
            <HomeIconSvg name={meta.icon} />
          </span>
          <h2>{title ?? meta.title}</h2>
          {when && <span className="cq-hc-when">{when}</span>}
          <ChevronDown size={18} className={collapsed ? 'cq-hc-caret' : 'cq-hc-caret flip'} aria-hidden="true" />
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
        <div className="cq-hc-body">{children}</div>
      )}
    </section>
  )
}
