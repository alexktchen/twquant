// 名單上的破底標記與大盤大跌提醒（D-183）的顯示。純函式，供 node 單元測試；後端資料形狀見 functions/_lib/weak.ts。
// 只供看盤：名單照模型排名產生，不因這些標記或大盤漲跌刪減。
import { pct } from './format.ts'

/** 破底狀態：low20＝收盤是近 20 個交易日最低（已還原除權息）；down＝連跌幾天。算不出來是 null。 */
export type Weak = { low20: boolean | null; down: number | null }
/** 加權大跌那天的歷史對照（後端 functions/_lib/weak.ts 的 DropStats）。 */
export type DropStats = {
  ret: number
  since: string
  n: number
  up: number
  median: number | null
  base: number | null
  worst: { date: string; ret: number } | null
}

/** 連跌幾天以上才標（同後端 DOWN_MIN）。 */
export const DOWN_MIN = 3
/** 大跌門檻與往後看的天數（同後端 DROP、DROP_HORIZON），只用在文字。 */
export const DROP_TEXT = '2%'
export const HORIZON = 20

const LOW_TITLE = '收盤是近 20 個交易日最低（已還原除權息）。只是提醒，名單不因此刪減。'

/** 名單一列的破底小標；舊後端沒有這欄、或都沒達到 → []。 */
export function weakTags(w: Weak | null | undefined): { text: string; title: string }[] {
  const out: { text: string; title: string }[] = []
  if (w?.low20) out.push({ text: '20日新低', title: LOW_TITLE })
  if (typeof w?.down === 'number' && w.down >= DOWN_MIN)
    out.push({ text: `連跌${w.down}天`, title: `收盤連續 ${w.down} 天比前一天低（已還原除權息）。只是提醒，名單不因此刪減。` })
  return out
}

/** 這條線有幾檔收在 20 日新低、幾檔連跌 DOWN_MIN 天以上；都沒有 → null。 */
export function weakSummary(picks: { weak?: Weak | null }[]): string | null {
  const low = picks.filter((p) => p.weak?.low20).length
  const down = picks.filter((p) => typeof p.weak?.down === 'number' && p.weak.down >= DOWN_MIN).length
  if (low === 0 && down === 0) return null
  const parts = [low > 0 ? `${low} 檔收在近 20 日新低` : null, down > 0 ? `${down} 檔連跌 ${DOWN_MIN} 天以上` : null]
  return `${picks.length} 檔裡 ${parts.filter(Boolean).join('、')}`
}

/** 名單上方的大跌提醒：title 是收起時的那一行，body 點開才看到；那天沒大跌（或舊後端沒有這欄）→ null。latest＝看的是最新一天（寫「今天」）。 */
export function dropNote(d: DropStats | null | undefined, latest: boolean): { title: string; body: string } | null {
  if (!d) return null
  const head = `加權指數${latest ? '今天' : '這天'} ${pct(d.ret, 1)}`
  const tail = '名單照模型排名產生，不因大盤漲跌刪減；買不買、買多少請自己斟酌。'
  if (d.n === 0) return { title: `${head}，單日跌 ${DROP_TEXT} 以上`, body: `指數資料裡還沒有可以對照的大跌。${tail}` }
  const title = `${head}・過去 ${d.n} 次大跌後 ${HORIZON} 天中位數 ${pct(d.median, 1)}`
  const worst = d.worst ? `；最差一次是 ${d.worst.date} 之後 ${pct(d.worst.ret, 1)}` : ''
  const body =
    `${d.since.slice(0, 4)} 年以來單日跌 ${DROP_TEXT} 以上有 ${d.n} 次（同一波下跌只算第一天），` +
    `之後 ${HORIZON} 個交易日有 ${d.up} 次上漲，中位數 ${pct(d.median, 1)}（平常任一天 ${pct(d.base, 1)}）${worst}。${tail}`
  return { title, body }
}
