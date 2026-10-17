import assert from 'node:assert/strict'
import test from 'node:test'
import { LINE_KEY, loadPref, savePref } from '../src/cloud/prefs.ts'

const g = globalThis as unknown as { window?: { localStorage: Pick<Storage, 'getItem' | 'setItem'> } }

test('T-332 偏好：策略與 K 線根數存在這台裝置，讀得回來', () => {
  const box = new Map<string, string>()
  g.window = { localStorage: { getItem: (k) => box.get(k) ?? null, setItem: (k, v) => void box.set(k, v) } }
  assert.equal(loadPref(LINE_KEY), null)
  savePref(LINE_KEY, 'QUIET')
  assert.equal(loadPref(LINE_KEY), 'QUIET')
})

test('T-332 偏好：瀏覽器禁用儲存時不出錯，當作沒記過', () => {
  const boom = () => {
    throw new Error('SecurityError')
  }
  g.window = { localStorage: { getItem: boom, setItem: boom } }
  assert.equal(loadPref(LINE_KEY), null)
  assert.doesNotThrow(() => savePref(LINE_KEY, 'CORE'))
  delete g.window
})

test('使用者 2026-10-17：首頁兩塊的展開／收起——存過就照存的，沒存過或看不懂用預設', async () => {
  const { openPref, HEAT_KEY, MOOD_KEY, NEWS_KEY, HEADS_KEY } = await import('../src/cloud/prefs.ts')
  assert.equal(openPref('1', false), true)
  assert.equal(openPref('0', true), false)
  assert.equal(openPref(null, true), true)
  assert.equal(openPref('yes', true), true)
  assert.equal(new Set([HEAT_KEY, MOOD_KEY, NEWS_KEY, HEADS_KEY]).size, 4)
})
