// GET /pub/line-card/:token(.png)：LINE 群組圖卡（T-423／D-204）。LINE 的伺服器要抓得到，所以公開（/pub 不經 Cloudflare Access）；
// 網址裡的 token 是 32 位亂數，猜不到。圖卡由排程畫好存在 web.line_cards，內容就是已經發到群組的那張圖。
// 同一個 token 的圖不會變 → 讓 Cloudflare 與 LINE 長期快取。
import { fail, guarded, type Env } from '../../_lib/db'
import { CARD_TOKEN_RE } from '../../_lib/line-admin'

export const onRequestGet: PagesFunction<Env> = async ({ env, params }) => {
  // 排程給 LINE 的網址結尾帶 .png（保險：有些用戶端看副檔名），不帶也認
  const token = String(params.token ?? '').replace(/\.png$/, '')
  if (!CARD_TOKEN_RE.test(token)) return fail(404, '找不到')
  return guarded(
    env,
    async (db) => {
      const [ok] = await db<{ ok: boolean }[]>`SELECT to_regclass('web.line_cards') IS NOT NULL AS ok`
      if (!ok?.ok) return fail(404, '找不到')
      const [row] = await db<{ png: Uint8Array }[]>`SELECT png FROM web.line_cards WHERE token = ${token}`
      if (!row) return fail(404, '找不到')
      return new Response(row.png, {
        headers: { 'content-type': 'image/png', 'cache-control': 'public, max-age=31536000, immutable' },
      })
    },
    '圖卡暫時無法取得',
  )
}
