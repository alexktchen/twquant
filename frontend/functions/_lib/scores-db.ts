// 模型分數的查詢（個股評分與追蹤股清單共用）。名次在資料庫算：同模型同一天依分數由高到低。
import type { sql } from './db.ts'
import { MODEL_IDS, type ModelDay, type ScoreRow } from './scores.ts'

/** 回看幾個打分日（雲端 ml_scores 只留 5 個交易日，見 configs/serving.yaml cloud.windows）。 */
export const SCORE_DAYS = 5

/** 這些股票最近 SCORE_DAYS 個打分日的分數與名次，加上每個模型每天的檔數與平均。 */
export async function loadScores(
  db: ReturnType<typeof sql>,
  sids: string[],
): Promise<{ rows: ScoreRow[]; days: ModelDay[] }> {
  const models = MODEL_IDS.join(',')
  const days = await db<ModelDay[]>`
    WITH d AS (SELECT DISTINCT date FROM serving.ml_scores ORDER BY date DESC LIMIT ${SCORE_DAYS})
    SELECT model_id, date::text AS date, count(*)::int AS total, avg(score)::float8 AS avg
    FROM serving.ml_scores
    WHERE date IN (SELECT date FROM d) AND model_id = ANY(string_to_array(${models}, ',')) AND score IS NOT NULL
    GROUP BY model_id, date`
  if (sids.length === 0) return { rows: [], days }
  const rows = await db<ScoreRow[]>`
    WITH d AS (SELECT DISTINCT date FROM serving.ml_scores ORDER BY date DESC LIMIT ${SCORE_DAYS})
    SELECT model_id, date::text AS date, stock_id, score, rank_pct, rank, total FROM (
      SELECT model_id, date, stock_id, score, rank_pct,
        rank() OVER (PARTITION BY model_id, date ORDER BY score DESC)::int AS rank,
        count(*) OVER (PARTITION BY model_id, date)::int AS total
      FROM serving.ml_scores
      WHERE date IN (SELECT date FROM d) AND model_id = ANY(string_to_array(${models}, ',')) AND score IS NOT NULL
    ) t
    WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ','))`
  return { rows, days }
}
