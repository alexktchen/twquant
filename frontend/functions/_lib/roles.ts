// 角色與功能權限（T-423／D-204，使用者 2026-10-21：「只有有權限的角色才能看到這功能，目前設定為 cktalex@gmail.com」）。
// 誰是哪個角色、哪個角色能用哪個功能。前端只拿來決定要不要顯示分頁；真正擋人的是後端每個端點自己 can()，
// 身分來自 Cloudflare Access 簽過、驗得過的權杖（identity.ts），不信標頭。改名單＝改這裡再部署。
// （repo 是公開的；這個 email 本來就在 git 提交紀錄裡。要加不想公開的人，改成讀 Pages 祕密。）
// T-430／D-208（使用者 2026-10-22：「可以把每日名單跟績效變成admin才能看到的功能」）：多一個角色 admin，
// 每日名單（picks）與績效（score）只給它。推播的收件人在 configs/push.yaml recipients，跟 admin 名單相同（tests/test_push_notify.py 守著）。

export type Role = 'admin' | 'line_admin'
export type Feature = 'line' | 'picks' | 'score' | 'futures'

/** 角色 → 成員 email（小寫） */
export const ROLE_MEMBERS: Readonly<Record<Role, readonly string[]>> = {
  admin: ['cktalex@gmail.com'],
  line_admin: ['cktalex@gmail.com'],
}

/** 功能 → 哪些角色能用。line＝「LINE 群組」分頁：發文帳號狀態、勾要發的群組、看發了什麼；
 *  picks＝每日名單：名單本身、哪天上過名單（K 線標記）、「在名單上」的各種標記、首頁「今日名單」；
 *  score＝績效：名單照規則操作的模擬帳戶與逐筆、首頁「名單成績」；
 *  futures＝小台模擬（T-450／D-220）：富邦即時行情跑的小台模擬單。富邦行情不得轉傳，只能給自己看。 */
export const FEATURE_ROLES: Readonly<Record<Feature, readonly Role[]>> = {
  line: ['line_admin'],
  picks: ['admin'],
  score: ['admin'],
  futures: ['admin'],
}

/** 這個 email 有哪些角色（沒登入或不在名單上 → 空）。 */
export function rolesOf(email: string | null | undefined): Role[] {
  if (!email) return []
  const e = email.trim().toLowerCase()
  return (Object.keys(ROLE_MEMBERS) as Role[]).filter((r) => ROLE_MEMBERS[r].includes(e))
}

/** 這個 email 能用哪些功能。 */
export function featuresOf(email: string | null | undefined): Feature[] {
  const roles = rolesOf(email)
  return (Object.keys(FEATURE_ROLES) as Feature[]).filter((f) => FEATURE_ROLES[f].some((r) => roles.includes(r)))
}

export const can = (email: string | null | undefined, feature: Feature): boolean => featuresOf(email).includes(feature)
