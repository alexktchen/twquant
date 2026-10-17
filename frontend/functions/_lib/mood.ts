// 市場氛圍（D-181）：盤中看類股強弱（證交所類股指數，跟個股即時報價同一個介面），
// 收盤後看資金往哪跑（族群成交比重，serving.group_flow_daily）。只供看盤，不進選股、不進模型。
// 純函數（讀資料庫的 readClose 除外），供 node 單元測試。
import type { sql } from './db.ts'
import type { Quote } from './quote.ts'
import { readRows } from './scorecard.ts'
import type { Session, TxQuote, TxSpot } from './taifex.ts'
import { US_INDICES, type UsIndex } from './yahoo.ts'

/** 證交所即時報價的指數頻道（tse_t00.tw 等）；QuoteHub 把代號當股票問，上市、上櫃各一個頻道，問錯的只回空殼。 */
export const TAIEX_CODE = 't00'
export const OTC_CODE = 'o00'

export type Side = 'def' | 'off'
/**
 * 證交所的類股指數（2026-10-15 實際問到的名稱，名稱去掉「類指數」）。
 * 不列：t13 電子工業、t07 化學生技醫療——它們是別的類股加起來的，放進排行會重複。
 * 攻守（D-181，AI 提的，使用者可改）：防禦＝金融保險、食品、油電燃氣；攻擊＝半導體、電子零組件、電腦及週邊設備。
 * 電信在證交所歸「通信網路」，跟網通設備混在一起，盤中不算防禦（收盤後的族群版有「電信」）。
 */
export const SECTORS: { code: string; name: string; side?: Side }[] = [
  { code: 't01', name: '水泥' },
  { code: 't02', name: '食品', side: 'def' },
  { code: 't03', name: '塑膠' },
  { code: 't04', name: '紡織纖維' },
  { code: 't05', name: '電機機械' },
  { code: 't06', name: '電器電纜' },
  { code: 't08', name: '玻璃陶瓷' },
  { code: 't09', name: '造紙' },
  { code: 't10', name: '鋼鐵' },
  { code: 't11', name: '橡膠' },
  { code: 't12', name: '汽車' },
  { code: 't14', name: '建材營造' },
  { code: 't15', name: '航運' },
  { code: 't16', name: '觀光餐旅' },
  { code: 't17', name: '金融保險', side: 'def' },
  { code: 't18', name: '貿易百貨' },
  { code: 't20', name: '其他' },
  { code: 't21', name: '化學' },
  { code: 't22', name: '生技醫療' },
  { code: 't23', name: '油電燃氣', side: 'def' },
  { code: 't24', name: '半導體', side: 'off' },
  { code: 't25', name: '電腦週邊', side: 'off' },
  { code: 't26', name: '光電' },
  { code: 't27', name: '通信網路' },
  { code: 't28', name: '電子零組件', side: 'off' },
  { code: 't29', name: '電子通路' },
  { code: 't30', name: '資訊服務' },
  { code: 't31', name: '其他電子' },
]
/** 一次問全部（加權、櫃買、28 個類股＝30 檔，60 個頻道，在一次請求的上限內）。 */
export const INDEX_CODES = [TAIEX_CODE, OTC_CODE, ...SECTORS.map((s) => s.code)]

/** 收盤後的防禦族群（族群表的名字，D-177）：金融、電信、食品、油電燃氣。 */
export const DEF_GROUPS = ['金融', '電信', '食品', '油電燃氣']

/** 大盤漲跌超過這個（小數）算「大漲」「大跌」。 */
export const BIG_MOVE = 0.01
/** 盤中：防禦類股平均漲跌減攻擊類股平均，超過這個（小數，1 個百分點）算偏防守／偏攻擊。 */
export const TILT = 0.01
/** 收盤後：防禦族群合計成交比重比前 20 日平均多（少）超過這個（小數，1 個百分點）算資金轉向防禦（攻擊）。 */
export const SHIFT = 0.01
/** 最強／最弱、資金流入／流出各列幾個。 */
export const TOP_N = 3

export type SectorMove = { code: string; name: string; chg: number; rel: number }
export type LiveMood = {
  date: string
  time: string | null
  taiex: number
  otc: number | null
  /** 防禦平均漲跌 − 攻擊平均漲跌；有一邊缺資料 → null */
  tilt: number | null
  label: string
  /** 相對大盤最強／最弱（rel＝類股漲跌 − 大盤漲跌） */
  strong: SectorMove[]
  weak: SectorMove[]
}

const dayChg = (q: Quote | undefined): number | null => (q && q.last !== null && q.prev !== null ? q.last / q.prev - 1 : null)

function marketWord(r: number | null): string | null {
  if (r === null) return null
  if (r <= -BIG_MOVE) return '大跌'
  if (r >= BIG_MOVE) return '大漲'
  return null
}
const join = (a: string | null, b: string) => (a ? `${a}・${b}` : b)

/** 盤中的標籤：大盤大漲／大跌（有的話）＋偏防守／偏攻擊／攻守均衡（tilt 算不出來 → 只寫大盤）。 */
export function liveLabel(taiex: number, tilt: number | null): string {
  const m = marketWord(taiex)
  if (tilt === null) return m ?? '類股強弱'
  return join(m, tilt >= TILT ? '偏防守' : tilt <= -TILT ? '偏攻擊' : '攻守均衡')
}

/** 收盤後的標籤：大盤大漲／大跌（有的話）＋資金轉向防禦／轉向攻擊／分布如常。 */
export function closeLabel(taiex: number | null, shift: number | null): string | null {
  const m = marketWord(taiex)
  if (shift === null) return m
  return join(m, shift >= SHIFT ? '資金轉向防禦' : shift <= -SHIFT ? '資金轉向攻擊' : '資金分布如常')
}

function sideAvg(moves: SectorMove[], side: Side): number | null {
  const want = SECTORS.filter((s) => s.side === side).map((s) => s.code)
  const got = moves.filter((m) => want.includes(m.code))
  // 少一檔就不算（平均會偏）
  if (got.length !== want.length) return null
  return got.reduce((a, m) => a + m.chg, 0) / got.length
}

/** 證交所的指數報價 → 盤中氛圍。加權還沒成交（開盤前）→ null；跟加權不是同一天的類股不算。 */
export function liveMood(quotes: Quote[]): LiveMood | null {
  const by = new Map(quotes.map((q) => [q.sid, q]))
  const t = by.get(TAIEX_CODE)
  const taiex = dayChg(t)
  if (!t || taiex === null) return null
  const sameDay = (q: Quote | undefined) => (q && q.date === t.date ? q : undefined)
  const moves: SectorMove[] = []
  for (const s of SECTORS) {
    const c = dayChg(sameDay(by.get(s.code)))
    if (c !== null) moves.push({ code: s.code, name: s.name, chg: c, rel: c - taiex })
  }
  const def = sideAvg(moves, 'def')
  const off = sideAvg(moves, 'off')
  const tilt = def !== null && off !== null ? def - off : null
  const sorted = [...moves].sort((a, b) => b.rel - a.rel || a.code.localeCompare(b.code))
  const n = Math.min(TOP_N, Math.floor(sorted.length / 2))
  return {
    date: t.date,
    time: t.time,
    taiex,
    otc: dayChg(sameDay(by.get(OTC_CODE))),
    tilt,
    label: liveLabel(taiex, tilt),
    strong: sorted.slice(0, n),
    weak: sorted.slice(sorted.length - n).reverse(),
  }
}

/** group_flow_daily 的一列（只取這裡用到的）。share／share_avg 是小數（0.012＝1.2%）。 */
export type FlowRow = { grp: string; share: number; share_avg: number | null; ret: number | null; up: number; down: number }
export type FlowMove = { grp: string; diff: number; share: number; ret: number | null }
export type CloseMood = {
  date: string
  taiex: number | null
  otc: number | null
  up: number
  down: number
  /** 防禦族群合計成交比重、前 20 日平均、差（小數）；有一個族群沒有平均 → avg、shift 為 null */
  def_share: number
  def_avg: number | null
  shift: number | null
  label: string | null
  /** 成交比重比前 20 日平均多最多／少最多的族群（diff＝share − share_avg） */
  inflow: FlowMove[]
  outflow: FlowMove[]
}

/** 收盤後的氛圍：族群資金那天的每一列＋加權、櫃買當天漲跌（算不出來給 null）。 */
export function closeMood(date: string, rows: FlowRow[], idx: { taiex: number | null; otc: number | null }): CloseMood {
  const defs = rows.filter((r) => DEF_GROUPS.includes(r.grp))
  const defShare = defs.reduce((a, r) => a + r.share, 0)
  const defAvg = defs.length > 0 && defs.every((r) => r.share_avg !== null) ? defs.reduce((a, r) => a + (r.share_avg ?? 0), 0) : null
  const shift = defAvg === null ? null : defShare - defAvg
  const moves = rows.flatMap((r) => (r.share_avg === null ? [] : [{ grp: r.grp, diff: r.share - r.share_avg, share: r.share, ret: r.ret }]))
  const byDiff = [...moves].sort((a, b) => b.diff - a.diff || a.grp.localeCompare(b.grp))
  return {
    date,
    taiex: idx.taiex,
    otc: idx.otc,
    up: rows.reduce((a, r) => a + r.up, 0),
    down: rows.reduce((a, r) => a + r.down, 0),
    def_share: defShare,
    def_avg: defAvg,
    shift,
    label: closeLabel(idx.taiex, shift),
    inflow: byDiff.filter((m) => m.diff > 0).slice(0, TOP_N),
    outflow: byDiff
      .filter((m) => m.diff < 0)
      .reverse()
      .slice(0, TOP_N),
  }
}

/** index_daily 的列 → 那個指數在 `date` 的漲跌；`date` 沒有收盤或沒有前一天 → null。 */
export function dayReturn(rows: { index_id: string; date: string; close: number }[], id: string, date: string): number | null {
  const xs = rows.filter((r) => r.index_id === id && r.date <= date).sort((a, b) => b.date.localeCompare(a.date))
  if (xs[0]?.date !== date || !xs[1] || !(xs[0].close > 0) || !(xs[1].close > 0)) return null
  return xs[0].close / xs[1].close - 1
}

type Db = ReturnType<typeof sql>

/** 最近一天有族群資金的收盤氛圍；表還沒建或還沒算 → null。 */
export async function readClose(db: Db): Promise<CloseMood | null> {
  const rows = await readRows(
    () => db<(FlowRow & { date: string })[]>`
      SELECT date::text AS date, grp, share, share_avg, ret, up::int AS up, down::int AS down
      FROM serving.group_flow_daily WHERE date = (SELECT max(date) FROM serving.group_flow_daily)`,
  )
  if (rows.length === 0) return null
  const date = rows[0].date
  // 往前 15 天內一定有前一個交易日（最長的連假也不到）
  const idx = await readRows(
    () => db<{ index_id: string; date: string; close: number }[]>`
      SELECT index_id, date::text AS date, close FROM serving.index_daily
      WHERE index_id IN ('TAIEX', 'TPEX') AND date <= ${date}::date AND date > ${date}::date - 15`,
  )
  return closeMood(date, rows, { taiex: dayReturn(idx, 'TAIEX', date), otc: dayReturn(idx, 'TPEX', date) })
}

// ── 期貨與美股（D-186）────────────────────────────────────────────────────────
// 台指期日盤／夜盤（期交所，_lib/taifex.ts）與美股四大指數（Yahoo，_lib/yahoo.ts）合成「多空」一句。只供看盤，不進選股、不進模型。

/** 台指期最新一節或美股平均漲跌的絕對值到這個（小數，0.5%）才算有方向；兩邊都不到就「多空平淡」。 */
export const LEAN = 0.005

export type GlobalMood = {
  /** 加權最新點數：日盤那份的現貨（盤中即時），開盤前沒有就用夜盤那份的參考價（＝加權收盤，prev 為 null） */
  spot: TxSpot | null
  day: TxQuote | null
  night: TxQuote | null
  /** 最近成交過的那一節（夜盤接在同一天的日盤後面）；兩節都還沒成交 → null */
  latest: Session | null
  /** 最新一節最後成交價 − 加權最新點數（夜盤對加權收盤），點數與對加權的比例；缺一邊 → null */
  gap: { pts: number; pct: number } | null
  /** 四大指數，照 US_INDICES 的順序；拿不到的不列 */
  us: UsIndex[]
  /** 四大指數平均漲跌；少一個就不算（平均會偏）→ null */
  us_avg: number | null
  label: string | null
  /** 日盤、夜盤都拿不到時的原因（GlobalHub 填；網站照樣畫台指期那一行並寫出來） */
  tx_error?: string | null
}

/** 最近成交過的那一節：依期交所的交易日排序，同一天夜盤在日盤後面；還沒成交的不算。 */
export function latestSession(day: TxQuote | null, night: TxQuote | null): Session | null {
  const traded = [day, night].filter((q): q is TxQuote => q !== null && q.last !== null)
  if (traded.length === 0) return null
  traded.sort((a, b) => a.date.localeCompare(b.date) || Number(a.session === 'night') - Number(b.session === 'night'))
  return traded[traded.length - 1].session
}

/**
 * 多空一句：最新一節台指期 ±1%「日盤／夜盤大漲／大跌」、美股平均 ±1%「美股大漲／大跌」（有的話，接在前面），
 * 再看方向——兩邊各自 ±LEAN 算一票，多的多就「偏多」、空的多就「偏空」、一多一空「多空分歧」、都沒方向「多空平淡」。
 * 兩邊都沒有資料 → null。
 */
export function globalLabel(fut: { session: Session; chg: number } | null, usAvg: number | null): string | null {
  if (fut === null && usAvg === null) return null
  const words: string[] = []
  if (fut !== null) {
    const name = fut.session === 'night' ? '夜盤' : '日盤'
    if (fut.chg <= -BIG_MOVE) words.push(`${name}大跌`)
    else if (fut.chg >= BIG_MOVE) words.push(`${name}大漲`)
  }
  if (usAvg !== null) {
    if (usAvg <= -BIG_MOVE) words.push('美股大跌')
    else if (usAvg >= BIG_MOVE) words.push('美股大漲')
  }
  const vote = (x: number | null): number => (x === null ? 0 : x >= LEAN ? 1 : x <= -LEAN ? -1 : 0)
  const a = vote(fut?.chg ?? null)
  const b = vote(usAvg)
  const score = a + b
  words.push(score > 0 ? '偏多' : score < 0 ? '偏空' : a !== 0 && b !== 0 ? '多空分歧' : '多空平淡')
  return words.join('・')
}

/** 期交所日盤、夜盤兩份（拿不到的給 null）＋美股四大指數（拿到幾個給幾個）→ 期貨與美股的氛圍。 */
export function globalMood(
  day: { spot: TxSpot | null; near: TxQuote | null } | null,
  night: { spot: TxSpot | null; near: TxQuote | null } | null,
  us: UsIndex[],
): GlobalMood {
  const dq = day?.near ?? null
  const nq = night?.near ?? null
  const spot: TxSpot | null =
    day?.spot && day.spot.last !== null ? day.spot : night?.spot ? { ...night.spot, last: night.spot.prev, prev: null } : (day?.spot ?? null)
  const latest = latestSession(dq, nq)
  const q = latest === 'night' ? nq : latest === 'day' ? dq : null
  const gap = q && q.last !== null && spot && spot.last !== null ? { pts: q.last - spot.last, pct: q.last / spot.last - 1 } : null
  const ordered = US_INDICES.flatMap((x) => us.filter((u) => u.symbol === x.symbol).slice(0, 1))
  const usAvg = ordered.length === US_INDICES.length ? ordered.reduce((a, u) => a + u.chg, 0) / ordered.length : null
  return {
    spot,
    day: dq,
    night: nq,
    latest,
    gap,
    us: ordered,
    us_avg: usAvg,
    label: globalLabel(q && q.chg !== null && latest ? { session: latest, chg: q.chg } : null, usAvg),
  }
}
