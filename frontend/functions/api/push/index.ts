// POST /api/push：這台裝置開通知（存訂閱）；DELETE /api/push：關通知（內容 { endpoint }）。兩個都冪等。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { accessEmail } from '../../_lib/identity'
import { PUSH_BODY_MAX, PUSH_DDL, PUSH_MAX_PER_USER, parseSubscription } from '../../_lib/push'
import { sameOrigin } from '../../_lib/watchlist'

async function readJson(request: Request): Promise<unknown> {
  const text = await request.text()
  if (text.length > PUSH_BODY_MAX) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const sub = parseSubscription(await readJson(request))
  if (sub === null) return fail(400, '通知訂閱的格式不對，或不是已知的推播服務')
  return guarded(env, async (db) => {
    const email = await accessEmail(request)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    const [{ ok }] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.push_subscriptions') IS NOT NULL AS ok`
    if (!ok) for (const stmt of PUSH_DDL) await db.unsafe(stmt)
    const [{ n }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n FROM web.push_subscriptions WHERE email = ${email} AND endpoint <> ${sub.endpoint}`
    if (n >= PUSH_MAX_PER_USER) return fail(409, `通知最多開 ${PUSH_MAX_PER_USER} 台裝置，請先在其他裝置關掉`)
    await db`
      INSERT INTO web.push_subscriptions (endpoint, email, p256dh, auth)
      VALUES (${sub.endpoint}, ${email}, ${sub.p256dh}, ${sub.auth})
      ON CONFLICT (endpoint) DO UPDATE SET email = EXCLUDED.email, p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth`
    return json({ ok: true })
  })
}

export const onRequestDelete: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const body = (await readJson(request)) as { endpoint?: unknown } | null
  const endpoint = body?.endpoint
  if (typeof endpoint !== 'string' || endpoint.length > 1000) return fail(400, '缺少要關掉的通知網址')
  return guarded(env, async (db) => {
    const email = await accessEmail(request)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    const [{ ok }] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.push_subscriptions') IS NOT NULL AS ok`
    if (ok) await db`DELETE FROM web.push_subscriptions WHERE endpoint = ${endpoint} AND email = ${email}`
    return json({ ok: true })
  })
}
