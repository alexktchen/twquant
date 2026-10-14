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

/**
 * 選股依據掛在哪張模型卡片下：名單是哪個模型選出來的就掛在那張（潛伏飆股、高分爆量＝飆漲機率；大盤精選＝漲幅排名）。
 * 沒有理由（不在名單上、追蹤分頁、那天沒算）→ null，維持三張卡片並列。
 */
export function primaryModel(line: string | null | undefined, reasons: Reason[] | null | undefined): 'rank' | 'quiet' | null {
  if (!line || !reasons?.length) return null
  if (line === 'CORE') return 'rank'
  if (line === 'QUIET' || line === 'QUIETV') return 'quiet'
  return null
}

/** 卡片裡要列的理由：掛在漲幅排名卡時，「排序模型分數在全市場前 X%」（篩選門檻）卡片上已經有，不重複列。 */
export function cardReasons(reasons: Reason[], primary: 'rank' | 'quiet'): Reason[] {
  return primary === 'rank' ? reasons.filter((r) => r.s !== 0) : reasons
}

/** 選股依據標題旁的排序說明：潛伏飆股／高分爆量是模型的特徵貢獻，大盤精選是規則因子（權重 × 全市場排名）。 */
export function factorsNote(primary: 'rank' | 'quiet'): string {
  return primary === 'rank' ? '依規則權重與全市場排名排序' : '依模型貢獻度排序'
}
