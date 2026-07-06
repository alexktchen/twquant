import { useMemo, useRef, useState } from 'react'
import raw from './data.json'

/* ---------- types ---------- */
type Candle = { d: string; o: number; h: number; l: number; c: number; amt?: number; v?: number }
type Fac = { name: string; direction: number; horizons: { h: number; ic: number; t: number; ir: number; ls: number; n: number }[] }
type Data = {
  meta: { as_of: string; window: string; rev_month: string; top_stocks: string[]; coverage: { label: string; days: number; span: string }[] }
  market: Candle[]
  stocks: Record<string, Candle[]>
  factors: Fac[]
  backtest: { name: string; equity: { d: string; e: number }[]; metrics: Record<string, number> }
  institutional: { sid: string; trust: number; frgn: number; px: number }[]
  revenue: { sid: string; rev: number; yoy: number }[]
  capacity: { twd: number; binding: string; n_positions: number }
}
const D = raw as unknown as Data

/* ---------- format helpers ---------- */
const pct = (x: number, d = 1) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(d)}%`
const sign = (x: number) => (x >= 0 ? 'var(--up)' : 'var(--down)')
const num = (x: number, d = 0) => x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
const oku = (x: number) => `${(x / 1e8).toFixed(1)} 億`

/* ---------- candlestick with hover + range ---------- */
function Candles({ data, height = 300 }: { data: Candle[]; height?: number }) {
  const [range, setRange] = useState<number>(0) // 0 = all
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const W = 940
  const H = height
  const padL = 44, padR = 10, padT = 12, padB = 20
  const shown = range > 0 ? data.slice(-range) : data
  const n = shown.length
  const lo = Math.min(...shown.map((k) => k.l))
  const hi = Math.max(...shown.map((k) => k.h))
  const iw = (W - padL - padR) / n
  const x = (i: number) => padL + i * iw + iw / 2
  const y = (p: number) => padT + (1 - (p - lo) / (hi - lo || 1)) * (H - padT - padB)
  const bw = Math.max(1, Math.min(iw * 0.62, 9))
  const ticks = 4
  const gy = Array.from({ length: ticks + 1 }, (_, i) => lo + ((hi - lo) * i) / ticks)

  const onMove = (e: React.MouseEvent) => {
    const r = svgRef.current!.getBoundingClientRect()
    const vx = ((e.clientX - r.left) / r.width) * W
    const i = Math.round((vx - padL - iw / 2) / iw)
    setHover(i >= 0 && i < n ? i : null)
  }
  const hc = hover != null ? shown[hover] : null

  return (
    <div className="relative">
      <div className="flex gap-1 justify-end mb-1">
        {([[0, '全部'], [120, '120日'], [60, '60日']] as [number, string][]).map(([r, label]) => (
          <button
            key={label}
            onClick={() => setRange(r)}
            className={`px-2 py-0.5 text-[11px] rounded border transition-colors ${
              range === r ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height: 'auto' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {gy.map((g, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={y(g)} y2={y(g)} stroke="var(--line)" strokeWidth={1} />
            <text x={padL - 6} y={y(g) + 3} textAnchor="end" fontSize={10} className="mono" fill="var(--muted)">
              {g.toFixed(0)}
            </text>
          </g>
        ))}
        {shown.map((k, i) => {
          const up = k.c >= k.o
          const col = up ? 'var(--up)' : 'var(--down)'
          return (
            <g key={i}>
              <line x1={x(i)} x2={x(i)} y1={y(k.h)} y2={y(k.l)} stroke={col} strokeWidth={1} />
              <rect x={x(i) - bw / 2} y={y(Math.max(k.o, k.c))} width={bw} height={Math.max(1, Math.abs(y(k.o) - y(k.c)))} fill={col} />
            </g>
          )
        })}
        {hover != null && hc && (
          <line x1={x(hover)} x2={x(hover)} y1={padT} y2={H - padB} stroke="var(--muted)" strokeWidth={1} strokeDasharray="3 3" opacity={0.5} />
        )}
      </svg>
      {hc && (
        <div className="absolute top-8 left-12 pointer-events-none bg-[var(--panel-2)] border border-[var(--line)] rounded px-2 py-1 text-[11px] mono tnum shadow-lg">
          <span className="text-[var(--muted)]">{hc.d}</span>{'  '}開 {hc.o} 高 {hc.h} 低 {hc.l} 收{' '}
          <span style={{ color: sign(hc.c - hc.o) }}>{hc.c}</span>
        </div>
      )}
    </div>
  )
}

/* ---------- equity area chart with hover ---------- */
function Area({ data, height = 220 }: { data: { d: string; e: number }[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const W = 940, H = height, padL = 56, padR = 10, padT = 12, padB = 20
  const n = data.length
  const lo = Math.min(...data.map((p) => p.e))
  const hi = Math.max(...data.map((p) => p.e))
  const x = (i: number) => padL + (i / (n - 1)) * (W - padL - padR)
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo || 1)) * (H - padT - padB)
  const line = data.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.e).toFixed(1)}`).join(' ')
  const area = `${line} L${x(n - 1)},${H - padB} L${x(0)},${H - padB} Z`
  const up = data[n - 1].e >= data[0].e
  const col = up ? 'var(--up)' : 'var(--down)'
  const gy = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * i) / 3)
  const onMove = (e: React.MouseEvent) => {
    const r = svgRef.current!.getBoundingClientRect()
    const i = Math.round((((e.clientX - r.left) / r.width) * W - padL) / ((W - padL - padR) / (n - 1)))
    setHover(i >= 0 && i < n ? i : null)
  }
  const hp = hover != null ? data[hover] : null
  return (
    <div className="relative">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="eq" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={col} stopOpacity={0.28} />
            <stop offset="100%" stopColor={col} stopOpacity={0} />
          </linearGradient>
        </defs>
        {gy.map((g, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={y(g)} y2={y(g)} stroke="var(--line)" strokeWidth={1} />
            <text x={padL - 6} y={y(g) + 3} textAnchor="end" fontSize={10} className="mono" fill="var(--muted)">
              {(g / 1e6).toFixed(2)}M
            </text>
          </g>
        ))}
        <path d={area} fill="url(#eq)" />
        <path d={line} fill="none" stroke={col} strokeWidth={1.6} />
        <circle cx={x(n - 1)} cy={y(data[n - 1].e)} r={3} fill={col} />
        {hover != null && hp && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={padT} y2={H - padB} stroke="var(--muted)" strokeDasharray="3 3" opacity={0.5} />
            <circle cx={x(hover)} cy={y(hp.e)} r={3} fill="var(--fg)" />
          </>
        )}
      </svg>
      {hp && (
        <div className="absolute top-2 left-14 pointer-events-none bg-[var(--panel-2)] border border-[var(--line)] rounded px-2 py-1 text-[11px] mono tnum">
          <span className="text-[var(--muted)]">{hp.d}</span>　權益 {num(hp.e)}
        </div>
      )}
    </div>
  )
}

/* ---------- horizontal bars ---------- */
function HBars({ rows, height = 220 }: { rows: { label: string; value: number; sub?: string; color?: string }[]; height?: number }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9)
  const rh = Math.min(26, (height - 8) / rows.length)
  const W = 940, mid = 250
  return (
    <svg viewBox={`0 0 ${W} ${rows.length * rh + 8}`} className="w-full" style={{ height: 'auto' }}>
      {rows.map((r, i) => {
        const w = (Math.abs(r.value) / max) * (W - mid - 90)
        const col = r.color ?? (r.value >= 0 ? 'var(--up)' : 'var(--down)')
        const yy = i * rh + 4
        return (
          <g key={i}>
            <text x={0} y={yy + rh / 2 + 3} fontSize={12} className="mono" fill="var(--fg)">
              {r.label}
            </text>
            {r.sub && (
              <text x={150} y={yy + rh / 2 + 3} fontSize={11} className="mono" fill="var(--muted)">
                {r.sub}
              </text>
            )}
            <rect x={mid} y={yy + 3} width={Math.max(2, w)} height={rh - 8} rx={2} fill={col} opacity={0.85} />
            <text x={mid + Math.max(2, w) + 6} y={yy + rh / 2 + 3} fontSize={11} className="mono tnum" fill={col}>
              {r.value >= 0 ? '+' : ''}
              {r.value.toFixed(2)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

/* ---------- small ui ---------- */
function Panel({ title, sub, children, className = '' }: { title?: string; sub?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`bg-[var(--panel)] border border-[var(--line)] rounded-lg p-4 ${className}`}>
      {title && (
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-[13px] font-semibold tracking-wide">{title}</h3>
          {sub && <span className="text-[11px] mono text-[var(--muted)]">{sub}</span>}
        </div>
      )}
      {children}
    </section>
  )
}
function Kpi({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="bg-[var(--panel)] border border-[var(--line)] rounded-lg px-4 py-3">
      <div className="eyebrow">{label}</div>
      <div className="text-[22px] font-semibold mono tnum mt-1" style={{ color: tone }}>
        {value}
      </div>
    </div>
  )
}

/* ---------- pages ---------- */
function Overview() {
  const m = D.backtest.metrics
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="最新行情日" value={D.meta.as_of} />
        <Kpi label="回測 Sharpe（成本後）" value={m.sharpe.toFixed(2)} tone={sign(m.sharpe)} />
        <Kpi label="策略容量上限" value={oku(D.capacity.twd)} tone="var(--accent)" />
        <Kpi label="目標持股" value={`${D.capacity.n_positions} 檔`} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Panel title="資料擷取狀態" sub="以交易日曆判缺口">
          <table className="w-full text-[13px] mono">
            <tbody>
              {D.meta.coverage.map((c) => (
                <tr key={c.label} className="border-b border-[var(--line)] last:border-0">
                  <td className="py-2 text-[var(--fg)]">{c.label}</td>
                  <td className="py-2 tnum text-right text-[var(--muted)]">{c.days} 交易日</td>
                  <td className="py-2 pl-3 text-right text-[11px] text-[var(--muted)]">{c.span}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="等權市場指數" sub={`${D.market.length} 交易日`}>
          <Candles data={D.market} height={230} />
        </Panel>
      </div>
    </div>
  )
}

function Market() {
  const [sid, setSid] = useState<string>(D.meta.top_stocks[0])
  return (
    <div className="flex flex-col gap-4">
      <Panel title="等權市場指數 K 線" sub="基期 1000 · 四碼普通股 · 紅漲綠跌">
        <Candles data={D.market} height={320} />
      </Panel>
      <Panel title="個股 K 線" sub="成交值前 8 檔">
        <div className="flex flex-wrap gap-1.5 mb-3">
          {D.meta.top_stocks.map((s) => (
            <button
              key={s}
              onClick={() => setSid(s)}
              className={`px-2.5 py-1 text-[12px] mono rounded border transition-colors ${
                sid === s ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--panel-2)]' : 'border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)]'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <Candles key={sid} data={D.stocks[sid]} height={320} />
      </Panel>
    </div>
  )
}

function Factors() {
  const verdict = (t: number) => (t > 2 ? { txt: '顯著正向', c: 'var(--up)' } : t < -2 ? { txt: '顯著反向', c: 'var(--down)' } : { txt: '無顯著', c: 'var(--muted)' })
  return (
    <div className="flex flex-col gap-4">
      <Panel title="因子健檢 · RankIC t 值（10 日）" sub="連續期 2025-08~12 · |t|>2 為 5% 顯著">
        <HBars
          height={D.factors.length * 26}
          rows={D.factors
            .map((f) => ({ f, h: f.horizons.find((h) => h.h === 10)! }))
            .sort((a, b) => b.h.t - a.h.t)
            .map(({ f, h }) => ({ label: f.name, value: h.t, sub: `IC ${h.ic >= 0 ? '+' : ''}${h.ic}` }))}
        />
      </Panel>
      <Panel title="全因子 IC 明細">
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] mono tnum whitespace-nowrap">
            <thead className="text-[var(--muted)] text-left">
              <tr className="border-b border-[var(--line)]">
                <th className="py-2 pr-3 font-medium">因子</th>
                <th className="py-2 px-2 font-medium text-right">方向</th>
                {[5, 10, 20].map((h) => (
                  <th key={h} className="py-2 px-2 font-medium text-right">{h}日 t</th>
                ))}
                <th className="py-2 pl-3 font-medium">判讀(10日)</th>
              </tr>
            </thead>
            <tbody>
              {D.factors.map((f) => {
                const v = verdict(f.horizons.find((h) => h.h === 10)!.t)
                return (
                  <tr key={f.name} className="border-b border-[var(--line)] last:border-0">
                    <td className="py-2 pr-3 text-[var(--fg)]">{f.name}</td>
                    <td className="py-2 px-2 text-right text-[var(--muted)]">{f.direction > 0 ? '+1' : '−1'}</td>
                    {[5, 10, 20].map((hh) => {
                      const t = f.horizons.find((x) => x.h === hh)!.t
                      return (
                        <td key={hh} className="py-2 px-2 text-right" style={{ color: Math.abs(t) > 2 ? sign(t) : 'var(--muted)' }}>
                          {t >= 0 ? '+' : ''}
                          {t.toFixed(2)}
                        </td>
                      )
                    })}
                    <td className="py-2 pl-3" style={{ color: v.c }}>
                      {v.txt}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-[var(--muted)] mt-3 leading-relaxed">
          月營收 YoY（revenue_yoy）最穩健；投信/外資買超占量比在近期呈反向（追高反轉），方向先驗保留、待更長樣本 walk-forward 定奪。
        </p>
      </Panel>
    </div>
  )
}

function Backtest() {
  const m = D.backtest.metrics
  const kpis: [string, string, string?][] = [
    ['總報酬', pct(m.total_ret, 2), sign(m.total_ret)],
    ['CAGR', pct(m.cagr, 1), sign(m.cagr)],
    ['Sharpe', m.sharpe.toFixed(2), sign(m.sharpe)],
    ['Sortino', m.sortino.toFixed(2)],
    ['最大回撤', pct(m.mdd, 1), 'var(--down)'],
    ['Calmar', m.calmar.toFixed(2)],
    ['勝率', pct(m.win_rate, 0)],
    ['年化周轉', pct(m.turnover, 0)],
  ]
  return (
    <div className="flex flex-col gap-4">
      <Panel title={`策略回測 · ${D.backtest.name}`} sub={`${D.meta.window} · 成本後淨值 · 成交 ${m.n_trades} 筆`}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
          {kpis.map(([l, v, t]) => (
            <div key={l} className="bg-[var(--panel-2)] border border-[var(--line)] rounded px-3 py-2">
              <div className="eyebrow">{l}</div>
              <div className="text-[17px] font-semibold mono tnum mt-0.5" style={{ color: t }}>
                {v}
              </div>
            </div>
          ))}
        </div>
        <Area data={D.backtest.equity} height={260} />
      </Panel>
      <p className="text-[11px] text-[var(--muted)] px-1 leading-relaxed">
        成交假設與 PaperBroker 同源；含手續費/證交稅/價格相依滑價；跨程序決定性。此為範例策略（月營收 YoY 0.6 + 投信連買 0.4），非最終 alpha。
      </p>
    </div>
  )
}

function Flows() {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid md:grid-cols-2 gap-4">
        <Panel title="投信買超前 15" sub={D.meta.as_of}>
          <HBars height={D.institutional.length * 22} rows={D.institutional.map((r) => ({ label: r.sid, value: r.trust / 1000, sub: `${num(r.px, 1)}` }))} />
          <p className="text-[10px] text-[var(--muted)] mt-2">單位：張（千股）</p>
        </Panel>
        <Panel title="月營收 YoY 成長前 15" sub={D.meta.rev_month}>
          <HBars height={D.revenue.length * 22} rows={D.revenue.map((r) => ({ label: r.sid, value: r.yoy, sub: `${r.rev}億`, color: 'var(--accent)' }))} />
          <p className="text-[10px] text-[var(--muted)] mt-2">單位：YoY %</p>
        </Panel>
      </div>
      <Panel title="策略容量分析">
        <div className="flex flex-wrap gap-6 items-end">
          <div>
            <div className="eyebrow">資金容量上限</div>
            <div className="text-[26px] font-semibold mono tnum" style={{ color: 'var(--accent)' }}>
              {oku(D.capacity.twd)}
            </div>
          </div>
          <div>
            <div className="eyebrow">瓶頸標的</div>
            <div className="text-[26px] font-semibold mono">{D.capacity.binding}</div>
          </div>
          <div>
            <div className="eyebrow">參與率上限</div>
            <div className="text-[26px] font-semibold mono tnum">5%</div>
          </div>
        </div>
        <p className="text-[11px] text-[var(--muted)] mt-3">容量 = min（參與率上限 × 20 日 ADV ÷ 權重）；瓶頸為組合中低流動性標的。</p>
      </Panel>
    </div>
  )
}

/* ---------- shell ---------- */
const PAGES = [
  { id: 'overview', label: '總覽', el: <Overview /> },
  { id: 'market', label: '市場 K 線', el: <Market /> },
  { id: 'factors', label: '因子健檢', el: <Factors /> },
  { id: 'backtest', label: '策略回測', el: <Backtest /> },
  { id: 'flows', label: '法人 · 營收 · 容量', el: <Flows /> },
]

export default function App() {
  const [page, setPage] = useState('overview')
  const cur = useMemo(() => PAGES.find((p) => p.id === page)!, [page])
  return (
    <div className="min-h-screen flex text-[var(--fg)]">
      <aside className="w-52 shrink-0 border-r border-[var(--line)] bg-[var(--panel)] flex flex-col sticky top-0 h-screen">
        <div className="px-4 py-4 border-b border-[var(--line)]">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: 'var(--accent)' }} />
            <span className="font-semibold tracking-wide">twquant</span>
          </div>
          <div className="text-[11px] text-[var(--muted)] mt-0.5">台股量化平台 · 監控</div>
        </div>
        <nav className="flex flex-col p-2 gap-0.5">
          {PAGES.map((p) => (
            <button
              key={p.id}
              onClick={() => setPage(p.id)}
              className={`text-left px-3 py-2 rounded text-[13px] transition-colors ${
                page === p.id ? 'bg-[var(--panel-2)] text-[var(--fg)] font-medium' : 'text-[var(--muted)] hover:text-[var(--fg)] hover:bg-[var(--panel-2)]'
              }`}
            >
              {p.id === page && <span className="inline-block w-1 h-1 rounded-full mr-2 align-middle" style={{ background: 'var(--accent)' }} />}
              {p.label}
            </button>
          ))}
        </nav>
        <div className="mt-auto p-4 text-[10px] text-[var(--muted)] border-t border-[var(--line)] leading-relaxed">
          <div>資料快照 {D.meta.as_of}</div>
          <div>TWSE · TPEX · MOPS</div>
          <div className="mt-1">
            <span style={{ color: 'var(--up)' }}>紅漲</span> · <span style={{ color: 'var(--down)' }}>綠跌</span>（台股慣例）
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0">
        <header className="px-6 py-4 border-b border-[var(--line)] flex items-baseline justify-between">
          <h1 className="text-[17px] font-semibold tracking-wide">{cur.label}</h1>
          <span className="text-[12px] mono text-[var(--muted)]">唯讀快照 · 非即時報價</span>
        </header>
        <div className="p-6 max-w-[1180px]">{cur.el}</div>
      </main>
    </div>
  )
}
