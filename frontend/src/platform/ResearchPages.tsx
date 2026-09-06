import { ColorType, LineSeries, createChart } from 'lightweight-charts'
import { useEffect, useRef, useState, type JSX } from 'react'
import { ACC, LINE, MUT, num, pct } from '../theme'
import { Panel } from '../ui'
import { IndustriesPage } from './IndustriesPage'
import { routeHref, type PlatformRoute } from './routes'
import {
  fetchBacktestArtifact,
  fetchBacktests,
  fetchModel,
  fetchModels,
  type ArtifactIssue,
  type BacktestArtifact,
  type BacktestDetail,
} from './researchApi'

type Query<T> = { data: T | null; loading: boolean; error: string | null }

function useArtifactQuery<T>(key: string, load: () => Promise<T>): Query<T> {
  const [query, setQuery] = useState<Query<T>>({ data: null, loading: true, error: null })
  useEffect(() => {
    let current = true
    setQuery({ data: null, loading: true, error: null })
    load().then(
      data => current && setQuery({ data, loading: false, error: null }),
      error => current && setQuery({ data: null, loading: false, error: error instanceof Error ? error.message : String(error) }),
    )
    return () => { current = false }
  }, [key]) // The key contains every request identity; functions are module-level API clients.
  return query
}

function QueryState({ loading, error, empty }: { loading: boolean; error: string | null; empty: string }) {
  if (loading) return <div className="platform-query-state">正在讀取保存的研究 artifact…</div>
  if (error) return <div className="platform-query-state platform-query-error">資料無法讀取：{error}</div>
  return <div className="platform-query-state">{empty}</div>
}

function StateTag({ state }: { state: 'active' | 'archived' | 'rejected' }) {
  const label = state === 'active' ? '保留中' : state === 'archived' ? '已封存' : '已淘汰'
  return <span className={`platform-state platform-state-${state}`}>{label}</span>
}

function IssueList({ issues }: { issues: ArtifactIssue[] }) {
  if (issues.length === 0) return null
  return <details className="platform-artifact-issues"><summary>有 {issues.length} 份 artifact 無法顯示</summary>
    <ul>{issues.map(issue => <li key={issue.artifact_id}><code>{issue.artifact_id}</code>：{issue.detail}</li>)}</ul>
  </details>
}

function Metadata({ label, value }: { label: string; value: string | number | null | undefined }) {
  return <div className="platform-metadata"><dt>{label}</dt><dd>{value === null || value === undefined || value === '' ? '未記錄' : value}</dd></div>
}

function ModelCenter({ route }: { route: PlatformRoute }): JSX.Element {
  const catalog = useArtifactQuery('models', fetchModels)
  const detail = useArtifactQuery(`model:${route.entityId ?? ''}`, () =>
    route.entityId ? fetchModel(route.entityId) : Promise.resolve(null),
  )
  // A recommendation run links here by model_id, so resolve that to the saved artifact.
  const requestedModel = route.query.get('model')
  const selected = detail.data?.item ?? catalog.data?.items.find(item => item.artifact_id === route.entityId)
    ?? (route.entityId ? null : catalog.data?.items.find(item => item.model_id === requestedModel)) ?? null
  return <div className="platform-research-layout">
    <Panel title="保存的模型版本" sub={catalog.data ? `${catalog.data.items.length} 份 metadata` : '讀取中'}>
      {!catalog.data ? <QueryState loading={catalog.loading} error={catalog.error} empty="目前尚未保存模型 metadata。" /> : catalog.data.items.length === 0 ? <QueryState loading={false} error={null} empty="目前尚未保存模型 metadata。" /> :
        <div className="platform-table-wrap"><table className="platform-data-table"><thead><tr><th>模型</th><th>訓練截點</th><th>OOS RankIC</th><th>狀態</th></tr></thead><tbody>
          {catalog.data.items.map(item => <tr key={item.artifact_id} className={item.artifact_id === (selected?.artifact_id ?? route.entityId) ? 'is-selected' : undefined}>
            <td><a href={routeHref('models', item.artifact_id, route.query)}>{item.model_id ?? item.artifact_id}</a></td>
            <td>{item.train_end ?? '未記錄'}</td><td>{item.oos_rank_ic == null ? '未記錄' : item.oos_rank_ic.toFixed(4)}</td><td><StateTag state={item.artifact_state} /></td>
          </tr>)}
        </tbody></table></div>}
      {catalog.data && <IssueList issues={catalog.data.issues} />}
    </Panel>
    {route.entityId && detail.error && <QueryState loading={false} error={detail.error} empty="" />}
    {selected && <Panel title="模型 metadata" sub={selected.artifact_id}>
      <div className="platform-model-note"><strong>詳細執行過程未記錄</strong><span>此頁只顯示已保存的 metadata；不會根據缺失欄位推測訓練時間、狀態或資格。</span></div>
      <dl className="platform-metadata-grid">
        <Metadata label="模型 ID" value={selected.model_id} /><Metadata label="訓練資料截止" value={selected.train_end} />
        <Metadata label="預測期間" value={selected.horizon_days == null ? null : `${selected.horizon_days} 個交易日`} /><Metadata label="標籤" value={selected.label} />
        <Metadata label="OOS RankIC" value={selected.oos_rank_ic?.toFixed(4)} /><Metadata label="artifact 指紋" value={selected.artifact_fingerprint} />
        <Metadata label="配置 hash" value={selected.config_hash} /><Metadata label="資料 hash" value={selected.data_hash} />
      </dl>
      <div className="platform-feature-list"><span>特徵欄位</span>{selected.features == null ? <em>未記錄</em> : selected.features.length === 0 ? <em>無</em> : selected.features.map(feature => <code key={feature}>{feature}</code>)}</div>
    </Panel>}
  </div>
}

function EquityChart({ rows }: { rows: BacktestDetail['equity'] }) {
  const target = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = target.current
    if (!element || rows.length === 0) return
    const chart = createChart(element, {
      width: element.clientWidth, height: 270,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: MUT, fontFamily: 'ui-monospace, "Cascadia Code", Consolas, monospace', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(35,42,51,0.45)' }, horzLines: { color: LINE } },
      rightPriceScale: { borderColor: LINE }, timeScale: { borderColor: LINE, rightOffset: 2, fixLeftEdge: true, fixRightEdge: true },
    })
    const series = chart.addSeries(LineSeries, { color: ACC, lineWidth: 2, priceLineVisible: false })
    series.setData(rows.map(row => ({ time: row.date, value: row.equity })))
    chart.timeScale().fitContent()
    const observer = new ResizeObserver(entries => chart.applyOptions({ width: entries[0].contentRect.width }))
    observer.observe(element)
    return () => { observer.disconnect(); chart.remove() }
  }, [rows])
  return <div className="platform-equity-chart" ref={target} aria-label="保存的回測權益曲線">
    {rows.length === 0 && <div className="platform-query-state">這份結果沒有保存權益曲線。</div>}
  </div>
}

function Metric({ label, value, format = 'number' }: { label: string; value: number | undefined; format?: 'number' | 'percent' }) {
  return <div className="platform-metric"><span>{label}</span><strong>{value === undefined ? '未記錄' : format === 'percent' ? pct(value) : num(value, label === '交易日' ? 0 : 2)}</strong></div>
}

function BacktestCenter({ route }: { route: PlatformRoute }): JSX.Element {
  const catalog = useArtifactQuery('backtests', fetchBacktests)
  const detail = useArtifactQuery(`backtest:${route.entityId ?? ''}`, () =>
    route.entityId ? fetchBacktestArtifact(route.entityId) : Promise.resolve(null),
  )
  const selected = detail.data?.item ?? null
  return <div className="platform-research-layout">
    <Panel title="保存的回測結果" sub={catalog.data ? `${catalog.data.items.length} 份 result bundle` : '讀取中'}>
      {!catalog.data ? <QueryState loading={catalog.loading} error={catalog.error} empty="目前沒有可顯示的保存回測結果。" /> : catalog.data.items.length === 0 ? <QueryState loading={false} error={null} empty="目前沒有可顯示的保存回測結果。" /> :
        <div className="platform-table-wrap"><table className="platform-data-table"><thead><tr><th>策略</th><th>期間</th><th>CAGR</th><th>Sharpe</th><th>狀態</th></tr></thead><tbody>
          {catalog.data.items.map(item => <BacktestRow key={item.artifact_id} item={item} route={route} />)}
        </tbody></table></div>}
      {catalog.data && <IssueList issues={catalog.data.issues} />}
    </Panel>
    {route.entityId && detail.error && <QueryState loading={false} error={detail.error} empty="" />}
    {selected && <Panel title="回測結果" sub={selected.artifact_id}>
      <div className="platform-metric-grid"><Metric label="CAGR" value={selected.metrics.cagr} format="percent" /><Metric label="Sharpe" value={selected.metrics.sharpe} />
        <Metric label="最大回撤" value={selected.metrics.mdd} format="percent" /><Metric label="交易日" value={selected.metrics.n_days} /></div>
      <div className="platform-chart-heading"><div><h3>權益曲線</h3><p>直接讀取結果 bundle 內保存的 equity；不在頁面重新計算報酬或指標。</p></div><span>{selected.start ?? '未記錄'} → {selected.end ?? '未記錄'}</span></div>
      <EquityChart rows={selected.equity} />
      <dl className="platform-metadata-grid platform-backtest-metadata"><Metadata label="資料來源" value={selected.source} /><Metadata label="資料指紋" value={selected.data_fingerprint} />
        <Metadata label="初始資金" value={selected.initial_capital == null ? null : `${num(selected.initial_capital)} TWD`} /><Metadata label="最大參與率" value={selected.max_participation == null ? null : pct(selected.max_participation)} /></dl>
      <FinalHoldings rows={selected.final_holdings} />
    </Panel>}
  </div>
}

function BacktestRow({ item, route }: { item: BacktestArtifact; route: PlatformRoute }) {
  return <tr className={item.artifact_id === route.entityId ? 'is-selected' : undefined}><td><a href={routeHref('backtests', item.artifact_id, route.query)}>{item.strategy_name ?? item.artifact_id}</a></td>
    <td>{item.start ?? '未記錄'} → {item.end ?? '未記錄'}</td><td>{item.metrics.cagr === undefined ? '未記錄' : pct(item.metrics.cagr)}</td><td>{item.metrics.sharpe === undefined ? '未記錄' : num(item.metrics.sharpe, 2)}</td><td><StateTag state={item.artifact_state} /></td></tr>
}

function FinalHoldings({ rows }: { rows: BacktestDetail['final_holdings'] }) {
  if (rows == null) return <div className="platform-holdings-note">最終持倉未保存在這份 result bundle。</div>
  return <div className="platform-table-wrap platform-holdings"><h3>最終持倉</h3>{rows.length === 0 ? <div className="platform-query-state">保存結果的最終持倉為空。</div> : <table className="platform-data-table"><thead><tr><th>股票</th><th>股數</th><th>市值</th><th>權重</th></tr></thead><tbody>{rows.map(row => <tr key={row.stock_id}><td>{row.stock_id}</td><td>{row.shares === undefined ? '未記錄' : num(row.shares)}</td><td>{row.value === undefined ? '未記錄' : `${num(row.value)} TWD`}</td><td>{row.weight === undefined ? '未記錄' : pct(row.weight)}</td></tr>)}</tbody></table>}</div>
}

/** Render the implemented artifact-driven model and backtest research destinations. */
export function ResearchPages({ route }: { route: PlatformRoute }): JSX.Element {
  if (route.page === 'models') return <ModelCenter route={route} />
  if (route.page === 'backtests') return <BacktestCenter route={route} />
  if (route.page === 'industries') return <IndustriesPage route={route} />
  return <div className="platform-query-state">這個研究頁面尚未接入資料。</div>
}
