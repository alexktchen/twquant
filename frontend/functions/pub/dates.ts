// GET /pub/dates：有名單的交易日，公開（不在 Access 保護的 /api 底下）。
// 只有日期、沒有任何股票，讓沒登入的人也能切到歷史名單；名單本身仍只從 /api 給已登入的人。
export { onRequestGet } from '../api/picks/dates'
