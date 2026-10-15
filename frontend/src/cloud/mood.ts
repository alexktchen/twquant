// 市場氛圍卡（D-181）：盤中顯示類股強弱，收盤後的族群資金算好（約 17:40）後換成資金版。純函式，供 node 單元測試。
// 後端資料形狀見 functions/_lib/mood.ts。只供看盤，不進選股。

export type SectorMove = { code: string; name: string; chg: number; rel: number }
export type LiveMood = {
  date: string
  time: string | null
  taiex: number
  otc: number | null
  tilt: number | null
  label: string
  strong: SectorMove[]
  weak: SectorMove[]
}
export type FlowMove = { grp: string; diff: number; share: number; ret: number | null }
export type CloseMood = {
  date: string
  taiex: number | null
  otc: number | null
  up: number
  down: number
  def_share: number
  def_avg: number | null
  shift: number | null
  label: string | null
  inflow: FlowMove[]
  outflow: FlowMove[]
}

/** 卡片要畫的東西：沒有任何資料 → moodView 回 null（不顯示卡片）。 */
export type MoodView = {
  /** 「盤中 10:32」或「10/14 收盤」 */
  when: string
  label: string | null
  tone: 'def' | 'off' | 'flat'
  taiex: number | null
  otc: number | null
  /** 類股強弱（證交所類股指數）；words＝兩行的標題（依大盤漲跌換字） */
  sectors: { words: [string, string]; strong: SectorMove[]; weak: SectorMove[] } | null
  /** 收盤後的資金（族群成交比重 vs 前 20 日平均） */
  flow: { inflow: FlowMove[]; outflow: FlowMove[]; defShare: number; defAvg: number | null; up: number; down: number } | null
  /** 盤中還沒有資金版時的說明 */
  flowNote: string | null
}

const CLOSE_TIME = '13:30:00'

const md = (date: string) => `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`

/** 大盤跌的日子說「抗跌／領跌」，漲的日子說「領漲／落後」。 */
export function sectorWords(taiex: number | null): [string, string] {
  if (taiex !== null && taiex < 0) return ['抗跌', '領跌']
  if (taiex !== null && taiex > 0) return ['領漲', '落後']
  return ['強勢', '弱勢']
}

/** 標籤的顏色：偏防守／轉向防禦＝def、偏攻擊／轉向攻擊＝off，其他 flat。 */
export function moodTone(label: string | null): 'def' | 'off' | 'flat' {
  if (!label) return 'flat'
  if (label.includes('防守') || label.includes('防禦')) return 'def'
  if (label.includes('攻擊')) return 'off'
  return 'flat'
}

/**
 * 盤中與收盤兩份資料合成一張卡片：盤中那份比收盤那份新（今天的資金傍晚才算好）→ 顯示類股強弱；
 * 同一天 → 資金版為主，類股強弱留著；只有收盤那份（開盤前、證交所擋下）→ 資金版。
 */
export function moodView(live: LiveMood | null, close: CloseMood | null, now: { date: string; time: string }): MoodView | null {
  const liveNewer = live !== null && (close === null || live.date > close.date)
  if (live && liveNewer) {
    const intraday = live.date === now.date && now.time < CLOSE_TIME
    return {
      when: intraday ? `盤中 ${(live.time ?? '').slice(0, 5)}`.trim() : `${md(live.date)} 收盤`,
      label: live.label,
      tone: moodTone(live.label),
      taiex: live.taiex,
      otc: live.otc,
      sectors: { words: sectorWords(live.taiex), strong: live.strong, weak: live.weak },
      flow: null,
      flowNote: '資金流向（族群成交比重）每個交易日傍晚約 5:40 更新',
    }
  }
  if (!close) return null
  const sameDay = live !== null && live.date === close.date
  const taiex = close.taiex ?? (sameDay ? live.taiex : null)
  return {
    when: `${md(close.date)} 收盤`,
    label: close.label,
    tone: moodTone(close.label),
    taiex,
    otc: close.otc ?? (sameDay ? live.otc : null),
    sectors: sameDay ? { words: sectorWords(taiex), strong: live.strong, weak: live.weak } : null,
    flow: { inflow: close.inflow, outflow: close.outflow, defShare: close.def_share, defAvg: close.def_avg, up: close.up, down: close.down },
    flowNote: null,
  }
}

/** 百分點（小數 0.019 → "+1.9"）。 */
export function pp(x: number): string {
  const v = x * 100
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}`
}

/** 盤中多久問一次類股指數（證交所約 5 秒一筆；氛圍不用那麼密，也少佔證交所的額度）。 */
export const MOOD_POLL_MS = 30_000
/** 收盤版多久重讀一次（傍晚排程算好後，開著的頁面自己換成資金版）。 */
export const CLOSE_POLL_MS = 10 * 60_000
