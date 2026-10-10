// POST /api/logout：撤銷 Access 登入並清 cookie，細節見 _lib/logout.ts。
import { logout, type LogoutEnv } from '../_lib/logout'

export const onRequestPost: PagesFunction<LogoutEnv> = ({ request, env }) => logout(request, env)
