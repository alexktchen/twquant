import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// iPhone 主畫面模式（viewport-fit=cover + black-translucent）內容會畫到狀態列與 Home 橫條底下。
// PR #135 的 padding-top 被手機斷點裡的 `padding: 0 12px` 簡寫歸零，頂欄仍被蓋住；這裡鎖住它。
const css = readFileSync(new URL('../src/cloud/cloud.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')

function rules(selector: string): string[] {
  const out: string[] = []
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (m[1].split(',').map((s) => s.trim()).includes(selector)) out.push(m[2])
  }
  return out
}

function check(selector: string, side: 'top' | 'bottom'): void {
  const env = `env(safe-area-inset-${side}`
  const blocks = rules(selector)
  assert.ok(blocks.some((b) => b.includes(env)), `${selector} 沒有任何規則用 ${env})`)
  for (const b of blocks) {
    for (const d of b.split(';')) {
      const [prop, value = ''] = d.split(':').map((s) => s.trim())
      if (prop === 'padding' || prop === `padding-${side}`) {
        assert.ok(value.includes(env), `${selector} 的 ${prop}: ${value} 會把 ${env}) 蓋掉`)
      }
    }
  }
}

test('T-332 回歸：頂欄在所有斷點都保留狀態列安全區', () => check('.cq-top', 'top'))

test('T-332 回歸：頁面底部在所有斷點都保留 Home 橫條安全區', () => check('.cq', 'bottom'))
