// 訊息匯流排（autotrade A-019／D-231）：網站的「啟動」「停止」寫進 web.autotrade_messages，交易 agent 讀到後執行並回報。
// 啟動＝待命 → 運作；停止＝先平倉、再回待命（不是緊急停止，隨時可以再啟動）。緊急停止仍然走 ./autotrade.ts 那張表。
// 這裡一樣沒有、也不可以加：手動買賣、改設定、解除緊急停止。網站只會新增「指令」這一種訊息；回報是 agent 寫的。
import type { sql } from './db.ts'
import { modeOk, taipeiToday, type AutotradeMode, type RemoteView } from './autotrade.ts'
import { COMMAND_MAX_PER_DAY, REQUESTS_SHOWN, REQUEST_TTL_S, ensureRequests, requestsView } from './autotrade_remote.ts'

type Db = ReturnType<typeof sql>

/** 跟 autotrade/src/autotrade/bus.py 的 MESSAGES_DDL 一字不差（兩邊各有測試守著）。 */
export const MESSAGES_DDL = [
  "CREATE TABLE IF NOT EXISTS web.autotrade_messages (id bigint PRIMARY KEY DEFAULT nextval('web.autotrade_commands_id_seq'), day date NOT NULL, topic text NOT NULL CHECK (topic IN ('command', 'report', 'signal', 'fill', 'status')), target text NOT NULL CHECK (target IN ('sim', 'live')), action text NOT NULL, body jsonb NOT NULL DEFAULT '{}'::jsonb, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), ttl_s real, reply_to bigint, CHECK (topic <> 'command' OR action IN ('arm', 'standby')))",
  'ALTER TABLE web.autotrade_messages ENABLE ROW LEVEL SECURITY',
  'CREATE INDEX IF NOT EXISTS autotrade_messages_day ON web.autotrade_messages (day, topic, id)',
  'CREATE INDEX IF NOT EXISTS autotrade_messages_reply ON web.autotrade_messages (reply_to) WHERE reply_to IS NOT NULL',
] as const

export const BUS_ACTIONS = ['arm', 'standby'] as const
export type BusAction = (typeof BUS_ACTIONS)[number]

/** 請求內容一定要是 {"confirm": "arm" 或 "standby", "target": "sim" 或 "live"}，多一個欄位都不收。 */
export function busCommandOk(body: unknown): { action: BusAction; target: AutotradeMode } | null {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null
  const b = body as Record<string, unknown>
  if (Object.keys(b).length !== 2) return null
  const action = b.confirm
  const target = b.target
  if (typeof action !== 'string' || !(BUS_ACTIONS as readonly string[]).includes(action)) return null
  if (typeof target !== 'string' || !modeOk(target)) return null
  return { action: action as BusAction, target }
}

export async function ensureMessages(db: Db): Promise<void> {
  await ensureRequests(db) // 編號用的是指令表的序號；回傳的清單也要另外那張表在
  const [{ n }] = await db<{ n: number }[]>`
    SELECT count(*)::int AS n FROM information_schema.tables
    WHERE table_schema = 'web' AND table_name = 'autotrade_messages'`
  if (Number(n) < 1) for (const stmt of MESSAGES_DDL) await db.unsafe(stmt)
}

/**
 * 那一天最近幾則匯流排指令（新的在前），整理成跟遙控指令同一個形狀：「收到」取第一則回報，結果取最後一則。
 * 表不存在會丟錯，由呼叫端決定怎麼處理。
 */
export const busView = (db: Db, day: string) => db<{ requests: string | null }[]>`
  SELECT (SELECT COALESCE(jsonb_agg(x.r ORDER BY x.id DESC), '[]'::jsonb)::text FROM (
     SELECT m.id, jsonb_build_object(
       'id', m.id::text,
       'action', m.action,
       'target', m.target,
       'created_at', to_char(m.created_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
       'age_s', EXTRACT(EPOCH FROM now() - m.created_at)::float8,
       'acked_at', to_char(a.created_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
       'ack_age_s', EXTRACT(EPOCH FROM now() - a.created_at)::float8,
       'code', a.body ->> 'code',
       'note', a.body ->> 'note',
       'done_at', to_char(d.created_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00',
       'done_code', d.body ->> 'code',
       'done_note', d.body ->> 'note') AS r
     FROM web.autotrade_messages m
     LEFT JOIN LATERAL (SELECT r.created_at, r.body FROM web.autotrade_messages r
        WHERE r.reply_to = m.id AND r.topic = 'report' ORDER BY r.id LIMIT 1) a ON true
     LEFT JOIN LATERAL (SELECT r.created_at, r.body FROM web.autotrade_messages r
        WHERE r.reply_to = m.id AND r.topic = 'report' AND r.action = 'done' ORDER BY r.id DESC LIMIT 1) d ON true
     WHERE m.day = ${day}::date AND m.topic = 'command' ORDER BY m.id DESC LIMIT ${REQUESTS_SHOWN}) x) AS requests`

/** 兩張表的指令併成一份清單（編號是同一個序號，越大越新），留最近幾筆。內容是資料庫組好的 JSON 文字。 */
export function mergeRequests(a: string | null, b: string | null): string {
  const rows = [...(JSON.parse(a ?? '[]') as { id: string }[]), ...(JSON.parse(b ?? '[]') as { id: string }[])]
  rows.sort((x, y) => Number(y.id) - Number(x.id))
  return JSON.stringify(rows.slice(0, REQUESTS_SHOWN))
}

/** 把匯流排的指令併進遙控指令的現況；兩邊的表都還沒建就回 null。 */
export function withBus(remote: RemoteView | null, bus: { requests: string | null } | null): RemoteView | null {
  if (remote === null && bus === null) return null
  return { requests: mergeRequests(remote?.requests ?? null, bus?.requests ?? null), remote: remote?.remote ?? null }
}

export type BusResult = { ok: true; reused: boolean; view: RemoteView } | { ok: false; detail: string }

/**
 * 送出啟動或停止：只會新增一則「指令」。
 * 同一種動作、同一種模式的上一則還在等（沒有回報、也還沒作廢）→ 不寫新的（手抖連按不會疊）。超過一天的上限就拒絕。
 */
export async function busAction(db: Db, email: string, nowMs: number, action: BusAction, target: AutotradeMode): Promise<BusResult> {
  await ensureMessages(db)
  const day = taipeiToday(nowMs)
  const [did] = await db<{ inserted: boolean; reused: boolean }[]>`
    WITH waiting AS (
           SELECT m.id FROM web.autotrade_messages m
           WHERE m.day = ${day}::date AND m.topic = 'command' AND m.action = ${action} AND m.target = ${target}
             AND m.created_at > now() - make_interval(secs => ${REQUEST_TTL_S}::double precision)
             AND NOT EXISTS (SELECT 1 FROM web.autotrade_messages r WHERE r.reply_to = m.id AND r.topic = 'report') LIMIT 1),
         ins AS (
           INSERT INTO web.autotrade_messages (day, topic, target, action, created_by, ttl_s)
           SELECT ${day}::date, 'command', ${target}, ${action}, ${email}, ${REQUEST_TTL_S}
           WHERE NOT EXISTS (SELECT 1 FROM waiting)
             AND (SELECT count(*) FROM web.autotrade_messages WHERE day = ${day}::date AND topic = 'command') < ${COMMAND_MAX_PER_DAY}
           RETURNING id)
    SELECT EXISTS (SELECT 1 FROM ins) AS inserted, EXISTS (SELECT 1 FROM waiting) AS reused`
  if (!did.inserted && !did.reused) return { ok: false, detail: `今天已經送過 ${COMMAND_MAX_PER_DAY} 次啟動／停止，不再接受。` }
  const [remote] = await requestsView(db, day)
  const [bus] = await busView(db, day)
  return { ok: true, reused: did.reused, view: withBus(remote ?? null, bus ?? null) ?? { requests: '[]', remote: null } }
}
