// GET /api/autotrade?mode=sim|live&day=YYYY-MM-DD：程式交易的狀態（autotrade A-005）。一次回日期清單與那一天的整份狀態（沒給 day＝最新一天）。
// 只給 admin（跟小台模擬同一道權限）；權限一定在讀資料庫之前擋。單向：這裡只讀，沒有寫回交易程式的路。
// 表還沒建、或還沒有任何紀錄 → 回空的（days: []、current: null），不是錯誤。盤中會變，不進暫存。
import { fail, guarded, type Env } from '../_lib/db'
import { AUTOTRADE_DAYS_LIMIT, envelope, modeOk, type AutotradeDayRow } from '../_lib/autotrade'
import { dayOk } from '../_lib/futures'
import { denied } from '../_lib/gate'
import { rawJson, readRows } from '../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const q = new URL(request.url).searchParams
  const mode = q.get('mode') ?? 'sim'
  const day = q.get('day')
  if (!modeOk(mode)) return fail(400, `模式不對：${mode}`)
  if (day !== null && !dayOk(day)) return fail(400, `日期格式不對：${day}`)
  return guarded(env, async (db) => {
    const days = await readRows(
      () => db<AutotradeDayRow[]>`
        SELECT day::text AS day, status, (payload->>'day_pnl_twd')::float8 AS pnl, jsonb_array_length(payload->'fills') AS fills
        FROM serving.autotrade_status WHERE mode = ${mode} ORDER BY day DESC LIMIT ${AUTOTRADE_DAYS_LIMIT}`,
    )
    const pick = day ?? days[0]?.day ?? null
    const rows =
      pick === null
        ? []
        : await readRows(() => db<{ body: string }[]>`SELECT payload::text AS body FROM serving.autotrade_status WHERE mode = ${mode} AND day = ${pick}`)
    return rawJson(envelope(days, rows[0]?.body ?? null))
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
