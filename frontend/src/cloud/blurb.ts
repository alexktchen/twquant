// 名單每檔一句話（T-393／D-195）的型別與說明文字；畫面在 CloudApp.tsx 的名單列與個股頁。
export type Blurb = { text: string; ai: boolean }

/** 滑過／長按看到的說明：AI 寫的與規則句分開講。 */
export function blurbTitle(b: Blurb): string {
  return b.ai
    ? 'AI 依當天的事實（選股理由、處置、除權息、法說會、公告、漲跌）寫的一句話；數字與說法都和事實逐一核對過。只陳述、不是投資建議'
    : '規則整理的一句話（AI 今天沒寫或沒通過核對）：選它的第一個理由，加上最要緊的提醒'
}
