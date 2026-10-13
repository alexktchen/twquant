// Supabase 連線（Cloudflare Workers 的 TCP socket，需 nodejs_compat）。
// 正式環境走 Hyperdrive（Cloudflare 的資料庫連線池，wrangler.toml 的 HYPERDRIVE 綁定）：
// 每個請求自己對 Supabase 開加密連線＋密碼驗證，在免費方案每次請求 10ms CPU 的上限下常被砍（error 1102，2026-10-13 實測
// 公開的 /pub/dates 十次失敗七次）。Hyperdrive 在 Cloudflare 那端保留連線，請求只要連到它。
// 本機 `wrangler pages dev` 沒有綁定時直連 TWQUANT_PG_DSN（transaction pooler :6543，不支援 prepared statements → prepare: false）。
import postgres from 'postgres'

export type Env = { TWQUANT_PG_DSN?: string; HYPERDRIVE?: { connectionString: string } }

export function sql(env: Env) {
  const dsn = env.HYPERDRIVE?.connectionString ?? env.TWQUANT_PG_DSN
  if (!dsn) throw new Error('沒有資料庫連線：HYPERDRIVE 綁定與 TWQUANT_PG_DSN 都沒設定')
  return postgres(dsn, { prepare: false, max: 1, fetch_types: false, idle_timeout: 5 })
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
