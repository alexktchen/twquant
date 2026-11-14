// 頁面用到的幾個小工具（autotrade A-018）。原本在 twquant 網站的台指期那一頁裡，這個網站只需要這四個。

/** 期交所代號 → 平常的叫法。 */
export const PRODUCT_NAME: Record<string, string> = { MTX: '小台', TX: '大台', TMF: '微台' }

/** 台北現在的日期與時分（不管裝置設在哪個時區）。 */
export function taipeiNow(now: Date): { day: string; hhmm: string } {
  const iso = new Date(now.getTime() + 8 * 3600_000).toISOString()
  return { day: iso.slice(0, 10), hhmm: iso.slice(11, 16) }
}

/** 分頁在背景時要不要自動更新：只在交易時段前後（08:25～13:50），而且看的是今天。 */
export function shouldPoll(now: Date, dayShown: string | null): boolean {
  const { day, hhmm } = taipeiNow(now)
  return (dayShown === null || dayShown === day) && hhmm >= '08:25' && hhmm <= '13:50'
}

/** 元：+12,345／-6,789。 */
export function yuan(v: number): string {
  const r = Math.round(v)
  if (r === 0) return '0'
  return `${r > 0 ? '+' : '-'}${Math.abs(r).toLocaleString('en-US')}`
}

/** 漲跌色：台股漲紅跌綠。 */
export const tone = (v: number | null): 'up' | 'down' | 'plain' => (v === null || v === 0 ? 'plain' : v > 0 ? 'up' : 'down')
