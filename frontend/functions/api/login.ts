// GET /api/login：Cloudflare Access 登入完的落點，轉回首頁，細節見 _lib/auth.ts。
import { afterLogin } from '../_lib/auth'

export const onRequestGet: PagesFunction = ({ request }) => afterLogin(request)
