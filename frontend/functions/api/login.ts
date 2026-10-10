// GET /api/login：登入入口（受 Access 保護），登入後轉回首頁，細節見 _lib/auth.ts。
import { afterLogin } from '../_lib/auth'

export const onRequestGet: PagesFunction = ({ request }) => afterLogin(request)
