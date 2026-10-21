// 「LINE 群組」分頁（T-423／D-204）的資料型別與顯示用小函式。型別與 functions/_lib/line-admin.ts 一致。
export type Feature = 'line'

export type LineAccount = {
  display_name: string | null
  mid: string | null
  updated_at: string
  paused_until: string | null
  last_error: string | null
}
export type LineGroupRow = { home_id: string; name: string; enabled: boolean; present: boolean; synced_at: string }
export type LinePostRow = {
  id: number
  date: string
  kind: string
  home_id: string
  group_name: string | null
  status: string
  via: string | null
  attempts: number
  last_error: string | null
  enqueued_at: string
  sent_at: string | null
  card_token: string
  alt_text: string
}
export type LineLogin = {
  status: string
  requested_by: string | null
  requested_at: string | null
  updated_at: string
  run_url: string | null
  qr_url: string | null
  qr_png: string | null
  pin: string | null
  display_name: string | null
  error: string | null
  fresh: boolean
}
export type LineStatus = {
  ready: boolean
  account: LineAccount | null
  groups: LineGroupRow[]
  posts: LinePostRow[]
  login: LineLogin | null
}

/** 登入進行中時多久重抓一次（QR 出來、PIN 出來都要快點看到） */
export const LOGIN_POLL_MS = 3000
const ACTIVE = ['requested', 'starting', 'scan', 'pin']

/** 登入是不是正在跑（同 functions/_lib/line-admin.ts loginBusy）。 */
export const loginActive = (l: LineLogin | null): boolean => l !== null && l.fresh && ACTIVE.includes(l.status)

/** 登入進度要顯示的那一句。 */
export function loginStep(l: LineLogin | null): string {
  if (!l) return ''
  if (!l.fresh && ACTIVE.includes(l.status)) return '上一次登入沒有跑完（超過 10 分鐘沒動靜），可以重按'
  switch (l.status) {
    case 'requested':
      return '已送出，等 GitHub Actions 開機（約 1 分鐘）…'
    case 'starting':
      return '登入程式啟動中，QR 碼馬上出來…'
    case 'scan':
      return '用「備用帳號」的手機 LINE 掃這個 QR 碼（3 分鐘內）'
    case 'pin':
      return '掃到了，手機 LINE 會要你輸入這組 PIN：'
    case 'done':
      return `已登入「${l.display_name ?? '?'}」`
    case 'failed':
      return `登入失敗：${l.error ?? '沒有原因'}`
    default:
      return l.status
  }
}

export const KIND_LABEL: Record<string, string> = {
  picks: '每日名單',
  groups: '族群資金',
  morning: '盤前晨報',
  evening: '盤後快訊',
}

/** 貼文狀態 → 文字與顏色（ok 綠、wait 灰、bad 紅）。 */
export function statusView(p: Pick<LinePostRow, 'status' | 'via'>): { text: string; tone: 'ok' | 'wait' | 'bad' } {
  switch (p.status) {
    case 'sent':
      return { text: p.via === 'text' ? '已發（改發文字）' : '已發', tone: p.via === 'text' ? 'wait' : 'ok' }
    case 'queued':
      return { text: '排隊中', tone: 'wait' }
    case 'expired':
      return { text: '太晚作廢', tone: 'bad' }
    case 'unknown':
      return { text: '不確定有沒有發出去', tone: 'bad' }
    case 'failed':
      return { text: '失敗', tone: 'bad' }
    default:
      return { text: p.status, tone: 'wait' }
  }
}

/** 圖卡網址（公開端點 functions/pub/line-card/[token].ts）。 */
export const cardUrl = (token: string) => `/pub/line-card/${token}.png`

/** 「2026-10-21 17:40」→「10/21 17:40」（後端已轉成台北時間）。 */
export function shortTime(ts: string | null): string {
  if (!ts) return ''
  const m = /^\d{4}-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(ts)
  return m ? `${Number(m[1])}/${Number(m[2])} ${m[3]}:${m[4]}` : ts
}

/** 群組區塊右上那行：勾了幾個、帳號在幾個群組。 */
export function groupsMeta(groups: LineGroupRow[]): string {
  const present = groups.filter((g) => g.present)
  const on = present.filter((g) => g.enabled).length
  return present.length ? `發 ${on}／在 ${present.length} 個群組` : '還沒有群組'
}
