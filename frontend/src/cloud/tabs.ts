// 網站的分頁，與推播通知點開時要開哪一個（族群資金通知帶 ?tab=group，T-354）。

export type Tab = 'picks' | 'group' | 'score' | 'watch'
const TABS: readonly Tab[] = ['picks', 'group', 'score', 'watch']

/** 網址上的 ?tab=：認得就回那個分頁，沒有、壞掉或不認得 → null（照原本的預設）。 */
export function tabFromUrl(href: string): Tab | null {
  let tab: string | null
  try {
    tab = new URL(href).searchParams.get('tab')
  } catch {
    return null
  }
  return TABS.find((t) => t === tab) ?? null
}

/** 用過的 ?tab= 從網址拿掉（重新整理不要一直跳回那個分頁）；其他參數與 hash 保留。 */
export function withoutTab(href: string): string {
  const u = new URL(href)
  u.searchParams.delete('tab')
  return u.pathname + u.search + u.hash
}
