// 首頁版面（使用者 2026-10-17：「每張卡可以自由移動位子，依照使用者帳號儲存擺置位置，卡也要可以縮起來」）：
// 每個帳號一筆——卡片由上到下的順序與哪幾張收起。卡片代號由網站定義（src/cloud/layout.ts）；這裡只驗格式，
// 不認得的代號網站讀回來時會略過，所以以後增刪卡片不用改資料庫。

export const LAYOUT_DDL = [
  'CREATE SCHEMA IF NOT EXISTS web',
  `CREATE TABLE IF NOT EXISTS web.home_layout (
    email text PRIMARY KEY,
    layout jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
  )`,
]

/** 卡片代號：小寫英文 2～16 字（例：brief、heat）。 */
export const CARD_ID_RE = /^[a-z]{2,16}$/
/** 一份版面最多幾張卡（網站目前十幾張，留餘裕）。 */
export const LAYOUT_MAX_CARDS = 40
/** PUT 內容上限（位元組）：40 張卡的代號兩份也不到 1.5 KB。 */
export const LAYOUT_BODY_MAX = 2000

export type SavedLayout = { order: string[]; collapsed: string[] }

const ids = (x: unknown, max: number): string[] | null => {
  if (!Array.isArray(x) || x.length > max) return null
  if (!x.every((s) => typeof s === 'string' && CARD_ID_RE.test(s))) return null
  return new Set(x).size === x.length ? (x as string[]) : null
}

/** 存版面的內容：{ order: [代號…]（由上到下，至少一張）, collapsed: [代號…]（都要在 order 裡） }；格式不對 → null。 */
export function parseLayout(body: unknown): SavedLayout | null {
  const b = body as { order?: unknown; collapsed?: unknown } | null
  const order = ids(b?.order, LAYOUT_MAX_CARDS)
  const collapsed = ids(b?.collapsed ?? [], LAYOUT_MAX_CARDS)
  if (order === null || order.length === 0 || collapsed === null) return null
  const inOrder = new Set(order)
  if (!collapsed.every((c) => inOrder.has(c))) return null
  return { order, collapsed }
}
