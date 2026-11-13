// 程式交易（autotrade A-005）：交易程式單向寫出來的狀態，網站只讀。這裡是純函數與型別，畫面在 AutotradeView.tsx。

export type AutoMode = 'sim' | 'live'
export type AutoState = 'ok' | 'halted' | 'suspended' | 'link_down'
export type AutoFill = { ts: string; side: 'BUY' | 'SELL'; qty: number; price: number; pnl_twd: number }
export type AutoEvent = { ts: string; kind: string; text: string }

/** autotrade/src/autotrade/export.py 的 snapshot（schema 1）。金額是元。 */
export type AutoStatus = {
  schema: number
  day: string
  mode: AutoMode
  source: 'replay' | 'live'
  status: 'live' | 'closed'
  updated_at: string
  symbol: string
  strategy: string
  state: AutoState
  halt: string | null
  position: number
  avg_price: number
  last_price: number
  last_ts: string
  open_pnl_twd: number
  realized_twd: number
  day_pnl_twd: number
  costs_twd: number
  entries: number
  limits: {
    max_position: number
    max_entries_per_day: number
    daily_loss_limit_twd: number | null
    position_stop_loss_twd: number | null
  }
  contract: { point_value: number; commission_twd: number; tax_rate: number }
  /** 模擬假設；真單是 null */
  sim: { latency_s: number; slippage_points: number } | null
  fills: AutoFill[]
  corrected: boolean
  events: AutoEvent[]
}

export type AutoDayRow = { day: string; status: string; pnl: number | null; fills: number | null }
export type AutoEnvelope = { days: AutoDayRow[]; current: AutoStatus | null }

/** 這一版網站認得的狀態版本（autotrade export.py 的 SCHEMA）。 */
export const SCHEMA = 1

export const MODE_NAME: Record<AutoMode, string> = { sim: '模擬', live: '真單' }
export const SOURCE_NAME: Record<AutoStatus['source'], string> = { replay: '歷史重播', live: '即時行情' }
export const STRATEGY_LABEL: Record<string, string> = { momentum: '動能順勢', none: '不下單（只檢查接線）' }

/** 停手原因（autotrade risk.py 的代碼）。 */
export const HALT_TEXT: Record<string, string> = {
  daily_loss: '當日虧損到上限，今天不再進場',
  kill: '緊急停止，要人工解除',
  mismatch: '部位跟券商對不上，要人工解除',
  order_rate: '送單太頻繁，要人工解除',
  broker_reject: '連續被券商拒絕，要人工解除',
}

/** 停手原因的短名：今日大事的文字裡，括號內的代碼換成這個（交易程式的日誌是給程式看的，所以帶代碼）。 */
export const HALT_SHORT: Record<string, string> = {
  daily_loss: '當日虧損到上限',
  kill: '緊急停止',
  mismatch: '跟券商對不上',
  order_rate: '送單太頻繁',
  broker_reject: '連續被券商拒絕',
}

/** 今日大事的文字：括號裡的停手代碼換成白話；不認得的代碼照原樣留著，不吞掉。 */
export const eventText = (text: string): string =>
  text.replace(/（([a-z_]+)）/g, (all, code: string) => (HALT_SHORT[code] ? `（${HALT_SHORT[code]}）` : all))

/** 今日大事的分類名稱。 */
export const EVENT_LABEL: Record<string, string> = {
  halt: '停手',
  release: '解除',
  stop: '停損',
  correction: '部位修正',
  link: '連線',
  broker_reject: '券商拒絕',
  foreign_order: '不明委託',
  reject: '風控擋下',
}

/** 要人處理的大事（紅色）；其餘是一般提醒。 */
export const EVENT_SERIOUS: ReadonlySet<string> = new Set(['halt', 'stop', 'correction', 'broker_reject', 'foreign_order'])

/** 盤中狀態超過這麼多秒沒更新，就當成交易程式可能沒在跑。 */
export const STALE_AFTER_S = 60

export type Lamp = { tone: 'ok' | 'bad' | 'warn' | 'off'; label: string; detail: string }

/** 狀態燈。嚴重的優先：停手／暫停送單 → 連線中斷 → 盤中太久沒更新 → 已收盤 → 運作中。 */
export function lamp(s: AutoStatus, now: Date): Lamp {
  const why = s.halt === null ? '' : (HALT_TEXT[s.halt] ?? s.halt)
  if (s.state === 'halted') return { tone: 'bad', label: '停手', detail: why }
  if (s.state === 'suspended') return { tone: 'bad', label: '暫停送單', detail: why }
  if (s.state === 'link_down') return { tone: 'warn', label: '券商連線中斷', detail: '斷線期間不送單也不刪單' }
  if (s.status === 'closed') return { tone: 'off', label: '已收盤', detail: '' }
  const age = Math.floor((now.getTime() - Date.parse(s.updated_at)) / 1000)
  if (age > STALE_AFTER_S) return { tone: 'warn', label: '可能沒在跑', detail: `${ageText(age)}沒有更新` }
  return { tone: 'ok', label: '運作中', detail: '' }
}

const ageText = (s: number): string => (s < 3600 ? `${Math.floor(s / 60)} 分鐘` : `${Math.floor(s / 3600)} 小時`)

/** 離每日虧損上限還有多少：used＝用掉的比例（0～1），left＝還能賠幾元。沒設上限是 null。 */
export function lossRoom(dayPnl: number, limit: number | null): { used: number; left: number } | null {
  if (limit === null || limit <= 0) return null
  const lost = Math.max(0, -dayPnl)
  return { used: Math.min(1, lost / limit), left: Math.max(0, limit - lost) }
}

/** 盤中每 15 秒看一次；畫面上的狀態不是「盤中」（已收盤、還沒有紀錄）時，每 SLOW_EVERY 次才抓一次，少打資料庫。 */
export const SLOW_EVERY = 4
export const refetchDue = (tick: number, live: boolean): boolean => live || tick % SLOW_EVERY === 0

/** 部位：空手／多 n 口／空 n 口。 */
export const positionText = (p: number): string => (p === 0 ? '空手' : `${p > 0 ? '多' : '空'} ${Math.abs(p)} 口`)

export type DayStats = { n: number; total: number; wins: number; losses: number; best: number | null; worst: number | null }

/** 每天的成績：有損益數字的日子才算；賺＝當日損益大於 0、賠＝小於 0（剛好 0 兩邊都不算）。 */
export function dayStats(days: readonly AutoDayRow[]): DayStats {
  const pnl = days.map((d) => d.pnl).filter((v): v is number => v !== null)
  return {
    n: pnl.length,
    total: pnl.reduce((a, b) => a + b, 0),
    wins: pnl.filter((v) => v > 0).length,
    losses: pnl.filter((v) => v < 0).length,
    best: pnl.length ? Math.max(...pnl) : null,
    worst: pnl.length ? Math.min(...pnl) : null,
  }
}

export type CumPoint = { day: string; pnl: number; cum: number }

/** 逐日累計（舊到新）：畫累計線用。 */
export function cumulative(days: readonly AutoDayRow[]): CumPoint[] {
  let cum = 0
  return days
    .filter((d): d is AutoDayRow & { pnl: number } => d.pnl !== null)
    .sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0))
    .map((d) => {
      cum += d.pnl
      return { day: d.day, pnl: d.pnl, cum }
    })
}

/** 累計線在寬 w、高 h 的圖裡的座標，與 0 那條線的高度。少於兩天畫不出線，回 null。 */
export function cumPath(points: readonly { cum: number }[], w: number, h: number): { line: string; zeroY: number } | null {
  if (points.length < 2) return null
  const lo = Math.min(0, ...points.map((p) => p.cum))
  const hi = Math.max(0, ...points.map((p) => p.cum))
  const span = hi - lo || 1
  const y = (v: number) => h - ((v - lo) / span) * h
  const x = (i: number) => (i / (points.length - 1)) * w
  return { line: points.map((p, i) => `${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(' '), zeroY: y(0) }
}

/** 損益是怎麼算的，一段話：模擬假設（真單沒有）＋已經扣掉的成本。 */
export function assumptionText(s: Pick<AutoStatus, 'sim' | 'contract'>): string {
  const tax = Math.round(s.contract.tax_rate * 1e7) / 100
  const cost = `損益已扣成本：手續費每口每邊 ${s.contract.commission_twd} 元、期交稅十萬分之 ${tax}（每點 ${s.contract.point_value} 元）。`
  if (s.sim === null) return cost
  return `模擬假設：下單後 ${s.sim.latency_s} 秒才成交、每邊滑價 ${s.sim.slippage_points} 點。${cost}模擬結果不代表真單會一樣。`
}

/** ISO 時間（帶 +08:00）→ HH:MM:SS。 */
export const clock = (iso: string): string => iso.slice(11, 19)
