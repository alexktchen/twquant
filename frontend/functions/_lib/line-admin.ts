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
/** 從網站登入的進度（web.line_login 一列；qr_png 是 base64 PNG，只給有權限的人看） */
export type LineLogin = {
  status: string
  requested_by: string | null
  requested_at: string | null
  updated_at: string
  run_url: string | null
  qr_url: string | null
  qr_png: string | null
  pin: string | null
  display_name: string | null
  error: string | null
  /** 10 分鐘內有動靜（排程那邊還活著）；超過就當它已經掛了，可以重按 */
  fresh: boolean
}
export type LineStatus = {
  ready: boolean
  account: LineAccount | null
  groups: LineGroupRow[]
  posts: LinePostRow[]
  login: LineLogin | null
}

/** 登入還在進行中的狀態（同 Python line_group.LOGIN_ACTIVE） */
export const LOGIN_ACTIVE = ['requested', 'starting', 'scan', 'pin'] as const
/** 多久沒動靜就當登入流程已經掛了 */
export const LOGIN_STALE_MINUTES = 10
export const DISPATCH_REPO = 'alexktchen/twquant'
export const LOGIN_WORKFLOW = 'line-login.yml'

/** web.line_login 的建表（Python monitoring/line_group.ensure_tables 也建這張，欄位要一樣，pytest 守著）。 */
export const LOGIN_DDL =
  'CREATE TABLE IF NOT EXISTS web.line_login (' +
  'id text PRIMARY KEY, status text NOT NULL, requested_by text, requested_at timestamptz, ' +
  'updated_at timestamptz NOT NULL, run_url text, qr_url text, qr_png text, pin text, ' +
  'display_name text, error text)'

/** 登入是不是正在跑（正在跑就不再觸發一次，免得兩支程式搶同一個 QR）。 */
export const loginBusy = (l: Pick<LineLogin, 'status' | 'fresh'> | null): boolean =>
  l !== null && l.fresh && (LOGIN_ACTIVE as readonly string[]).includes(l.status)

/** 觸發 GitHub Actions line-login.yml 的請求；沒有 token 直接丟錯（不能靜默變成「什麼都沒觸發」）。 */
export function dispatchRequest(token: string | undefined): { url: string; init: RequestInit } {
  if (!token) throw new Error('網站沒設 TWQUANT_DISPATCH_TOKEN（GitHub fine-grained token，Actions: Read and write），沒辦法從網頁登入')
  return {
    url: `https://api.github.com/repos/${DISPATCH_REPO}/actions/workflows/${LOGIN_WORKFLOW}/dispatches`,
    init: {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'twquant-pages',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main' }),
    },
  }
}

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

/** 登入進度；表還沒建（沒按過登入、也沒跑過 Python）→ null。 */
export async function loginState(db: Db): Promise<LineLogin | null> {
  const [ok] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.line_login') IS NOT NULL AS ok`
  if (!ok?.ok) return null
  const [row] = await db<LineLogin[]>`
    SELECT status, requested_by,
           to_char(requested_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS requested_at,
           to_char(updated_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS updated_at,
           run_url, qr_url, qr_png, pin, display_name, error,
           updated_at > now() - make_interval(mins => ${LOGIN_STALE_MINUTES}) AS fresh
    FROM web.line_login WHERE id = 'default'`
  return row ?? null
}

/** 記下「有人按了登入」（沒有表就先建）。 */
export async function markLoginRequested(db: Db, email: string): Promise<void> {
  await db.unsafe('CREATE SCHEMA IF NOT EXISTS web')
  await db.unsafe(LOGIN_DDL)
  await db`
    INSERT INTO web.line_login (id, status, requested_by, requested_at, updated_at)
    VALUES ('default', 'requested', ${email}, now(), now())
    ON CONFLICT (id) DO UPDATE SET status = 'requested', requested_by = EXCLUDED.requested_by,
      requested_at = EXCLUDED.requested_at, updated_at = EXCLUDED.updated_at,
      run_url = NULL, qr_url = NULL, qr_png = NULL, pin = NULL, error = NULL`
}

/** 觸發失敗時記下原因（網站上顯示，讓人知道不是在等）。 */
export async function markLoginFailed(db: Db, error: string): Promise<void> {
  await db`UPDATE web.line_login SET status = 'failed', error = ${error.slice(0, 1000)}, updated_at = now() WHERE id = 'default'`
}

async function tablesReady(db: Db): Promise<boolean> {
  const [r] = await db<{ ok: boolean }[]>`
    SELECT to_regclass('web.line_account') IS NOT NULL AND to_regclass('web.line_groups') IS NOT NULL
       AND to_regclass('web.line_posts') IS NOT NULL AS ok`
  return Boolean(r?.ok)
}

/** 整頁要的資料；排程還沒建過表（還沒用過 `twquant line-group`）→ ready=false（登入進度照樣給）。 */
export async function lineStatus(db: Db): Promise<LineStatus> {
  const login = await loginState(db)
  if (!(await tablesReady(db))) return { ready: false, account: null, groups: [], posts: [], login }
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
  return { ready: true, account: account ?? null, groups: [...groups], posts: [...posts], login }
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
