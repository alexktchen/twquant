// GET /pub/market-universe（T-397／D-198）：盤中大盤體檢要問哪些股票——四碼普通股的代號、市場，
// 前 20 個交易日的最高／最低收盤（算新高新低）與四碼普通股每日成交金額的平均（算量能）。
// 給排程 Worker（market-live/）每天第一次執行時抓一次。公開：都是證交所／櫃買公布過的行情，沒有名單或使用者資料。
// 資料庫直接組好一段 JSON 文字回傳（不在 Worker 裡逐列解析，免費方案 CPU 只有 10ms）。
import { guarded, type Env } from '../_lib/db'
import { cached, cacheKey } from '../_lib/cache'
import { AVG_SESSIONS, HIGH_LOW_WINDOW, STOCK_PATTERN } from '../_lib/market-live'

/** 往回看幾個日曆日找交易日：要蓋住 max(AVG_SESSIONS, HIGH_LOW_WINDOW) 個交易日（春節連假也夠）。 */
const RECENT_DAYS = 90

const handle = (env: Env) =>
  guarded(
    env,
    async (db) => {
      // 交易日只在最近 RECENT_DAYS 個日曆日裡找，不掃整張表（2026-10-19 實測整張表 8 秒、這樣約 0.1 秒）；
      // 只看台北今天以前：盤中比的是「前 20 日」，今天的收盤（17:40 以後才進資料庫）不能算進視窗，否則新高新低永遠是 0
      const [row] = await db<{ j: string | null }[]>`
        WITH days AS (
          SELECT DISTINCT date FROM serving.prices_daily
          WHERE date >= (SELECT max(date) FROM serving.prices_daily) - ${RECENT_DAYS}::int
            AND date < (now() AT TIME ZONE 'Asia/Taipei')::date
        ),
        hl_days AS (SELECT date FROM days ORDER BY date DESC LIMIT ${HIGH_LOW_WINDOW}),
        avg_days AS (SELECT date FROM days ORDER BY date DESC LIMIT ${AVG_SESSIONS}),
        asof AS (SELECT max(date) AS d FROM days),
        hl AS (
          SELECT stock_id, max(close) AS hi, min(close) AS lo, count(*) AS n
          FROM serving.prices_daily
          WHERE date IN (SELECT date FROM hl_days) AND stock_id ~ ${STOCK_PATTERN} AND close > 0
          GROUP BY stock_id
        ),
        listed AS (
          SELECT p.stock_id, CASE m.market WHEN 'TPEX' THEN 'otc' ELSE 'tse' END AS mk
          FROM serving.prices_daily p JOIN serving.stock_meta m USING (stock_id)
          WHERE p.date = (SELECT d FROM asof) AND p.stock_id ~ ${STOCK_PATTERN} AND m.market IN ('TWSE', 'TPEX')
        )
        SELECT json_build_object(
          'asof', (SELECT d FROM asof)::text,
          'amount_avg', (
            SELECT avg(s) FROM (
              SELECT sum(amount) AS s FROM serving.prices_daily
              WHERE date IN (SELECT date FROM avg_days) AND stock_id ~ ${STOCK_PATTERN} GROUP BY date
            ) a
            WHERE (SELECT count(*) FROM avg_days) = ${AVG_SESSIONS}
          ),
          -- 分上市、上櫃的 20 日均額（D-199：盤中「上市／上櫃」切換的量能比較用）
          'amount_avg_by', (
            SELECT json_object_agg(mk, a) FROM (
              SELECT mk, avg(s) AS a FROM (
                SELECT CASE m.market WHEN 'TPEX' THEN 'otc' ELSE 'tse' END AS mk, p.date, sum(p.amount) AS s
                FROM serving.prices_daily p JOIN serving.stock_meta m USING (stock_id)
                WHERE p.date IN (SELECT date FROM avg_days) AND p.stock_id ~ ${STOCK_PATTERN} AND m.market IN ('TWSE', 'TPEX')
                GROUP BY 1, 2
              ) d GROUP BY mk
            ) x
            WHERE (SELECT count(*) FROM avg_days) = ${AVG_SESSIONS}
          ),
          'stocks', (
            SELECT json_agg(json_build_array(
              l.stock_id, l.mk,
              CASE WHEN h.n = ${HIGH_LOW_WINDOW} THEN h.hi END,
              CASE WHEN h.n = ${HIGH_LOW_WINDOW} THEN h.lo END
            ) ORDER BY l.stock_id)
            FROM listed l LEFT JOIN hl h USING (stock_id)
          )
        )::text AS j`
      // 平均：視窗不足 AVG_SESSIONS 天就是 null（同 market_daily：不知道，不是 0）
      if (!row?.j) throw new Error('沒有股票清單')
      return new Response(row.j, { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } })
    },
    '資料暫時無法取得',
  )

export const onRequestGet: PagesFunction<Env> = ({ request, env }) => cached(cacheKey(request), () => handle(env))
