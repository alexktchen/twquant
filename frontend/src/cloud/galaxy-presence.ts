// 星雲圖「同時幾個人在看」的前端（T-445／D-216）：分頁自己產生一個亂數 id（不含任何身分），定時送心跳；伺服器端在 functions/_lib/presence.ts。

/** 多久送一次心跳（秒）；伺服器 PRESENCE_WINDOW_S 至少要是它的兩倍（測試守著） */
export const PRESENCE_BEAT_S = 30
const KEY = 'twquant.galaxyViewer'

/** 32 位小寫十六進位的亂數 id。 */
export function newViewerId(rand: (a: Uint8Array<ArrayBuffer>) => Uint8Array<ArrayBuffer> = (a) => crypto.getRandomValues(a)): string {
  return [...rand(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** 這個分頁的 id：重新整理沿用（不然 90 秒內同一個人會算兩次）；存不了（無痕、封鎖）就每次新的。 */
export function viewerId(): string {
  try {
    const old = sessionStorage.getItem(KEY)
    if (old && /^[0-9a-f]{32}$/.test(old)) return old
    const id = newViewerId()
    sessionStorage.setItem(KEY, id)
    return id
  } catch {
    return newViewerId()
  }
}

/** 摘要那行的字：「3 人在看」；只有自己也照寫 1。 */
export const viewersText = (n: number): string => `${n.toLocaleString('en-US')} 人在看`
