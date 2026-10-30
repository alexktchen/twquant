// GET /api/futures/estimate?product=MTX|TX|TMF&day=YYYY-MM-DD：網友原版那一天的 1 分 K 估算（合計、筆數、每筆成交）。只給 admin（T-462）。
// 估算是研究分支 ms_halfyear.py 用富邦 1 分 K 還原逐筆跑的，1 分 K 對網友原版偏悲觀（每筆少算約 10 點）；畫面要標「估算」。
import { fail, guarded, type Env } from '../../_lib/db'
import { dayOk, flowProductOk } from '../../_lib/futures'
import { denied } from '../../_lib/gate'
import { rawJson, readRows } from '../../_lib/scorecard'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url)
  const product = url.searchParams.get('product')
  const day = url.searchParams.get('day')
  if (!flowProductOk(product)) return fail(400, `沒有這個商品：${product ?? '（未指定）'}`)
  if (day === null || !dayOk(day)) return fail(400, `日期格式不對：${day ?? '（未指定）'}`)
  return guarded(env, async (db) => {
    const rows = await readRows(
      () => db<{ body: string }[]>`
        SELECT json_build_object('day', day::text, 'product', product, 'source', source, 'total', total, 'n', n, 'trades', trades)::text AS body
        FROM serving.futures_ms_estimate WHERE product = ${product} AND day = ${day}`,
    )
    if (rows.length === 0) return fail(404, `${day} 沒有動能順勢的估算`)
    return rawJson(rows[0].body)
  })
}

export const onRequestGet: PagesFunction<Env> = async (ctx) => (await denied(ctx.request, 'futures', ctx.env)) ?? handle(ctx)
