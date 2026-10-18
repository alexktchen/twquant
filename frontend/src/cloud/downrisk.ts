// 「模型:易跌」小標（T-391／D-193）。純函式，供 node 單元測試；後端資料形狀見 functions/_lib/down-risk.ts。
// D-192 研究的結論：這個模型挑出來最會跌的，同時也是最會漲的；剔掉它們平均報酬反而降。所以這是提醒，不是叫你別買。

export type DownRisk = { p: number; flagged: boolean }

/** 名單一列的小標：只有 flagged 才顯示；title 寫機率與那個結論。沒有、舊後端沒有這欄 → null。 */
export function downRiskTag(d: DownRisk | null | undefined): { text: string; title: string } | null {
  if (!d || !d.flagged) return null
  return {
    text: '模型:易跌',
    title:
      `D-192 的模型認為它是今天大盤精選裡最可能 20 個交易日內碰到 −15% 的 5 檔之一（模型機率 ${Math.round(d.p * 100)}%，這個機率偏高、只能在名單內相對比）。` +
      '但歷史上這 5 檔平均反而漲比較多：把它們剔掉，20 日平均報酬從 4.29% 掉到 3.86%。所以這只是提醒，不是叫你別買；名單不因此刪減。',
  }
}

/** 個股列滑過或個股頁用：「模型易跌機率 37%」；沒有 → ''。 */
export function downRiskText(d: DownRisk | null | undefined): string {
  return d ? `模型易跌機率 ${Math.round(d.p * 100)}%${d.flagged ? '（今天名單裡前 5）' : ''}` : ''
}
