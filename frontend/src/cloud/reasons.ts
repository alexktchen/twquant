// 選股依據（D-180）的顯示：名單每一檔下面一行、個股頁完整列出。
// 潛伏飆股／高分爆量的理由是模型的特徵貢獻（加分最多的三個＋扣分最多的一個）；
// 大盤精選是它的四個規則因子（依權重 × 全市場百分位排）＋排序模型的百分位（前提）。

/** 一條理由（後端 functions/_lib/reasons.ts）：s＝+1 加分／−1 扣分／0 前提。 */
export type Reason = { f: string; t: string; s: number }

/** 名單那一行：只放加分的，最多 n 個，用「・」串起來；沒有就 null。 */
export function whyLine(reasons: Reason[] | null | undefined, n = 2): string | null {
  const plus = (reasons ?? []).filter((r) => r.s > 0).slice(0, n)
  return plus.length ? plus.map((r) => r.t).join('・') : null
}

/** 個股頁每一條前面的標記與說明（滑過去看）。 */
export function mark(s: number): { sym: string; label: string; tone: 'plus' | 'minus' | 'base' } {
  if (s > 0) return { sym: '＋', label: '正向因子', tone: 'plus' }
  if (s < 0) return { sym: '－', label: '負向因子', tone: 'minus' }
  return { sym: '・', label: '篩選門檻', tone: 'base' }
}
