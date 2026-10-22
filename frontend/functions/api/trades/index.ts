// /api/trades：我的下單（T-388／D-193 §一）。GET 列出（新到舊）、PUT 記一筆（同代號同日同方向覆蓋）、DELETE 刪一筆。
// 身分來自 Cloudflare Access 驗過的憑證（同追蹤股）；PUT／DELETE 只收本站送出的請求。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { userEmail } from '../../_lib/identity'
import { TRADE_BODY_MAX, TRADE_LIST_MAX, TRADE_MAX, parseTrade, parseTradeKey, type Trade } from '../../_lib/trades'
import { ensureTrades } from '../../_lib/trades-db'
import { sameOrigin } from '../../_lib/watchlist'
import { taipeiDay } from '../../_lib/warroom'

type Row = { stock_id: string; trade_date: string; side: Trade['side']; price: unknown; qty: unknown; source: Trade['source']; line: string | null; created_at: string }

const toTrade = (r: Row): Trade & { created_at: string } => ({
  sid: r.stock_id,
  date: r.trade_date,
  side: r.side,
  price: r.price === null ? null : Number(r.price),
  qty: r.qty === null ? null : Number(r.qty),
  source: r.source,
  line: r.line,
  created_at: r.created_at,
})

async function body(request: Request): Promise<unknown> {
  const text = await request.text()
  if (text.length > TRADE_BODY_MAX) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) =>
  guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureTrades(db)
    const rows = await db<Row[]>`
      SELECT stock_id, trade_date::text AS trade_date, side, price, qty, source, line, created_at::text AS created_at
      FROM web.my_trades WHERE email = ${email}
      ORDER BY trade_date DESC, created_at DESC, stock_id LIMIT ${TRADE_LIST_MAX}`
    return json({ today: taipeiDay(Date.now()), items: rows.map(toTrade), max: TRADE_MAX })
  })

export const onRequestPut: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const t = parseTrade(await body(request), taipeiDay(Date.now()))
  if (!t) return fail(400, '內容不合法：要代號、日期（不能是未來）、買或賣；價格 ≥ 0、張數正整數')
  return guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureTrades(db)
    const [{ n }] = await db<{ n: number }[]>`SELECT count(*)::int AS n FROM web.my_trades WHERE email = ${email}`
    if (Number(n) >= TRADE_MAX) return fail(409, `最多記 ${TRADE_MAX} 筆`)
    await db`
      INSERT INTO web.my_trades (email, stock_id, trade_date, side, price, qty, source, line)
      VALUES (${email}, ${t.sid}, ${t.date}::date, ${t.side}, ${t.price}, ${t.qty}, ${t.source}, ${t.line})
      ON CONFLICT (email, stock_id, trade_date, side)
      DO UPDATE SET price = EXCLUDED.price, qty = EXCLUDED.qty, source = EXCLUDED.source, line = EXCLUDED.line`
    return json({ ok: true, trade: t })
  })
}

export const onRequestDelete: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const k = parseTradeKey(await body(request))
  if (!k) return fail(400, '要代號、日期、買或賣')
  return guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureTrades(db)
    await db`DELETE FROM web.my_trades WHERE email = ${email} AND stock_id = ${k.sid} AND trade_date = ${k.date}::date AND side = ${k.side}`
    return json({ ok: true })
  })
}
