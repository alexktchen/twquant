// 新聞類型（T-421；使用者 2026-10-20 選的「新聞類型標籤」）：本機訓好的 softmax 探針（config/news_types.json，
// bge-m3 向量上 6 類），這裡對每則新聞的向量算一次 softmax(w·v+b) 就有類型，不花 AI 額度。
// 跟 Python 的 twquant.data.news_types.classify 同一條公式；信心不到 min_confidence → other。

export type TypeWeights = {
  version: string
  model: string
  classes: string[]
  labels: Record<string, string>
  min_confidence: number
  w: number[][] // k × d
  b: number[]
}

export function validateTypeWeights(x: unknown): TypeWeights {
  const t = x as Partial<TypeWeights> | null
  const fail = (why: string): never => {
    throw new Error(`news_types.json 格式錯誤：${why}`)
  }
  if (!t || typeof t.version !== 'string' || !t.version) fail('version')
  if (!Array.isArray(t!.classes) || t!.classes.length < 2 || t!.classes[t!.classes.length - 1] !== 'other') fail('classes（最後要是 other）')
  if (!t!.labels || t!.classes!.some((c) => typeof t!.labels![c] !== 'string')) fail('labels')
  if (typeof t!.min_confidence !== 'number' || t!.min_confidence <= 0 || t!.min_confidence >= 1) fail('min_confidence')
  if (!Array.isArray(t!.w) || t!.w.length !== t!.classes!.length || !Array.isArray(t!.b) || t!.b.length !== t!.classes!.length) fail('w／b 列數要等於類別數')
  const d = t!.w![0].length
  if (d < 2 || t!.w!.some((row) => row.length !== d)) fail('w 每列長度要一樣')
  return t as TypeWeights
}

export type Classified = { type: string; confidence: number }

/** softmax(w·v+b) 最高的那類與信心；信心不到 min_confidence → other。 */
export function classify(vec: number[], w: TypeWeights): Classified {
  const d = w.w[0].length
  if (vec.length !== d) throw new Error(`向量長度 ${vec.length} ≠ 探針維度 ${d}`)
  const z = w.w.map((row, k) => {
    let s = w.b[k]
    for (let i = 0; i < d; i++) s += row[i] * vec[i]
    return s
  })
  const m = Math.max(...z)
  const e = z.map((v) => Math.exp(v - m))
  const sum = e.reduce((a, b) => a + b, 0)
  let best = 0
  for (let k = 1; k < e.length; k++) if (e[k] > e[best]) best = k
  const confidence = e[best] / sum
  return { type: confidence >= w.min_confidence ? w.classes[best] : 'other', confidence }
}

export type TypeRow = { item_id: string; type: string; confidence: number; version: string }
export type TypeDeps = {
  /** 有向量、還沒有這一版類型的新聞（新的先） */
  pending: (limit: number) => Promise<{ item_id: string; vec: number[] }[]>
  save: (rows: TypeRow[]) => Promise<void>
}

/** ingest 第六步：把還沒分類的新聞分完（每次最多 limit 則；不花額度，舊新聞第一次部署後幾輪補完）。 */
export async function typePending(w: TypeWeights, limit: number, deps: TypeDeps): Promise<{ typed: number; by_type: Record<string, number> }> {
  const items = await deps.pending(limit)
  const rows: TypeRow[] = items.map((it) => ({ item_id: it.item_id, ...classify(it.vec, w), version: w.version }))
  const by_type: Record<string, number> = {}
  for (const r of rows) by_type[r.type] = (by_type[r.type] ?? 0) + 1
  if (rows.length > 0) await deps.save(rows)
  return { typed: rows.length, by_type }
}
