// 追蹤股（每個登入帳號自己的清單）：存在 Supabase 的 web.watchlist，跟每日 pipeline 的 serving 分開，
// 修剪、重建、同步都不會碰到它。純函數放這裡供 node 單元測試。
import { change } from './picks.ts'
import { modelScores, type ModelDay, type ModelScore, type ScoreRow } from './scores.ts'

/** 每個帳號最多追蹤幾檔（清單一次查完分數與報價，太多會拖慢）。 */
export const WATCH_MAX = 100

/** 第一次有人加追蹤股時才建表（冪等）。email 一律存小寫（identity.ts 已轉）。 */
export const WATCHLIST_DDL = [
  'CREATE SCHEMA IF NOT EXISTS web',
  `CREATE TABLE IF NOT EXISTS web.watchlist (
    email text NOT NULL,
    stock_id text NOT NULL,
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (email, stock_id)
  )`,
]

/** 寫入只接受本站網頁送來的請求：跨站表單或腳本帶著登入 cookie 送 PUT／DELETE 一律擋（瀏覽器對這兩種方法一定帶 Origin）。 */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin')
  return origin === null || origin === new URL(request.url).origin
}

export type WatchItem = {
  sid: string
  name: string | null
  added_at: string
  /** 最新一根 K 棒的日期與收盤；漲跌對前一根收盤，缺任一價 → null */
  date: string | null
  close: number | null
  chg: number | null
  models: ModelScore[]
}

/** 清單每一列：報價（每檔最多兩根，新到舊）＋三個模型最新一天的分數（不帶歷史，清單用不到）。 */
export function watchItems(
  list: { sid: string; name: string | null; added_at: string }[],
  px: { sid: string; date: string; close: number | null }[],
  scores: { rows: ScoreRow[]; days: ModelDay[] },
): WatchItem[] {
  return list.map((w) => {
    const bars = px.filter((p) => p.sid === w.sid).sort((a, b) => b.date.localeCompare(a.date))
    const close = bars[0]?.close ?? null
    return {
      ...w,
      date: bars[0]?.date ?? null,
      close,
      chg: change(close, bars[1]?.close ?? null),
      models: modelScores(w.sid, scores.rows, scores.days).map((m) => ({ ...m, history: [] })),
    }
  })
}

/** 分數日：三個模型最近一次打分日裡最新的那天（雲端完全沒分數 → null）。 */
export function latestDate(days: ModelDay[]): string | null {
  return days.reduce<string | null>((a, d) => (a === null || d.date > a ? d.date : a), null)
}
