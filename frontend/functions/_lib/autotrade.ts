// 程式交易的狀態（autotrade A-005）：serving.autotrade_status 一天、一種模式一列，payload 是整份狀態。
// 寫入者是獨立的交易系統（autotrade/tools/push_status.py）。單向：網站只讀，沒有任何寫回去的端點。只給 admin。

export const AUTOTRADE_MODES = ['sim', 'live'] as const
export type AutotradeMode = (typeof AUTOTRADE_MODES)[number]

/** ?mode= 是不是模擬或真單。 */
export const modeOk = (m: string | null): m is AutotradeMode => m !== null && (AUTOTRADE_MODES as readonly string[]).includes(m)

/** 日期清單一次最多給幾天（約三個月的交易日）。 */
export const AUTOTRADE_DAYS_LIMIT = 60

export type AutotradeDayRow = { day: string; status: string; pnl: number | null; fills: number | null }

/** 回應本體：日期清單＋那一天的狀態。狀態直接用資料庫給的 JSON 文字，不 parse 再 stringify；沒有紀錄是 null。 */
export const envelope = (days: readonly AutotradeDayRow[], body: string | null): string =>
  `{"days":${JSON.stringify(days)},"current":${body ?? 'null'}}`
