import assert from 'node:assert/strict'
import test from 'node:test'
import { tabFromUrl, withoutTab } from '../src/cloud/tabs.ts'

test('T-354：族群資金通知帶 ?tab=group，點開直接到族群分頁；不認得的不理', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=group'), 'group')
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=score&x=1'), 'score')
  assert.equal(tabFromUrl('https://twquant.pages.dev/'), null)
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=admin'), null)
  assert.equal(tabFromUrl('不是網址'), null)
})

test('T-354：用過的 ?tab= 從網址拿掉，其他參數與 hash 留著', () => {
  assert.equal(withoutTab('https://twquant.pages.dev/?tab=group'), '/')
  assert.equal(withoutTab('https://twquant.pages.dev/x?tab=group&a=1#k'), '/x?a=1#k')
})
