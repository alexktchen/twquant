// 名單績效 API（D-176）的純函數：線別檢查、把資料庫回來的 JSON 文字原樣包成回應。
// 名單績效每天由排程整份算好存在 serving.scorecard（每條線一列 jsonb），網站只讀不算。
// 回應直接用資料庫給的文字、不在這裡 JSON.parse 再 stringify：逐筆明細一條線可達上千列，免費方案每次請求只有 10ms CPU。
import { LINES } from './picks.ts'

/** 只接受登記過的線別（configs/pick_board.yaml）；其他一律 400，也擋掉奇怪字元。 */
export function lineOk(code: string | null): code is string {
  return code !== null && LINES.some((l) => l.code === code)
}

const HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' }

/** 資料庫回來的 JSON 文字 → 回應；包一層時（例：{"rows": …}）用 key。 */
export function rawJson(text: string, key?: string): Response {
  return new Response(key ? `{${JSON.stringify(key)}:${text}}` : text, { headers: HEADERS })
}
