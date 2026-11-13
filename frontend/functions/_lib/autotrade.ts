// 程式交易（autotrade A-005、A-011）：
// （一）狀態：serving.autotrade_status 一天、一種模式一列，payload 是整份狀態；寫入者是獨立的交易系統，網站只讀。
// （二）遠端緊急停止（D-224）：網站只會在 web.autotrade_commands 新增一筆「停止」。交易程式那邊的中繼程式讀到後交給交易程式執行，
//      每支程式（模擬／真單／看門）各自在 web.autotrade_command_acks 回報「收到了、最後平掉了沒」，並在 web.autotrade_listeners 留心跳。
//      這條路只能叫停——這裡沒有、也不可以加解除、開始交易或改設定的動作。只給 admin。
import type { sql } from './db.ts'

export const AUTOTRADE_MODES = ['sim', 'live'] as const
export type AutotradeMode = (typeof AUTOTRADE_MODES)[number]

/** ?mode= 是不是模擬或真單。 */
export const modeOk = (m: string | null): m is AutotradeMode => m !== null && (AUTOTRADE_MODES as readonly string[]).includes(m)

/** 日期清單一次最多給幾天（約三個月的交易日）。 */
export const AUTOTRADE_DAYS_LIMIT = 60

export type AutotradeDayRow = { day: string; status: string; pnl: number | null; fills: number | null }

/** 遠端停止的現況，兩段都是資料庫組好的 JSON 文字：stop＝今天最新的一筆停止指令與每支程式的回報（沒有是 null）、listeners＝每支程式的心跳。 */
export type ControlView = { stop: string | null; listeners: string | null }

/** 回應本體：日期清單＋那一天的狀態＋遠端停止的現況。狀態與停止都直接用資料庫給的 JSON 文字，不 parse 再 stringify；沒有就是 null／{}。 */
export const envelope = (days: readonly AutotradeDayRow[], body: string | null, control: ControlView | null = null): string =>
  `{"days":${JSON.stringify(days)},"current":${body ?? 'null'},"stop":${control?.stop ?? 'null'},"listeners":${control?.listeners ?? '{}'}}`

// ── 遠端緊急停止 ──

/** 跟 autotrade/src/autotrade/relay.py 的 COMMANDS_DDL 一字不差（兩邊各有測試守著）。
 *  指令表只可能有「停止」（CHECK）；回報一支程式一列，模擬的回報蓋不到真單的；RLS 是多一道保險。 */
export const COMMANDS_DDL = [
  'CREATE SCHEMA IF NOT EXISTS web',
  "CREATE TABLE IF NOT EXISTS web.autotrade_commands (id bigserial PRIMARY KEY, day date NOT NULL, action text NOT NULL CHECK (action = 'stop'), created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())",
  "CREATE TABLE IF NOT EXISTS web.autotrade_command_acks (command_id bigint NOT NULL REFERENCES web.autotrade_commands (id) ON DELETE CASCADE, consumer text NOT NULL CHECK (consumer IN ('sim', 'live', 'watchdog')), acked_at timestamptz NOT NULL DEFAULT now(), code text NOT NULL, note text NOT NULL, done_at timestamptz, done_code text, done_note text, PRIMARY KEY (command_id, consumer))",
  "CREATE TABLE IF NOT EXISTS web.autotrade_listeners (consumer text PRIMARY KEY CHECK (consumer IN ('sim', 'live', 'watchdog')), relay_seen_at timestamptz NOT NULL, engine_seen_at timestamptz, poll_s real NOT NULL)",
  'ALTER TABLE web.autotrade_commands ENABLE ROW LEVEL SECURITY',
  'ALTER TABLE web.autotrade_command_acks ENABLE ROW LEVEL SECURITY',
  'ALTER TABLE web.autotrade_listeners ENABLE ROW LEVEL SECURITY',
  'CREATE INDEX IF NOT EXISTS autotrade_commands_day ON web.autotrade_commands (day, id)',
] as const

/** 請求內容一定要帶的確認字。 */
export const STOP_CONFIRM = 'stop'
/** 請求內容最長幾個字元。 */
export const STOP_BODY_MAX = 200
/** 一天最多寫幾筆。上一筆還沒有任何程式回報時不會寫新的，所以正常用不到這個上限。 */
export const STOP_MAX_PER_DAY = 20

/** 請求內容一定要是 {"confirm":"stop"}，多一個欄位都不收。 */
export function confirmOk(body: unknown): boolean {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return false
  return Object.keys(body).length === 1 && (body as Record<string, unknown>).confirm === STOP_CONFIRM
}

/** 台北日期（伺服器的時鐘決定，不收使用者給的日期）。 */
export const taipeiToday = (nowMs: number): string => new Date(nowMs + 8 * 3600_000).toISOString().slice(0, 10)

type Db = ReturnType<typeof sql>

export async function ensureCommands(db: Db): Promise<void> {
  const [{ n }] = await db<{ n: number }[]>`
    SELECT count(*)::int AS n FROM information_schema.tables
    WHERE table_schema = 'web' AND table_name IN ('autotrade_commands', 'autotrade_command_acks', 'autotrade_listeners')`
  if (Number(n) < 3) for (const stmt of COMMANDS_DDL) await db.unsafe(stmt)
}

/**
 * 遠端停止的現況，一句查詢拿完：
 * stop＝那一天最新的一筆停止指令，帶每支程式的回報（acks：收到的時間與當下的處理、最後的結果）；
 * listeners＝每支程式的心跳（中繼程式多久前回報、交易程式多久前讀過指令、今天有沒有出現過）。
 * 時間帶 +08:00；「多久以前」一律由資料庫的時鐘算（age_s 等），不拿手機的時鐘去比。表不存在會丟錯，由呼叫端決定怎麼處理。
 */
export const controlView = (db: Db, day: string) => db<ControlView[]>`
  SELECT
    (SELECT jsonb_build_object(
        'id', c.id::text,
        'day', c.day::text,
        'created_at', to_char(c.created_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
        'age_s', EXTRACT(EPOCH FROM now() - c.created_at)::float8,
        'acks', COALESCE((
          SELECT jsonb_object_agg(a.consumer, jsonb_build_object(
            'acked_at', to_char(a.acked_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
            'ack_age_s', EXTRACT(EPOCH FROM now() - a.acked_at)::float8,
            'code', a.code,
            'note', a.note,
            'done_at', to_char(a.done_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
            'done_code', a.done_code,
            'done_note', a.done_note))
          FROM web.autotrade_command_acks a WHERE a.command_id = c.id), '{}'::jsonb))::text
     FROM web.autotrade_commands c WHERE c.day = ${day}::date ORDER BY c.id DESC LIMIT 1) AS stop,
    (SELECT COALESCE(jsonb_object_agg(l.consumer, jsonb_build_object(
        'relay_age_s', EXTRACT(EPOCH FROM now() - l.relay_seen_at)::float8,
        'engine_age_s', EXTRACT(EPOCH FROM now() - l.engine_seen_at)::float8,
        'poll_s', l.poll_s::float8,
        'seen_today', (l.relay_seen_at AT TIME ZONE 'Asia/Taipei')::date = ${day}::date)), '{}'::jsonb)::text
     FROM web.autotrade_listeners l) AS listeners`

export type StopResult = { ok: true; reused: boolean; control: ControlView } | { ok: false; detail: string }

/**
 * 送出一筆停止：只會新增 action='stop' 的列。
 * 今天最新的那一筆還沒有任何程式回報 → 它還在等，不寫新的、回同一筆（連按幾次、或以為沒送出去再按，都不會疊出一堆）。
 * 已經有程式回報過（例如停過、在電腦上解除了）→ 寫新的一筆。超過一天的上限就拒絕。
 */
export async function requestStop(db: Db, email: string, nowMs: number): Promise<StopResult> {
  await ensureCommands(db)
  const day = taipeiToday(nowMs)
  const [did] = await db<{ inserted: boolean; reused: boolean }[]>`
    WITH last AS (SELECT id FROM web.autotrade_commands WHERE day = ${day}::date ORDER BY id DESC LIMIT 1),
         waiting AS (SELECT id FROM last WHERE NOT EXISTS (SELECT 1 FROM web.autotrade_command_acks a WHERE a.command_id = last.id)),
         ins AS (
           INSERT INTO web.autotrade_commands (day, action, created_by)
           SELECT ${day}::date, 'stop', ${email}
           WHERE NOT EXISTS (SELECT 1 FROM waiting)
             AND (SELECT count(*) FROM web.autotrade_commands WHERE day = ${day}::date) < ${STOP_MAX_PER_DAY}
           RETURNING id)
    SELECT EXISTS (SELECT 1 FROM ins) AS inserted, EXISTS (SELECT 1 FROM waiting) AS reused`
  if (!did.inserted && !did.reused)
    return { ok: false, detail: `今天已經送過 ${STOP_MAX_PER_DAY} 次停止，不再接受。請直接用券商的 App 處理部位。` }
  const [control] = await controlView(db, day)
  return { ok: true, reused: did.reused, control }
}
