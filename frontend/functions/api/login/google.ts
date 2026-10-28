// GET /api/login/google：Google 一鍵登入（T-457），登入後經 /api/login 換主應用通行證再回首頁，細節見 _lib/auth.ts。
import { viaMainLogin } from '../../_lib/auth'

export const onRequestGet: PagesFunction = ({ request }) => viaMainLogin(request)
