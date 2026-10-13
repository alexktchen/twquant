// 投資風險警語（進網站前要勾選同意）：文字與「這台裝置同意過沒」的判斷（純函式，node 單元測試用）。
// 同意紀錄存在這台裝置的瀏覽器（隱私權政策有列）；換裝置、換瀏覽器或清除瀏覽器資料就會再問一次。

/** 瀏覽器裡記同意紀錄的鍵。 */
export const RISK_KEY = 'twquant.risk'
/** 警語內容的版本：內容有實質修改時加一，所有人下次進來都要重新同意。 */
export const RISK_VERSION = '1'

export const RISK_TITLE = '投資風險警語'

export const RISK_POINTS: string[] = [
  '投資一定有風險，股票投資有賺有賠，可能損失部分或全部本金。進行任何投資前，請審慎評估自己的財務狀況與風險承受能力。',
  '本網站提供的選股名單、模型評分、回測數字、即時報價與研究報告，都是程式依歷史資料計算的研究結果，僅供參考，不構成任何投資建議、買賣推薦或要約。',
  '過去的績效與回測結果不代表未來的表現，也不保證獲利。模型可能判斷錯誤，資料可能延遲、缺漏或有誤（盤中價格來自證交所，約延遲 5 秒）。',
  '本網站不是證券投資顧問事業，不提供個別化的投資建議，也不代客操作。使用者依本網站資訊所做的任何投資決定，應自行判斷並自負盈虧；本網站及其提供者不負任何損失賠償責任。',
  '本網站內容僅供個人參考，未經同意請勿轉載或作商業用途。',
]

export const RISK_AGREE = '我已閱讀並了解以上內容，同意自行承擔投資風險'

/** 這台裝置同意過「目前這一版」警語沒有。存的值是「版本:同意日期」。 */
export function riskAccepted(stored: string | null, version: string = RISK_VERSION): boolean {
  return stored !== null && stored.split(':')[0] === version
}

/** 要存的同意紀錄：版本與同意的日期（只存這兩樣，不含任何個人資料）。 */
export function riskRecord(today: string, version: string = RISK_VERSION): string {
  return `${version}:${today}`
}
