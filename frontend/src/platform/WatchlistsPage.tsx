import { useEffect, useState, type JSX } from 'react'
import { apiUrl } from '../api'
import { Panel } from '../ui'
import { ruleVerdict, type ExitCheckResponse, type ExitStock } from './exitCheck'
import { StockName } from './RecommendationsPage'
import { routeHref, type PlatformRoute } from './routes'
import {
  readWatchlist,
  setEntryPrice,
  setNote,
  toggleWatch,
  writeWatchlist,
  type WatchEntry,
  type WatchState,
} from './watchlist'

/**
 * Local watchlist with research notes.
 *
 * Storage stays in this browser under the existing key. Server-side, cross-account watchlists
 * are a later version and are never presented here as if they already worked.
 */
export function WatchlistsPage({ route }: { route: PlatformRoute }): JSX.Element {
  const [state, setState] = useState<WatchState>(() => readWatchlist())
  const [writeError, setWriteError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  useEffect(() => { setState(readWatchlist()) }, [])
  const commit = (entries: WatchEntry[]) => {
    // A corrupt store is never overwritten by an edit; the user must reset it deliberately.
    setWriteError(writeWatchlist(entries))
    setState({ entries, status: 'ok', detail: null })
  }
  const blocked = state.status === 'corrupt'
  // 出場檢查只送股票代號；買進價留在本機，停損在這裡算（D-116）。
  const ids = state.entries.map(entry => entry.stock_id).join(',')
  const [checks, setChecks] = useState<Record<string, ExitStock>>({})
  const [checkError, setCheckError] = useState<string | null>(null)
  useEffect(() => {
    if (!ids) { setChecks({}); return }
    let live = true
    fetch(`${apiUrl('/api/platform/exit-check')}?${new URLSearchParams({ ids })}`, { headers: { accept: 'application/json' } })
      .then(response => response.ok ? response.json() as Promise<ExitCheckResponse> : Promise.reject(new Error(`HTTP ${response.status}`)))
      .then(body => { if (live) { setChecks(Object.fromEntries(body.stocks.map(stock => [stock.stock_id, stock]))); setCheckError(null) } })
      .catch((error: Error) => { if (live) setCheckError(`出場檢查讀取失敗（${error.message}）——下方不代表沒有賣出訊號`) })
    return () => { live = false }
  }, [ids])
  const add = () => {
    const stock = draft.trim()
    if (!stock) return
    commit(toggleWatch(state.entries, stock, route.query.get('run')))
    setDraft('')
  }
  return <Panel title="我的追蹤" sub={`${state.entries.length} 檔 · 只存在這台瀏覽器`}>
    {state.detail && <div className={`platform-model-note${blocked ? ' platform-store-blocked' : ''}`}>
      <strong>{blocked ? '自選資料無法讀取' : '讀到舊版自選格式'}</strong><span>{state.detail}</span>
    </div>}
    {writeError && <div className="platform-query-state platform-query-error">{writeError}</div>}
    {checkError && <div className="platform-query-state platform-query-error">{checkError}</div>}
    {blocked ? <div className="platform-actions">
      <button type="button" onClick={() => commit([])}>清空並重建自選（會覆寫損毀資料）</button>
    </div> : <>
      <div className="platform-filter-row"><label>加入追蹤
        <input aria-label="加入追蹤的股票代號" value={draft} placeholder="股票代號"
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter') add() }} />
      </label><button type="button" onClick={add}>加入</button></div>
      {state.entries.length === 0
        ? <div className="platform-query-state">還沒有追蹤任何股票。這份清單只存在這台瀏覽器，換裝置不會同步。</div>
        : <div className="platform-table-wrap"><table className="platform-data-table">
          <thead><tr><th>股票</th><th>收盤</th><th>買進價</th><th>出場檢查（E 系列）</th><th>研究筆記</th><th>來源 run</th><th /></tr></thead>
          <tbody>{state.entries.map(entry => <tr key={entry.stock_id}>
            <td><a href={routeHref('stocks', entry.stock_id, route.query)}>{entry.stock_id}</a><StockName stockId={entry.stock_id} /></td>
            <td>{checks[entry.stock_id]?.close ?? '—'}<small className="platform-exit-date">{checks[entry.stock_id]?.date ?? ''}</small></td>
            <td><input className="platform-price-input" type="number" min="0" step="0.01" inputMode="decimal"
              aria-label={`${entry.stock_id} 的買進價`} value={entry.entry_price ?? ''} placeholder="未填"
              onChange={event => { const value = Number(event.target.value); commit(setEntryPrice(state.entries, entry.stock_id, event.target.value !== '' && value > 0 ? value : null)) }} /></td>
            <td><ExitChips stock={checks[entry.stock_id]} entryPrice={entry.entry_price} /></td>
            <td><input className="platform-note-input" aria-label={`${entry.stock_id} 的研究筆記`} value={entry.note}
              onChange={event => commit(setNote(state.entries, entry.stock_id, event.target.value))} /></td>
            <td>{entry.run_id ?? '未記錄'}</td>
            <td><button type="button" onClick={() => commit(toggleWatch(state.entries, entry.stock_id))}>移除</button></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="platform-source-note">
        <strong>RESEARCH ONLY · 出場規則尚未通過送檢。</strong>
        紅＝該規則今天收盤已觸發（回測語意是隔日開盤賣出）；灰＝無法判斷，不代表沒事；綠＝未觸發。
        買進價只存在這台瀏覽器，不會送到伺服器；停損用它在本機計算。
      </p>
      <p className="platform-source-note">
        自選與筆記存在本機 localStorage；跨帳號同步與公開會員功能尚未實作，這裡不代表已保存到伺服器。
      </p>
    </>}
  </Panel>
}

/** One chip per registered exit rule; an unknown verdict is drawn differently from a clear one. */
function ExitChips({ stock, entryPrice }: { stock: ExitStock | undefined; entryPrice: number | null }): JSX.Element {
  if (!stock) return <span className="platform-exit-chip platform-exit-unknown">尚未取得</span>
  return <span className="platform-exit-chips">{stock.rules.map(rule => {
    const verdict = ruleVerdict(rule, stock.close, entryPrice)
    return <span key={rule.code} className={`platform-exit-chip platform-exit-${verdict.state}`}
      title={`${rule.label}：${verdict.detail}`}>{rule.code} {rule.label}<small>{verdict.detail}</small></span>
  })}</span>
}
