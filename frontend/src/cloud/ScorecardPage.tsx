// 名單績效（D-176，網站分頁「績效」）：每條線照公布的名單自動操作的模擬帳戶淨值曲線，以及每一檔新進名單的逐筆結果。
// 名單出來就照固定規則全部當成買了、事後結算，使用者不能挑——這是名單本身的紀錄，不是個人損益。
// 0050 對照：2026-10-13 拿掉（D-176 修訂），2026-10-14 使用者「績效把0050也放上去試試」放回來（D-176 修訂三）。
// 2026-10-18 第二版：使用者看第一版說「看不太懂」。每張卡先回答一個問題、給一句結論，再用長條／比例條呈現，
// 術語換白話（最大回撤 → 中途最多跌掉、中位數 → 排中間的那檔、事後補／即時 → 回頭補算／每天公布）。
import { ArrowLeftRight, BookOpen, Clock, Lightbulb, LineChart, ListOrdered, Target, TrendingDown, TriangleAlert, Wallet } from 'lucide-react'
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
  filterRows,
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
} from './scorecard'
import { LIVE_NAME, barWidth, benchDiff, cashRatio, mddTimes, outcomeBuckets, segments, shapeVerdict, splitReturn, tone, type BucketKey } from './score-view'
import { Kpi, Section } from './Section'

/** 名單（帳戶）的線：名單路線色（紫） */
const ACCOUNT = '#A78BFA'
/** 0050 對照：中性的淺灰，不搶名單的顏色 */
const BENCH = '#9AA0AC'
/** 另外的對照（D-176 修訂四）依序用的顏色 */
const EXTRA = ['#4FB3BF', '#FF7A00']
/** 明細一次顯示幾筆（「再顯示」每按一次多這麼多）。 */
const PAGE = 50
/** 帳戶下單紀錄一次顯示幾筆。 */
const LEDGER_PAGE = 20
/** 已賣出筆數少於這個數，統計很不穩定，要提醒。 */
const FEW = 30
/** 明細每列的小長條：漲跌這麼多（小數）就畫滿半邊；更大的也只畫滿，數字照寫。 */
const ROW_BAR_FULL = 0.3

type Line = { key: string; label: string; color: string; ret: number | null; mdd: number | null }

export function ScorecardPage({ line, onLine, refresh }: { line: string; onLine: (code: string) => void; refresh: number }) {
  const [card, setCard] = useState<Scorecard | null>(null)
  const [rows, setRows] = useState<TradeRow[] | null>(null)
  const [err, setErr] = useState('')
  const [rowsErr, setRowsErr] = useState('')
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
  // 換線後新資料回來前，不拿上一條線的數字
  const c = card?.code === line ? card : null
  const lines = useMemo<Line[]>(() => {
    if (!c) return []
    const name = GUEST_LINES.find((l) => l.code === c.code)?.name ?? c.name
    return [
      { key: 'acct', label: name, color: ACCOUNT, ret: c.account.all.ret, mdd: c.account.all.mdd },
      { key: c.benchmark.code, label: c.benchmark.code, color: BENCH, ret: c.account.all.bench_ret, mdd: c.account.all.bench_mdd },
      ...(c.extra_benchmarks ?? []).map((e, k) => ({ key: e.code, label: e.code, color: EXTRA[k % EXTRA.length], ret: e.all?.ret ?? null, mdd: e.all?.mdd ?? null })),
    ]
  }, [c])
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
        {c && (
          <div className="cq-score-acct">
            <ReturnCard c={c} lines={lines} />
            <DrawdownCard c={c} lines={lines} />
            <TimelineCard c={c} />
            <RulesCard c={c} />
          </div>
        )}
      </aside>
      <section className="cq-detail cq-score-main">
        {c && (
          <>
            <NavChart c={c} lines={lines} />
            <OutcomeCard c={c} rows={rows} />
            <PickList rows={rows} err={rowsErr} hold={c.trades.hold_sessions} lineCode={c.code} />
            <AccountTrades c={c} />
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

/** 一組橫條：名稱、長條（顏色＝這條線的顏色）、數字。有負的就從中間分左右。 */
function Bars({ items, pick }: { items: Line[]; pick: (l: Line) => number | null }) {
  const values = items.map(pick)
  const split = values.some((v) => v !== null && v < 0) && values.some((v) => v !== null && v > 0)
  return (
    <div className="cq-bars">
      {items.map((l, i) => {
        const v = values[i]
        const w = barWidth(v, values)
        const style = split
          ? { width: `${w / 2}%`, left: v !== null && v < 0 ? `${50 - w / 2}%` : '50%', background: l.color }
          : { width: `${w}%`, left: 0, background: l.color }
        return (
          <div key={l.key} className={`cq-bar-row${i === 0 ? ' main' : ''}`}>
            <span className="cq-bar-label">
              <i style={{ background: l.color }} />
              {l.label}
            </span>
            <span className={`cq-bar-track${split ? ' split' : ''}`}>
              <span className="cq-bar-fill" style={style} />
            </span>
            <b className={`cq-bar-value ${tone(v)}`}>{pct(v, 1)}</b>
          </div>
        )
      })}
    </div>
  )
}

/** 第一個問題：照名單全買，賺多少？跟同期買 ETF 抱著比。 */
function ReturnCard({ c, lines }: { c: Scorecard; lines: Line[] }) {
  const all = c.account.all
  const cash = cashRatio(c.account.cash, c.account.equity)
  const diffs = lines.slice(1).map((l) => ({ key: l.key, d: benchDiff(all.ret, l.ret, l.label) }))
  return (
    <Section icon={Wallet} title="照名單全買，賺多少？" meta={`${md(c.start)} → ${md(c.as_of)}`}>
      <div className="cq-sc-big" title={all.short ? `不滿一年；換算成一年約 ${pct(all.cagr, 1)}，只供參考` : `換算成一年 ${pct(all.cagr, 1)}`}>
        <b className={tone(all.ret)}>{pct(all.ret, 1)}</b>
        <span>
          {wan(c.initial_capital)} → {wan(c.account.equity)}
        </span>
      </div>
      <Bars items={lines} pick={(l) => l.ret} />
      <div className="cq-sc-chips">
        {diffs.map(
          ({ key, d }) =>
            d && (
              <i key={key} className={`cq-cat ${d.win ? 'cq-cat-list' : 'cq-cat-mute'}`}>
                {d.win ? '▲' : '▼'} {d.text}
              </i>
            ),
        )}
      </div>
      <p className="cq-sc-note">
        ETF 是同一段期間、同樣的錢買進抱著（含股利）。現在抱 {c.account.holdings.length} 檔、現金 {ratio(cash)}。
      </p>
    </Section>
  )
}

/** 第二個問題：中途最多跌掉多少？ */
function DrawdownCard({ c, lines }: { c: Scorecard; lines: Line[] }) {
  const times = mddTimes(c.account.all.mdd, c.account.all.bench_mdd, c.benchmark.code)
  return (
    <Section icon={TrendingDown} title="中途最多跌掉多少？">
      <Bars items={lines} pick={(l) => l.mdd} />
      {times && (
        <p className="cq-sc-verdict">
          <Lightbulb size={16} strokeWidth={2.2} aria-hidden="true" />
          {lines[0].label}跌最深時 {pct(c.account.all.mdd, 1)}，{times}
        </p>
      )}
    </Section>
  )
}

/** 這些數字怎麼來的：回頭補算與每天公布兩段的時間軸。 */
function TimelineCard({ c }: { c: Scorecard }) {
  const live = c.account.live
  const seg = segments(c.nav.length, live?.n_days ?? null)
  const sp = splitReturn(c.nav, c.live_since)
  return (
    <Section icon={Clock} title="數字怎麼來的">
      <div className="cq-timeline">
        <span className="cq-tl-back" style={{ flexGrow: seg.backfill }} />
        {seg.live > 0 && <span className="cq-tl-live" style={{ flexGrow: Math.max(seg.live, 0.06) }} />}
      </div>
      <div className="cq-tl-legend">
        <div>
          <span className="cq-tl-key back" />
          <b>回頭補算</b>
          <small>
            {md(c.start)}–{md(sp.backfillEnd)}
          </small>
          <em className={tone(sp.backfill)}>{pct(sp.backfill, 1)}</em>
        </div>
        <div>
          <span className="cq-tl-key live" />
          <b>每天公布</b>
          <small>{c.live_since ? `${md(c.live_since)} 起 · ${live?.n_days ?? 0} 天` : '還沒有'}</small>
          <em className={tone(sp.live)}>{pct(sp.live, 1)}</em>
        </div>
      </div>
      <p className="cq-sc-note">網站上線前的名單，是用同一套程式回頭算出來的；上線後才是每天收盤後真的公布。</p>
    </Section>
  )
}

function RulesCard({ c }: { c: Scorecard }) {
  return (
    <Section icon={BookOpen} title="規則" fold summary="事先寫死、不改">
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
          <i className="cq-cat cq-cat-event">模型</i>
          <span>兩個模型的訓練資料都在 2026-05-04 之前截止，圖上這段對模型是沒看過的資料。</span>
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
  )
}

/** 淨值走勢（累積報酬 %）：回頭補算那段虛線、每天公布以來實線。 */
function NavChart({ c, lines }: { c: Scorecard; lines: Line[] }) {
  const el = useRef<HTMLDivElement>(null)
  const nl = useMemo(() => navLines(c.nav, c.live_since), [c])
  const [hover, setHover] = useState<string | null>(null)
  const at = useMemo(() => {
    const d = hover ?? c.nav.at(-1)?.d ?? null
    const i = c.nav.findIndex((p) => p.d === d)
    const p0 = c.nav[0]
    if (i < 0 || !p0) return null
    const b = c.nav[i].b
    const vals: Record<string, number | null> = { acct: c.nav[i].v / p0.v - 1, [c.benchmark.code]: p0.b && b !== null ? b / p0.b - 1 : null }
    for (const e of c.extra_benchmarks ?? []) vals[e.code] = extraAt(e.values, i)
    return { d: c.nav[i].d, vals }
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
      localization: { priceFormatter: (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%` },
      handleScroll: { vertTouchDrag: false },
      crosshair: { mode: CrosshairMode.Normal },
    })
    const common = { priceLineVisible: false, lastValueVisible: false }
    if (nl.bench.length > 0) chart.addSeries(LineSeries, { ...common, color: BENCH, lineWidth: 2 }).setData(nl.bench)
    ;(c.extra_benchmarks ?? []).forEach((e, k) => {
      const pts = extraLine(c.nav, e.values)
      if (pts.length > 0) chart.addSeries(LineSeries, { ...common, color: EXTRA[k % EXTRA.length], lineWidth: 2 }).setData(pts)
    })
    const past = chart.addSeries(LineSeries, { ...common, color: ACCOUNT, lineWidth: 2, lineStyle: LineStyle.Dashed })
    past.setData(nl.backfill)
    past.createPriceLine({ price: 0, color: '#3A3D46', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false })
    if (nl.live.length > 0) {
      const now = chart.addSeries(LineSeries, { ...common, color: ACCOUNT, lineWidth: 3 })
      now.setData(nl.live)
      createSeriesMarkers(now, [{ time: nl.live[0].time, position: 'belowBar', shape: 'arrowUp', color: ACCOUNT, text: '每天公布', size: 0.6 }])
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
  }, [nl, c.nav.length])
  return (
    <Section icon={LineChart} title="一路走來" meta={at && <span className="cq-sc-at">{md(at.d)}</span>}>
      <div className="cq-sc-legend">
        {lines.map((l) => {
          const v = at?.vals[l.key] ?? null
          return (
            <span key={l.key}>
              <i style={{ background: l.color }} />
              {l.label}
              <b className={tone(v)}>{pct(v, 1)}</b>
            </span>
          )
        })}
        <span className="cq-sc-legend-note">
          <i className="dash" style={{ borderColor: ACCOUNT }} />
          回頭補算
          <i style={{ background: ACCOUNT }} />
          每天公布
        </span>
      </div>
      <div className="cq-score-chart" ref={el} />
    </Section>
  )
}

const BUCKET_CLASS: Record<BucketKey, string> = { bigWin: 'bw', win: 'w', loss: 'l', bigLoss: 'bl' }

/** 第三個問題：每一檔進榜後抱滿，結果如何？四格比例條＋四張數字卡＋一句結論。 */
function OutcomeCard({ c, rows }: { c: Scorecard; rows: TradeRow[] | null }) {
  const t = c.trades
  const [col, setCol] = useState<RowFilter>('all')
  useEffect(() => setCol('all'), [c.code])
  const s = t.stats[col]
  const rets = useMemo(
    () => filterRows(rows ?? [], col).flatMap((r) => (r.status === 'closed' && r.ret !== null ? [r.ret] : [])),
    [rows, col],
  )
  const buckets = outcomeBuckets(rets, t.big_loss)
  const verdict = shapeVerdict(s.mean, s.median)
  return (
    <Section
      icon={Target}
      title={`每一檔進榜後抱 ${t.hold_sessions} 天，結果如何？`}
      meta={
        <Seg
          value={col}
          items={[
            ['all', LIVE_NAME.all, t.stats.all.n],
            ['live', LIVE_NAME.live, t.stats.live.n],
            ['backfill', LIVE_NAME.backfill, t.stats.backfill.n],
          ]}
          onChange={setCol}
        />
      }
    >
      {s.n === 0 ? (
        <p className="cq-sc-note">{col === 'live' ? `每天公布的名單還沒有抱滿 ${t.hold_sessions} 天的。` : '還沒有賣出的。'}</p>
      ) : (
        <>
          {verdict && (
            <p className="cq-sc-verdict big">
              <Lightbulb size={18} strokeWidth={2.2} aria-hidden="true" />
              {verdict}
            </p>
          )}
          {rows !== null && rets.length > 0 && (
            <>
              <div className="cq-stack" role="img" aria-label={buckets.map((b) => `${b.label} ${b.n} 檔`).join('、')}>
                {buckets.map(
                  (b) =>
                    b.n > 0 && (
                      <span key={b.key} className={`cq-stack-${BUCKET_CLASS[b.key]}`} style={{ flexGrow: b.share }}>
                        {b.share >= 0.08 ? `${Math.round(b.share * 100)}%` : ''}
                      </span>
                    ),
                )}
              </div>
              <div className="cq-stack-legend">
                {buckets.map((b) => (
                  <div key={b.key}>
                    <span className={`cq-stack-dot cq-stack-${BUCKET_CLASS[b.key]}`} />
                    <b>{b.label}</b>
                    <small>{b.range}</small>
                    <em>{b.n} 檔</em>
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="cq-tiles cq-tiles-2 cq-sc-out-tiles">
            <Kpi label="平均每檔" value={pct(s.mean, 1)} tone={tone(s.mean)} sub="含成本與股利" />
            <Kpi label="排中間的那檔" value={pct(s.median, 1)} tone={tone(s.median)} sub="一半比它好、一半比它差" />
            <Kpi label="最好的一檔" value={pct(s.best, 1)} tone={tone(s.best)} />
            <Kpi label="最差的一檔" value={pct(s.worst, 1)} tone={tone(s.worst)} />
          </div>
          {s.n < FEW && (
            <p className="cq-sc-warn">
              <TriangleAlert size={15} strokeWidth={2.2} aria-hidden="true" />
              才賣出 {s.n} 檔，數字很不穩定，看看就好。
            </p>
          )}
        </>
      )}
      <div className="cq-sc-foot">
        <span>
          <i className="cq-cat cq-cat-list">已賣</i> {s.n}
        </span>
        <span>
          <i className="cq-cat cq-cat-list">還抱著</i> {t.stats.open}
        </span>
        <span>
          <i className="cq-cat cq-cat-event">明天買</i> {t.stats.pending}
        </span>
        <span>
          <i className="cq-cat cq-cat-mute">買不到</i> {t.stats.unfilled}
        </span>
      </div>
    </Section>
  )
}

/** 一列的買賣路徑：「9/14 買 40.45 → 10/16 賣 59.30」。 */
function path(r: TradeRow): string {
  if (r.status === 'closed') return `${md(r.entry_date)} 買 ${price(r.entry_price)} → ${md(r.exit_date)} 賣 ${price(r.exit_price)}`
  if (r.status === 'open') return `${md(r.entry_date)} 買 ${price(r.entry_price)} → 現在 ${price(r.exit_price)}`
  return statusText(r)
}

/** 每一檔的結果：名稱、小長條、報酬，第二行是買賣路徑。 */
function PickList({ rows, err, hold, lineCode }: { rows: TradeRow[] | null; err: string; hold: number; lineCode: string }) {
  const [filter, setFilter] = useState<RowFilter>('all')
  // 預設只列已賣出：看得到賺賠；還沒賣的（持有中、明天才買）另外一顆鈕
  const [status, setStatus] = useState<StatusFilter>('closed')
  const [shown, setShown] = useState(PAGE)
  useEffect(() => {
    setShown(PAGE)
    setFilter('all')
    setStatus('closed')
  }, [lineCode])
  const list = useMemo(() => viewRows(rows ?? [], status, filter), [rows, status, filter])
  const counts = useMemo(() => statusCounts(rows ?? []), [rows])
  return (
    <Section
      icon={ListOrdered}
      title="每一檔的結果"
      meta={
        <Seg
          value={status}
          items={[
            ['closed', '已賣', counts.closed],
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
          ['all', LIVE_NAME.all],
          ['live', LIVE_NAME.live],
          ['backfill', LIVE_NAME.backfill],
        ]}
        onChange={(k) => {
          setFilter(k)
          setShown(PAGE)
        }}
        small
      />
      {err && <p className="cq-empty">{err}</p>}
      {!err && rows === null && <p className="cq-empty">讀取中…</p>}
      {rows !== null && list.length === 0 && <p className="cq-empty">沒有符合的。</p>}
      {list.length > 0 && (
        <div className="cq-pk-list" title={`每一檔新進名單都算一筆：隔天開盤買，抱滿 ${hold} 個交易日後隔天開盤賣；報酬扣成本、含股利`}>
          {list.slice(0, shown).map((r) => {
            const w = r.ret === null ? 0 : Math.min(Math.abs(r.ret) / ROW_BAR_FULL, 1) * 50
            return (
              <div key={`${r.signal}-${r.sid}`} className="cq-pk">
                <span className="cq-pk-name">
                  <b>{stockTitle(r)}</b>
                  <small>
                    {r.name && <em>{r.sid}</em>}
                    {md(r.signal)} 進榜
                    <i className={r.live ? 'cq-cat cq-cat-event' : 'cq-cat cq-cat-mute'}>{r.live ? '每天公布' : '補算'}</i>
                  </small>
                </span>
                <span className="cq-pk-bar">
                  <span
                    className={r.ret !== null && r.ret < 0 ? 'neg' : 'pos'}
                    style={{ width: `${w}%`, left: r.ret !== null && r.ret < 0 ? `${50 - w}%` : '50%' }}
                  />
                </span>
                <span className="cq-pk-ret">
                  <b className={tone(r.ret)}>{pct(r.ret, 1)}</b>
                  <small>{r.status === 'closed' ? `${r.held} 天` : r.status === 'open' ? `抱 ${r.held} 天` : ''}</small>
                </span>
                <span className="cq-pk-path">{path(r)}</span>
              </div>
            )
          })}
        </div>
      )}
      {list.length > shown && (
        <button className="cq-more" onClick={() => setShown(shown + PAGE)}>
          再顯示 {Math.min(PAGE, list.length - shown)} 檔（共 {list.length} 檔）
        </button>
      )}
    </Section>
  )
}

/** 模擬帳戶自己的下單（新到舊）：照名單真的下了哪些單、什麼時候賣、為什麼賣。預設收起。 */
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
    <Section icon={ArrowLeftRight} title="模擬帳戶的下單紀錄" fold summary={all ? `${all.length} 筆` : '下一次排程後'}>
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
        都是隔天開盤成交；同一天先賣後買。賣出的「價差」是賣價對平均買價，不含費用與股利。
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
