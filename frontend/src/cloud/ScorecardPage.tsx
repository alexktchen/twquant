// 名單績效（D-176，網站分頁「績效」）：每條線照公布的名單自動操作的模擬帳戶淨值曲線，以及每一檔新進名單的逐筆結果。
// 名單出來就照固定規則全部當成買了、事後結算，使用者不能挑——這是名單本身的紀錄，不是個人損益。
// 0050 對照：2026-10-13 拿掉（D-176 修訂），2026-10-14 使用者「績效把0050也放上去試試」放回來（D-176 修訂三）。
// 2026-10-18 改版（地鐵告示）：整頁是「名單」路線（紫）；數字全部是數字卡，說明收進「規則」卡，列表有固定欄位與分類色標。
import { ArrowLeftRight, BarChart3, BookOpen, Landmark, LineChart, ListOrdered, Scale, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers } from 'lightweight-charts'
import { fetchScorecard, fetchScorecardTrades } from './api'
import { GUEST_LINES } from './auth'
import { pct, price } from './format'
import {
  md,
  navLines,
  extraLine,
  extraAt,
  ratio,
  statusCounts,
  statusText,
  stockTitle,
  tradeReason,
  viewRows,
  wan,
  type RowFilter,
  type Scorecard,
  type StatusFilter,
  type TradeRow,
  type TradeStats,
} from './scorecard'
import { benchDiff, cashRatio, heldText, statTiles, tone } from './score-view'
import { Kpi, Section } from './Section'

/** 帳戶的線：名單路線色（紫） */
const ACCOUNT = '#A78BFA'
/** 0050 對照線：中性的淺灰，不搶帳戶的顏色 */
const BENCH = '#9AA0AC'
/** 另外的對照（D-176 修訂四）依序用的顏色 */
const EXTRA = ['#4FB3BF', '#FF7A00']
/** 明細一次顯示幾筆（「再顯示」每按一次多這麼多）。 */
const PAGE = 50
/** 帳戶買賣紀錄一次顯示幾筆。 */
const LEDGER_PAGE = 20
/** 已結算筆數少於這個數，統計很不穩定，要提醒。 */
const FEW = 30

export function ScorecardPage({ line, onLine, refresh }: { line: string; onLine: (code: string) => void; refresh: number }) {
  const [card, setCard] = useState<Scorecard | null>(null)
  const [rows, setRows] = useState<TradeRow[] | null>(null)
  const [err, setErr] = useState('')
  const [rowsErr, setRowsErr] = useState('')
  const [filter, setFilter] = useState<RowFilter>('all')
  // 預設只列已賣出：看得到賺賠；還沒賣的（持有中、明天才買）另外一顆鈕
  const [status, setStatus] = useState<StatusFilter>('closed')
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
    setStatus('closed')
  }, [line])
  // 換線後新資料回來前，不拿上一條線的數字
  const c = card?.code === line ? card : null
  const list = useMemo(() => viewRows(rows ?? [], status, filter), [rows, status, filter])
  const counts = useMemo(() => statusCounts(rows ?? []), [rows])
  return (
    <main className="cq-body cq-score cq-sc">
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
            <AccountTrades c={c} />
            <Section
              icon={ListOrdered}
              title="逐筆明細"
              meta={
                <Seg
                  value={status}
                  items={[
                    ['closed', '已賣出', counts.closed],
                    ['open', '還沒賣', counts.open],
                    ['all', '全部', counts.all],
                  ]}
                  counted={rows !== null}
                  onChange={(k) => {
                    setStatus(k)
                    setShown(PAGE)
                  }}
                />
              }
            >
              <Seg
                value={filter}
                items={[
                  ['all', '全部'],
                  ['live', '即時'],
                  ['backfill', '事後補'],
                ]}
                onChange={(k) => {
                  setFilter(k)
                  setShown(PAGE)
                }}
                small
              />
              {rowsErr && <p className="cq-empty">{rowsErr}</p>}
              {!rowsErr && rows === null && <p className="cq-empty">讀取中…</p>}
              {rows !== null && list.length === 0 && <p className="cq-empty">沒有符合的筆數。</p>}
              {list.length > 0 && <TradeTable rows={list.slice(0, shown)} />}
              {list.length > shown && (
                <button className="cq-more" onClick={() => setShown(shown + PAGE)}>
                  再顯示 {Math.min(PAGE, list.length - shown)} 筆（共 {list.length} 筆）
                </button>
              )}
            </Section>
          </>
        )}
      </section>
    </main>
  )
}

/** 分段按鈕：值、標籤、（有的話）筆數。 */
function Seg<K extends string>({
  value,
  items,
  onChange,
  counted = true,
  small = false,
}: {
  value: K
  items: readonly (readonly [K, string] | readonly [K, string, number])[]
  onChange: (k: K) => void
  counted?: boolean
  small?: boolean
}) {
  return (
    <div className={`cq-seg cq-seg-small${small ? ' cq-seg-tiny' : ''}`}>
      {items.map(([k, label, n]) => (
        <button key={k} className={value === k ? 'on' : ''} onClick={() => onChange(k)}>
          {label}
          {counted && n !== undefined && <em>{n}</em>}
        </button>
      ))}
    </div>
  )
}

/** 左欄：模擬帳戶的數字卡、對照、規則。 */
function Account({ c }: { c: Scorecard }) {
  const all = c.account.all
  const live = c.account.live
  const cash = cashRatio(c.account.cash, c.account.equity)
  const benches = [
    { code: c.benchmark.code, ret: all.bench_ret, mdd: all.bench_mdd, error: c.benchmark.error, liveRet: live?.bench_ret ?? null },
    ...(c.extra_benchmarks ?? []).map((e) => ({ code: e.code, ret: e.all?.ret ?? null, mdd: e.all?.mdd ?? null, error: e.error, liveRet: e.live?.ret ?? null })),
  ]
  return (
    <div className="cq-score-acct">
      <div className="cq-sc-hero" title="模擬帳戶：名單出來就照固定規則全部當成買了、事後結算，使用者不能挑。這是名單本身的紀錄，不是個人損益，也不是投資建議">
        <span className="cq-sc-hero-label">
          <Landmark size={16} strokeWidth={2.2} aria-hidden="true" /> 模擬帳戶 · 照名單自動買賣
        </span>
        <b className={`cq-sc-hero-value ${tone(all.ret)}`}>{pct(all.ret, 1)}</b>
        <span className="cq-sc-hero-sub">
          {md(c.start)} 起 · {wan(c.initial_capital)} → {wan(c.account.equity)}
        </span>
      </div>
      <div className="cq-tiles cq-tiles-2">
        <Kpi label="最大回撤" value={pct(all.mdd, 1)} tone="down" title="淨值從最高點往下掉最多的幅度" />
        <Kpi label={all.short ? '年化（不滿一年）' : '年化'} value={pct(all.cagr, 1)} tone={tone(all.cagr)} title={all.short ? '不滿一年，按日數換算成一年' : undefined} />
        <Kpi label="目前持股" value={`${c.account.holdings.length} 檔`} sub={`現金 ${ratio(cash)}`} />
        {live ? (
          <Kpi label="即時公布以來" value={pct(live.ret, 1)} tone={tone(live.ret)} sub={`${md(live.start)} 起 · ${live.n_days} 天 · 回撤 ${pct(live.mdd, 1)}`} title="名單在下一個交易日開盤前寫進資料庫才算即時公布；之前的是事後補的" />
        ) : (
          <Kpi label="即時公布以來" value="—" tone="na" sub="全部是事後補的" />
        )}
      </div>
      <Section icon={Scale} title="同期對照" meta="買進抱著 · 含息">
        <div className="cq-tiles cq-tiles-2">
          {benches.map((b) => {
            const d = benchDiff(all.ret, b.ret)
            if (b.ret === null)
              return <Kpi key={b.code} label={b.code} value="—" tone="na" sub={b.error ? `算不出來：${b.error}` : '算不出來'} />
            return (
              <Kpi
                key={b.code}
                label={b.code}
                value={pct(b.ret, 1)}
                tone={tone(b.ret)}
                sub={`回撤 ${pct(b.mdd, 1)}`}
                side={d && <i className={`cq-cat ${d.win ? 'cq-cat-list' : 'cq-cat-mute'}`}>{d.text}</i>}
                title={`同一段期間把同樣的錢拿去買 ${b.code} 抱著不動（含股利）的結果；右邊是帳戶比它多賺或少賺幾個百分點${b.liveRet !== null ? `。即時公布以來這段：${pct(b.liveRet, 1)}` : ''}`}
              />
            )
          })}
        </div>
      </Section>
      <Section icon={BookOpen} title="規則" fold summary="事先寫死、之後不改">
        <ul className="cq-sc-rules">
          <li>
            <i className="cq-cat cq-cat-list">名單</i>
            <span>{c.summary}</span>
          </li>
          <li>
            <i className="cq-cat cq-cat-list">成交</i>
            <span>
              名單收盤後才公布，一律隔天開盤價買進；零股、{wan(c.initial_capital)}起始；手續費 {(c.trades.costs.commission * 100).toFixed(4)}
              %（最低 20 元）、賣出證交稅 {(c.trades.costs.tax * 100).toFixed(1)}%、滑價；股利入帳。
            </span>
          </li>
          <li>
            <i className="cq-cat cq-cat-event">即時</i>
            <span>
              名單在下一個交易日開盤前寫進資料庫才算即時公布。
              {c.live_since ? `${md(c.live_since)} 之前的名單是網站上線後用同一套程式回頭補的（圖上虛線）。` : '目前全部是網站上線後用同一套程式回頭補的（圖上虛線）。'}
              兩個模型的訓練資料都在 2026-05-04 之前截止，這段對模型是沒看過的資料。
            </span>
          </li>
          <li>
            <i className="cq-cat cq-cat-mute">聲明</i>
            <span>賠的照列，不挑不刪。這是名單本身的紀錄，不是個人損益，也不是投資建議。</span>
          </li>
        </ul>
        {c.gaps.length > 0 && (
          <p className="cq-sc-warn">
            <TriangleAlert size={15} strokeWidth={2.2} aria-hidden="true" />
            {c.gaps.length} 個交易日沒有名單（{c.gaps.slice(0, 5).map(md).join('、')}
            {c.gaps.length > 5 ? '…' : ''}），那幾天沒有新買進。
          </p>
        )}
      </Section>
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
    const b = c.nav[i].b
    const extras = (c.extra_benchmarks ?? []).map((e) => ({ code: e.code, r: extraAt(e.values, i) }))
    return { d: c.nav[i].d, v: c.nav[i].v / p0.v - 1, b: p0.b && b !== null ? b / p0.b - 1 : null, extras }
  }, [hover, c])
  useEffect(() => {
    const box = el.current
    if (!box || c.nav.length === 0) return
    const chart = createChart(box, {
      width: box.clientWidth,
      height: box.clientHeight,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8A8F98', fontSize: 13 },
      grid: { vertLines: { color: '#1C1E25' }, horzLines: { color: '#1C1E25' } },
      rightPriceScale: { borderColor: '#23252D' },
      timeScale: { borderColor: '#23252D', fixLeftEdge: true, fixRightEdge: true },
      localization: { priceFormatter: (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` },
      handleScroll: { vertTouchDrag: false },
      crosshair: { mode: CrosshairMode.Normal },
    })
    const common = { priceLineVisible: false, lastValueVisible: false }
    if (lines.bench.length > 0) {
      // 先畫對照，帳戶的線疊在上面
      chart.addSeries(LineSeries, { ...common, color: BENCH, lineWidth: 2 }).setData(lines.bench)
    }
    ;(c.extra_benchmarks ?? []).forEach((e, k) => {
      const pts = extraLine(c.nav, e.values)
      if (pts.length > 0) chart.addSeries(LineSeries, { ...common, color: EXTRA[k % EXTRA.length], lineWidth: 2 }).setData(pts)
    })
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
  const extras = (c.extra_benchmarks ?? []).filter((e) => extraLine(c.nav, e.values).length > 0)
  return (
    <Section icon={LineChart} title={`${c.name} 淨值`} meta={at && <span className="cq-sc-at">{md(at.d)}</span>}>
      <div className="cq-sc-legend">
        <span>
          <i className="dash" style={{ borderColor: ACCOUNT }} />
          事後補
        </span>
        <span>
          <i style={{ background: ACCOUNT }} />
          即時公布
          {at && <b style={{ color: ACCOUNT }}>{pct(at.v, 1)}</b>}
        </span>
        {lines.bench.length > 0 && (
          <span>
            <i style={{ background: BENCH }} />
            {c.benchmark.code}
            {at && at.b !== null && <b style={{ color: BENCH }}>{pct(at.b, 1)}</b>}
          </span>
        )}
        {extras.map((e, k) => {
          const r = at?.extras.find((x) => x.code === e.code)?.r ?? null
          return (
            <span key={e.code}>
              <i style={{ background: EXTRA[k % EXTRA.length] }} />
              {e.code}
              {r !== null && <b style={{ color: EXTRA[k % EXTRA.length] }}>{pct(r, 1)}</b>}
            </span>
          )
        })}
      </div>
      <div className="cq-score-chart" ref={el} />
    </Section>
  )
}

/** 模擬帳戶自己的買賣（新到舊）：照名單真的下了哪些單、什麼時候賣、為什麼賣。預設收起。 */
function AccountTrades({ c }: { c: Scorecard }) {
  const [shown, setShown] = useState(LEDGER_PAGE)
  const [onlySells, setOnlySells] = useState(false)
  useEffect(() => {
    setShown(LEDGER_PAGE)
    setOnlySells(false)
  }, [c.code])
  const all = c.account.trades
  const sells = all?.filter((t) => t.side === 'sell').length ?? 0
  const list = all && onlySells ? all.filter((t) => t.side === 'sell') : all
  return (
    <Section icon={ArrowLeftRight} title="帳戶買賣紀錄" fold summary={all ? `${all.length} 筆 · 賣出 ${sells}` : '下一次排程後'}>
      {all && (
        <Seg
          value={onlySells ? 'sell' : 'all'}
          items={[
            ['all', '買賣都看', all.length],
            ['sell', '只看賣出', sells],
          ]}
          onChange={(k) => {
            setOnlySells(k === 'sell')
            setShown(LEDGER_PAGE)
          }}
          small
        />
      )}
      <p className="cq-sc-note" title="帳戶淨值才是扣完成本、含股利的結果">
        都是隔天開盤成交；同一天先賣後買。賣出的「價差」是賣價對這一輪平均買價，不含費用與股利。
      </p>
      {!list && <p className="cq-empty">下一次排程算完後會出現。</p>}
      {list && list.length === 0 && <p className="cq-empty">還沒有任何買賣。</p>}
      {list && list.length > 0 && (
        <div className="cq-score-trades">
          <div className="cq-trade cq-trade-head">
            <span>股票 · 成交日</span>
            <span>動作</span>
            <span>成交價</span>
            <span>價差</span>
          </div>
          {list.slice(0, shown).map((t, i) => (
            <div key={`${t.date}-${t.sid}-${t.side}-${i}`} className="cq-trade">
              <span className="cq-trade-name">
                <b>{stockTitle(t)}</b>
                <small>
                  {t.name && <em>{t.sid}</em>}
                  {md(t.date)}
                </small>
              </span>
              <span className="cq-trade-px">
                <i className={t.side === 'buy' ? 'cq-cat cq-cat-event' : 'cq-cat cq-cat-mute'}>{t.side === 'buy' ? '買' : '賣'}</i>
                <small>{tradeReason(t, c.kind)}</small>
              </span>
              <span className="cq-trade-px">
                {price(t.price)}
                <small>{t.shares.toLocaleString('zh-TW')} 股</small>
              </span>
              <span className="cq-trade-ret">{t.ret === null ? <small>—</small> : <b className={tone(t.ret)}>{pct(t.ret, 1)}</b>}</span>
            </div>
          ))}
        </div>
      )}
      {list && list.length > shown && (
        <button className="cq-more" onClick={() => setShown(shown + LEDGER_PAGE)}>
          再顯示 {Math.min(LEDGER_PAGE, list.length - shown)} 筆（共 {list.length} 筆）
        </button>
      )}
    </Section>
  )
}

type StatCol = 'all' | 'backfill' | 'live'

/** 逐筆統計：全部／事後補／即時三欄，一次看一欄的數字卡。 */
function Trades({ c }: { c: Scorecard }) {
  const t = c.trades
  const [col, setCol] = useState<StatCol>('all')
  useEffect(() => setCol('all'), [c.code])
  const cols: Record<StatCol, TradeStats> = { all: t.stats.all, backfill: t.stats.backfill, live: t.stats.live }
  const s = cols[col]
  const tiles = statTiles(s, t.big_loss)
  return (
    <Section
      icon={BarChart3}
      title="逐筆統計"
      meta={
        <Seg
          value={col}
          items={[
            ['all', '全部', t.stats.all.n],
            ['backfill', '事後補', t.stats.backfill.n],
            ['live', '即時', t.stats.live.n],
          ]}
          onChange={setCol}
        />
      }
    >
      <div
        className="cq-tiles cq-tiles-stat"
        title={`每一檔「新進名單」都算一筆：隔天開盤買，抱滿 ${t.hold_sessions} 個交易日後隔天開盤賣；每筆約 ${wan(t.notional)}、扣成本、含股利，不受帳戶位子限制。同一檔還抱著時又進名單不重複買、不另算一筆`}
      >
        {tiles.map((x) => (
          <Kpi key={x.key} label={x.label} value={x.value} sub={x.sub} tone={x.tone} />
        ))}
      </div>
      <div className="cq-sc-foot">
        <span>
          <i className="cq-cat cq-cat-list">抱著</i> {t.stats.open}
        </span>
        <span>
          <i className="cq-cat cq-cat-event">明天買</i> {t.stats.pending}
        </span>
        <span>
          <i className="cq-cat cq-cat-mute">買不到</i> {t.stats.unfilled}
        </span>
        {t.stats.repeat !== undefined && t.stats.repeat > 0 && (
          <span>
            <i className="cq-cat cq-cat-mute">重複進榜</i> {t.stats.repeat}
          </span>
        )}
      </div>
      {s.n < FEW && (
        <p className="cq-sc-warn">
          <TriangleAlert size={15} strokeWidth={2.2} aria-hidden="true" />
          已結算不到 {FEW} 筆，數字很不穩定，看看就好。
        </p>
      )}
      {col === 'live' && t.stats.live.n === 0 && <p className="cq-sc-note">即時公布的名單還沒有抱滿 {t.hold_sessions} 天的。</p>}
    </Section>
  )
}

function TradeTable({ rows }: { rows: TradeRow[] }) {
  return (
    <div className="cq-score-trades">
      <div className="cq-trade cq-trade-head">
        <span>股票 · 公布日</span>
        <span>買進</span>
        <span>賣出</span>
        <span>報酬</span>
      </div>
      {rows.map((r) => (
        <div key={`${r.signal}-${r.sid}`} className="cq-trade">
          <span className="cq-trade-name">
            <b>{stockTitle(r)}</b>
            <small>
              {r.name && <em>{r.sid}</em>}
              {md(r.signal)}
              <i className={r.live ? 'cq-cat cq-cat-event' : 'cq-cat cq-cat-mute'}>{r.live ? '即時' : '補'}</i>
            </small>
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
                <small>{r.status === 'open' ? '最新' : md(r.exit_date)}</small>
              </>
            ) : (
              '—'
            )}
          </span>
          <span className="cq-trade-ret">
            <b className={tone(r.ret)}>{pct(r.ret, 1)}</b>
            <small>
              {statusText(r)}
              {heldText(r.status, r.held) ? ` · ${heldText(r.status, r.held)}` : ''}
            </small>
          </span>
        </div>
      ))}
    </div>
  )
}
