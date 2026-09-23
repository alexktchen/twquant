import { useEffect, useState, type JSX } from 'react'
import { routeHref, type PlatformRoute } from './routes'
import { apiUrl } from '../api'
import { nameOf, useStockNames } from './stockNames'
import { Panel } from '../ui'
import { DayNav } from './DayNav'
import { streakReason, streakText, type StreakOpen } from './tracking'

type Vault = { total_return: number; max_drawdown: number; benchmark_total_return: number; benchmark_max_drawdown: number }
type Research = { decision: string; window_start: string; window_end: string; cagr: number; max_drawdown: number; calmar: number }
/** T-300 的欄位全部選填：舊 API／舊快照沒有它們，頁面照樣畫得出來。 */
type Target = { stock_id: string; target_weight: number; rank: number; score?: number | null; entered?: boolean | null; streak_days?: number; streak_open?: StreakOpen }
type CompareStatus = 'compared' | 'no_previous' | 'strategy_new' | 'config_changed' | 'previous_failed' | 'current_failed'
type Tracking = { previous_as_of: string | null; status: CompareStatus; entered: string[]; exited: Target[] }
type Run = {
  code: string; config: string; label: string; note: string; status: 'ok' | 'flat' | 'failed'; detail: string | null; exposure: number
  targets: Target[]; vault: Vault | null; research?: Research | null; kind?: 'rebalance' | 'slots' | null; tracking?: Tracking
}
type Snapshot = { run_id: string; as_of: string; generated_at: string; mode: 'research'; strategies: Run[] }
type Response = { snapshots: Snapshot[]; issues: { run_id: string; detail: string }[]; available_dates?: string[]; tracking_lookback?: number }

/** 一張卡先顯示幾檔；其餘收合。名單長短由策略決定，這裡只管版面。 */
const VISIBLE_ROWS = 20

const pct = (v: number, digits = 1): string => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`

async function fetchTargets(date: string | null): Promise<Response> {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  const response = await fetch(`${apiUrl('/api/platform/strategy-targets')}?${params}`, { headers: { accept: 'application/json' } })
  if (!response.ok) throw new Error(`/api/platform/strategy-targets → HTTP ${response.status}`)
  return response.json() as Promise<Response>
}

function StockName({ stockId }: { stockId: string }) {
  const name = nameOf(useStockNames(), stockId)
  return name ? <span className="platform-stock-name">{name}</span> : null
}

function StockLinks({ ids, context }: { ids: string[]; context: URLSearchParams }) {
  return <>{ids.map((sid, i) => <span key={sid} className="platform-change-stock">{i > 0 && '、'}<a href={routeHref('stocks', sid, context)}>{sid}</a><StockName stockId={sid} /></span>)}</>
}

/**
 * 金庫成績條：這支策略在「從沒被看過的半年」裡實際交出什麼。
 * 刻意放在名單上方、每一張卡都有、不收合——D-107 判定沒有任何一支可部署，名單不得脫離成績單獨出現。
 */
function Evidence({ vault, research }: { vault: Vault | null; research?: Research | null }) {
  if (!vault && research) return <>
    {/* D-147：研究期成績不是金庫期——整段期間研究者看過、也參與了設計，必須跟數字一起講清楚。 */}
    <div className="platform-stale-note">這支策略沒有金庫期成績。以下是研究期（{research.decision}，walk-forward 樣本外）的回測成績——那段期間研究者看過、也參與了設計，不是未見資料。</div>
    <dl className="platform-evidence">
      <div><dt>研究期年化</dt><dd>{pct(research.cagr)}<span>{research.window_start} ~ {research.window_end}</span></dd></div>
      <div><dt>研究期最深回撤</dt><dd>{pct(research.max_drawdown)}</dd></div>
      <div><dt>Calmar</dt><dd>{research.calmar.toFixed(2)}<span>年化 ÷ 最深回撤</span></dd></div>
    </dl>
  </>
  if (!vault) return <div className="platform-stale-note">這支策略沒有金庫期成績紀錄——無法判斷它在未見資料上的表現。</div>
  const worseReturn = vault.total_return < vault.benchmark_total_return
  const deeperDrawdown = vault.max_drawdown < vault.benchmark_max_drawdown
  const verdict = worseReturn && deeperDrawdown ? '報酬較低，回撤也較深' : worseReturn ? '報酬較低，回撤相近或較淺' : deeperDrawdown ? '報酬較高，但回撤較深' : '兩項都不輸'
  return <dl className="platform-evidence">
    <div><dt>金庫期報酬</dt><dd>{pct(vault.total_return)}<span>同期 0050 {pct(vault.benchmark_total_return)}</span></dd></div>
    <div><dt>金庫期最深回撤</dt><dd>{pct(vault.max_drawdown)}<span>同期 0050 {pct(vault.benchmark_max_drawdown)}</span></dd></div>
    <div><dt>對照結果</dt><dd className={worseReturn && deeperDrawdown ? 'platform-evidence-bad' : undefined}>{verdict}</dd></div>
  </dl>
}

/** 卡片頂端與頁首摘要共用：這支策略今天跟前一份快照比起來怎樣。 */
function changeSummary(run: Run): string {
  const tracking = run.tracking
  if (run.status === 'failed') return '計算失敗'
  if (!tracking) return run.status === 'flat' ? '空手' : `${run.targets.length} 檔`
  if (tracking.status === 'compared') return `新進 ${tracking.entered.length}／移出 ${tracking.exited.length}`
  if (tracking.status === 'strategy_new' || tracking.status === 'no_previous') return '首份名單'
  if (tracking.status === 'config_changed') return '配置已變更'
  return '無從比較'
}

function Changes({ run, context }: { run: Run; context: URLSearchParams }) {
  const tracking = run.tracking
  if (!tracking || tracking.status === 'current_failed') return null
  const prev = tracking.previous_as_of
  if (tracking.status !== 'compared') {
    const why: Record<Exclude<CompareStatus, 'compared' | 'current_failed'>, string> = {
      no_previous: '這是最早一份快照，沒有前一份可比較。',
      strategy_new: `前一份快照（${prev}）還沒有這支策略：今天是它的第一份名單，所以不標新進。`,
      config_changed: `這支策略的配置與前一份快照（${prev}）不同，名單不能直接比較。`,
      previous_failed: `前一份快照（${prev}）這支策略計算失敗，無從比較新進與移出。`,
    }
    return <p className="platform-changes platform-muted">{why[tracking.status]}</p>
  }
  return <div className="platform-changes">
    <strong>與前一份快照（{prev}）相比：新進 {tracking.entered.length}／移出 {tracking.exited.length}</strong>
    {tracking.entered.length > 0 && <span>新進：<StockLinks ids={tracking.entered} context={context} /></span>}
    {tracking.exited.length > 0 && <span>今日移出：<StockLinks ids={tracking.exited.map(t => t.stock_id)} context={context} /></span>}
  </div>
}

/** 名單是什麼意思——兩種策略講法不同，補位型絕不能出現「每月再平衡」（D-147）。 */
function listSentence(run: Run, day: string): string {
  const n = run.targets.length
  if (run.kind === 'slots') {
    const slot = run.targets[0] ? ` 每格 ${(run.targets[0].target_weight * 100).toFixed(2)}%` : ''
    return `今天若有空位會買進的前 ${n} 名，依模型分數由高到低排列（${slot.trim()}）。這不是實際持股——實際持股取決於先前的進出場與出場規則。`
  }
  const exposure = `合計曝險 ${(run.exposure * 100).toFixed(1)}%`
  if (run.kind === 'rebalance') return `若今天再平衡會持有的 ${n} 檔，${exposure}。這支策略每月再平衡一次，名單不是每天都該照著換股。`
  return `以 ${day} 為訊號日的名單 ${n} 檔，${exposure}。`
}

function RunCard({ run, day, lookback, earliest }: { run: Run; day: string; lookback: number; earliest: string | null }) {
  const [expanded, setExpanded] = useState(false)
  const slots = run.kind === 'slots'
  // T-300 之前的補位快照沒有分數，順位是代號順序——照實說，不假裝那是模型的排序。
  const legacyOrder = slots && run.targets.some(t => t.score == null)
  const rows = expanded ? run.targets : run.targets.slice(0, VISIBLE_ROWS)
  const context = new URLSearchParams({ date: day })
  const openReasons = [...new Set(run.targets.map(t => t.streak_open).filter((o): o is Exclude<StreakOpen, null> => !!o))]
  return <section className="platform-run-card" id={`strategy-${run.code}`}>
    <header><div>
      <span className="platform-eyebrow">RESEARCH ONLY · 未通過部署裁決</span>
      <h2>{run.label}</h2><p>{run.code} · {run.config}{slots ? ' · 每日補位' : run.kind === 'rebalance' ? ' · 月頻再平衡' : ''}</p>
    </div><a href={routeHref('backtests')}>查看回測紀錄 →</a></header>
    <Changes run={run} context={context} />
    <Evidence vault={run.vault} research={run.research} />
    {run.status === 'failed' && <div className="platform-query-state platform-query-error">這支策略今天算不出名單：{run.detail}<br />這是計算失敗，不是「今天空手」；其餘策略不受影響。</div>}
    {run.status === 'flat' && <div className="platform-query-state">{run.detail ?? '空手日'}——策略規則下今天沒有合格標的，這是合法結果。</div>}
    {run.status === 'ok' && <>
      <p className="platform-strategy-note">{listSentence(run, day)}</p>
      {legacyOrder && <div className="platform-stale-note">這份快照產生於 T-300 之前，當時沒有記錄模型分數：以下依代號排列，順位不代表分數高低。</div>}
      <div className="platform-table-wrap"><table className="platform-data-table platform-compact-table">
        <thead><tr><th>順位</th><th>股票</th><th>{slots ? '模型分數' : '目標權重'}</th><th>連續</th></tr></thead>
        <tbody>{rows.map(t => <tr key={t.stock_id} className={t.entered ? 'platform-row-new' : undefined}>
          <td>{t.rank}</td>
          <td><a href={routeHref('stocks', t.stock_id, context)}>{t.stock_id}</a><StockName stockId={t.stock_id} />{t.entered && <span className="platform-new-badge">新進</span>}</td>
          <td>{slots ? (t.score == null ? '—' : t.score.toFixed(3)) : `${(t.target_weight * 100).toFixed(2)}%`}</td>
          <td>{t.streak_days == null ? '—' : streakText(t.streak_days, t.streak_open ?? null)}</td>
        </tr>)}</tbody>
      </table></div>
      {run.targets.length > VISIBLE_ROWS && <button type="button" className="platform-link-button" onClick={() => setExpanded(v => !v)}>{expanded ? '收合' : `顯示全部 ${run.targets.length} 檔`}</button>}
      {slots && !legacyOrder && <p className="platform-footnote">模型分數是策略訊號的原始輸出，只用來排序；不要把它讀成實際會漲的機率。</p>}
      {openReasons.map(open => <p key={open} className="platform-footnote">「≥」：{streakReason(open, lookback, earliest)}。</p>)}
    </>}
    {run.note && <details className="platform-note-details"><summary>策略說明</summary><p>{run.note}</p></details>}
  </section>
}

/** 已登記策略的每日目標持股（D-112）。只讀已保存的快照；頁面不觸發任何策略計算。 */
export function StrategyTargetsPage({ route }: { route: PlatformRoute }): JSX.Element {
  const date = route.query.get('date')
  const [data, setData] = useState<Response | null>(null); const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true; setData(null); setError(null)
    fetchTargets(date).then(value => alive && setData(value), err => alive && setError(err instanceof Error ? err.message : String(err)))
    return () => { alive = false }
  }, [date])
  const title = '策略持股'
  if (error) return <Panel title={title}><div className="platform-query-state platform-query-error">策略持股快照無法讀取：{error}<br />這是讀取錯誤，不是「今天沒有名單」。</div></Panel>
  if (!data) return <Panel title={title}><div className="platform-query-state">正在讀取已保存的策略持股快照…</div></Panel>
  const dates = data.available_dates ?? []
  const snapshot = data.snapshots[0]
  const day = snapshot ? snapshot.as_of.slice(0, 10) : date
  const nav = dates.length > 0 ? <DayNav dates={dates} day={day} label="訊號日" latestHref={date ? routeHref('strategies') : null}
    hrefFor={value => routeHref('strategies', undefined, new URLSearchParams({ date: value }))} /> : null
  if (!snapshot) return <Panel title={title}>{nav}<div className="platform-query-state">{date ? `${date} 尚未產生策略持股快照。` : '尚未保存任何策略持股快照。'}<br />快照由每日排程的「策略持股」步驟產生，也可用 <code>twquant strategy-targets</code> 手動補跑。</div></Panel>
  const shownDay = snapshot.as_of.slice(0, 10)
  const counts = { ok: 0, flat: 0, failed: 0 }
  for (const run of snapshot.strategies) counts[run.status] += 1
  const lookback = data.tracking_lookback ?? 0
  const earliest = dates.length > 0 ? dates[dates.length - 1] : null
  return <div className="platform-recommendations">
    <Panel title={title} sub={`訊號日 ${shownDay} · ${snapshot.strategies.length} 支策略（有名單 ${counts.ok}、空手 ${counts.flat}、失敗 ${counts.failed}）`}>
      {nav}
      <div className="platform-stale-note">
        <strong>這一頁是研究輸出，不是買賣建議。</strong>每張卡片上方都附上它自己的成績，請連同名單一起看。
        <details className="platform-note-details"><summary>為什麼這樣說</summary>
          <p>金庫期（2026-03-02 ~ 08-31，事前封存、從未用於調整）實測的 8 支策略全部輸給同期的 0050，其中集中型的幾支在七月的下跌裡回撤是大盤的兩倍以上。之後加上的 LW-E0 與 TAIL-B 各有但書，寫在各自卡片的成績與策略說明裡。</p>
          <p>「新進」「今日移出」是和前一份快照比名單成員；「連續」是這檔連續出現在同一支策略名單上的快照日數（中間沒有快照的日子不計），最多往回看 {lookback || '若干'} 份。這一頁不計算任何名單事後的漲跌。</p>
        </details>
      </div>
      <nav className="platform-change-strip" aria-label="各策略今日異動">
        {snapshot.strategies.map(run => <button key={run.code} type="button" onClick={() => document.getElementById(`strategy-${run.code}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
          <strong>{run.code}</strong><span>{changeSummary(run)}</span>
        </button>)}
      </nav>
      <div className="platform-strategy-grid">
        {snapshot.strategies.map(run => <RunCard key={run.code} run={run} day={shownDay} lookback={lookback} earliest={earliest} />)}
      </div>
    </Panel>
    {data.issues.length > 0 && <div className="platform-artifact-issues">有 {data.issues.length} 份策略持股快照無法讀取，已列為問題項而非丟棄。</div>}
  </div>
}
