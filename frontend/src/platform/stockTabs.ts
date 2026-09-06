/** Data tabs below the stock research first screen; the K-line itself is never inside a tab. */
export const stockTabs = [
  { id: 'chips', label: '籌碼' },
  { id: 'fundamentals', label: '基本面' },
  { id: 'factors', label: '因子' },
  // Condition screening keeps its own explicit entry so it is never read as an ML recommendation.
  { id: 'screener', label: '條件選股' },
] as const

export type StockTabId = (typeof stockTabs)[number]['id']
export const DEFAULT_STOCK_TAB: StockTabId = 'chips'

/** Resolve a requested tab, falling back to the default instead of hiding the page. */
export function parseStockTab(value: string | null): StockTabId {
  return stockTabs.some(tab => tab.id === value) ? (value as StockTabId) : DEFAULT_STOCK_TAB
}

/** Build the quote identity so a stock or date change can never reuse another view's rows. */
export function quoteIdentity(sid: string, date: string | null): string {
  return `${sid}@${date ?? 'latest'}`
}
