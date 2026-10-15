// GET /api/mood/close：市場氛圍的收盤版（D-181）——最近一天族群資金（成交比重 vs 前 20 日平均）的流入、流出、
// 防禦族群合計，加上加權、櫃買當天漲跌。每天收盤後排程算好族群資金（約 17:40），還沒算過 → close: null。
import { guarded, json, type Env } from '../../_lib/db'
import { readClose } from '../../_lib/mood'
import { cached, cacheKey } from '../../_lib/cache'

const handle: PagesFunction<Env> = async ({ env }) => guarded(env, async (db) => json({ close: await readClose(db) }))

// 每個人看到都一樣、一天只更新兩次：同一執行個體 60 秒暫存，省下連資料庫的 CPU（_lib/cache.ts）
export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
