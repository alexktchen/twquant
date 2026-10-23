// 星雲圖的時間軸（T-443；使用者 2026-10-23 看「台中恐怖島」事件地圖截圖：「我的新聞星雲圖有辦法也有時間軸嗎」→「我先做7天版」）的純函式。
// 快照裡每顆光點都帶發布時間，所以全在瀏覽器算、不打 API：近 7 天切成每 3 小時一格（台北時間對齊），
// 每格數台灣／國際各幾則畫成長條；拉桿或播放把「現在」移到某一格，之後的光點不畫、亮點只算到那時候被提到幾則；
// 「累積」＝到那時候為止的都亮，「當時」＝只有那一格亮、之前的淡掉。畫面在 NewsGalaxy.tsx。
import type { GalaxyItem } from './news-galaxy'

/** 一格幾小時（7 天 ＝ 56 格；手機 330px 寬每格約 6px） */
export const TL_BIN_HOURS = 3
/** 播放時每格停多久（ms） */
export const TL_STEP_MS = 650
/** 那一格底下列幾則 */
export const TL_LIST = 5
/** 「當時」模式裡已經過去的光點淡到幾成 */
export const TL_PAST = 0.18
/** 台北 ＝ UTC+8，沒有夏令時間 */
const TPE_MS = 8 * 3600_000

export type TlMode = 'cum' | 'at'
export type TlBin = { at: number; tw: number; intl: number; idx: number[] }
export type Timeline = { start: number; end: number; binMs: number; bins: TlBin[]; of: number[] }

/**
 * 切格：最後一格的終點＝快照時間往上取整到格界（台北時間），往前 days 天；每則照發布時間落在哪一格（超出範圍的夾到頭尾）。
 * of[i]＝第 i 則在第幾格。
 */
export function buildTimeline(items: readonly GalaxyItem[], built: string, days: number, binHours = TL_BIN_HOURS): Timeline {
  const binMs = binHours * 3600_000
  const end = Math.ceil((Date.parse(built) + TPE_MS) / binMs) * binMs - TPE_MS
  const n = Math.max(1, Math.round((days * 24) / binHours))
  const start = end - n * binMs
  const bins: TlBin[] = Array.from({ length: n }, (_, i) => ({ at: start + i * binMs, tw: 0, intl: 0, idx: [] }))
  const of = items.map((it, i) => {
    const k = Math.min(n - 1, Math.max(0, Math.floor((Date.parse(it.t) - start) / binMs)))
    bins[k][it.kind === 'intl' ? 'intl' : 'tw'] += 1
    bins[k].idx.push(i)
    return k
  })
  return { start, end, binMs, bins, of }
}

/** 光點的亮度倍率：還沒發生＝0；「當時」模式裡已經過去＝TL_PAST；其餘 1 */
export const tlAlpha = (bin: number, cursor: number, mode: TlMode): number => (bin > cursor ? 0 : mode === 'at' && bin < cursor ? TL_PAST : 1)

/** 到 cursor 那格為止，每個亮點被幾則提到（亮點的大小照這個；0 ＝ 還沒出現，不畫） */
export function tlHubCounts(items: readonly GalaxyItem[], of: readonly number[], cursor: number): Map<string, number> {
  const c = new Map<string, number>()
  items.forEach((it, i) => {
    if (of[i] > cursor) return
    for (const m of new Set(it.m)) c.set(m, (c.get(m) ?? 0) + 1)
  })
  return c
}

const tpe = (ms: number): Date => new Date(ms + TPE_MS)
const md = (d: Date): string => `${d.getUTCMonth() + 1}/${d.getUTCDate()}`

/** 一格的名字：「10/22 09–12時」 */
export function tlBinLabel(at: number, binHours = TL_BIN_HOURS): string {
  const d = tpe(at)
  const h0 = d.getUTCHours(), h1 = (h0 + binHours) % 24 || 24
  return `${md(d)} ${String(h0).padStart(2, '0')}–${String(h1).padStart(2, '0')}時`
}

/** 軸上每天的刻度：每天第一格的位置與「10/16」 */
export function tlDayTicks(tl: Timeline): { i: number; label: string }[] {
  const out: { i: number; label: string }[] = []
  let seen = ''
  tl.bins.forEach((b, i) => {
    const d = tpe(b.at), day = d.toISOString().slice(0, 10)
    if (day !== seen && d.getUTCHours() === 0) {
      seen = day
      out.push({ i, label: md(d) })
    }
  })
  return out
}

/** 最高的那格有幾則（長條的比例尺；全空時 1，免得除以 0） */
export const tlBinMax = (bins: readonly TlBin[]): number => Math.max(1, ...bins.map((b) => b.tw + b.intl))
