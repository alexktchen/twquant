import { apiUrl } from '../api'

export type StockEvent = {
  run_id: string
  model_id: string
  stock_id: string
  as_of: string
  published_at: string | null
  mode: 'research' | 'reconstructed'
  rank: number
  score: number
  bucket: string
  reason_ref: string | null
  insight_run_id: string | null
  data_status: string
}

/** Read one stock's saved recommendation events; this endpoint never runs inference. */
export async function fetchStockEvents(sid: string): Promise<StockEvent[]> {
  const path = `/api/platform/stocks/${encodeURIComponent(sid)}/recommendations`
  const response = await fetch(apiUrl(path), { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`推薦事件讀取失敗（HTTP ${response.status}）`)
  const body = (await response.json()) as { events: StockEvent[] }
  return body.events
}
