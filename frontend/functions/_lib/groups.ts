// 族群（D-177）：每檔股票的族群與族群資金。排程每週從櫃買中心「產業價值鏈資訊平台」抓、挑好存在
// serving.stock_group（twquant stock-groups）；每天收盤後算每個族群的資金倍數、漲跌家數存在
// serving.group_flow_daily（twquant group-flow）、三大法人買賣超與象限圖的 x、y、軌跡存在
// serving.group_inst_daily（twquant group-inst，D-177 修訂三）。表上沒有的股票（重抓之間新上市）用官方產業別。
import type { sql } from './db.ts'
import { readRows } from './scorecard.ts'

/** stock_group 的一列；陣列欄位在 SQL 裡轉成 JSON 文字（連線設定 fetch_types: false，text[] 不會自己變成陣列）。 */
export type GroupRow = { stock_id: string; grp: string; chain: string | null; segments: string | null; also: string | null }
/** group_flow_daily 的一列（不含成員）：資金倍數 ratio＝當天成交比重 ÷ 前 20 日平均比重。 */
export type Flow = {
  date: string
  ratio: number | null
  share: number
  share_avg: number | null
  n: number
  up: number
  down: number
  ret: number | null
  hot: boolean
}
/** 網站用的族群：name＝族群名；chain＝挑中的產業鏈（官方產業別時 null）；segs＝在鏈上掛哪幾段；also＝還掛了哪些鏈；flow＝那天的族群資金。 */
export type Group = { name: string; chain: string | null; segs: string[]; also: string[]; official: boolean; flow: Flow | null }

function list(text: string | null): string[] {
  if (!text) return []
  const v: unknown = JSON.parse(text)
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/** 族群表有這檔就用表上的，沒有就用官方產業別；兩個都沒有 → null（不顯示）。 */
export function groupOf(row: GroupRow | undefined, industry: string | null, flows: Map<string, Flow>): Group | null {
  if (row) {
    return { name: row.grp, chain: row.chain, segs: list(row.segments), also: list(row.also), official: row.chain === null, flow: flows.get(row.grp) ?? null }
  }
  const ind = industry?.trim()
  return ind ? { name: ind, chain: null, segs: [], also: [], official: true, flow: flows.get(ind) ?? null } : null
}

type Db = ReturnType<typeof sql>
const csv = (xs: string[]) => [...new Set(xs)].join('\u001f') // 族群名可能有逗號，用不會出現的分隔字元

/** 這幾檔的族群列。表還沒建（新功能上線後第一次排程跑完之前）當成都沒有，網站先顯示官方產業別。 */
export async function readGroups(db: Db, sids: string[]): Promise<Map<string, GroupRow>> {
  if (sids.length === 0) return new Map()
  const rows = await readRows(
    () => db<GroupRow[]>`
      SELECT stock_id, grp, chain, array_to_json(segments)::text AS segments, array_to_json(also)::text AS also
      FROM serving.stock_group WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ','))`,
  )
  return new Map(rows.map((r) => [r.stock_id, r]))
}

/** 這幾個族群在 `date` 那天（null＝最近一天有算的）的資金；那天還沒算（或表還沒建）就是空的。 */
export async function readFlows(db: Db, date: string | null, names: string[]): Promise<Map<string, Flow>> {
  if (names.length === 0) return new Map()
  const rows = await readRows(
    () => db<(Flow & { grp: string })[]>`
      SELECT date::text AS date, grp, ratio, share, share_avg, n::int AS n, up::int AS up, down::int AS down, ret, hot
      FROM serving.group_flow_daily
      WHERE date = coalesce(${date}::date, (SELECT max(date) FROM serving.group_flow_daily))
        AND grp = ANY(string_to_array(${csv(names)}, chr(31)))`,
  )
  return new Map(rows.map(({ grp, ...f }) => [grp, f]))
}

/** 名單（或個股）上這幾檔的族群，連同那天的族群資金。 */
export async function groupsFor(
  db: Db,
  date: string | null,
  meta: { stock_id: string; industry: string | null }[],
): Promise<Map<string, Group | null>> {
  const rows = await readGroups(
    db,
    meta.map((m) => m.stock_id),
  )
  const names = meta.map((m) => rows.get(m.stock_id)?.grp ?? m.industry?.trim() ?? '').filter(Boolean)
  const flows = await readFlows(db, date, names)
  return new Map(meta.map((m) => [m.stock_id, groupOf(rows.get(m.stock_id), m.industry, flows)]))
}

/** 族群排行：`date` 以前（含）最近一天有算的，每個族群一列（含成員），資金倍數大的在前。整份 JSON 在資料庫組好。 */
export async function readRanking(db: Db, date: string | null): Promise<{ date: string; body: string } | null> {
  const rows = await readRows(
    () => db<{ date: string; body: string }[]>`
      WITH d AS (SELECT max(date) AS date FROM serving.group_flow_daily WHERE date <= ${date ?? '9999-12-31'}::date)
      SELECT d.date::text AS date, coalesce(json_agg(json_build_object(
        'grp', g.grp, 'n', g.n, 'up', g.up, 'down', g.down, 'ret', g.ret, 'share', g.share,
        'share_avg', g.share_avg, 'ratio', g.ratio, 'hot', g.hot, 'members', g.members
      ) ORDER BY g.ratio DESC NULLS LAST, g.grp)::text, '[]') AS body
      FROM d JOIN serving.group_flow_daily g ON g.date = d.date GROUP BY d.date`,
  )
  return rows[0] ?? null
}

/** 族群法人（象限圖）：`date` 那天每個族群的近 5 日法人買賣超（x）、加速（y）、外資／投信／自營商近 5 日、軌跡。
 * 整份 JSON（{族群: {...}}）在資料庫組好；那天還沒算（或表還沒建）回 null，網站寫「還沒算出來」。 */
export async function readInst(db: Db, date: string): Promise<string | null> {
  const rows = await readRows(
    () => db<{ body: string | null }[]>`
      SELECT json_object_agg(grp, json_build_object(
        'x', net5, 'y', accel, 'net', net, 'f5', foreign5, 't5', trust5, 'd5', dealer5, 'trail', trail
      ))::text AS body
      FROM serving.group_inst_daily WHERE date = ${date}::date AND net5 IS NOT NULL AND accel IS NOT NULL`,
  )
  return rows[0]?.body ?? null
}
