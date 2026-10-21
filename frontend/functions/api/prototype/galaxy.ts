// GET /api/prototype/galaxy：使用者 2026-10-21「我要這位置直接可以看」。原本是星雲圖原型快照（T-418 換成正式版時拿掉），
// 現在轉到新聞頁並直接開星雲圖（即時資料，一天更新四次）。放在 /api 底下＝在 Cloudflare Access 後面，要登入（同新聞頁）。
export const onRequestGet: PagesFunction = ({ request }) =>
  Response.redirect(new URL('/?tab=news&view=galaxy', request.url).toString(), 302)
