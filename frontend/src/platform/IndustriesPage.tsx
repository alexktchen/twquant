import { useEffect, useState, type JSX } from 'react'
import { fetchSectorFlows, fetchStockMeta, type SectorFlows, type StockMetaMap } from '../api'
import { num } from '../theme'
import { Panel } from '../ui'
import { coverageNote, stocksInIndustry } from './industry'
import { routeHref, type PlatformRoute } from './routes'

type Load<T> = { data: T | null; error: string | null }

function useLoad<T>(key: string, load: () => Promise<T>): Load<T> {
  const [state, setState] = useState<Load<T>>({ data: null, error: null })
  useEffect(() => {
    let alive = true
    setState({ data: null, error: null })
    load().then(
      data => alive && setState({ data, error: null }),
      error => alive && setState({ data: null, error: error instanceof Error ? error.message : String(error) }),
    )
    return () => { alive = false }
  }, [key]) // key carries every request input; load is a module-level API client.
  return state
}

const WINDOWS = [5, 20, 60]

/** Present the sector-flow API as it is: its own window, data date and coverage, no narrative. */
export function IndustriesPage({ route }: { route: PlatformRoute }): JSX.Element {
  const window = Number(route.query.get('window')) || 20
  const selected = route.query.get('industry')
  const flows = useLoad<SectorFlows>(`flows:${window}`, () => fetchSectorFlows(window))
  const meta = useLoad<StockMetaMap>('stock-meta', fetchStockMeta)
  const href = (patch: Record<string, string | null>) => {
    const query = new URLSearchParams(route.query)
    for (const [key, value] of Object.entries(patch)) value ? query.set(key, value) : query.delete(key)
    return routeHref('industries', undefined, query)
  }
  const sectors = flows.data?.sectors ?? []
  const members = selected && meta.data ? stocksInIndustry(meta.data, selected) : []
  const reported = sectors.find(s => s.industry === selected)?.n ?? 0
  return <div className="platform-research-layout">
    <Panel title="產業資金流向" sub={flows.data ? `窗口 ${flows.data.window} 交易日 · 資料日 ${flows.data.as_of ?? '未記錄'}` : '讀取中'}>
      <div className="platform-filter-row"><span className="platform-bucket-filter">統計窗口
        {WINDOWS.map(size => <a key={size} href={href({ window: String(size) })} aria-current={window === size ? 'true' : undefined}>{size} 日</a>)}
      </span></div>
      {flows.error ? <div className="platform-query-state platform-query-error">產業資料無法讀取：{flows.error}</div>
        : !flows.data ? <div className="platform-query-state">讀取產業資金流向…</div>
        : sectors.length === 0 ? <div className="platform-query-state">這個窗口沒有任何產業資料（零筆，不是讀取失敗）。</div>
        : <>
          <div className="platform-table-wrap"><table className="platform-data-table">
            <thead><tr><th>產業</th><th>檔數</th><th>法人淨流入(億)</th><th>外資(億)</th><th>投信(億)</th><th>報酬</th><th>成交值(億)</th></tr></thead>
            <tbody>{sectors.map(sector => <tr key={sector.industry} className={sector.industry === selected ? 'is-selected' : undefined}>
              <td><a href={href({ industry: sector.industry })}>{sector.industry}</a></td>
              <td>{sector.n}</td><td>{num(sector.net_yi, 2)}</td><td>{num(sector.frgn_yi, 2)}</td>
              <td>{num(sector.trust_yi, 2)}</td><td>{sector.ret_pct.toFixed(2)}%</td><td>{num(sector.amt_yi, 2)}</td>
            </tr>)}</tbody>
          </table></div>
          <p className="platform-source-note">
            數字直接來自 <code>/api/sector-flows</code> 的 {flows.data.window} 交易日窗口彙總，頁面不另做成長或消息面的判讀。
          </p>
        </>}
    </Panel>
    <Panel title={selected ? `${selected} · 目前分類成分` : '產業成分'} sub={selected ? coverageNote(reported, members.length) : undefined}>
      {!selected ? <div className="platform-query-state">選擇左側任一產業，查看目前分類下的股票。</div>
        : meta.error ? <div className="platform-query-state platform-query-error">分類表無法讀取：{meta.error}</div>
        : !meta.data ? <div className="platform-query-state">讀取分類表…</div>
        : members.length === 0 ? <div className="platform-query-state">目前分類表沒有屬於「{selected}」的股票，因此無法列出成分。</div>
        : <>
          <div className="platform-model-note"><strong>這是目前分類，不是歷史 PIT 分類</strong>
            <span>stock_meta 只保存當前產業別；查看歷史日期時不代表該股當時屬於這個產業。</span></div>
          <div className="platform-table-wrap"><table className="platform-data-table">
            <thead><tr><th>股票</th><th>名稱</th></tr></thead>
            <tbody>{members.map(stock => <tr key={stock.stock_id}>
              <td><a href={routeHref('stocks', stock.stock_id, route.query)}>{stock.stock_id}</a></td>
              <td>{stock.name ?? '未記錄'}</td>
            </tr>)}</tbody>
          </table></div>
        </>}
    </Panel>
  </div>
}
