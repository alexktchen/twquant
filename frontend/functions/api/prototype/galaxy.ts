// GET /api/prototype/galaxy：新聞全景原型（快照）。放在 /api 底下＝在 Cloudflare Access 後面，要登入才看得到（新聞標題與來源同新聞頁一樣只給登入者）。
import { GALAXY_HTML } from '../../_lib/prototype-galaxy'

export const onRequestGet: PagesFunction = () =>
  new Response(GALAXY_HTML, { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'private, no-store' } })
