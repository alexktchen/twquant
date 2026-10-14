// 族群標籤（D-177）的顯示：名單每一檔名字旁邊的小字、個股頁標題下那一行。
// 資料來自櫃買中心「產業價值鏈資訊平台」，每檔挑一條產業鏈（例：大毅 → 被動元件／電阻器）；
// 平台查不到的用官方產業別（official）。

/** name＝顯示的字；segs＝在這條鏈上掛哪幾段；also＝還掛了哪些鏈；official＝用的是官方產業別。 */
export type Group = { name: string; segs: string[]; also: string[]; official: boolean }

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

/** 名單小字滑過去的說明。 */
export function groupTitle(g: Group): string {
  if (g.official) return `官方產業別：${g.name}（產業價值鏈平台沒有更細的族群）`
  const segs = segNames(g)
  return segs.length ? `${g.name}：${segs.join('、')}` : g.name
}

/** 個股頁那一行：族群 › 在鏈上的段；還掛了哪些鏈（沒有就 null）。 */
export function groupLine(g: Group): { main: string; also: string | null } {
  const segs = segNames(g)
  return {
    main: segs.length ? `${g.name} › ${head(segs, MAX_SEGS, '段')}` : g.name,
    also: g.also.length ? `也在 ${head(g.also, MAX_ALSO, '條鏈')}` : null,
  }
}
