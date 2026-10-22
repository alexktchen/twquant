// GET /api/me：目前登入的 email，細節見 _lib/auth.ts。
import { me } from '../_lib/auth'
import type { IdentityEnv } from '../_lib/identity'

export const onRequestGet: PagesFunction<IdentityEnv> = ({ request, env }) => me(request, env)
