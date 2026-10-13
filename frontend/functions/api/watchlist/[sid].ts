// PUT /api/watchlist/:sid 加入追蹤、DELETE 移除。兩個都冪等：已追蹤再加、沒追蹤再刪都回成功。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { accessEmail } from '../../_lib/identity'
import { SID_RE } from '../../_lib/picks'
import { WATCH_MAX, sameOrigin } from '../../_lib/watchlist'
import { ensureWatchlist } from '../../_lib/watchlist-db'

function check(request: Request, sid: string): Response | null {
  if (!sameOrigin(request)) return fail(403, '只接受本站送出的變更')
  if (!SID_RE.test(sid)) return fail(400, `非法股票代號：${sid}`)
  return null
}

export const onRequestPut: PagesFunction<Env, 'sid'> = async ({ request, params, env }) => {
  const sid = String(params.sid)
  return (
    check(request, sid) ??
    guarded(env, async (db) => {
      const email = await accessEmail(request)
      if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
      const known = await db`SELECT 1 FROM serving.prices_daily WHERE stock_id = ${sid} LIMIT 1`
      if (known.length === 0) return fail(404, `雲端沒有 ${sid} 的股價（只收上市櫃四碼普通股）`)
      await ensureWatchlist(db)
      // 新加入的排最上面：位置比目前最小的再小 1
      const added = await db`
        INSERT INTO web.watchlist (email, stock_id, position)
        SELECT ${email}, ${sid}, (SELECT coalesce(min(position), 1) - 1 FROM web.watchlist WHERE email = ${email})
        WHERE (SELECT count(*) FROM web.watchlist WHERE email = ${email}) < ${WATCH_MAX}
        ON CONFLICT (email, stock_id) DO NOTHING RETURNING stock_id`
      if (added.length === 0) {
        const has = await db`SELECT 1 FROM web.watchlist WHERE email = ${email} AND stock_id = ${sid}`
        if (has.length === 0) return fail(409, `追蹤股最多 ${WATCH_MAX} 檔，請先移除一些`)
      }
      return json({ ok: true, sid })
    })
  )
}

export const onRequestDelete: PagesFunction<Env, 'sid'> = async ({ request, params, env }) => {
  const sid = String(params.sid)
  return (
    check(request, sid) ??
    guarded(env, async (db) => {
      const email = await accessEmail(request)
      if (email === null) return fail(401, '登入身分驗證失敗，請重新登入')
      await ensureWatchlist(db)
      await db`DELETE FROM web.watchlist WHERE email = ${email} AND stock_id = ${sid}`
      return json({ ok: true, sid })
    })
  )
}
