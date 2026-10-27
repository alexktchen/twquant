// GET /api/futures/days：有小台模擬紀錄的交易日（新到舊），每天的來源、狀態、合計與收盤重算是否一致。只給 admin（T-450／D-220）；gap_total＝開高走低的合計（T-452，舊資料沒有 → null）；trades、gap_trades＝兩邊已平倉的成交（T-454，日曆算帳戶多空各 1 口的損益）。
// 權限一定在讀資料庫之前擋；盤中那一天的合計會變，不進暫存。
import { guarded, json, type Env } from '../../_lib/db'
import { DAYS_LIMIT } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { readRows } from '../../_lib/scorecard'

type Row = { day: string; source: string; status: string; total: number | null; match: boolean | null; gap_total: number | null; trades: unknown[] | null; gap_trades: unknown[] | null }

const handle: PagesFunction<Env> = async ({ env }) =>
  guarded(env, async (db) => {
    const days = await readRows(
      () => db<Row[]>`
        SELECT day::text AS day, source, status, (payload->>'total')::float8 AS total,
               (payload->>'official_match')::boolean AS match,
               (payload->'gap_fail'->>'total')::float8 AS gap_total,
               payload->'trades' AS trades, payload->'gap_fail'->'trades' AS gap_trades
        FROM serving.futures_paper ORDER BY day DESC LIMIT ${DAYS_LIMIT}`,
    )
    return json({ days })
  })

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
