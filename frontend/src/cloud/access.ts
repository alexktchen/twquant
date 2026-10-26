// 這個帳號能用哪些功能（/api/access；誰能用什麼由後端 functions/_lib/roles.ts 決定）。
// T-423：LINE 群組；T-430／D-208：每日名單、績效只給 admin。前端只拿來決定顯示什麼，真正擋人的是後端每支端點。純函式，供 node 單元測試。
import type { CardId, Layout } from './layout.ts'
import type { Tab } from './tabs.ts'

/** 與 functions/_lib/roles.ts 的 Feature 相同（tests/cloud-access.test.ts 守著） */
export const FEATURES = ['line', 'picks', 'score', 'futures'] as const
export type Feature = (typeof FEATURES)[number]

/** 要權限才看得到的分頁 → 功能、分頁名稱 */
const TAB_FEATURE: Partial<Record<Tab, { feature: Feature; title: string }>> = {
  picks: { feature: 'picks', title: '每日名單' },
  score: { feature: 'score', title: '績效' },
  line: { feature: 'line', title: 'LINE 群組' },
  futures: { feature: 'futures', title: '小台模擬' },
}

/** 要權限才看得到的首頁卡 */
const CARD_FEATURE: Partial<Record<CardId, Feature>> = { picks: 'picks', score: 'score' }

/** 這個分頁看不看得到（不需要權限的分頁永遠看得到）。 */
export function tabAllowed(tab: Tab, features: readonly Feature[]): boolean {
  const need = TAB_FEATURE[tab]
  return need === undefined || features.includes(need.feature)
}

/** 沒權限卻開到這個分頁（例：LINE 圖卡連到 ?tab=picks）時顯示的一句話。 */
export function lockedText(tab: Tab, guest: boolean): string {
  if (guest) return '登入後才看得到'
  return `這個帳號沒有「${TAB_FEATURE[tab]?.title ?? tab}」的權限`
}

/** 首頁版面裡這個帳號看得到的卡：順序與收起照舊，沒權限的卡拿掉（存起來的版面不動，有權限的人照樣看得到原位置）。 */
export function visibleLayout(l: Layout, features: readonly Feature[]): Layout {
  const ok = (id: CardId) => {
    const need = CARD_FEATURE[id]
    return need === undefined || features.includes(need)
  }
  return { order: l.order.filter(ok), collapsed: l.collapsed.filter(ok) }
}
