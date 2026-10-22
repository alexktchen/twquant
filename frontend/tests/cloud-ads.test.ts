import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { ADSENSE, AD_FORMAT, adSlotId, adsTxtAsset, type AdsConfig } from '../src/cloud/ads.ts'

// T-433／D-210：廣告位先做好、預設關閉。發布商 ID 空白＝不載 Google 的程式、不畫版位、不產生 ads.txt。
const read = (p: string): string => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const ON: AdsConfig = { client: 'ca-pub-1234567890123456', slots: { home: '1111111111', news: '2222222222' } }

test('T-433 AC1 預設關閉：版本庫裡的設定沒有發布商 ID，任何位置都不畫廣告、不產生 ads.txt', () => {
  assert.equal(ADSENSE.client, '', '開啟要先過 D-210 的前置條件，不准在這個任務裡填')
  for (const place of ['home', 'news'] as const) assert.equal(adSlotId(ADSENSE, place, true), '')
  assert.equal(adsTxtAsset(ADSENSE), null)
})

test('T-433 AC2 開啟後：確定登入的人才看到；沒設版位的位置不畫', () => {
  assert.equal(adSlotId(ON, 'home', true), '1111111111')
  assert.equal(adSlotId(ON, 'news', true), '2222222222')
  // 沒登入只有模糊假名單與登入卡片，屬於「沒有內容的畫面」，AdSense 政策不准放；
  // 剛打開還不知道有沒有登入時也算沒登入，否則訪客也會先載到 Google 的程式（建置後實測抓到過）
  assert.equal(adSlotId(ON, 'home', false), '')
  assert.equal(adSlotId(ON, 'news', false), '')
  assert.equal(adSlotId({ ...ON, slots: { ...ON.slots, news: '' } }, 'news', true), '')
})

test('T-433 AC3 格式不對就丟錯，不默默關掉', () => {
  assert.throws(() => adSlotId({ ...ON, client: 'pub-1234567890123456' }, 'home', true), /ca-pub-/)
  assert.throws(() => adSlotId({ ...ON, client: 'ca-pub-123' }, 'home', true), /ca-pub-/)
  assert.throws(() => adSlotId({ ...ON, slots: { ...ON.slots, home: 'abc' } }, 'home', true), /home/)
  assert.throws(() => adSlotId({ client: '', slots: { home: '1111111111', news: '' } }, 'home', true), /發布商/)
})

test('T-433 AC4 ads.txt：開啟後建置時產生，內容是 Google 規定的一行', () => {
  assert.deepEqual(adsTxtAsset(ON), {
    fileName: 'ads.txt',
    source: 'google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n',
  })
  assert.match(read('vite.config.ts'), /adsTxtAsset\(ADSENSE\)/, 'vite 建置要呼叫它')
})

test('T-433 AC5 版位：首頁戰情室與新聞清單各一個，不放在登入卡片、也不放在只給 admin 的每日名單', () => {
  assert.match(read('src/cloud/WarRoom.tsx'), /<AdSlot place="home" signedIn=\{signedIn\}/)
  assert.match(read('src/cloud/NewsPage.tsx'), /<AdSlot place="news" signedIn=\{signedIn\}/)
  const app = read('src/cloud/CloudApp.tsx')
  // 確定登入＝/api/me 回來了（email 有值），不是「還沒被判成訪客」
  assert.equal((app.match(/signedIn=\{email !== null\}/g) ?? []).length, 2, '戰情室與新聞頁')
  assert.ok(!app.includes('<AdSlot'), '每日名單（D-208 只給 admin）與登入卡片都不放')
  const slot = read('src/cloud/AdSlot.tsx')
  assert.match(slot, /廣告/, '版位要標「廣告」')
  assert.match(slot, /data-ad-format=\{AD_FORMAT\[place\]\}/)
  assert.deepEqual(AD_FORMAT, { home: 'auto', news: 'auto' })
})

test('T-433 AC6 隱私權政策寫明廣告 Cookie 與停用方式；開啟後不能還寫「尚未開啟」', () => {
  const privacy = read('public/privacy.html')
  assert.match(privacy, /<h2>廣告<\/h2>/)
  assert.match(privacy, /Google AdSense/)
  assert.match(privacy, /Cookie/)
  assert.match(privacy, /https:\/\/adssettings\.google\.com/)
  assert.doesNotMatch(privacy, /不使用分析工具、廣告/, '舊句子說不用廣告，開啟後會變成假話')
  assert.equal(privacy.includes('尚未開啟'), ADSENSE.client === '', '開啟廣告時要一起改隱私權政策')
})
