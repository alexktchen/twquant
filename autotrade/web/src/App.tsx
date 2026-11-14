// 程式交易的遙控網站（autotrade A-018／D-230）：從 twquant 網站拉出來，自己一個網站、自己一道門。
// 只有一頁：程式交易的狀態、遠端緊急停止、遙控按鈕。登入由 Cloudflare Access 擋在最前面。
import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchMe } from './api'
import { AutotradeView } from './AutotradeView'
import './base.css'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function App() {
  const [refresh, setRefresh] = useState(0)
  const [who, setWho] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    let gone = false
    fetchMe()
      .then((me) => !gone && setWho(me.email))
      .catch((e) => !gone && setProblem(errText(e)))
    return () => {
      gone = true
    }
  }, [])

  return (
    <div className="cq">
      <header className="cq-top">
        <b className="cq-brand">程式交易</b>
        {who && <span className="cq-who">{who}</span>}
        <button className="cq-reload" onClick={() => setRefresh((n) => n + 1)} aria-label="重新整理">
          <RefreshCw size={18} aria-hidden="true" />
        </button>
      </header>
      <main className="cq-fu">
        {problem === null ? (
          <AutotradeView refresh={refresh} />
        ) : (
          // 沒放行就不顯示任何按鈕：把原因講清楚，讓人重新整理（重新登入）
          <div className="cq-gate" role="alert">
            <b>進不去</b>
            <span>{problem}</span>
            <button onClick={() => window.location.reload()}>重新整理</button>
          </div>
        )}
      </main>
    </div>
  )
}
