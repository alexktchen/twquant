// 族群（D-177）的顯示：名單每一檔名字旁邊的小框、名單上方「同族群」、個股頁標題下那兩行、族群排行。
// 族群來自櫃買中心「產業價值鏈資訊平台」（大毅 → 被動元件、奇鋐 → 散熱），平台查不到的用官方產業別（official）。
// 族群資金（flow）：資金倍數＝當天族群成交金額佔全市場的比重 ÷ 前 20 個交易日的平均比重；
// hot＝資金流入（倍數與上漲家數都過門檻，門檻在 configs/stock_groups.yaml）。

/** 族群某一天的資金與漲跌（後端 functions/_lib/groups.ts 的 Flow）。 */
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
/** name＝族群名；chain＝挑中的產業鏈（官方產業別時 null）；segs＝在鏈上掛哪幾段；also＝還掛了哪些鏈；flow＝那天的族群資金。 */
export type Group = { name: string; chain?: string | null; segs: string[]; also: string[]; official: boolean; flow?: Flow | null }
/** 族群排行的一列（/api/groups）。 */
export type RankRow = Omit<Flow, 'date'> & { grp: string; members: { sid: string; name: string | null; chg: number | null; amount: number }[] }

/** 個股頁最多列幾段、幾條別的鏈（台達電掛了 20 幾條鏈），其餘寫「等 N 段／條」。 */
const MAX_SEGS = 3
const MAX_ALSO = 4

/** 段名去掉括號裡的舉例：「網路設備(如數據機、網路卡…)」→「網路設備」。 */
export function segName(s: string): string {
  return s.replace(/[（(][^）)]*[）)]/g, '').trim() || s
}

function head(items: string[], n: number, unit: string): string {
  const shown = items.slice(0, n).join('、')
  return items.length > n ? `${shown} 等 ${items.length} ${unit}` : shown
}

const segNames = (g: Group) => [...new Set(g.segs.map(segName))]
const pct1 = (x: number) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`

/** 資金倍數：1.83 → 「1.8 倍」；算不出來（歷史不夠）→ null。 */
export function ratioText(r: number | null | undefined): string | null {
  return r === null || r === undefined ? null : `${r.toFixed(1)} 倍`
}

/** 名單小框上的字：資金流入的族群多寫倍數（「被動元件 2.1×」）。 */
export function tagText(g: Group): string {
  return g.flow?.hot && g.flow.ratio !== null ? `${g.name} ${g.flow.ratio.toFixed(1)}×` : g.name
}

/** 一句話講族群那天的資金與漲跌：「資金 2.1 倍（成交比重 3.2%，前 20 日平均 1.5%）・12 檔 9 漲 2 跌・平均 +2.3%」。 */
export function flowText(f: Flow): string {
  const parts: string[] = []
  const share = `成交比重 ${(f.share * 100).toFixed(1)}%`
  parts.push(
    f.ratio !== null && f.share_avg !== null
      ? `資金 ${ratioText(f.ratio)}（${share}，前 20 日平均 ${(f.share_avg * 100).toFixed(1)}%）`
      : `${share}（歷史不夠，還算不出倍數）`,
  )
  parts.push(`${f.n} 檔 ${f.up} 漲 ${f.down} 跌`)
  if (f.ret !== null) parts.push(`平均 ${pct1(f.ret)}`)
  return parts.join('・')
}

/** 名單小字滑過去的說明。 */
export function groupTitle(g: Group): string {
  const base = g.official
    ? `官方產業別：${g.name}（產業價值鏈平台沒有更細的族群）`
    : segNames(g).length
      ? `${g.chain ?? g.name}：${segNames(g).join('、')}`
      : g.name
  return g.flow ? `${base}\n${g.flow.hot ? '資金流入：' : ''}${flowText(g.flow)}` : base
}

/** 個股頁的族群行：族群（› 鏈上的段）；還掛了哪些鏈（沒有就 null）。拆到段的族群（散熱）前面帶鏈名。 */
export function groupLine(g: Group): { main: string; also: string | null } {
  const segs = segNames(g)
  const chain = g.chain && g.chain !== g.name ? `${g.chain} › ` : ''
  const main = g.official || !segs.length ? g.name : chain ? `${chain}${g.name}` : `${g.name} › ${head(segs, MAX_SEGS, '段')}`
  return { main, also: g.also.length ? `也在 ${head(g.also, MAX_ALSO, '條鏈')}` : null }
}

/** 名單上方的「同族群」：同一個族群至少兩檔才列，檔數多的在前、一樣多看資金倍數。 */
export function sameGroups(picks: { group?: Group | null }[]): { name: string; count: number; flow: Flow | null }[] {
  const by = new Map<string, { name: string; count: number; flow: Flow | null }>()
  for (const p of picks) {
    if (!p.group) continue
    const got = by.get(p.group.name)
    if (got) got.count += 1
    else by.set(p.group.name, { name: p.group.name, count: 1, flow: p.group.flow ?? null })
  }
  return [...by.values()]
    .filter((x) => x.count >= 2)
    .sort((a, b) => b.count - a.count || (b.flow?.ratio ?? 0) - (a.flow?.ratio ?? 0) || a.name.localeCompare(b.name))
}

/** 成交金額 → 「12.3 億」／「4,500 萬」。 */
export function yi(v: number): string {
  return v >= 1e8 ? `${(v / 1e8).toFixed(1)} 億` : `${Math.round(v / 1e4).toLocaleString('zh-TW')} 萬`
}

/** 族群排行的篩選：只看資金流入；至少幾檔成交才列（太小的族群一檔漲停就翻倍，排在前面沒意義）。 */
export function rankRows(rows: RankRow[], hotOnly: boolean, minMembers: number): RankRow[] {
  return rows.filter((r) => (!hotOnly || r.hot) && r.n >= minMembers)
}
