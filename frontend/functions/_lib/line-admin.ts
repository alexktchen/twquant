// 「LINE 群組」分頁（T-423／D-204）：發文帳號狀態、帳號在哪些群組（勾要發的）、最近發了什麼。
// 資料由排程寫（Python `twquant line-group`，表在 web schema，第一次用時由它建表）；這裡只讀、只改勾選。
// 帳號表裡有登入憑證，**只選要顯示的欄位**，憑證永遠不出資料庫。時間一律在資料庫轉成台北時間的「YYYY-MM-DD HH:MI」。
import type { sql } from './db'

type Db = ReturnType<typeof sql>

export const HOME_ID_RE = /^c[0-9a-f]{32}$/
export const CARD_TOKEN_RE = /^[0-9a-f]{32}$/
/** 最近幾則貼文 */
export const POSTS_SHOWN = 40
export const LINE_BODY_MAX = 200

export type LineAccount = {
  display_name: string | null
  mid: string | null
  updated_at: string
  paused_until: string | null
  last_error: string | null
}
export type LineGroupRow = { home_id: string; name: string; enabled: boolean; present: boolean; synced_at: string }
export type LinePostRow = {
  id: number
  date: string
  kind: string
  home_id: string
  group_name: string | null
  status: string
  via: string | null
  attempts: number
  last_error: string | null
  enqueued_at: string
  sent_at: string | null
  card_token: string
  alt_text: string
}
export type LineStatus = { ready: boolean; account: LineAccount | null; groups: LineGroupRow[]; posts: LinePostRow[] }

/** PUT /api/line/groups/:id 的內容：{ enabled: true|false }，其他一律不收。 */
export function parseToggle(text: string): { enabled: boolean } | null {
  if (text.length > LINE_BODY_MAX) return null
  let v: unknown
  try {
    v = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return null
  const keys = Object.keys(v)
  const enabled = (v as { enabled?: unknown }).enabled
  return keys.length === 1 && typeof enabled === 'boolean' ? { enabled } : null
}

async function tablesReady(db: Db): Promise<boolean> {
  const [r] = await db<{ ok: boolean }[]>`
    SELECT to_regclass('web.line_account') IS NOT NULL AND to_regclass('web.line_groups') IS NOT NULL
       AND to_regclass('web.line_posts') IS NOT NULL AS ok`
  return Boolean(r?.ok)
}

/** 整頁要的資料；排程還沒建過表（還沒用過 `twquant line-group`）→ ready=false。 */
export async function lineStatus(db: Db): Promise<LineStatus> {
  if (!(await tablesReady(db))) return { ready: false, account: null, groups: [], posts: [] }
  const [account] = await db<LineAccount[]>`
    SELECT display_name, mid, to_char(updated_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS updated_at, to_char(paused_until AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS paused_until, last_error
    FROM web.line_account WHERE id = 'default'`
  const groups = await db<LineGroupRow[]>`
    SELECT home_id, name, enabled, present, to_char(synced_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS synced_at
    FROM web.line_groups ORDER BY present DESC, name`
  const posts = await db<LinePostRow[]>`
    SELECT p.id::int AS id, p.date::text AS date, p.kind, p.home_id, g.name AS group_name, p.status, p.via,
           p.attempts, p.last_error, to_char(p.enqueued_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS enqueued_at, to_char(p.sent_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS sent_at,
           p.card_token, p.alt_text
    FROM web.line_posts p LEFT JOIN web.line_groups g USING (home_id)
    ORDER BY p.id DESC LIMIT ${POSTS_SHOWN}`
  return { ready: true, account: account ?? null, groups: [...groups], posts: [...posts] }
}

/** 勾／取消一個群組；回改好的那列，找不到 → null。 */
export async function setGroupEnabled(db: Db, homeId: string, enabled: boolean, email: string): Promise<LineGroupRow | null> {
  if (!(await tablesReady(db))) return null
  const [row] = await db<LineGroupRow[]>`
    UPDATE web.line_groups SET enabled = ${enabled}, updated_by = ${email}, updated_at = now()
    WHERE home_id = ${homeId}
    RETURNING home_id, name, enabled, present, to_char(synced_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS synced_at`
  return row ?? null
}
