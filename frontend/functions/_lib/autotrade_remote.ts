// 遙控指令（autotrade A-016、A-017／D-229）：平倉、暫停／恢復自動交易、啟動／結束交易程式。
// 跟遠端緊急停止（./autotrade.ts，D-225）分開放：那一份仍然只會新增「停止」，這一份只碰另一張表。
import type { sql } from './db.ts'
import { ensureCommands, modeOk, taipeiToday, type AutotradeMode, type RemoteView } from './autotrade.ts'

type Db = ReturnType<typeof sql>

// ── 遙控指令（A-016、A-017／D-229）：平倉、暫停／恢復自動交易、啟動／結束交易程式 ──
// 使用者 2026-11-14 要網站不只能叫停。另外一張表，不動上面那張停止的表（它仍然只可能有「停止」）。
// 這裡一樣沒有、也不可以加：手動買賣、改設定、解除緊急停止。

/** 跟 autotrade/src/autotrade/relay.py 的 REQUESTS_DDL 一字不差（兩邊各有測試守著）。
 *  編號跟停止共用同一個序號（兩張表不會撞號）；一筆只對一種模式；結果直接記在同一列。 */
export const REQUESTS_DDL = [
  "CREATE TABLE IF NOT EXISTS web.autotrade_requests (id bigint PRIMARY KEY DEFAULT nextval('web.autotrade_commands_id_seq'), day date NOT NULL, action text NOT NULL CHECK (action IN ('flatten', 'pause', 'resume', 'start', 'quit')), target text NOT NULL CHECK (target IN ('sim', 'live')), created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), acked_at timestamptz, code text, note text, done_at timestamptz, done_code text, done_note text)",
  "CREATE TABLE IF NOT EXISTS web.autotrade_remote (id text PRIMARY KEY CHECK (id = 'admin'), seen_at timestamptz NOT NULL, reader_seen_at timestamptz, poll_s real NOT NULL)",
  'ALTER TABLE web.autotrade_requests ENABLE ROW LEVEL SECURITY',
  'ALTER TABLE web.autotrade_remote ENABLE ROW LEVEL SECURITY',
  'CREATE INDEX IF NOT EXISTS autotrade_requests_day ON web.autotrade_requests (day, id)',
] as const

export const REMOTE_ACTIONS = ['flatten', 'pause', 'resume', 'start', 'quit'] as const
export type RemoteAction = (typeof REMOTE_ACTIONS)[number]
/** 一天最多寫幾筆遙控指令。 */
export const COMMAND_MAX_PER_DAY = 100
/** 按下超過這麼多秒沒有程式接手就作廢。要跟 relay.py 的 request_ttl_s 一樣。 */
export const REQUEST_TTL_S = 30
/** GET 回今天最近幾筆。 */
export const REQUESTS_SHOWN = 6

/** 請求內容一定要是 {"confirm": 動作, "target": "sim" 或 "live"}，多一個欄位都不收。 */
export function commandOk(body: unknown): { action: RemoteAction; target: AutotradeMode } | null {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null
  const b = body as Record<string, unknown>
  if (Object.keys(b).length !== 2) return null
  const action = b.confirm
  const target = b.target
  if (typeof action !== 'string' || !(REMOTE_ACTIONS as readonly string[]).includes(action)) return null
  if (typeof target !== 'string' || !modeOk(target)) return null
  return { action: action as RemoteAction, target }
}

export async function ensureRequests(db: Db): Promise<void> {
  await ensureCommands(db) // 編號用的是指令表的序號，所以那張表要先在
  const [{ n }] = await db<{ n: number }[]>`
    SELECT count(*)::int AS n FROM information_schema.tables
    WHERE table_schema = 'web' AND table_name IN ('autotrade_requests', 'autotrade_remote')`
  if (Number(n) < 2) for (const stmt of REQUESTS_DDL) await db.unsafe(stmt)
}

/**
 * 遙控指令的現況，一句查詢拿完：requests＝那一天最近幾筆（新的在前），每筆帶接手的程式回報的結果；
 * remote＝後台那支中繼程式的心跳（啟動／結束要靠它）。「多久以前」一律由資料庫的時鐘算。表不存在會丟錯，由呼叫端決定怎麼處理。
 */
export const requestsView = (db: Db, day: string) => db<RemoteView[]>`
  SELECT
    (SELECT COALESCE(jsonb_agg(x.r ORDER BY x.id DESC), '[]'::jsonb)::text FROM (
       SELECT r.id, jsonb_build_object(
         'id', r.id::text,
         'action', r.action,
         'target', r.target,
         'created_at', to_char(r.created_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
         'age_s', EXTRACT(EPOCH FROM now() - r.created_at)::float8,
         'acked_at', to_char(r.acked_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
         'ack_age_s', EXTRACT(EPOCH FROM now() - r.acked_at)::float8,
         'code', r.code,
         'note', r.note,
         'done_at', to_char(r.done_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
         'done_code', r.done_code,
         'done_note', r.done_note) AS r
       FROM web.autotrade_requests r WHERE r.day = ${day}::date ORDER BY r.id DESC LIMIT ${REQUESTS_SHOWN}) x) AS requests,
    (SELECT jsonb_build_object(
        'age_s', EXTRACT(EPOCH FROM now() - m.seen_at)::float8,
        'reader_age_s', EXTRACT(EPOCH FROM now() - m.reader_seen_at)::float8,
        'poll_s', m.poll_s::float8,
        'seen_today', (m.seen_at AT TIME ZONE 'Asia/Taipei')::date = ${day}::date)::text
     FROM web.autotrade_remote m WHERE m.id = 'admin') AS remote`

export type CommandResult = { ok: true; reused: boolean; view: RemoteView } | { ok: false; detail: string }

/**
 * 送出一筆遙控指令：只會新增一列。
 * 同一種動作、同一種模式的上一筆還在等（沒有程式接手、也還沒作廢）→ 不寫新的（手抖連按不會疊）。超過一天的上限就拒絕。
 */
export async function requestAction(db: Db, email: string, nowMs: number, action: RemoteAction, target: AutotradeMode): Promise<CommandResult> {
  await ensureRequests(db)
  const day = taipeiToday(nowMs)
  const [did] = await db<{ inserted: boolean; reused: boolean }[]>`
    WITH waiting AS (
           SELECT id FROM web.autotrade_requests
           WHERE day = ${day}::date AND action = ${action} AND target = ${target} AND acked_at IS NULL
             AND created_at > now() - make_interval(secs => ${REQUEST_TTL_S}::double precision) LIMIT 1),
         ins AS (
           INSERT INTO web.autotrade_requests (day, action, target, created_by)
           SELECT ${day}::date, ${action}, ${target}, ${email}
           WHERE NOT EXISTS (SELECT 1 FROM waiting)
             AND (SELECT count(*) FROM web.autotrade_requests WHERE day = ${day}::date) < ${COMMAND_MAX_PER_DAY}
           RETURNING id)
    SELECT EXISTS (SELECT 1 FROM ins) AS inserted, EXISTS (SELECT 1 FROM waiting) AS reused`
  if (!did.inserted && !did.reused) return { ok: false, detail: `今天已經送過 ${COMMAND_MAX_PER_DAY} 次遙控指令，不再接受。` }
  const [view] = await requestsView(db, day)
  return { ok: true, reused: did.reused, view }
}
