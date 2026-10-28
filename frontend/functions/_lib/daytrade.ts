// 個股當沖（T-458／D-221）：serving.stock_paper 一個交易日一列（盤中強勢股榜＋網友原版模擬單的整份狀態），
// serving.stock_paper_history 收盤後一列（榜單每 30 秒一份、有成交的股票的 1 分 K 與秒級，復盤才讀）。
// 寫入者是使用者電腦上的 research/momentum_scalping/scripts/stock_live.py（富邦即時行情；盤中每 15 秒覆蓋、收盤定稿）。
// 富邦行情不得轉傳 → 跟台指期同一個權限（futures，只給 admin），不進 60 秒暫存。

/** 還沒有任何紀錄：表還沒建，或電腦上的程式還沒寫過任何一天。 */
export const DAYTRADE_NOT_READY = '還沒有當沖紀錄：電腦上的錄製程式（stock_live.py --upload）開盤後就會寫進來。'

/** 這天沒有復盤資料：收盤定稿後才寫。 */
export const NO_HISTORY = '這天還沒有復盤資料（收盤後才會有）'
