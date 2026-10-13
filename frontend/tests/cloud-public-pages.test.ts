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

test('公開的研究報告（/reports/）：每頁都不給搜尋引擎收錄、沒有 email、沒有登入後才看得到的名單 API', async () => {
  const { readdirSync, readFileSync } = await import('node:fs')
  const dir = new URL('../public/reports/', import.meta.url)
  const pages = readdirSync(dir).filter((f) => f.endsWith('.html'))
  assert.ok(pages.includes('index.html') && pages.length >= 2)
  for (const f of pages) {
    const html = readFileSync(new URL(f, dir), 'utf8')
    assert.ok(html.includes('<meta name="robots" content="noindex,nofollow">'), `${f} 要有 noindex`)
    assert.ok(!/[\w.+-]+@[\w-]+\.[\w.]+/.test(html), `${f} 不能有 email`)
    // 只擋連到本站要登入的 /api（報告裡提到別人網站的 …/api/… 網址不算）
    assert.ok(!/["'(]\/api\/|twquant\.pages\.dev\/api/.test(html), `${f} 不能連到要登入的 /api`)
  }
})
