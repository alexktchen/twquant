// 網站的分頁，與推播通知點開時要開哪一個。
// T-379／D-188：首頁改成戰情室（home），原本的「盤勢」分頁併進首頁。
// T-431／D-209：族群資金又搬回自己的「盤勢」分頁（group）——族群資金通知與 LINE 族群圖卡帶的 ?tab=group 直接開這頁。

// line＝「LINE 群組」（T-423）：只有有權限的角色看得到分頁（_lib/roles.ts）
// futures＝「台指期」（T-450／T-451／D-220；小台模擬單＋大台、微台行情）：只給 admin
export type Tab = 'home' | 'news' | 'group' | 'picks' | 'score' | 'watch' | 'line' | 'futures'
const TABS: readonly Tab[] = ['home', 'news', 'group', 'picks', 'score', 'watch', 'line', 'futures']
/**
 * 星雲圖的公開專屬連結（T-445；使用者 2026-10-23：「讓不登入也有一個專屬連結可以看到我的星雲圖，可以直接導航到全景這一頁 我主要想要去推廣」）：
 * https://twquant.pages.dev/galaxy ＝新聞頁、一打開就是全景。Pages 找不到檔案就回 index.html（同 /login），所以不用另外設路由。
 */
export const GALAXY_PATH = '/galaxy'
/** 舊網址的分頁名 → 現在的分頁 */
const ALIASES: Record<string, Tab> = {}

/** 網址上的 ?tab=：認得就回那個分頁，沒有、壞掉或不認得 → null（照原本的預設）。 */
export function tabFromUrl(href: string): Tab | null {
  let tab: string | null
  try {
    const u = new URL(href)
    tab = u.searchParams.get('tab')
    if (tab === null && u.pathname === GALAXY_PATH) return 'news'
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
    const u = new URL(href)
    return u.pathname === GALAXY_PATH || u.searchParams.get('view') === 'galaxy'
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

/** 沒登入的人一打開網站要落在哪（T-438）：新聞是公開的，沒指定分頁就去新聞；網址明寫 ?tab=、或已經在別的分頁就不動。 */
export function guestTab(current: Tab, explicit: Tab | null): Tab {
  return explicit === null && current === 'home' ? 'news' : current
}
