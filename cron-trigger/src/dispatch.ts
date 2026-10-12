// 純函數（D-175）：算台北日期、組 GitHub workflow_dispatch 請求。供 frontend/tests 單元測試。

export interface DispatchEnv {
  GITHUB_TOKEN?: string
  GITHUB_REPO: string
  WORKFLOW: string
}

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000 // 台灣沒有日光節約時間

/** 某個時刻在台北是哪一天（YYYY-MM-DD）。 */
export function taipeiDate(at: Date): string {
  return new Date(at.getTime() + TAIPEI_OFFSET_MS).toISOString().slice(0, 10)
}

/** workflow_dispatch 的請求；沒有 token 直接 raise——不得靜默變成「什麼都沒觸發」。 */
export function dispatchRequest(env: DispatchEnv, date: string): { url: string; init: RequestInit } {
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN 沒設定：到 Cloudflare 後台的 Worker 設定加祕密')
  if (!/^[\w.-]+\/[\w.-]+$/.test(env.GITHUB_REPO)) throw new Error(`GITHUB_REPO 格式錯誤：${env.GITHUB_REPO}`)
  return {
    url: `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW}/dispatches`,
    init: {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'twquant-cron-trigger',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main', inputs: { date } }),
    },
  }
}
