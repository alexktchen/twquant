import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// /about 與 /privacy 是 Cloudflare Access 以外唯一公開的頁面（Google OAuth 發布需要首頁與隱私權政策）。
// 保持純靜態：不載入任何腳本，也不呼叫 /api，公開路徑才不會漏出要登入才能看的資料。
const page = (name: string): string => readFileSync(new URL(`../public/${name}`, import.meta.url), 'utf8')

test('T-332 公開頁：首頁連到隱私權政策與網站', () => {
  const about = page('about.html')
  assert.match(about, /href="\/privacy"/)
  assert.match(about, /href="\/"/)
})

test('T-332 公開頁：不載入腳本、不碰 API', () => {
  for (const name of ['about.html', 'privacy.html']) {
    const html = page(name)
    assert.doesNotMatch(html, /<script/i, `${name} 不可有 <script>`)
    assert.doesNotMatch(html, /\/api\//, `${name} 不可呼叫 /api/`)
  }
})
