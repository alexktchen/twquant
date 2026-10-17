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

// ── 期貨與美股（D-186）────────────────────────────────────────────────────────
// 後端資料形狀見 functions/_lib/taifex.ts、yahoo.ts、mood.ts（GlobalMood）。

export type TxSession = 'day' | 'night'
export type TxQuote = {
  session: TxSession
  symbol: string
  name: string
  /** 期交所的交易日；夜盤標的是它接在後面的那個日盤的日期 */
  date: string
  time: string | null
  last: number | null
  ref: number | null
  settlement: number | null
  volume: number | null
  test: number | null
  chg: number | null
}
export type TxSpot = { date: string; time: string | null; last: number | null; prev: number | null }
export type UsIndex = { symbol: string; name: string; date: string; time: string | null; open: boolean; last: number; prev: number; chg: number }
export type GlobalMood = {
  spot: TxSpot | null
  day: TxQuote | null
  night: TxQuote | null
  latest: TxSession | null
  gap: { pts: number; pct: number } | null
  us: UsIndex[]
  us_avg: number | null
  label: string | null
}

/** 多久問一次期貨與美股（伺服器 60 秒暫存，問得再密也是同一份）。 */
export const GLOBAL_POLL_MS = 60_000

// 台指期交易時段（台北）：日盤 8:45～13:45、夜盤 15:00～隔天 5:00
const TX_DAY_OPEN = '08:45:00'
const TX_DAY_CLOSE = '13:45:00'
const TX_NIGHT_OPEN = '15:00:00'
const TX_NIGHT_CLOSE = '05:00:00'

/** YYYY-MM-DD 的隔天（日曆日，不管假日）。 */
export function nextDay(date: string): string {
  return new Date(Date.parse(`${date}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
}

/** 這一節現在是不是在交易：日盤看同一天 8:45～13:45；夜盤看同一天 15:00 起、或隔天 5:00 前。 */
export function txOpen(q: { session: TxSession; date: string }, now: { date: string; time: string }): boolean {
  if (q.session === 'day') return now.date === q.date && now.time >= TX_DAY_OPEN && now.time < TX_DAY_CLOSE
  return (now.date === q.date && now.time >= TX_NIGHT_OPEN) || (now.date === nextDay(q.date) && now.time < TX_NIGHT_CLOSE)
}

/** 多空標籤的顏色：偏多紅、偏空綠（台股習慣），分歧與平淡不上色。 */
export function globalTone(label: string | null): 'bull' | 'bear' | 'flat' {
  if (!label) return 'flat'
  if (label.includes('偏多')) return 'bull'
  if (label.includes('偏空')) return 'bear'
  return 'flat'
}

export type TxView = { session: TxSession; name: string; last: number | null; chg: number | null; test: number | null; when: string }
export type GlobalView = {
  label: string | null
  tone: 'bull' | 'bear' | 'flat'
  /** 日盤、夜盤（拿到的才列）；when＝「盤中 10:32」「10/16 收」「尚未成交」 */
  tx: TxView[]
  /** 最新一節比加權高（低）幾點 */
  gap: { session: TxSession; pts: number; pct: number } | null
  us: { name: string; chg: number; last: number }[]
  /** 美股的時間：「盤中」或「10/16 收」；沒有指數 → null */
  usWhen: string | null
}

const mdOf = (date: string) => `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`

/** 一節台指期的狀態字：交易中「盤中 HH:MM」、收了「M/D 收」、還沒成交「尚未成交」（有試撮價時由卡片另外標）。 */
export function txWhen(q: TxQuote, now: { date: string; time: string }): string {
  if (q.last === null) return '尚未成交'
  if (txOpen(q, now)) return `盤中 ${(q.time ?? '').slice(0, 5)}`.trim()
  return `${mdOf(q.date)} 收`
}

/** 期貨與美股那一塊要畫的東西；沒有資料 → null。 */
export function globalView(g: GlobalMood | null, now: { date: string; time: string }): GlobalView | null {
  if (!g || (g.day === null && g.night === null && g.us.length === 0)) return null
  const tx: TxView[] = []
  for (const q of [g.day, g.night]) {
    if (q) tx.push({ session: q.session, name: q.session === 'day' ? '日盤' : '夜盤', last: q.last, chg: q.chg, test: q.test, when: txWhen(q, now) })
  }
  const first = g.us[0]
  return {
    label: g.label,
    tone: globalTone(g.label),
    tx,
    gap: g.gap && g.latest ? { session: g.latest, ...g.gap } : null,
    us: g.us.map((u) => ({ name: u.name, chg: u.chg, last: u.last })),
    usWhen: first ? (first.open ? '盤中' : `${mdOf(first.date)} 收`) : null,
  }
}

/** 點數（47242.5 → "47,243"）。 */
export const pts = (x: number): string => Math.round(x).toLocaleString('en-US')
