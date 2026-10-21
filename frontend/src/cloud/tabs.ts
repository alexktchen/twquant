// 網站的分頁，與推播通知點開時要開哪一個。
// T-379／D-188：首頁改成戰情室（home），原本的「盤勢」分頁併進首頁——族群資金通知帶的 ?tab=group（T-354）照樣認得，開首頁。

// line＝「LINE 群組」（T-423）：只有有權限的角色看得到分頁（_lib/roles.ts）
export type Tab = 'home' | 'news' | 'picks' | 'score' | 'watch' | 'line'
const TABS: readonly Tab[] = ['home', 'news', 'picks', 'score', 'watch', 'line']
/** 舊網址的分頁名 → 現在的分頁 */
const ALIASES: Record<string, Tab> = { group: 'home' }

/** 網址上的 ?tab=：認得就回那個分頁，沒有、壞掉或不認得 → null（照原本的預設）。 */
export function tabFromUrl(href: string): Tab | null {
  let tab: string | null
  try {
    tab = new URL(href).searchParams.get('tab')
  } catch {
    return null
  }
  if (tab === null) return null
  return TABS.find((t) => t === tab) ?? ALIASES[tab] ?? null
}

/** 用過的 ?tab= 從網址拿掉（重新整理不要一直跳回那個分頁）；其他參數與 hash 保留。 */
export function withoutTab(href: string): string {
  const u = new URL(href)
  u.searchParams.delete('tab')
  return u.pathname + u.search + u.hash
}

/** 新聞頁一打開就是星雲圖（?view=galaxy；舊網址 /api/prototype/galaxy 轉到這裡）。 */
export function galaxyFromUrl(href: string): boolean {
  try {
    return new URL(href).searchParams.get('view') === 'galaxy'
  } catch {
    return false
  }
}

/** 用過的 ?view= 從網址拿掉（重新整理回清單）；其他參數與 hash 保留。 */
export function withoutView(href: string): string {
  const u = new URL(href)
  u.searchParams.delete('view')
  return u.pathname + u.search + u.hash
}
