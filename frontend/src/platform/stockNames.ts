import { useEffect, useState } from 'react'
import { fetchStockMeta, type StockMetaMap } from '../api'

// 三個平台頁面都要顯示股票名稱，共用同一份對照表，不各自打一次 /api/stock-meta。
let pending: Promise<StockMetaMap> | null = null
let cached: StockMetaMap = {}

/** Load the stock name map once per session; a failure degrades to codes only. */
export function loadStockNames(): Promise<StockMetaMap> {
  pending ??= fetchStockMeta().then(
    meta => {
      cached = meta
      return meta
    },
    // 名稱是輔助資訊：讀不到就只顯示代號，不讓整頁壞掉，也不假裝某檔沒有名字。
    () => ({}) as StockMetaMap,
  )
  return pending
}

/** The stock name map; empty while loading or if the lookup failed. */
export function useStockNames(): StockMetaMap {
  const [names, setNames] = useState<StockMetaMap>(cached)
  useEffect(() => {
    let alive = true
    loadStockNames().then(meta => alive && setNames(meta))
    return () => {
      alive = false
    }
  }, [])
  return names
}

/** The Chinese name for a stock, or null when the map has no entry for it. */
export function nameOf(names: StockMetaMap, stockId: string): string | null {
  return names[stockId]?.name ?? null
}
