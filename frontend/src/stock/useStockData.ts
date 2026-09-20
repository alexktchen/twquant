// 個股儀表板的資料：一檔股票一次把各面板要的序列讀齊，面板之間共用，不各打一次。
// 每一項失敗就回 null（res.live=false）；面板據此顯示「讀取失敗」，絕不以其他來源或快照補位。
import {
  apiUrl,
  fetchStockBrokerFlow,
  fetchStockBrokers,
  fetchStockCompositeHistory,
  fetchStockFactorHistory,
  fetchStockInstitutional,
  fetchStockMargin,
  fetchStockOverview,
  fetchStockRevenue,
} from '../api'
import type { FactorOption } from '../api'
import { useResource } from '../hooks'
import type { InsightPack } from '../platform/InsightsPage'
import { factorCardFrom } from './readings.ts'

/** 各序列向 API 要多長；資料完整度面板也以這些數字當分母。 */
export const DATA_WINDOW = { chipDays: 120, historyDays: 250, revenueMonths: 36, brokerDays: 5, poolSize: 200 } as const

async function fetchInsightPacks(sid: string): Promise<InsightPack[]> {
  const response = await fetch(`${apiUrl('/api/platform/insights')}?stock=${encodeURIComponent(sid)}`, { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`洞悉證據包讀取失敗（HTTP ${response.status}）`)
  return ((await response.json()) as { packs: InsightPack[] }).packs
}

const none = () => null

/** Load every per-stock series the dashboard shows. The market-wide screener is not here: it loads on demand in its panel. */
export function useStockData(sid: string, factorOptions: FactorOption[] | null) {
  // 尚未選股（入口畫面）時不發任何請求；各項維持「未載入」。
  const get = <T,>(load: () => Promise<T>) => () => (sid ? load() : Promise.reject<T>(new Error('尚未選股')))
  return {
    overview: useResource(`overview:${sid}`, get(() => fetchStockOverview(sid)), none),
    inst: useResource(`inst:${sid}`, get(() => fetchStockInstitutional(sid, DATA_WINDOW.chipDays)), none),
    margin: useResource(`margin:${sid}`, get(() => fetchStockMargin(sid, DATA_WINDOW.chipDays)), none),
    revenue: useResource(`rev:${sid}`, get(() => fetchStockRevenue(sid, DATA_WINDOW.revenueMonths)), none),
    brokers: useResource(`brokers:${sid}`, get(() => fetchStockBrokers(sid, DATA_WINDOW.brokerDays)), none),
    flow: useResource(`bflow:${sid}`, get(() => fetchStockBrokerFlow(sid, DATA_WINDOW.historyDays)), none),
    comp: useResource(`chist:${sid}`, get(() => fetchStockCompositeHistory(sid, DATA_WINDOW.historyDays)), none),
    // 因子卡：factorOptions 為 null＝因子清單還在路上（維持載入中）；空陣列＝清單讀不到（視為失敗）。
    scorecard: useResource(
      `fcard:${sid}:${factorOptions === null ? 'pending' : factorOptions.length}`,
      get(() =>
        factorOptions === null
          ? new Promise<never>(() => {})
          : factorOptions.length === 0
            ? Promise.reject(new Error('因子清單讀取失敗'))
            : fetchStockFactorHistory(sid, factorOptions.map((f) => f.factor), 1).then((h) => factorCardFrom(h.rows, factorOptions)),
      ),
      none,
    ),
    insights: useResource(`insights:${sid}`, get(() => fetchInsightPacks(sid)), none),
  }
}

export type StockData = ReturnType<typeof useStockData>
