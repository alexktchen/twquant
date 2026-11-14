// 跟這個網站自己的後端講話（autotrade A-018）。登入由 Cloudflare Access 處理：瀏覽器帶著它發的 cookie，
// 這裡不用自己管權杖。三支會動到交易程式的請求都在最下面，除此之外沒有別的。
import type { AutoEnvelope, AutoMode, RemoteAction, RemoteBeat, RemoteRequest } from './autotrade'

const OFFLINE = '連線中斷，請重新整理頁面'
const EXPIRED = '登入過期了，請重新整理頁面再登入一次'

async function get<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let res: Response
  try {
    const headers: Record<string, string> = { accept: 'application/json' }
    // no-store：盤中會變；redirect manual：登入過期時 Access 會把請求轉去登入頁，不跟過去
    const init: RequestInit = { method, headers, redirect: 'manual', cache: 'no-store', credentials: 'same-origin' }
    if (body !== undefined) {
      headers['content-type'] = 'application/json'
      init.body = JSON.stringify(body)
    }
    res = await fetch(path, init)
  } catch {
    throw new Error(OFFLINE)
  }
  if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) throw new Error(EXPIRED)
  if (!(res.headers.get('content-type') ?? '').includes('json')) throw new Error(`${res.status} ${res.statusText}`)
  if (!res.ok) {
    const problem = await res.json().catch(() => null)
    throw new Error(problem?.detail ?? `${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

/** 現在登入的是誰；沒放行會丟出原因（登入還沒設定好、登入過期、不在名單上）。 */
export const fetchMe = () => get<{ email: string }>('/api/me')

/** 程式交易的狀態（autotrade A-005）：日期清單＋那一天的整份狀態，以及遠端停止與遙控指令的現況；只讀。 */
export const fetchAutotrade = (mode: AutoMode, day?: string) => get<AutoEnvelope>(`/api/autotrade?mode=${mode}${day ? `&day=${encodeURIComponent(day)}` : ''}`)

/** 遙控指令（autotrade A-016、A-017／D-229）：平倉、暫停／恢復、啟動／結束。手抖連按會拿到同一筆；30 秒沒有程式接手就作廢。 */
export const sendAutotradeCommand = (action: RemoteAction, target: AutoMode) =>
  get<{ ok: boolean; reused: boolean; requests: RemoteRequest[]; remote: RemoteBeat | null }>('/api/autotrade/command', 'PUT', { confirm: action, target })

/** 遠端緊急停止（autotrade A-011／D-225）：只會叫停，沒有解除。手抖連按會拿到同一筆。 */
export const sendAutotradeStop = () => get<{ ok: boolean; reused: boolean } & Pick<AutoEnvelope, 'stop' | 'listeners'>>('/api/autotrade/stop', 'PUT', { confirm: 'stop' })
