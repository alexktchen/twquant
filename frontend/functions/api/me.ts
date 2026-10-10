// GET /api/me：目前登入的 email，細節見 _lib/auth.ts。
import { me } from '../_lib/auth'

export const onRequestGet: PagesFunction = ({ request }) => me(request)
