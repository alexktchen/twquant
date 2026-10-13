// 個股的模型評分（追蹤股功能）：每天收盤後打分的三個模型，雲端 ml_scores 只留最近 5 個交易日。
// 純函數放這裡供 node 單元測試；SQL 在 scores-db.ts。

/** 三個每日打分的模型——以 configs/ml.yaml 為準（tests/test_cloud_web_models.py 守著，換模型時那邊會紅）。
 *  rank：production_ranking_model；quiet：quiet_live_model；risk：recommendation_buckets.risk_gate_model。 */
export const MODELS = [
  { code: 'rank', model_id: 'lgbm_lw_net_h20_20260225_83890036' },
  { code: 'quiet', model_id: 'lgbm_quietA20_tu_h20_20260430_655df6d7' },
  { code: 'risk', model_id: 'lgbm_lw_dd_h60_20260225_1bd7c6a0' },
] as const
export type ModelCode = (typeof MODELS)[number]['code']
export const MODEL_IDS: string[] = MODELS.map((m) => m.model_id)

/** 某檔某天的分數，rank＝當天分數由高到低第幾名、total＝當天打了幾檔。 */
export type ScoreRow = {
  model_id: string
  date: string
  stock_id: string
  score: number
  rank_pct: number | null
  rank: number
  total: number
}
/** 每個模型每天打了幾檔、平均分數（給「池內平均」與不在池子時的檔數）。 */
export type ModelDay = { model_id: string; date: string; total: number; avg: number | null }

export type ModelScore = {
  code: ModelCode
  /** 這個模型最近一次打分的日子；雲端沒有任何分數 → null */
  date: string | null
  /** 這檔在 date 那天的分數；那天沒評到這檔（例：不在潛伏池）→ 全部 null，total／avg 仍是那天的全體 */
  score: number | null
  rank_pct: number | null
  rank: number | null
  total: number | null
  avg: number | null
  /** 這檔有分數的日子，舊到新 */
  history: { date: string; score: number; rank_pct: number | null }[]
}

/** 把一檔股票的分數列整理成三個模型各一張卡片的資料（依 MODELS 順序）。 */
export function modelScores(sid: string, rows: ScoreRow[], days: ModelDay[]): ModelScore[] {
  return MODELS.map(({ code, model_id }) => {
    const latest = days
      .filter((d) => d.model_id === model_id)
      .reduce<ModelDay | null>((a, d) => (a === null || d.date > a.date ? d : a), null)
    const mine = rows.filter((r) => r.model_id === model_id && r.stock_id === sid).sort((a, b) => a.date.localeCompare(b.date))
    const today = latest ? mine.find((r) => r.date === latest.date) ?? null : null
    return {
      code,
      date: latest?.date ?? null,
      score: today?.score ?? null,
      rank_pct: today?.rank_pct ?? null,
      rank: today?.rank ?? null,
      total: latest?.total ?? null,
      avg: latest?.avg ?? null,
      history: mine.map((r) => ({ date: r.date, score: r.score, rank_pct: r.rank_pct })),
    }
  })
}

/** 搜尋最多回幾檔。 */
export const SEARCH_LIMIT = 10

/** 搜尋字串：去頭尾空白、1～20 字。text 給「代號完全相同排最前」，like 是跳脫過萬用字元的 LIKE 片段
 *  （使用者打 % 或 _ 只當一般字）。不合格 → null。 */
export function searchPattern(raw: string | null): { text: string; like: string } | null {
  const text = (raw ?? '').trim()
  if (text.length < 1 || text.length > 20) return null
  return { text, like: text.replace(/[\\%_]/g, (c) => `\\${c}`) }
}
