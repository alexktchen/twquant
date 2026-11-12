// /api/futures/journal：台指期檢討單（T-467）。GET 列出（新到舊）、PUT 記一筆、DELETE 刪一筆。
// 權限與台指期分頁相同；身分來自驗過的憑證；只動自己帳號的資料，PUT／DELETE 只收本站送出的請求。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { JOURNAL_BODY_MAX, JOURNAL_LIST_MAX, JOURNAL_MAX, ensureJournal, parseJournal } from '../../_lib/futures-journal'
import { denied } from '../../_lib/gate'
import { userEmail } from '../../_lib/identity'
import { sameOrigin } from '../../_lib/watchlist'
import { taipeiDay } from '../../_lib/warroom'

async function body(request: Request): Promise<unknown> {
  const text = await request.text()
  if (text.length > JOURNAL_BODY_MAX) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

type Ctx = Parameters<PagesFunction<Env>>[0]

const list = ({ request, env }: Ctx) =>
  guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureJournal(db)
    const rows = await db`
      SELECT id::text AS id, trade_date::text AS date, product, side, entry_time AS time, entry_price AS entry, exit_price AS exit,
             stop_price AS stop, qty, setup, followed, note
      FROM web.futures_journal WHERE email = ${email}
      ORDER BY trade_date DESC, entry_time DESC, id DESC LIMIT ${JOURNAL_LIST_MAX}`
    return json({ today: taipeiDay(Date.now()), items: rows, max: JOURNAL_MAX })
  })

const add = async ({ request, env }: Ctx) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const t = parseJournal(await body(request), taipeiDay(Date.now()))
  if (!t) return fail(400, '內容不合法：要日期（不能是未來）、商品、方向、時間 HH:MM、進出場價、口數、進場理由')
  return guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureJournal(db)
    const [{ n }] = await db<{ n: number }[]>`SELECT count(*)::int AS n FROM web.futures_journal WHERE email = ${email}`
    if (Number(n) >= JOURNAL_MAX) return fail(409, `最多記 ${JOURNAL_MAX} 筆`)
    const [row] = await db<{ id: string }[]>`
      INSERT INTO web.futures_journal (email, trade_date, product, side, entry_time, entry_price, exit_price, stop_price, qty, setup, followed, note)
      VALUES (${email}, ${t.date}::date, ${t.product}, ${t.side}, ${t.time}, ${t.entry}, ${t.exit}, ${t.stop}, ${t.qty}, ${t.setup}, ${t.followed}, ${t.note})
      RETURNING id::text AS id`
    return json({ ok: true, id: row.id })
  })
}

const remove = async ({ request, env }: Ctx) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const b = (await body(request)) as { id?: unknown } | null
  const id = String(b?.id ?? '')
  if (!/^\d{1,18}$/.test(id)) return fail(400, '要這筆的編號')
  return guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureJournal(db)
    await db`DELETE FROM web.futures_journal WHERE id = ${id}::bigint AND email = ${email}`
    return json({ ok: true })
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? list(ctx)
export const onRequestPut: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? add(ctx)
export const onRequestDelete: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? remove(ctx)
