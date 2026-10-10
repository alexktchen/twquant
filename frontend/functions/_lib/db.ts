// Supabase 連線（Cloudflare Workers 的 TCP socket，需 nodejs_compat）。
// transaction pooler（:6543）不支援 prepared statements → prepare: false。
import postgres from 'postgres'

export type Env = { TWQUANT_PG_DSN: string }

export function sql(env: Env) {
  if (!env.TWQUANT_PG_DSN) throw new Error('TWQUANT_PG_DSN 未設定')
  return postgres(env.TWQUANT_PG_DSN, { prepare: false, max: 1, fetch_types: false, idle_timeout: 5 })
}

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    // no-store：登出後瀏覽器不能再從快取拿到上一個人的名單
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' },
  })

export const fail = (status: number, detail: string) => json({ detail }, status)

/** 包住每個端點：資料庫錯誤一律 503，不外洩堆疊。公開端點傳 publicDetail，連錯誤訊息都不回（可能含主機名）。 */
export async function guarded(
  env: Env,
  fn: (db: ReturnType<typeof sql>) => Promise<Response>,
  publicDetail?: string,
) {
  const db = sql(env)
  try {
    return await fn(db)
  } catch (e) {
    return fail(503, publicDetail ?? (e instanceof Error ? e.message : String(e)))
  } finally {
    await db.end({ timeout: 2 })
  }
}
