// 廣告版位（T-433）。沒開啟、這個位置沒設、或還沒確定登入時什麼都不畫，也不載 Google 的程式。
import { useEffect, useRef } from 'react'
import { ADSENSE, AD_FORMAT, adScriptUrl, adSlotId, type AdPlace } from './ads'

declare global {
  interface Window {
    adsbygoogle?: unknown[]
  }
}

const SCRIPT_ID = 'adsbygoogle-js'

function loadScript(client: string): void {
  if (document.getElementById(SCRIPT_ID)) return
  const s = document.createElement('script')
  s.id = SCRIPT_ID
  s.async = true
  s.src = adScriptUrl(client)
  s.crossOrigin = 'anonymous'
  document.head.appendChild(s)
}

/** 一個 AdSense 廣告單元，上方標「廣告」。 */
export function AdSlot({ place, signedIn }: { place: AdPlace; signedIn: boolean }) {
  const slot = adSlotId(ADSENSE, place, signedIn)
  // StrictMode 開發模式會跑兩次 effect；同一個 <ins> 只能要一次廣告
  const asked = useRef(false)
  useEffect(() => {
    if (!slot || asked.current) return
    asked.current = true
    loadScript(ADSENSE.client)
    ;(window.adsbygoogle = window.adsbygoogle || []).push({})
  }, [slot])
  if (!slot) return null
  return (
    <aside className="cq-ad" aria-label="廣告">
      <span className="cq-ad-label">廣告</span>
      <ins
        className="adsbygoogle"
        style={{ display: 'block' }}
        data-ad-client={ADSENSE.client}
        data-ad-slot={slot}
        data-ad-format={AD_FORMAT[place]}
        data-full-width-responsive="true"
      />
    </aside>
  )
}
