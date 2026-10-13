// GET /pub/dates：有名單的交易日，公開（不在 Access 保護的 /api 底下）。
// 只有日期、沒有任何股票，讓沒登入的人也能切到歷史名單；名單本身仍只從 /api 給已登入的人。
import { type Env } from '../_lib/db'
import { cached, cacheKey } from '../_lib/cache'
import { listDates } from '../api/picks/dates'

// 60 秒暫存（_lib/cache.ts）
export const onRequestGet: PagesFunction<Env> = ({ request, env }) =>
  cached(cacheKey(request), () => listDates(request, env, '資料暫時無法取得'))
