// twquant-market-live（T-397／D-198）：Cloudflare Cron Trigger，盤中每分鐘問一份股票的即時報價存進 KV。
// 怎麼問、怎麼存見 frontend/functions/_lib/market-live.ts runOnce；失敗就丟錯（Cron 事件紀錄看得到），下一分鐘照常輪下一份。
import { MIS_HEADERS, runOnce, type Kv } from '../../frontend/functions/_lib/market-live.ts'

type Env = { LIVE: Kv; UNIVERSE_URL: string }

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url, { headers: MIS_HEADERS })
  if (!res.ok) throw new Error(`${new URL(url).pathname} HTTP ${res.status}`)
  return res.text()
}

export default {
  async scheduled(event: { scheduledTime: number }, env: Env): Promise<void> {
    const done = await runOnce({
      now: event.scheduledTime,
      kv: env.LIVE,
      fetchText,
      sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
      universeUrl: env.UNIVERSE_URL,
    })
    console.log(done)
  },
}
