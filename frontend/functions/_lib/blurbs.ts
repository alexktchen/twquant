// 名單每檔一句話（T-393／D-195）：每日 pipeline 寫進 serving.pick_blurbs。source＝model（AI 寫、數字與說法都核對過）
// 或 rule（AI 缺席或沒過檢查，改用規則句）。只顯示，不改名單。純函數 blurbOf 供 node 單元測試。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

export type Blurb = { text: string; ai: boolean }

type Db = ReturnType<typeof sql>

/** 資料庫一列 → 網站要的；空白句子不顯示（null）。 */
export function blurbOf(r: { text: string | null; source: string | null }): Blurb | null {
  const text = (r.text ?? '').trim()
  return text ? { text, ai: r.source === 'model' } : null
}

/** date 那天每條線每檔的一句話，key＝「線|代號」。表還沒建、那天沒算 → 空。 */
export async function readBlurbs(db: Db, date: string): Promise<Map<string, Blurb>> {
  const rows = await readRows(
    () => db<{ line: string; stock_id: string; text: string | null; source: string | null }[]>`
      SELECT line, stock_id, text, source FROM serving.pick_blurbs WHERE date = ${date}::date`,
  )
  const out = new Map<string, Blurb>()
  for (const r of rows) {
    const b = blurbOf(r)
    if (b) out.set(`${r.line}|${r.stock_id}`, b)
  }
  return out
}
