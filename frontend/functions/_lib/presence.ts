// 星雲圖「同時幾個人在看」（T-445／D-216；使用者 2026-10-23：「幫我做一個同時有幾個人在看星雲圖的 user count」）。
// 每個開著星雲圖的分頁定時送心跳（間隔在前端 src/cloud/galaxy-presence.ts 的 PRESENCE_BEAT_S）（一個亂數 id，不含任何身分），伺服器記最後一次時間；
// 最近 PRESENCE_WINDOW_S 秒內有心跳的 id 數就是「在看」的人數。關掉或切走送 leave 立刻拿掉。
// 不用 KV：免費方案一天只能寫 1,000 次，心跳會用完；走既有的 Supabase（Hyperdrive）。

/** 多久沒心跳就不算在看（秒）：至少兩次心跳的時間，漏一次不會掉 */
export const PRESENCE_WINDOW_S = 90
/** 超過這麼久沒心跳的列直接刪掉（秒），表永遠只有幾十列 */
export const PRESENCE_PURGE_S = 600
/** 請求內容上限（位元組） */
export const PRESENCE_BODY_MAX = 200

/** 建表（第一次有人送心跳時才建） */
export const PRESENCE_DDL = ['CREATE TABLE IF NOT EXISTS web.galaxy_presence (id text PRIMARY KEY, seen_at timestamptz NOT NULL)']

const ID_RE = /^[0-9a-f]{16,32}$/

/** POST /pub/galaxy-presence 的內容：{ id } 或 { id, leave: true }；id 是 16～32 位小寫十六進位。其他一律不收 → null。 */
export function parsePresence(text: string): { id: string; leave: boolean } | null {
  if (text.length > PRESENCE_BODY_MAX) return null
  let v: unknown
  try {
    v = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return null
  const { id, leave, ...rest } = v as { id?: unknown; leave?: unknown }
  if (Object.keys(rest).length || typeof id !== 'string' || !ID_RE.test(id)) return null
  if (leave !== undefined && leave !== true) return null
  return { id, leave: leave === true }
}
