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
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, max-age=60' },
  })

export const fail = (status: number, detail: string) => json({ detail }, status)

/** 包住每個端點：資料庫錯誤一律 503，不外洩堆疊。 */
export async function guarded(env: Env, fn: (db: ReturnType<typeof sql>) => Promise<Response>) {
  const db = sql(env)
  try {
    return await fn(db)
  } catch (e) {
    return fail(503, e instanceof Error ? e.message : String(e))
  } finally {
    await db.end({ timeout: 2 })
  }
}
