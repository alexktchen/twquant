// GET /api/warroom/live（T-397／D-198、D-199）：盤中大盤體檢——讀排程 Worker（market-live/）存在 KV 的各份報價與股票清單，
// 加總成漲跌家數、漲跌停、新高新低、累計成交金額與加權分時走勢。不連資料庫、不問證交所（證交所只由 Worker 問）。
// 每個人看到都一樣：同一執行個體 60 秒暫存。KV 還沒綁定（本機）或今天還沒有任何一份 → live: null，網站照舊顯示收盤後的體檢。
import { fail, json } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { MAX_SLICES, UNIVERSE_KEY, sliceKey, summarize, taipeiClock, type Slice, type Universe } from '../../_lib/market-live'

type LiveEnv = { LIVE?: KVNamespace }

const handle = async (env: LiveEnv): Promise<Response> => {
  if (!env.LIVE) return json({ live: null })
  const kv = env.LIVE
  try {
    const [universe, ...slices] = await Promise.all([
      kv.get<Universe>(UNIVERSE_KEY, 'json'),
      // 讀到 MAX_SLICES：Worker 改份數的過渡期，舊切法與新切法的份都讀得到，summarize 照最新那份的切法判斷
      ...Array.from({ length: MAX_SLICES }, (_, k) => kv.get<Slice>(sliceKey(k), 'json')),
    ])
    if (!universe) return json({ live: null })
    return json({ live: summarize(universe, slices, taipeiClock(Date.now()).date) })
  } catch (e) {
    return fail(503, `盤中統計暫時取不到：${e instanceof Error ? e.message : String(e)}`)
  }
}

export const onRequestGet: PagesFunction<LiveEnv> = ({ request, env }) => cached(cacheKey(request), () => handle(env))
