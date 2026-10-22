// 名單與個股頁的「標記」（2026-10-18 使用者：「又小又亂…多用符號來代替文字」）。純函式，供 node 單元測試。
// 每一個提醒統一成：圖示＋極短字（日期或數字）＋顏色類別＋完整說明。名單列只放圖示與極短字，個股頁再加上名稱；
// 完整說明放在 title（滑過／長按）與「圖示說明」裡。研究依據：圖示要配短標籤（NN/g），重要的放前面、次要的收起（漸進展開）。
import type { DownRisk } from './downrisk.ts'
import { downRiskTag } from './downrisk.ts'
import type { HoldersFlag } from './holders.ts'
import { holdersTag } from './holders.ts'
import type { InsiderFlag } from './insider.ts'
import { insiderTag } from './insider.ts'
import type { Risk } from './risks.ts'
import { riskTag } from './risks.ts'
import type { Weak } from './weak.ts'
import type { NewsCount } from './news.ts'
import { weakTags } from './weak.ts'

/** 顏色類別：risk 紅（交易受限）、warn 黃（留意）、event 藍（日程）、chip 灰（籌碼／走勢）、good 橘（正面）。 */
export type BadgeTone = 'risk' | 'warn' | 'event' | 'chip' | 'good'
/** 圖示代號（畫面用 lucide 對應，見 Icon.tsx）。 */
export type BadgeIcon =
  | 'ban'
  | 'alert'
  | 'trendDown'
  | 'userMinus'
  | 'coins'
  | 'mic'
  | 'fileClock'
  | 'usersUp'
  | 'usersDown'
  | 'floor'
  | 'arrowDown'
  | 'sparkles'
  | 'flame'
  | 'rocket'
  | 'newspaper'

export type Badge = {
  key: string
  icon: BadgeIcon
  /** 圖示旁的極短字（日期、天數、百分比）；沒有就只顯示圖示 */
  short: string | null
  /** 名稱（個股頁與圖示說明用） */
  label: string
  /** 完整說明（滑過或長按看） */
  title: string
  tone: BadgeTone
}

const TONE_ORDER: Record<BadgeTone, number> = { risk: 0, warn: 1, event: 2, chip: 3, good: 4 }
const md = (d: string) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`

/** 圖示說明：每種標記一行（圖示、名稱、一句話）。名單上方「圖示說明」與測試共用。 */
export const BADGE_LEGEND: { icon: BadgeIcon; tone: BadgeTone; label: string; meaning: string }[] = [
  { icon: 'ban', tone: 'risk', label: '處置', meaning: '處置中或即將處置：分盤撮合、要先圈存，進出不方便' },
  { icon: 'alert', tone: 'warn', label: '注意股', meaning: '交易所公布注意交易資訊，連續達標會進處置' },
  { icon: 'trendDown', tone: 'warn', label: '模型易跌', meaning: '模型認為名單裡最可能 20 日內碰 −15% 的 5 檔；歷史上這 5 檔平均反而漲較多，只是提醒' },
  { icon: 'userMinus', tone: 'warn', label: '內部人申讓', meaning: '近 30 天董監事、經理人或大股東申報要賣股票' },
  { icon: 'coins', tone: 'event', label: '除權息', meaning: '10 天內除權息，當天參考價會往下調' },
  { icon: 'mic', tone: 'event', label: '法說會', meaning: '10 天內有法說會，前後常有大波動' },
  { icon: 'fileClock', tone: 'event', label: '營收未公布', meaning: '上月營收還沒公布（每月 10 日前）' },
  { icon: 'usersUp', tone: 'chip', label: '大戶連增', meaning: '千張大戶持股比例連續增加的週數' },
  { icon: 'usersDown', tone: 'chip', label: '大戶連減', meaning: '千張大戶持股比例連續減少的週數' },
  { icon: 'floor', tone: 'chip', label: '20日新低', meaning: '收盤是近 20 個交易日最低（已還原除權息）' },
  { icon: 'arrowDown', tone: 'chip', label: '連跌', meaning: '收盤連續下跌的天數' },
  { icon: 'sparkles', tone: 'good', label: '新上榜', meaning: '今天才進這條線的名單' },
  { icon: 'flame', tone: 'good', label: '連續上榜', meaning: '連續在名單上的天數' },
  { icon: 'rocket', tone: 'good', label: '飆漲機率', meaning: '潛伏飆股模型估的 20 日內漲 +30% 機率' },
  { icon: 'newspaper', tone: 'event', label: '相關新聞', meaning: '近 3 天提到它、或經供應商／族群連到它的新聞則數；點一下跳到個股頁的「相關新聞」看是哪幾則' },
]

const RISK_ICON: Record<Risk['kind'], BadgeIcon> = {
  disposition: 'ban',
  disposition_soon: 'ban',
  attention: 'alert',
  dividend: 'coins',
  conference: 'mic',
  revenue: 'fileClock',
}
const RISK_TONE: Record<Risk['kind'], BadgeTone> = {
  disposition: 'risk',
  disposition_soon: 'risk',
  attention: 'warn',
  dividend: 'event',
  conference: 'event',
  revenue: 'event',
}

function riskBadge(r: Risk): Badge {
  const t = riskTag(r)
  let short: string | null = null
  let label = r.text
  if (r.kind === 'disposition' && r.date) {
    short = `~${md(r.date)}`
    label = '處置中'
  } else if (r.kind === 'disposition_soon' && r.date) {
    short = `${md(r.date)}起`
    label = '即將處置'
  } else if (r.kind === 'attention') {
    label = '注意股'
  } else if ((r.kind === 'dividend' || r.kind === 'conference') && r.date) {
    short = md(r.date)
    label = r.text.replace(/^\d+\/\d+\s*/, '')
  } else if (r.kind === 'revenue') {
    label = '營收未公布'
  }
  return { key: `risk-${r.kind}-${r.date ?? ''}`, icon: RISK_ICON[r.kind], short, label, title: t.title, tone: RISK_TONE[r.kind] }
}

/** 追蹤股的行事曆標記（T-431／D-209：除權息、法說會、營收待公布；跟名單列同一種小標，從首頁行事曆搬來）。 */
export function eventBadges(events: Risk[] | null | undefined): Badge[] {
  return (events ?? []).map(riskBadge)
}

/** 一檔名單股的全部標記（依嚴重度排：紅、黃、藍、灰、橘）。 */
export function pickBadges(p: {
  new?: boolean
  streak?: number
  recent?: (boolean | null)[]
  score?: number | null
  line?: string | null
  weak?: Weak | null
  insider?: InsiderFlag | null
  holders?: HoldersFlag | null
  risks?: Risk[] | null
  down_risk?: DownRisk | null
  /** 近 3 天的相關新聞則數（反向看圖）；新聞服務沒回 → 沒有這個標記 */
  news?: NewsCount | null
}): Badge[] {
  const out: Badge[] = []
  for (const r of p.risks ?? []) out.push(riskBadge(r))
  const dr = downRiskTag(p.down_risk)
  if (dr && p.down_risk) out.push({ key: 'downrisk', icon: 'trendDown', short: `${Math.round(p.down_risk.p * 100)}%`, label: '模型易跌', title: dr.title, tone: 'warn' })
  const ins = insiderTag(p.insider)
  if (ins && p.insider) out.push({ key: 'insider', icon: 'userMinus', short: p.insider.n > 1 ? `×${p.insider.n}` : null, label: '內部人申讓', title: ins.title, tone: 'warn' })
  const hd = holdersTag(p.holders)
  if (hd && p.holders) {
    const up = p.holders.streak > 0
    out.push({ key: 'holders', icon: up ? 'usersUp' : 'usersDown', short: `${Math.abs(p.holders.streak)}週`, label: up ? '大戶連增' : '大戶連減', title: hd.title, tone: 'chip' })
  }
  for (const w of weakTags(p.weak)) {
    const low = w.text === '20日新低'
    out.push({
      key: low ? 'low20' : 'down',
      icon: low ? 'floor' : 'arrowDown',
      short: low ? null : `${p.weak?.down ?? ''}天`,
      label: low ? '20日新低' : '連跌',
      title: w.title,
      tone: 'chip',
    })
  }
  const nn = p.news ? p.news.direct + p.news.indirect : 0
  if (p.news && nn > 0)
    out.push({ key: 'news', icon: 'newspaper', short: `${nn}`, label: '相關新聞', title: `近 3 天直接提到 ${p.news.direct} 則、經供應商或族群連到 ${p.news.indirect} 則；點一下看是哪幾則`, tone: 'event' })
  if (p.new) out.push({ key: 'new', icon: 'sparkles', short: null, label: '新上榜', title: '今天才進這條線的名單', tone: 'good' })
  const streak = p.streak ?? 0
  if (streak > (p.recent?.length ?? 0)) out.push({ key: 'streak', icon: 'flame', short: `${streak}天`, label: '連續上榜', title: `連續 ${streak} 個交易日在名單上`, tone: 'good' })
  if (typeof p.score === 'number' && p.line !== 'CORE')
    out.push({ key: 'prob', icon: 'rocket', short: `${Math.round(p.score * 100)}%`, label: '飆漲機率', title: '潛伏飆股模型估的 20 個交易日內漲 +30% 的機率', tone: 'good' })
  return out.sort((a, b) => TONE_ORDER[a.tone] - TONE_ORDER[b.tone])
}

/** 名單列放幾個標記（其餘收成「+N」，點進個股頁看全部）。 */
export const ROW_BADGES_MAX = 4

/** 名單列要顯示的標記與收起來的數量。 */
export function rowBadges(all: Badge[], max = ROW_BADGES_MAX): { shown: Badge[]; more: number } {
  return { shown: all.slice(0, max), more: Math.max(0, all.length - max) }
}
