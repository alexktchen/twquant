// GET /api/autotrade?mode=sim|live&day=YYYY-MM-DD：程式交易的狀態（autotrade A-005）。一次回日期清單、那一天的整份狀態（沒給 day＝最新一天），
// 以及遠端停止的現況（A-011）：今天最新的一筆停止指令、每支程式的回報與心跳——不管看的是哪個分頁，模擬與真單的都回。
// 只給管理員（Cloudflare Access 簽的登入＋名單，_lib/access.ts）；權限一定在讀資料庫之前擋。這支只讀；送出停止在 ./stop.ts。
// 表還沒建、或還沒有任何紀錄 → 回空的（days: []、current: null、stop: null、listeners: {}），不是錯誤。盤中會變，不進暫存。
import { admit } from '../../_lib/access'
import { fail, guarded, type Env } from '../../_lib/db'
import { AUTOTRADE_DAYS_LIMIT, controlView, envelope, modeOk, taipeiToday, type AutotradeDayRow } from '../../_lib/autotrade'
import { requestsView } from '../../_lib/autotrade_remote'
import { dayOk, rawJson, readRows } from '../../_lib/http'

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
    const control = await readRows(() => controlView(db, taipeiToday(Date.now())))
    // 遙控指令（A-016）：今天最近幾筆與後台的心跳；表還沒建就整段不回
    const remote = await readRows(() => requestsView(db, taipeiToday(Date.now())))
    return rawJson(envelope(days, rows[0]?.body ?? null, control[0] ?? null, remote[0] ?? null))
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const who = await admit(ctx.request, ctx.env)
  return who instanceof Response ? who : handle(ctx)
}
