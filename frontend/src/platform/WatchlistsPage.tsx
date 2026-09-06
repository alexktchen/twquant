import { useEffect, useState, type JSX } from 'react'
import { Panel } from '../ui'
import { routeHref, type PlatformRoute } from './routes'
import {
  readWatchlist,
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
          <thead><tr><th>股票</th><th>研究筆記</th><th>來源 run</th><th /></tr></thead>
          <tbody>{state.entries.map(entry => <tr key={entry.stock_id}>
            <td><a href={routeHref('stocks', entry.stock_id, route.query)}>{entry.stock_id}</a></td>
            <td><input className="platform-note-input" aria-label={`${entry.stock_id} 的研究筆記`} value={entry.note}
              onChange={event => commit(setNote(state.entries, entry.stock_id, event.target.value))} /></td>
            <td>{entry.run_id ?? '未記錄'}</td>
            <td><button type="button" onClick={() => commit(toggleWatch(state.entries, entry.stock_id))}>移除</button></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="platform-source-note">
        自選與筆記存在本機 localStorage；跨帳號同步與公開會員功能尚未實作，這裡不代表已保存到伺服器。
      </p>
    </>}
  </Panel>
}
