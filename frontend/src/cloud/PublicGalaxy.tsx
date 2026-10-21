// 公開星雲圖（使用者 2026-10-21：「我想要不登入」）：https://twquant.pages.dev/api/prototype/galaxy 不用登入就能看。
// 那個網址在 Cloudflare Access 開了例外（只有這一條路徑），頁面與資料都從同一個網址拿（?format=json），
// 所以不會連帶公開新聞清單、名單或任何其他 /api。只有星雲圖本身：沒有名單、沒有評分，不放風險聲明閘門。
// 點股票 → 開網站首頁（其他內容照樣要登入）。
import { NewsGalaxy } from './NewsGalaxy'
import type { GalaxyData } from './news-galaxy'

export const PUBLIC_GALAXY_PATH = '/api/prototype/galaxy'

async function loadPublic(): Promise<GalaxyData> {
  const r = await fetch(`${PUBLIC_GALAXY_PATH}?format=json`, { headers: { accept: 'application/json' } })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as GalaxyData
}

export default function PublicGalaxy() {
  return (
    <main className="cq-public-galaxy">
      <header className="cq-public-galaxy-head">
        <a href="/" className="cq-public-galaxy-brand">twquant</a>
        <span>新聞全景</span>
      </header>
      <NewsGalaxy standalone load={loadPublic} onStock={() => window.location.assign('/?tab=news')} />
    </main>
  )
}
