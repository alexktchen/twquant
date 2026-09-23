import { useEffect, useState, type JSX } from 'react'
import { routeHref, type PlatformRoute } from './routes'
import { apiUrl } from '../api'
import { nameOf, useStockNames } from './stockNames'
import { Panel } from '../ui'
import { DayNav } from './DayNav'

type Insight = {
  insight_run_id: string
  stance: 'support' | 'neutral' | 'conflict' | 'insufficient_data' | null
  recommended_bucket: 'select' | 'watch' | 'exclude' | null
  absent_reason: string | null
  support_n: number
  contradict_n: number
  gap_n: number
}
type Candidate = { stock_id: string; rank: number; score: number; bucket: string; reason_ref: string | null; insight_run_id: string | null; data_status: string; insight: Insight | null }

const STANCE_LABEL: Record<NonNullable<Insight['stance']>, string> = {
  support: '支持', neutral: '中性', conflict: '矛盾', insufficient_data: '資料不足',
}
const BUCKET_LABEL: Record<NonNullable<Insight['recommended_bucket']>, string> = {
  select: '精選', watch: '觀察', exclude: '排除',
}

/** The model's reading of the evidence. Its bucket is a suggestion, never the final class. */
function InsightCell({ insight }: { insight: Insight | null }) {
  if (insight === null) return <span className="platform-muted">尚未分析</span>
  if (insight.stance === null) {
    return <span className="platform-muted" title={insight.absent_reason ?? ''}>無分析（{insight.absent_reason ?? '未記錄原因'}）</span>
  }
  return <span>
    {STANCE_LABEL[insight.stance]}
    <span className="platform-evidence-counts"> 支持 {insight.support_n}／反對 {insight.contradict_n}／缺口 {insight.gap_n}</span>
    {insight.recommended_bucket && <span className="platform-suggestion">LLM 提議：{BUCKET_LABEL[insight.recommended_bucket]}</span>}
  </span>
}
type Measurement = { label: string; rank_ic: number; hit_rate: number; n_days: number; period_start: string; period_end: string; measured_on: string; source: string }
type ModelInfo = { name: string; role: string; description: string; caveat: string; hit_rate_note: string; artifact_fingerprint: string; run_fingerprint: string; stale: boolean; measurements: Measurement[] }
type Lane = 'production' | 'research_live' | null
type Run = { run_id: string; model_id: string; as_of: string; generated_at: string; published_at: string | null; mode: 'research' | 'reconstructed'; artifact_fingerprint: string; candidates: Candidate[]; candidate_total: number; candidate_total_all: number; bucket_counts: Record<string, number>; model_info?: ModelInfo | null; lane?: Lane }
type Query = { date: string | null; model: string | null; run: string | null; bucket: string | null; q: string | null; page: number; page_size: number }
/** T-301：有快照的模型與日期（新到舊），以及伺服器補齊的模型／日期。舊 API 沒有這兩塊。 */
type AvailableModel = { model_id: string; name: string | null; role: string | null; lane: Lane; dates: string[] }
type Resolved = { model: string | null; date: string | null; model_defaulted: boolean; date_defaulted: boolean }
type Response = { runs: Run[]; issues: { run_id: string; detail: string }[]; query: Query; available?: { models: AvailableModel[] }; resolved?: Resolved }

/** D-111：畫面預設只顯示晉級前段；`all` 是「顯示全部」的明示值，不是缺省。 */
const DEFAULT_BUCKET = 'ml_top20'
const ALL_BUCKETS = 'all'
/** 分組的人話名稱。代號是資料，畫面上要講人看得懂的。 */
const CANDIDATE_BUCKET_LABEL: Record<string, string> = {
  ml_top20: '前 20 名',
  ml_watch: '觀察區（21~50）',
  ml_candidate: '其餘候選',
  ml_risk_excluded: '風控剔除',
  ml_liquidity_excluded: '流動性剔除',
}
const bucketLabel = (bucket: string): string => CANDIDATE_BUCKET_LABEL[bucket] ?? bucket
/** 固定順序：畫面上的分類列不該隨當頁剛好載到哪些分組而跳動。 */
const BUCKET_ORDER = ['ml_top20', 'ml_watch', 'ml_candidate', 'ml_risk_excluded', 'ml_liquidity_excluded'] as const

/** Filter keys the daily list keeps in the URL so a shared link restores the same view. */
export const recommendationFilters = ['date', 'model', 'bucket', 'q', 'run'] as const

async function fetchRuns(route: PlatformRoute): Promise<Response> {
  const params = new URLSearchParams()
  for (const key of recommendationFilters) { const value = route.query.get(key); if (value) params.set(key, value) }
  // 沒指定分類時預設只要前段：在此之前每天回整個宇宙，畫面上看起來像「推薦 200 檔」，
  // 那其實是 page_size 而不是模型選的（D-111）。`all` 才是要看全部。
  const bucket = route.query.get('bucket')
  if (!bucket) params.set('bucket', DEFAULT_BUCKET)
  else if (bucket === ALL_BUCKETS) params.delete('bucket')
  // T-301：沒指定的模型與日期由伺服器補成「生產模型最新一天」，不再把每一天的 run 疊成一頁。
  params.set('resolve', '1')
  const response = await fetch(`${apiUrl('/api/platform/recommendations')}?${params}`, { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`/api/platform/recommendations → HTTP ${response.status}`)
  return response.json() as Promise<Response>
}

function href(route: PlatformRoute, patch: Record<string, string | null>, page = route.page): string {
  const query = new URLSearchParams(route.query)
  for (const [key, value] of Object.entries(patch)) value ? query.set(key, value) : query.delete(key)
  return routeHref(page as 'overview' | 'recommendations', undefined, query)
}

/** Show the stock's Chinese name beside its code; absent names simply do not render. */
export function StockName({ stockId }: { stockId: string }) {
  const name = nameOf(useStockNames(), stockId)
  return name ? <span className="platform-stock-name">{name}</span> : null
}

/** 模型角色的人話名稱；代號仍是身分，這裡只是註解（D-122）。 */
const MODEL_ROLE_LABEL: Record<string, string> = {
  ranking: '排序（生產）', risk_gate: '風控閘門', aux: '輔助（策略池）', retired: '已換下',
}
const signed = (value: number): string => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(4)}`

/** 模型選單上的名字：目錄有登記就用人話名稱，否則照實給代號。 */
function modelLabel(model: AvailableModel): string {
  const lane = model.lane === 'production' ? '生產排序' : model.lane === 'research_live' ? '研究模型' : model.role ? MODEL_ROLE_LABEL[model.role] ?? model.role : '未登記'
  const name = model.name ?? model.model_id
  // 目錄名稱有時已經寫了角色（如「短窗口排序模型（已換下）」），不重複。
  return name.includes(lane) ? name : `${name}（${lane}）`
}

/** 模型的人話名稱、描述與勝率。數字全部來自 configs/model_catalog.yaml，這裡不算任何東西。 */
function ModelCard({ info }: { info: ModelInfo | null }) {
  if (info === null) return <p className="platform-model-card platform-muted">
    這個模型還沒有登記說明（configs/model_catalog.yaml）；卡片不借用其他模型的名稱或數字。
  </p>
  return <div className="platform-model-card">
    <p className="platform-model-headline">
      <strong>{info.name}</strong><span className="platform-model-role">{MODEL_ROLE_LABEL[info.role] ?? info.role}</span>
    </p>
    <p className="platform-model-desc">{info.description}</p>
    {/* 指紋不符＝模型重訓過而登記的數字還是舊的。寧可少一塊資訊，不要一塊看不出過期的資訊。 */}
    {info.stale ? <p className="platform-model-stale">
      登記的數字對應的是另一版模型（登記 {info.artifact_fingerprint}、這份快照 {info.run_fingerprint}），已隱藏勝率而不是照舊顯示。
    </p> : <ul className="platform-model-metrics">{info.measurements.map(m => <li key={m.label}>
      <span className="platform-model-metric-label">{m.label}</span>
      <strong>IC 勝率 {(m.hit_rate * 100).toFixed(1)}%</strong>
      <span>RankIC {signed(m.rank_ic)}</span>
      <span className="platform-muted">{m.n_days} 個訊號日 · {m.period_start} ~ {m.period_end} · {m.measured_on} 以 {m.source} 量測</span>
    </li>)}</ul>}
    {/* 口徑與數字同框：86% 少了這一句會被讀成「買了八成六會賺」（D-061）。 */}
    {!info.stale && info.measurements.length > 0 && <p className="platform-model-note">{info.hit_rate_note}</p>}
    {info.caveat && <p className="platform-model-caveat">{info.caveat}</p>}
  </div>
}

/** Present saved ML recommendation runs without substituting factor rankings or demo data. */
export function RecommendationsPage({ route, dataAsOf }: { route: PlatformRoute; dataAsOf: string | null }): JSX.Element {
  const [data, setData] = useState<Response | null>(null); const [error, setError] = useState<string | null>(null)
  const identity = recommendationFilters.map(key => route.query.get(key) ?? '').join(':')
  useEffect(() => {
    let alive = true; setData(null); setError(null)
    fetchRuns(route).then(value => alive && setData(value), err => alive && setError(err instanceof Error ? err.message : String(err)))
    return () => { alive = false }
  }, [identity]) // Every request input is encoded in identity; route is read synchronously per run.
  const compact = route.page === 'overview'
  const title = compact ? '今日研究候選' : '每日 ML 選股'
  const controls = <Filters route={route} data={data} />
  if (error) return <Panel title={title}><div className="platform-query-state platform-query-error">推薦快照無法讀取：{error}<br />這是讀取錯誤，不是「今天沒有推薦」；頁面不會改用綜合因子排名或示範資料補位。</div></Panel>
  if (!data) return <Panel title={title}><div className="platform-query-state">正在讀取已保存的推薦快照…</div></Panel>
  if (data.runs.length === 0) return <Panel title={title}>{controls}<div className="platform-query-state">
    {route.query.get('date') ? `${route.query.get('date')} 尚未產生推薦快照。` : '尚未保存任何推薦快照。'}
    {route.query.get('date') && data.resolved?.model && <>（模型 {data.resolved.model}；可用上方的前一天／後一天跳到它有快照的日子。）</>}
    <br />尚未產生與讀取失敗是不同狀態；沒有快照時不會退回最近一期，也不會顯示示範候選。
  </div></Panel>
  return <div className="platform-recommendations">
    <Panel title={title} sub={`${data.runs.length} 個保存 run`}>{controls}
      {data.runs.map(run => <RunCard key={run.run_id} run={run} route={route} compact={compact} dataAsOf={dataAsOf} />)}
    </Panel>
    {data.issues.length > 0 && <div className="platform-artifact-issues">有 {data.issues.length} 份推薦快照無法讀取，已列為問題項而非丟棄。</div>}
  </div>
}

function Filters({ route, data }: { route: PlatformRoute; data: Response | null }) {
  const active = route.query.get('bucket')
  // 已知分組照固定順序；資料裡若有別的分組（舊快照、重建的快照）接在後面，不讓它們變成點不到的死角。
  const present = new Set((data?.runs ?? []).flatMap(run => Object.keys(run.bucket_counts ?? {})))
  const extra = [...present].filter(bucket => !(BUCKET_ORDER as readonly string[]).includes(bucket)).sort()
  const models = data?.available?.models ?? []
  const model = route.query.get('model') ?? data?.resolved?.model ?? null
  const day = route.query.get('date') ?? data?.resolved?.date ?? null
  const dates = models.find(m => m.model_id === model)?.dates ?? []
  return <div className="platform-filter-row">
    {models.length > 0 && <div className="platform-filter-group">
      <label>模型<select aria-label="模型" value={model ?? ''} onChange={e => { window.location.hash = href(route, { model: e.target.value || null, run: null }) }}>
        {models.map(m => <option key={m.model_id} value={m.model_id}>{modelLabel(m)}</option>)}
      </select></label>
      <DayNav dates={dates} day={day} label="推薦日期" latestHref={route.query.get('date') ? href(route, { date: null, run: null }) : null}
        hrefFor={value => href(route, { date: value, run: null })} />
    </div>}
    <div className="platform-filter-group">
      {models.length === 0 && <label>日期<input type="date" aria-label="推薦日期" value={route.query.get('date') ?? ''} onChange={e => { window.location.hash = href(route, { date: e.target.value || null }) }} /></label>}
      <label>搜尋<input type="search" aria-label="搜尋股票代號" placeholder="股票代號" value={route.query.get('q') ?? ''} onChange={e => { window.location.hash = href(route, { q: e.target.value || null }) }} /></label>
      <span className="platform-bucket-filter">分類
        {[...BUCKET_ORDER, ...extra].map(bucket => <a key={bucket} href={href(route, { bucket })} aria-current={(active ?? DEFAULT_BUCKET) === bucket ? 'true' : undefined}>{bucketLabel(bucket)}</a>)}
        <a href={href(route, { bucket: ALL_BUCKETS })} aria-current={active === ALL_BUCKETS ? 'true' : undefined}>全部</a>
      </span>
    </div>
    {route.page === 'overview' && <a href={href(route, {}, 'recommendations')}>展開完整清單 →</a>}
  </div>
}

function RunCard({ run, route, compact, dataAsOf }: { run: Run; route: PlatformRoute; compact: boolean; dataAsOf: string | null }) {
  const day = run.as_of.slice(0, 10)
  const stale = dataAsOf != null && day < dataAsOf
  const candidates = compact ? run.candidates.slice(0, 10) : run.candidates
  const context = new URLSearchParams(route.query); context.set('date', day); context.set('model', run.model_id); context.set('run', run.run_id)
  return <section className="platform-run-card">
    <header><div>
      <span className="platform-eyebrow">{run.mode === 'research' ? 'RESEARCH ONLY' : 'RECONSTRUCTED'}{run.published_at ? '' : ' · 未發布'}</span>
      <h2>{run.model_id}</h2><p>{day} · run {run.run_id}</p>
    </div><a href={routeHref('models', undefined, context)}>查看來源模型 →</a></header>
    <ModelCard info={run.model_info ?? null} />
    {/* D-148：研究模型的名單可以每天看，但保留期開啟前不得算它事後的漲跌。 */}
    {run.lane === 'research_live' && <div className="platform-stale-note">
      <strong>研究模型。</strong>只因上架的研究策略（D-147）而每天打分，不是生產排序模型；名單只套流動性閘門、沒有套風控閘門（D-150）。
      第二保留期（2026-09-09 起）開啟前，平台不計算這些名單事後的報酬或勝率（D-148）。
    </div>}
    {stale && <div className="platform-stale-note">快照過期：這個 run 的資料截點是 {day}，行情資料已到 {dataAsOf}。這是舊快照，不是今日推薦。</div>}
    {run.candidate_total === 0 ? <div className="platform-query-state">
      {run.candidate_total_all === 0 ? '此 run 已保存，合法地沒有候選股票（零筆，不是讀取失敗）。'
        : !route.query.get('q') && (route.query.get('bucket') ?? DEFAULT_BUCKET) === DEFAULT_BUCKET && !(DEFAULT_BUCKET in run.bucket_counts)
          ? <>這份快照是 D-111 之前產生的，當時每個候選一律標 <code>ml_candidate</code>、沒有晉級分組，所以「前 20 名」是空的。
            <a href={href(route, { bucket: ALL_BUCKETS })}>看全部候選</a>，或重新匯出這一天的快照以套用分組。</>
          : '目前的分類／搜尋條件下沒有符合的候選；清除條件可看回完整名單。'}
    </div> : <div className="platform-table-wrap"><table className="platform-data-table">
      <thead><tr><th>排名</th><th>股票</th><th>原始 ML 分數</th><th>分組</th><th>洞悉</th></tr></thead>
      <tbody>{candidates.map(candidate => <tr key={candidate.stock_id}>
        <td>{candidate.rank}</td>
        <td><a href={routeHref('stocks', candidate.stock_id, context)}>{candidate.stock_id}</a><StockName stockId={candidate.stock_id} /></td>
        <td>{candidate.score.toFixed(4)}</td><td>{bucketLabel(candidate.bucket)}</td>
        <td><InsightCell insight={candidate.insight} /></td>
      </tr>)}</tbody></table></div>}
    {compact && run.candidate_total > candidates.length && <a className="platform-more-link" href={routeHref('recommendations', undefined, context)}>查看其餘 {run.candidate_total - candidates.length} 檔 →</a>}
  </section>
}
