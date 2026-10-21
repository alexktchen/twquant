import assert from 'node:assert/strict'
import test from 'node:test'
import { galaxyFromUrl, tabFromUrl, withoutTab, withoutView } from '../src/cloud/tabs.ts'

test('T-354／T-379：族群資金通知帶 ?tab=group，盤勢併進首頁後點開到首頁；不認得的不理', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=group'), 'home')
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=home'), 'home')
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=score&x=1'), 'score')
  assert.equal(tabFromUrl('https://twquant.pages.dev/'), null)
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=admin'), null)
  assert.equal(tabFromUrl('不是網址'), null)
})

test('T-354：用過的 ?tab= 從網址拿掉，其他參數與 hash 留著', () => {
  assert.equal(withoutTab('https://twquant.pages.dev/?tab=group'), '/')
  assert.equal(withoutTab('https://twquant.pages.dev/x?tab=group&a=1#k'), '/x?a=1#k')
})

test('2026-10-21：?view=galaxy 直接開星雲圖，用過就從網址拿掉（tab 與其他參數保留）', () => {
  assert.equal(galaxyFromUrl('https://twquant.pages.dev/?tab=news&view=galaxy'), true)
  assert.equal(galaxyFromUrl('https://twquant.pages.dev/?tab=news'), false)
  assert.equal(galaxyFromUrl('https://twquant.pages.dev/?view=list'), false)
  assert.equal(withoutView('https://twquant.pages.dev/?tab=news&view=galaxy#x'), '/?tab=news#x')
})
