import assert from 'node:assert/strict'
import test from 'node:test'
import { GALAXY_PATH, galaxyFromUrl, guestTab, tabFromUrl, withoutTab, withoutView } from '../src/cloud/tabs.ts'

test('T-354／T-431：族群資金通知帶 ?tab=group，點開到「盤勢」分頁；不認得的不理', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=group'), 'group')
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

test('T-445 AC1 專屬連結 /galaxy：沒登入也落在新聞頁、一打開就是全景；用過的參數拿掉但路徑留著（複製網址還是這個連結）；明寫 ?tab= 照寫的', () => {
  const href = `https://twquant.pages.dev${GALAXY_PATH}`
  assert.equal(GALAXY_PATH, '/galaxy')
  assert.equal(tabFromUrl(href), 'news')
  const explicit = tabFromUrl(href)
  assert.equal(guestTab(explicit ?? 'home', explicit), 'news', '同 CloudApp：一開始的分頁＝網址指定的，沒登入也不改')
  assert.equal(galaxyFromUrl(href), true)
  assert.equal(galaxyFromUrl(`${href}?utm_source=line`), true)
  assert.equal(withoutView(`${href}?view=galaxy&utm_source=line`), '/galaxy?utm_source=line')
  assert.equal(tabFromUrl(`${href}?tab=home`), 'home')
  assert.equal(tabFromUrl('https://twquant.pages.dev/galaxyx'), null)
  assert.equal(galaxyFromUrl('https://twquant.pages.dev/'), false)
})
