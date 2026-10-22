// GET /api/layout：登入者的首頁版面 { layout: { order, collapsed } | null }（null＝沒存過，網站用預設）。
// PUT /api/layout：存版面，內容 { order: [卡片代號…], collapsed: [卡片代號…] }。
// DELETE /api/layout：恢復預設（刪掉這個帳號的版面紀錄）。
// 身分一律取自 Cloudflare Access 驗過的登入憑證，不信任網頁帶上來的任何帳號資訊；寫入只接受本站網頁送來的請求。
import { fail, guarded, json, type Env } from '../_lib/db'
import { userEmail } from '../_lib/identity'
import { LAYOUT_BODY_MAX, parseLayout, type SavedLayout } from '../_lib/layout'
import { ensureLayout } from '../_lib/layout-db'
import { sameOrigin } from '../_lib/watchlist'

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) =>
  guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureLayout(db)
    const [row] = await db<{ layout: SavedLayout }[]>`SELECT layout FROM web.home_layout WHERE email = ${email}`
    // 存進去時驗過；讀回來再驗一次，萬一被手動改壞就當沒存過
    return json({ layout: row ? parseLayout(row.layout) : null })
  })

export const onRequestPut: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  const text = await request.text()
  if (text.length > LAYOUT_BODY_MAX) return fail(413, '版面內容太長')
  let body: unknown = null
  try {
    body = JSON.parse(text)
  } catch {
    return fail(400, '版面內容不是 JSON')
  }
  const layout = parseLayout(body)
  if (layout === null) return fail(400, '版面格式不對：order 要是不重複的卡片代號，collapsed 只能是 order 裡的卡片')
  return guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureLayout(db)
    await db`
      INSERT INTO web.home_layout (email, layout, updated_at) VALUES (${email}, ${JSON.stringify(layout)}::jsonb, now())
      ON CONFLICT (email) DO UPDATE SET layout = EXCLUDED.layout, updated_at = now()`
    return json({ ok: true })
  })
}

export const onRequestDelete: PagesFunction<Env> = async ({ request, env }) => {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  return guarded(env, async (db) => {
    const email = await userEmail(request, env)
    if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
    await ensureLayout(db)
    await db`DELETE FROM web.home_layout WHERE email = ${email}`
    return json({ ok: true })
  })
}
