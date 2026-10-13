// 名單績效（D-176，網站分頁「績效」）：每條線照公布的名單自動操作的模擬帳戶淨值曲線，以及每一檔新進名單的逐筆結果。
// 名單出來就照固定規則全部當成買了、事後結算，使用者不能挑——這是名單本身的紀錄，不是個人損益。
// 2026-10-13 使用者裁決：頁面不放 0050 對照（D-176 修訂）；資料裡仍算著，要放回來只改這頁。
import { useEffect, useMemo, useRef, useState } from 'react'
import { ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers } from 'lightweight-charts'
import { fetchScorecard, fetchScorecardTrades } from './api'
import { GUEST_LINES } from './auth'
import { pct, price, trend } from './format'
import {
  filterRows,
  md,
  navLines,
  ratio,
  statusText,
  wan,
  type RowFilter,
  type Scorecard,
  type TradeRow,
  type TradeStats,
} from './scorecard'

const ACCOUNT = '#FF7A00'
/** 明細一次顯示幾筆（「再顯示」每按一次多這麼多）。 */
const PAGE = 50
/** 已結算筆數少於這個數，統計很不穩定，要提醒。 */
const FEW = 30

export function ScorecardPage({ line, onLine, refresh }: { line: string; onLine: (code: string) => void; refresh: number }) {
  const [card, setCard] = useState<Scorecard | null>(null)
  const [rows, setRows] = useState<TradeRow[] | null>(null)
  const [err, setErr] = useState('')
  const [rowsErr, setRowsErr] = useState('')
  const [filter, setFilter] = useState<RowFilter>('all')
  const [shown, setShown] = useState(PAGE)
  useEffect(() => {
    // stale：快速切換線別時，晚到的上一條線回應不能蓋到這一條
    let stale = false
    setErr('')
    setRowsErr('')
    fetchScorecard(line)
      .then((c) => !stale && setCard(c))
      .catch((e: Error) => !stale && setErr(e.message))
    fetchScorecardTrades(line)
      .then((r) => !stale && setRows(r))
      .catch((e: Error) => !stale && setRowsErr(e.message))
    return () => {
      stale = true
    }
  }, [line, refresh])
  useEffect(() => {
    setShown(PAGE)
    setFilter('all')
  }, [line])
  // 換線後新資料回來前，不拿上一條線的數字
  const c = card?.code === line ? card : null
  const list = useMemo(() => filterRows(rows ?? [], filter), [rows, filter])
  return (
    <main className="cq-body cq-score">
      <aside className="cq-list cq-score-side">
        <div className="cq-seg">
          {GUEST_LINES.map((l) => (
            <button key={l.code} className={l.code === line ? 'on' : ''} onClick={() => onLine(l.code)}>
              {l.name}
            </button>
          ))}
        </div>
        {err && <p className="cq-empty">{err}</p>}
        {!err && !c && <p className="cq-empty">讀取中…</p>}
        {c && <Account c={c} />}
      </aside>
      <section className="cq-detail cq-score-main">
        {c && (
          <>
            <NavChart c={c} />
            <Trades c={c} />
            <div className="cq-score-block">
              <div className="cq-score-head">
                <h2>逐筆明細</h2>
                <div className="cq-seg cq-seg-small">
                  {(
                    [
                      ['all', '全部'],
                      ['live', '即時'],
                      ['backfill', '事後補'],
                    ] as const
                  ).map(([k, label]) => (
                    <button key={k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {rowsErr && <p className="cq-empty">{rowsErr}</p>}
              {!rowsErr && rows === null && <p className="cq-empty">讀取中…</p>}
              {rows !== null && list.length === 0 && <p className="cq-empty">沒有符合的筆數。</p>}
              {list.length > 0 && <TradeTable rows={list.slice(0, shown)} />}
              {list.length > shown && (
                <button className="cq-more" onClick={() => setShown(shown + PAGE)}>
                  再顯示 {Math.min(PAGE, list.length - shown)} 筆（共 {list.length} 筆）
                </button>
              )}
            </div>
          </>
        )}
      </section>
    </main>
  )
}

/** 左欄：模擬帳戶的數字、即時段、規則與注意事項。 */
function Account({ c }: { c: Scorecard }) {
  const all = c.account.all
  const live = c.account.live
  const cashPct = c.account.equity > 0 ? c.account.cash / c.account.equity : null
  return (
    <div className="cq-score-acct">
      <div className="cq-score-label">模擬帳戶 · 照名單自動買賣</div>
      <div className={`cq-big ${trend(all.ret)}`}>
        {pct(all.ret, 1)}
        <span className="cq-score-sub">
          {md(c.start)} 起 · {wan(c.initial_capital)} → {wan(c.account.equity)}
        </span>
      </div>
      <dl className="cq-score-dl">
        <dt>最大回撤</dt>
        <dd className="down">{pct(all.mdd, 1)}</dd>
        <dt>年化{all.short ? '（不滿一年，換算）' : ''}</dt>
        <dd className={trend(all.cagr)}>{pct(all.cagr, 1)}</dd>
        <dt>目前持股</dt>
        <dd>
          {c.account.holdings.length} 檔 · 現金 {ratio(cashPct)}
        </dd>
      </dl>
      <div className="cq-score-live">
        <b>即時公布以來</b>
        {live ? (
          <>
            <small>
              {md(live.start)} 起 · {live.n_days} 個交易日
            </small>
            <dl className="cq-score-dl compact">
              <dt>帳戶</dt>
              <dd className={trend(live.ret)}>{pct(live.ret, 1)}</dd>
              <dt>最大回撤</dt>
              <dd className="down">{pct(live.mdd, 1)}</dd>
            </dl>
          </>
        ) : (
          <small>還沒有——目前所有名單都是事後補的。</small>
        )}
      </div>
      <div className="cq-score-notes">
        <p>
          <b>規則</b>
          {c.summary}
        </p>
        <p>
          <b>成交</b>
          名單收盤後才公布，一律隔天開盤價買進（不假裝用當天收盤價買到）；零股、1,000 萬起始；扣手續費（{(c.trades.costs.commission * 100).toFixed(4)}
          %，最低 20 元）、賣出證交稅 {(c.trades.costs.tax * 100).toFixed(1)}% 與滑價；股利入帳。
        </p>
        <p>
          <b>即時與事後補</b>
          名單在下一個交易日開盤前寫進資料庫才算即時公布。
          {c.live_since
            ? `${md(c.live_since)} 之前的名單是網站上線後用同一套程式回頭補出來的（圖上虛線），不是當天公布的。`
            : '目前全部是網站上線後用同一套程式回頭補出來的（圖上虛線），不是當天公布的。'}
          兩個模型的訓練資料都在 2026-05-04 之前截止（排序模型到 2/25、潛伏飆股模型到 4/30），這段對模型是沒看過的資料。
        </p>
        <p>規則事先寫死、之後不改；賠的照列，不挑不刪。這是名單本身的紀錄，不是個人損益，也不是投資建議。</p>
        {c.gaps.length > 0 && (
          <p className="cq-score-warn">
            有 {c.gaps.length} 個交易日沒有公布名單（{c.gaps.slice(0, 5).map(md).join('、')}
            {c.gaps.length > 5 ? '…' : ''}），那幾天沒有新買進。
          </p>
        )}
      </div>
    </div>
  )
}

/** 淨值曲線（累積報酬 %）：事後補那段虛線、即時公布以來實線。 */
function NavChart({ c }: { c: Scorecard }) {
  const el = useRef<HTMLDivElement>(null)
  const lines = useMemo(() => navLines(c.nav, c.live_since), [c])
  const [hover, setHover] = useState<string | null>(null)
  const at = useMemo(() => {
    const d = hover ?? c.nav.at(-1)?.d ?? null
    const i = c.nav.findIndex((p) => p.d === d)
    const p0 = c.nav[0]
    if (i < 0 || !p0) return null
    return { d: c.nav[i].d, v: c.nav[i].v / p0.v - 1 }
  }, [hover, c])
  useEffect(() => {
    const box = el.current
    if (!box || c.nav.length === 0) return
    const chart = createChart(box, {
      width: box.clientWidth,
      height: box.clientHeight,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8A8F98', fontSize: 12 },
      grid: { vertLines: { color: '#1C1E25' }, horzLines: { color: '#1C1E25' } },
      rightPriceScale: { borderColor: '#23252D' },
      timeScale: { borderColor: '#23252D', fixLeftEdge: true, fixRightEdge: true },
      localization: { priceFormatter: (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` },
      handleScroll: { vertTouchDrag: false },
      crosshair: { mode: CrosshairMode.Normal },
    })
    const common = { priceLineVisible: false, lastValueVisible: false }
    const past = chart.addSeries(LineSeries, { ...common, color: ACCOUNT, lineWidth: 2, lineStyle: LineStyle.Dashed })
    past.setData(lines.backfill)
    past.createPriceLine({ price: 0, color: '#3A3D46', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false })
    if (lines.live.length > 0) {
      const now = chart.addSeries(LineSeries, { ...common, color: ACCOUNT, lineWidth: 3 })
      now.setData(lines.live)
      createSeriesMarkers(now, [
        { time: lines.live[0].time, position: 'belowBar', shape: 'arrowUp', color: ACCOUNT, text: '即時公布起', size: 0.6 },
      ])
    }
    chart.timeScale().fitContent()
    chart.subscribeCrosshairMove((param) => setHover(param.time ? String(param.time) : null))
    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth, height: box.clientHeight }))
    ro.observe(box)
    return () => {
      ro.disconnect()
      setHover(null)
      chart.remove()
    }
  }, [lines, c.nav.length])
  return (
    <div className="cq-score-block">
      <div className="cq-score-head">
        <h2>{c.name} 模擬帳戶</h2>
        {at && (
          <span className="cq-score-at">
            {md(at.d)}　<b style={{ color: ACCOUNT }}>累積 {pct(at.v, 1)}</b>
          </span>
        )}
      </div>
      <div className="cq-score-legend">
        <span>
          <i className="dash" style={{ borderColor: ACCOUNT }} />
          事後補的名單
        </span>
        <span>
          <i style={{ background: ACCOUNT }} />
          即時公布的名單
        </span>
      </div>
      <div className="cq-score-chart" ref={el} />
    </div>
  )
}

/** 逐筆統計：全部／事後補／即時三欄。 */
function Trades({ c }: { c: Scorecard }) {
  const t = c.trades
  const cols: [string, TradeStats][] = [
    ['全部', t.stats.all],
    ['事後補', t.stats.backfill],
    ['即時', t.stats.live],
  ]
  const loss = Math.round(-t.big_loss * 100)
  const rows: [string, (s: TradeStats) => string, (s: TradeStats) => string][] = [
    ['已結算筆數', (s) => String(s.n), () => ''],
    ['每筆平均', (s) => pct(s.mean, 2), (s) => trend(s.mean)],
    ['中位數', (s) => pct(s.median, 2), (s) => trend(s.median)],
    ['賺錢的比例', (s) => ratio(s.win_rate), () => ''],
    [`虧超過 ${loss}% 的比例`, (s) => ratio(s.big_loss_rate), () => ''],
    ['最好的一筆', (s) => pct(s.best, 1), (s) => trend(s.best)],
    ['最差的一筆', (s) => pct(s.worst, 1), (s) => trend(s.worst)],
  ]
  return (
    <div className="cq-score-block">
      <div className="cq-score-head">
        <h2>逐筆統計</h2>
      </div>
      <p className="cq-score-p">
        每一檔「新進名單」（前一份名單沒有的）都算一筆：隔天開盤買，抱滿 {t.hold_sessions} 個交易日後隔天開盤賣；每筆約 {wan(t.notional)}
        、扣成本、含股利，不受帳戶位子限制。
      </p>
      <table className="cq-score-stats">
        <thead>
          <tr>
            <th />
            {cols.map(([label]) => (
              <th key={label}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, fmt, cls]) => (
            <tr key={label}>
              <th>{label}</th>
              {cols.map(([k, s]) => (
                <td key={k} className={cls(s)}>
                  {fmt(s)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="cq-score-p">
        還抱著 {t.stats.open} 筆、明天開盤買 {t.stats.pending} 筆、買不到 {t.stats.unfilled} 筆（開盤漲停、停牌等），都不算進上面的統計。
        {t.stats.all.n < FEW && <span className="cq-score-warn"> 已結算不到 {FEW} 筆，數字很不穩定，看看就好。</span>}
        {t.stats.live.n === 0 && <span> 即時公布的名單還沒有抱滿 {t.hold_sessions} 天的，所以即時欄還是空的。</span>}
      </p>
    </div>
  )
}

function TradeTable({ rows }: { rows: TradeRow[] }) {
  return (
    <div className="cq-score-trades">
      <div className="cq-trade cq-trade-head">
        <span>公布日／股票</span>
        <span>買進</span>
        <span>賣出</span>
        <span>報酬</span>
      </div>
      {rows.map((r) => (
        <div key={`${r.signal}-${r.sid}`} className="cq-trade">
          <span className="cq-trade-name">
            <small>
              {md(r.signal)}
              <i className={r.live ? 'cq-tag new' : 'cq-tag'}>{r.live ? '即時' : '補'}</i>
            </small>
            <b>
              <em>{r.sid}</em>
              {r.name ?? ''}
            </b>
          </span>
          <span className="cq-trade-px">
            {r.entry_price !== null ? (
              <>
                {price(r.entry_price)}
                <small>{md(r.entry_date)}</small>
              </>
            ) : (
              '—'
            )}
          </span>
          <span className="cq-trade-px">
            {r.status === 'closed' || r.status === 'open' ? (
              <>
                {price(r.exit_price)}
                <small>{r.status === 'open' ? '最新收盤' : md(r.exit_date)}</small>
              </>
            ) : (
              '—'
            )}
          </span>
          <span className="cq-trade-ret">
            <b className={trend(r.ret)}>{pct(r.ret, 1)}</b>
            <small>
              {statusText(r)}
              {r.status === 'closed' || r.status === 'open' ? ` · ${r.held} 天` : ''}
            </small>
          </span>
        </div>
      ))}
    </div>
  )
}
