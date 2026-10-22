// 廣告（T-433／D-210）。發布商 ID 空白＝整個關閉：不載 Google 的程式、不畫版位、建置也不產生 ads.txt。
// 開啟前先看 D-210 的前置條件：自己的網域、AdSense 審核、投顧法風險、Cloudflare Access 免費 50 人上限；
// 開啟時隱私權政策「廣告」一節的「尚未開啟」要一起拿掉（tests/cloud-ads.test.ts 守著）。

export type AdPlace = 'home' | 'news'
export type AdsConfig = { client: string; slots: Record<AdPlace, string> }

/** AdSense 設定：client＝發布商 ID（ca-pub-16 位數字），slots＝各位置的廣告單元 ID（AdSense 後台建立）。 */
export const ADSENSE: AdsConfig = {
  client: '',
  slots: { home: '', news: '' },
}

/** 各位置的廣告形狀：首頁在所有卡片之後、新聞在清單最後，都交給 AdSense 依寬度決定。 */
export const AD_FORMAT: Record<AdPlace, 'auto' | 'horizontal'> = { home: 'auto', news: 'auto' }

/** Google 規定 ads.txt 的認證機構 ID（每個 AdSense 帳號都一樣）。 */
const GOOGLE_CERT_ID = 'f08c47fec0942fa0'

function checkClient(cfg: AdsConfig): void {
  if (cfg.client === '') {
    const set = Object.entries(cfg.slots).filter(([, v]) => v !== '')
    if (set.length) throw new Error(`廣告設定：有版位（${set.map(([k]) => k).join('、')}）卻沒有發布商 ID`)
    return
  }
  if (!/^ca-pub-\d{16}$/.test(cfg.client)) throw new Error(`廣告設定：發布商 ID 要是 ca-pub-加 16 位數字，拿到「${cfg.client}」`)
}

/** 這個位置要放的廣告單元 ID；不放就是空字串（沒開啟、沒設這個位置、或還沒確定登入）。
 *  signedIn 要等 /api/me 回來才是 true：網站一打開時還不知道有沒有登入，那段時間也不載 Google 的程式。 */
export function adSlotId(cfg: AdsConfig, place: AdPlace, signedIn: boolean): string {
  checkClient(cfg)
  const slot = cfg.slots[place]
  if (slot !== '' && !/^\d+$/.test(slot)) throw new Error(`廣告設定：位置 ${place} 的廣告單元 ID 要是數字，拿到「${slot}」`)
  // 沒登入的畫面只有模糊假資料與登入卡片，AdSense 不准在沒有內容的畫面放廣告
  if (cfg.client === '' || !signedIn) return ''
  return slot
}

/** 建置時要多產生的 ads.txt；沒開啟回 null。 */
export function adsTxtAsset(cfg: AdsConfig): { fileName: string; source: string } | null {
  checkClient(cfg)
  if (cfg.client === '') return null
  return { fileName: 'ads.txt', source: `google.com, ${cfg.client.replace(/^ca-/, '')}, DIRECT, ${GOOGLE_CERT_ID}\n` }
}

/** AdSense 程式的網址。 */
export const adScriptUrl = (client: string): string =>
  `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(client)}`
