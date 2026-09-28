// 個股儀表板頂部：狀態膠囊＋搜尋、KPI 列、工具列、快速選擇；以及尚未選股時的入口畫面。
import { useMemo, useState, type FormEvent } from 'react'
import type { Candle, StockMetaMap } from '../api'
import { pct, priceFmt, zhang } from '../theme'
import { resolveStockQuery } from './search.ts'
import { chip } from './ui'

export type QuickPick = { sid: string; name: string; rank: number; pct: number | null }

const LIST_ID = 'sf-stocklist'

/* ---------- 搜尋：代號或名稱；認不出來就說認不出來，不猜 ---------- */
function useStockSearch(mm: StockMetaMap, onPick: (sid: string) => void) {
  const [q, setQ] = useState('')
  const [miss, setMiss] = useState<string | null>(null)
  const options = useMemo(
    () => Object.entries(mm).map(([c, e]) => `${c} ${e.name ?? ''}`.trim()).sort(),
    [mm],
  )
  const submit = (raw: string) => {
    const sid = resolveStockQuery(raw, mm)
    if (!sid) {
      if (raw.trim()) setMiss(raw.trim())
      return
    }
    setMiss(null)
    setQ('')
    onPick(sid)
  }
  // 從 datalist 點選會一次填入「代號 名稱」整串：整串命中清單就直接進該檔，不必再按 Enter。
  const change = (value: string) => {
    setQ(value)
    setMiss(null)
    if (options.includes(value)) submit(value)
  }
  return { q, miss, options, submit, change }
}

function StockOptions({ options }: { options: string[] }) {
  return <datalist id={LIST_ID}>{options.map((o) => <option key={o} value={o} />)}</datalist>
}

/** big＝放大顯示（個股總分，T-326）。 */
export type Pill = { icon: string; label: string; value: string; color: string; big?: boolean }

export function CommandBar({ sid, name, industry, market, pills, mm, onPick }: {
  sid: string; name: string; industry: string | null; market: string | null
  pills: Pill[]; mm: StockMetaMap; onPick: (sid: string) => void
}) {
  const s = useStockSearch(mm, onPick)
  return (
    <div className="dash-bar">
      <div className="dash-ident">
        <b>{sid}</b>
        <strong>{name}</strong>
        {industry && <span className="dash-tag">{industry}</span>}
        {market && <span className="text-[11px] text-[var(--muted)]">{market}</span>}
      </div>
      <div className="dash-pills">
        {pills.map((p) => (
          <span key={p.icon + p.label} className={`dash-pill${p.big ? ' is-big' : ''}`} style={{ color: p.color }}>
            <i aria-hidden>{p.icon}</i>
            <span>{p.label}<small>{p.value}</small></span>
          </span>
        ))}
      </div>
      <form className="dash-search" role="search" onSubmit={(e: FormEvent) => { e.preventDefault(); s.submit(s.q) }}>
        <input list={LIST_ID} value={s.q} onChange={(e) => s.change(e.target.value)} aria-label="股票代號或名稱"
          placeholder="代號或名稱，如 0050、環球晶" aria-invalid={s.miss !== null} />
        <button type="submit">分析</button>
      </form>
      <StockOptions options={s.options} />
      {s.miss && <div role="alert" className="w-full text-[12px] text-[var(--warn)]">找不到「{s.miss}」：請輸入股票代號，或完整／唯一的名稱片段。</div>}
    </div>
  )
}

/* ---------- KPI 列：全部取自同一份日 K（與主圖同源），沒有就是「—」---------- */
export function KpiStrip({ bars }: { bars: Candle[] | undefined }) {
  const last = bars?.[bars.length - 1]
  const prev = bars && bars.length > 1 ? bars[bars.length - 2] : undefined
  const chg = last && prev ? last.c - prev.c : null
  const tone = chg == null || chg === 0 ? undefined : chg > 0 ? 'var(--up)' : 'var(--down)'
  const vs = (v: number) => (prev ? (v > prev.c ? 'var(--up)' : v < prev.c ? 'var(--down)' : undefined) : undefined)
  const cells: [string, string, string | undefined][] = [
    ['收盤價', last ? priceFmt(last.c) : '—', tone],
    ['漲跌', chg == null ? '—' : `${chg > 0 ? '+' : ''}${chg.toFixed(2)}`, tone],
    ['漲幅', chg == null || !prev ? '—' : pct(chg / prev.c, 2), tone],
    ['成交量（張）', last?.v == null ? '—' : zhang(last.v).toLocaleString(), undefined],
    ['開盤', last ? priceFmt(last.o) : '—', last ? vs(last.o) : undefined],
    ['最高', last ? priceFmt(last.h) : '—', last ? vs(last.h) : undefined],
    ['最低', last ? priceFmt(last.l) : '—', last ? vs(last.l) : undefined],
    ['振幅', last && prev ? `${(((last.h - last.l) / prev.c) * 100).toFixed(2)}%` : '—', undefined],
    ['最新交易日', last?.d ?? '—', 'var(--accent)'],
    ['資料筆數', bars ? `${bars.length.toLocaleString()} 日` : '—', 'var(--accent)'],
  ]
  return (
    <div className="dash-kpis" aria-label="最新交易日行情">
      {cells.map(([label, value, color]) => (
        <div key={label} className="dash-kpi"><span>{label}</span><strong style={{ color }}>{value}</strong></div>
      ))}
    </div>
  )
}

/* ---------- 工具列：資料來源、區間、追蹤、匯出 ---------- */
export function ToolRow({ bars, watched, onToggleWatch, onPng, onCsv }: {
  bars: Candle[] | undefined; watched: boolean; onToggleWatch: () => void; onPng: () => void; onCsv: () => void
}) {
  return (
    <div className="dash-tools">
      <span className="dash-source">資料來源：PG 供應層（serving）</span>
      {bars && bars.length > 0 && <span>區間 {bars[0].d} ～ {bars[bars.length - 1].d}，共 {bars.length.toLocaleString()} 個交易日</span>}
      <span className="ml-auto" />
      <button type="button" className="dash-btn" aria-pressed={watched} onClick={onToggleWatch} title={watched ? '點擊取消追蹤' : '加入追蹤（存於本機瀏覽器）'}>
        {watched ? '★ 已追蹤' : '☆ 加入追蹤'}
      </button>
      <button type="button" className="dash-btn" disabled={!bars?.length} onClick={onPng}>⬇ K 線圖 PNG</button>
      <button type="button" className="dash-btn" disabled={!bars?.length} onClick={onCsv}>⬇ 日 K CSV</button>
      <button type="button" className="dash-btn" onClick={() => window.print()}>🖨 列印／PDF</button>
    </div>
  )
}

/* ---------- 快速選擇：追蹤股＋宇宙綜合排名（描述性定位，非預測訊號 D-020）---------- */
export function QuickRow({ sid, watch, hot, hotIsRanked, mm, onPick }: {
  sid: string; watch: string[]; hot: QuickPick[]; hotIsRanked: boolean; mm: StockMetaMap; onPick: (sid: string) => void
}) {
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto hscroll">
      {watch.length > 0 && <span className="shrink-0 text-[10px] tracking-wider text-[var(--muted)]">追蹤</span>}
      {watch.map((s) => (
        <button key={`w${s}`} type="button" onClick={() => onPick(s)} className={chip(sid === s)}>
          <span style={{ color: 'var(--warn)' }}>★</span> {s}
          {mm[s]?.name && <span className="ml-1 text-[11px] opacity-75">{mm[s]?.name}</span>}
        </button>
      ))}
      <span className="shrink-0 text-[10px] tracking-wider text-[var(--muted)] ml-1" title="宇宙綜合排名＝描述性相對定位，非預測訊號（D-020）">
        {hotIsRanked ? `宇宙前 ${hot.length}` : '熱門'}
      </span>
      {hot.map((h) => (
        <button key={`h${h.sid}`} type="button" onClick={() => onPick(h.sid)} className={chip(sid === h.sid)} title={h.pct != null ? `綜合分位 ${Math.round(h.pct * 100)}%` : undefined}>
          {h.rank > 0 && <span className="mr-1 opacity-60">{h.rank}</span>}
          {h.sid}
          {h.name && <span className="ml-1 text-[11px] opacity-75">{h.name}</span>}
        </button>
      ))}
    </div>
  )
}

/* ---------- 入口：尚未選股 ---------- */
export function StockHero({ mm, watch, hot, hotIsRanked, live, onPick }: {
  mm: StockMetaMap; watch: string[]; hot: QuickPick[]; hotIsRanked: boolean; live: boolean; onPick: (sid: string) => void
}) {
  const s = useStockSearch(mm, onPick)
  return (
    <section className="dash-hero" aria-label="選擇個股">
      <h2>台股個股研究儀表板</h2>
      <p>
        輸入股票代號或名稱，讀取 PG 供應層的日 K、三大法人、融資券、主力券商、月營收、因子體檢與 ML 推薦事件，產生 18 面板儀表板。
        <br />所有數字都是真實資料；讀不到就明示「讀取失敗」或「零筆」，不產生任何模擬數據。
      </p>
      <form onSubmit={(e: FormEvent) => { e.preventDefault(); s.submit(s.q) }} role="search">
        <input list={LIST_ID} value={s.q} onChange={(e) => s.change(e.target.value)} aria-label="股票代號或名稱" placeholder="例如 2330" autoFocus aria-invalid={s.miss !== null} />
        <button type="submit">開始分析</button>
      </form>
      <StockOptions options={s.options} />
      {s.miss && <div role="alert" className="text-[13px] text-[var(--warn)] -mt-2 mb-3">找不到「{s.miss}」：請輸入股票代號，或完整／唯一的名稱片段。</div>}
      <div className="dash-quick">
        {watch.length > 0 && <div className="dash-quick-label">我的追蹤</div>}
        {watch.map((w) => <button key={`w${w}`} type="button" onClick={() => onPick(w)}>{w}{mm[w]?.name && <span>{mm[w]?.name}</span>}</button>)}
        <div className="dash-quick-label">{hotIsRanked ? '宇宙綜合排名前段（描述性相對定位，非預測訊號）' : '快速選擇'}</div>
        {hot.map((h) => <button key={`h${h.sid}`} type="button" onClick={() => onPick(h.sid)}>{h.sid}{h.name && <span>{h.name}</span>}</button>)}
      </div>
      {!live && <p>API 未連線：目前只有內嵌快照的幾檔有 K 線，其餘面板會顯示讀取失敗。</p>}
    </section>
  )
}
