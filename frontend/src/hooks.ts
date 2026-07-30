// 共用 hooks：meta context、逐頁懶載入 resource、media query。
import { createContext, useContext, useEffect, useState } from 'react'
import type { Meta } from './api'

/* ---------- meta context (輕量，app 啟動載一次；shell 與各頁預設用) ---------- */
export type MetaCtxT = { meta: Meta; live: boolean }
export const MetaCtx = createContext<MetaCtxT | null>(null)
export function useMeta(): MetaCtxT {
  const c = useContext(MetaCtx)
  if (!c) throw new Error('MetaCtx 未提供')
  return c
}

/* ---------- 逐頁懶載入 resource ----------
   切到某頁才 fetch 該段（重運算的因子/回測/容量只在對應頁開啟時才算）。不快取：
   每次掛載/換鍵都即時重打 API，資料一更新下一次就反映；API 不可達則退回內嵌 data.json
   對應段（保留離線可用）。*/
export type Res<T> = { data: T | null; loading: boolean; live: boolean }
export function useResource<T>(key: string, fetcher: () => Promise<T>, fallback: () => T): Res<T> {
  const [res, setRes] = useState<Res<T>>({ data: null, loading: true, live: false })
  useEffect(() => {
    let alive = true
    setRes({ data: null, loading: true, live: false })
    fetcher()
      .then((d) => alive && setRes({ data: d, loading: false, live: true }))
      .catch(() => alive && setRes({ data: fallback(), loading: false, live: false }))
    return () => {
      alive = false
    }
    // key 即快取鍵：fetcher/fallback 為呼叫端 inline closure，僅 key 變更時重抓
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return res
}

/* ---------- media query（mobile-first 響應式：圖表高度等 JS 端斷點）---------- */
export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const on = () => setMatch(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [query])
  return match
}
