// 首頁（戰情室）的「地鐵告示」規則（使用者 2026-10-18：「像地鐵告示一樣，顏色歸類、圖案輔佐、減少閱讀文字」）。
// 純函式與對照表，供 node 單元測試；畫面在 CardFrame.tsx／HomeCards.tsx 等。
// 五條「路線」：看法（橘）、行情（青）、名單（紫）、事件（藍）、族群（粉）。每張卡一個圖示＋一個顏色，固定不變。
import type { CardId } from './layout.ts'

export type CardTone = 'view' | 'quote' | 'list' | 'event' | 'group'
export type HomeIcon =
  | 'sunrise'
  | 'globe'
  | 'activity'
  | 'scale'
  | 'barChart'
  | 'landmark'
  | 'megaphone'
  | 'listChecks'
  | 'trophy'
  | 'calendar'
  | 'grid'
  | 'compass'
  | 'wallet'
  | 'eye'
  | 'flag'
  | 'zap'

export const CARD_META: Record<CardId, { tone: CardTone; icon: HomeIcon; title: string }> = {
  brief: { tone: 'view', icon: 'sunrise', title: '今日一句話' },
  heads: { tone: 'view', icon: 'globe', title: '新聞頭條' },
  mood: { tone: 'quote', icon: 'activity', title: '市場氛圍' },
  intl: { tone: 'quote', icon: 'globe', title: '國際行情' },
  taifex: { tone: 'quote', icon: 'scale', title: '期貨籌碼' },
  market: { tone: 'quote', icon: 'barChart', title: '大盤體檢' },
  chips: { tone: 'quote', icon: 'landmark', title: '法人與融資' },
  news: { tone: 'event', icon: 'megaphone', title: '昨日消息' },
  picks: { tone: 'list', icon: 'listChecks', title: '今日名單' },
  score: { tone: 'list', icon: 'trophy', title: '名單成績' },
  cal: { tone: 'event', icon: 'calendar', title: '行事曆' },
  heat: { tone: 'group', icon: 'grid', title: '族群資金' },
}

export const TONE_NAME: Record<CardTone, string> = { view: '看法', quote: '行情', list: '名單', event: '事件', group: '族群' }

/** 晨報每一點開頭的【標籤】→ 圖示；不認得的用旗子。 */
export const BULLET_TAGS: Record<string, HomeIcon> = {
  研判: 'compass',
  大事: 'globe',
  國際: 'globe',
  台股: 'barChart',
  籌碼: 'landmark',
  資金: 'wallet',
  觀察: 'eye',
  風險: 'zap',
}

/** 「【研判】國際盤重挫…」→ { tag: '研判', icon, text: '國際盤重挫…' }；沒有標籤 → tag null。 */
export function parseBullet(s: string): { tag: string | null; icon: HomeIcon; text: string } {
  const m = /^\s*[【[]([^\]】]{1,6})[】\]]\s*(.*)$/s.exec(s)
  if (!m) return { tag: null, icon: 'flag', text: s.trim() }
  return { tag: m[1], icon: BULLET_TAGS[m[1]] ?? 'flag', text: m[2].trim() }
}

/** 漲跌幅的一行小字：「▲ 1.23%」「▼ 0.50%」「— 0.00%」。 */
export function chgSub(chg: number | null | undefined, digits = 2): string {
  if (chg === null || chg === undefined || !Number.isFinite(chg)) return '—'
  const v = Math.abs(chg * 100).toFixed(digits)
  return chg > 0 ? `▲ ${v}%` : chg < 0 ? `▼ ${v}%` : `— ${v}%`
}

/** 點數的一行小字：「▲ 123 點」。 */
export function ptsSub(diff: number | null | undefined, chg?: number | null): string {
  if (diff === null || diff === undefined || !Number.isFinite(diff)) return '—'
  const v = Math.abs(Math.round(diff)).toLocaleString('en-US')
  const c = chg === null || chg === undefined ? '' : ` ${Math.abs(chg * 100).toFixed(2)}%`
  return `${diff > 0 ? '▲' : diff < 0 ? '▼' : '—'} ${v}${c}`
}

/** 金額（元）→ 「1.13 兆」「451 億」「8,000 萬」。 */
export function bigMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const a = Math.abs(v)
  if (a >= 1e12) return `${(a / 1e12).toFixed(2)} 兆`
  if (a >= 1e8) return `${Math.round(a / 1e8).toLocaleString('en-US')} 億`
  return `${Math.round(a / 1e4).toLocaleString('en-US')} 萬`
}

/** 帶正負號的億：「+108 億」「−451 億」。 */
export function signedBig(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${bigMoney(v)}`
}

/** 口數：「淨空 93,675」「淨多 3,197」。 */
export function lots(x: number | null | undefined): { value: string; label: string } {
  if (x === null || x === undefined || !Number.isFinite(x)) return { value: '—', label: '' }
  return { value: Math.abs(Math.round(x)).toLocaleString('en-US'), label: x >= 0 ? '淨多' : '淨空' }
}

/** 新聞頭條分類 → 路線顏色。 */
export function headlineTone(category: string | null): CardTone {
  if (!category) return 'view'
  if (/美股|國際|政經|外匯|原物料/.test(category)) return 'quote'
  if (/台股/.test(category)) return 'list'
  return 'view'
}
