// twquant-cron-trigger（D-175）：Cloudflare Cron Trigger → GitHub workflow_dispatch。
// 17:33／21:13 → daily-pipeline；06:30（T-377）→ morning-brief；21:45（T-409）→ morning-brief 帶 edition=evening。
// 哪條 cron 對哪個工作流程見 dispatch.ts workflowFor／inputsFor。
import { dispatchRequest, inputsFor, taipeiDate, workflowFor, type DispatchEnv } from './dispatch'

export default {
  async scheduled(event: { scheduledTime: number; cron?: string }, env: DispatchEnv): Promise<void> {
    const date = taipeiDate(new Date(event.scheduledTime))
    const workflow = workflowFor(env, event.cron)
    const { url, init } = dispatchRequest(env, date, workflow, inputsFor(env, event.cron, date))
    const res = await fetch(url, init)
    // GitHub 成功回 204；其他一律丟錯，Cloudflare 的 Cron 事件紀錄會顯示失敗
    if (res.status !== 204) throw new Error(`觸發 ${workflow} ${date} 失敗：GitHub ${res.status} ${await res.text()}`)
    console.log(`已觸發 ${workflow} ${date}`)
  },
}
