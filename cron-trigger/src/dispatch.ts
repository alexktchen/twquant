// 純函數（D-175）：算台北日期、組 GitHub workflow_dispatch 請求。供 frontend/tests 單元測試。

export interface DispatchEnv {
  GITHUB_TOKEN?: string
  GITHUB_REPO: string
  WORKFLOW: string
  /** 06:30 那班要觸發的工作流程（T-377 晨報）；沒設就全部走 WORKFLOW */
  MORNING_WORKFLOW?: string
  MORNING_CRON?: string
  /** 18:00 那班（T-409 盤後快訊）：同一個晨報工作流程、多帶 edition=evening */
  EVENING_CRON?: string
}

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000 // 台灣沒有日光節約時間

/** 某個時刻在台北是哪一天（YYYY-MM-DD）。 */
export function taipeiDate(at: Date): string {
  return new Date(at.getTime() + TAIPEI_OFFSET_MS).toISOString().slice(0, 10)
}

/** 哪個 cron 觸發哪個工作流程：晨報（MORNING_CRON）與盤後快訊（EVENING_CRON）→ MORNING_WORKFLOW，其他 → WORKFLOW（每日 pipeline）。 */
export function workflowFor(env: DispatchEnv, cron: string | undefined): string {
  if (cron && env.MORNING_WORKFLOW && ((env.MORNING_CRON && cron === env.MORNING_CRON) || (env.EVENING_CRON && cron === env.EVENING_CRON))) return env.MORNING_WORKFLOW
  return env.WORKFLOW
}

/** 工作流程的輸入：日期；盤後那班多 edition=evening。 */
export function inputsFor(env: DispatchEnv, cron: string | undefined, date: string): Record<string, string> {
  return cron && env.EVENING_CRON && cron === env.EVENING_CRON ? { date, edition: 'evening' } : { date }
}

/** workflow_dispatch 的請求；沒有 token 直接 raise——不得靜默變成「什麼都沒觸發」。 */
export function dispatchRequest(env: DispatchEnv, date: string, workflow: string = env.WORKFLOW, inputs: Record<string, string> = { date }): { url: string; init: RequestInit } {
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN 沒設定：到 Cloudflare 後台的 Worker 設定加祕密')
  if (!/^[\w.-]+\/[\w.-]+$/.test(env.GITHUB_REPO)) throw new Error(`GITHUB_REPO 格式錯誤：${env.GITHUB_REPO}`)
  if (!/^[\w.-]+\.ya?ml$/.test(workflow)) throw new Error(`工作流程檔名格式錯誤：${workflow}`)
  return {
    url: `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`,
    init: {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'twquant-cron-trigger',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main', inputs }),
    },
  }
}
