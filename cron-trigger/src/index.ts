// twquant-cron-trigger（D-175）：Cloudflare Cron Trigger → GitHub workflow_dispatch（daily-pipeline）。
import { dispatchRequest, taipeiDate, type DispatchEnv } from './dispatch'

export default {
  async scheduled(event: { scheduledTime: number }, env: DispatchEnv): Promise<void> {
    const date = taipeiDate(new Date(event.scheduledTime))
    const { url, init } = dispatchRequest(env, date)
    const res = await fetch(url, init)
    // GitHub 成功回 204；其他一律丟錯，Cloudflare 的 Cron 事件紀錄會顯示失敗
    if (res.status !== 204) throw new Error(`觸發 ${date} 失敗：GitHub ${res.status} ${await res.text()}`)
    console.log(`已觸發 daily-pipeline ${date}`)
  },
}
