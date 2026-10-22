// 名單風險日曆（T-387／D-191 方向 3）：每檔上榜股「什麼會讓它變地雷」——處置中／即將處置、注意股、近期除權息、近期法說會、月營收還沒公布。
// 只顯示，不改名單、不進模型。純函數放這裡供 node 單元測試；/api/picks 讀資料庫後呼叫。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** 往後看幾天的除權息、法說會。 */
export const RISK_AHEAD_DAYS = 10
/** 上月營收依法每月 10 日前公布。 */
export const REVENUE_DUE_DAY = 10
/** 截止前幾天內還沒入庫才提醒。 */
export const REVENUE_WARN_DAYS = 5

export type RiskKind = 'disposition' | 'disposition_soon' | 'attention' | 'dividend' | 'conference' | 'revenue'
export type Risk = { kind: RiskKind; date: string | null; text: string; detail: string | null }

export const ALL_RISK_KINDS: readonly RiskKind[] = ['disposition', 'disposition_soon', 'attention', 'dividend', 'conference', 'revenue']
/** 行事曆那幾種（T-430／D-208：追蹤股列只標這些；處置、注意是交易狀態，不是行事曆）。 */
export const CALENDAR_KINDS: readonly RiskKind[] = ['dividend', 'conference', 'revenue']

export type Disp = { stock_id: string; effective_start: string; effective_end: string; reason: string | null; match_minutes: number | null }
export type Att = { stock_id: string; clauses: string | null; info: string | null }
export type Div = { stock_id: string; date: string; kind: string; cash_dividend: number | null; stock_ratio: number | null }
export type Conf = { stock_id: string; date: string; conf_time: string | null; location: string | null }
export type Inputs = { disp: Disp[]; att: Att[]; divs: Div[]; confs: Conf[]; revenuePublished: Set<string> }

const md = (d: string) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`
const addDays = (d: string, n: number) => new Date(Date.parse(`${d}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10)

/** 上月的 YYYY-MM 與這個月的公布截止日（10 日）。 */
export function revenueDue(date: string): { ym: string; due: string } {
  const y = Number(date.slice(0, 4))
  const m = Number(date.slice(5, 7))
  const py = m === 1 ? y - 1 : y
  const pm = m === 1 ? 12 : m - 1
  return { ym: `${py}-${String(pm).padStart(2, '0')}`, due: `${date.slice(0, 7)}-${String(REVENUE_DUE_DAY).padStart(2, '0')}` }
}

const DIV_TEXT: Record<string, string> = { cash: '除息', stock: '除權', both: '除權息' }

/** 一檔在 date 那天看得到的風險，依日期（沒有日期的在前）排。 */
export function risksFor(sid: string, date: string, inp: Inputs): Risk[] {
  const out: Risk[] = []
  const until = addDays(date, RISK_AHEAD_DAYS)
  for (const d of inp.disp.filter((x) => x.stock_id === sid)) {
    const mins = d.match_minutes ? `每 ${d.match_minutes} 分鐘撮合一次` : null
    const detail = [d.reason, mins].filter(Boolean).join('；') || null
    if (d.effective_start <= date && date <= d.effective_end) out.push({ kind: 'disposition', date: d.effective_end, text: `處置中到 ${md(d.effective_end)}`, detail })
    else if (d.effective_start > date && d.effective_start <= until) out.push({ kind: 'disposition_soon', date: d.effective_start, text: `${md(d.effective_start)} 起處置`, detail })
  }
  for (const a of inp.att.filter((x) => x.stock_id === sid)) {
    const first = (a.info ?? '').split('\n').map((s) => s.trim()).filter(Boolean)[0] ?? null
    out.push({ kind: 'attention', date: null, text: '注意股', detail: first })
  }
  for (const v of inp.divs.filter((x) => x.stock_id === sid && x.date > date && x.date <= until)) {
    const parts: string[] = []
    if (v.cash_dividend) parts.push(`現金 ${Number(v.cash_dividend.toFixed(4))} 元`)
    if (v.stock_ratio) parts.push(`配股 ${Number(v.stock_ratio.toFixed(4))}`)
    out.push({ kind: 'dividend', date: v.date, text: `${md(v.date)} ${DIV_TEXT[v.kind] ?? '除權息'}`, detail: parts.join('、') || null })
  }
  for (const c of inp.confs.filter((x) => x.stock_id === sid && x.date >= date && x.date <= until)) {
    out.push({ kind: 'conference', date: c.date, text: `${md(c.date)} 法說會`, detail: [c.conf_time, c.location].filter(Boolean).join(' ') || null })
  }
  const { ym, due } = revenueDue(date)
  const daysLeft = Math.round((Date.parse(`${due}T00:00:00Z`) - Date.parse(`${date}T00:00:00Z`)) / 86_400_000)
  if (daysLeft >= 0 && daysLeft <= REVENUE_WARN_DAYS && !inp.revenuePublished.has(sid)) {
    out.push({ kind: 'revenue', date: due, text: `${ym.slice(5)} 月營收待公布`, detail: `依規定 ${md(due)} 前要公布，到目前還沒看到` })
  }
  return out.sort((a, b) => ((a.date ?? '') < (b.date ?? '') ? -1 : (a.date ?? '') > (b.date ?? '') ? 1 : 0))
}

type Db = ReturnType<typeof sql>

/** date 那天這些股票的風險：名單股看全部種類；追蹤股傳 CALENDAR_KINDS 只看行事曆，用不到的表不查。每張表還沒建 → 那一類當沒有。 */
export async function readRisks(db: Db, date: string, sids: string[], kinds: readonly RiskKind[] = ALL_RISK_KINDS): Promise<Map<string, Risk[]>> {
  const out = new Map<string, Risk[]>()
  if (sids.length === 0) return out
  const list = sids.join(',')
  const until = addDays(date, RISK_AHEAD_DAYS)
  const want = (...k: RiskKind[]) => k.some((x) => kinds.includes(x))
  const disp = !want('disposition', 'disposition_soon') ? [] : await readRows(
    () => db<Disp[]>`
      SELECT stock_id, effective_start::text AS effective_start, effective_end::text AS effective_end, reason, match_minutes
      FROM serving.disposition_stocks
      WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date <= ${date}::date AND effective_end >= ${date}::date`,
  )
  const att = !want('attention') ? [] : await readRows(
    () => db<Att[]>`
      SELECT stock_id, clauses, info FROM serving.attention_stocks
      WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date = ${date}::date`,
  )
  const divs = !want('dividend') ? [] : await readRows(
    () => db<Div[]>`
      SELECT stock_id, date::text AS date, kind, cash_dividend, stock_ratio FROM serving.dividend_calendar
      WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date > ${date}::date AND date <= ${until}::date`,
  )
  const confs = !want('conference') ? [] : await readRows(
    () => db<Conf[]>`
      SELECT stock_id, date::text AS date, conf_time, location FROM serving.conference_calendar
      WHERE stock_id = ANY(string_to_array(${list}, ',')) AND date >= ${date}::date AND date <= ${until}::date`,
  )
  const { ym } = revenueDue(date)
  // 不看營收時不查：revenuePublished 空的，算出來的營收提醒在下面一律濾掉
  const rev = !want('revenue') ? [] : await readRows(
    () => db<{ stock_id: string }[]>`
      SELECT stock_id FROM serving.monthly_revenue
      WHERE stock_id = ANY(string_to_array(${list}, ',')) AND year_month = ${ym}`,
  )
  const inp: Inputs = {
    disp: disp.map((d) => ({ ...d, match_minutes: d.match_minutes === null ? null : Number(d.match_minutes) })),
    att,
    divs: divs.map((v) => ({ ...v, cash_dividend: v.cash_dividend === null ? null : Number(v.cash_dividend), stock_ratio: v.stock_ratio === null ? null : Number(v.stock_ratio) })),
    confs,
    revenuePublished: new Set(rev.map((r) => r.stock_id)),
  }
  for (const sid of sids) {
    const rs = risksFor(sid, date, inp).filter((r) => kinds.includes(r.kind))
    if (rs.length) out.set(sid, rs)
  }
  return out
}
