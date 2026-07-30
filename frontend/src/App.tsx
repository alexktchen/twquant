import { useEffect, useMemo, useState } from 'react'
import {
  fetchBacktest,
  fetchCapacity,
  fetchFactors,
  fetchHealth,
  fetchHeartbeat,
  fetchHolidayCalendar,
  fetchIndices,
  fetchIngestGaps,
  fetchIngestJobs,
  fetchIngestMatrix,
  fetchIngestStatus,
  fetchIngestTracks,
  fetchInstitutional,
  fetchMarket,
  fetchMeta,
  fetchRevenue,
  fetchSectorFlows,
  fetchStockList,
  fetchStockMeta,
  fetchStockOhlc,
  fetchStrategies,
  startHolidayFetch,
  startIngestFetch,
  startIngestJob,
  startPipelineJob,
  type Backtest as BacktestT,
  type Candle,
  type CellState,
  type Fac,
  type Heartbeat,
  type HolidayCalendar,
  type Indices,
  type IngestGap,
  type IngestGaps,
  type IngestJob,
  type IngestJobs,
  type IngestMatrix,
  type IngestStatus,
  type IngestTrack,
  type Inst,
  type Rev,
  type SectorFlows,
  type StockMetaMap,
  type StrategyInfo,
} from './api'
import { CandleChart, EquityChart, HBars, IndexLine, RangeCandle } from './charts'
import { FALLBACK } from './fallback'
import { MetaCtx, useMeta, useResource, type MetaCtxT } from './hooks'
import { StockFactorsPage } from './stock'
import { ACC, fnum, labelOf, num, oku, pct, sign } from './theme'
import { Kpi, Panel, Skeleton } from './ui'

/* ---------- ingestion status (live; falls back to snapshot coverage) ---------- */
function IngestPanel() {
  const { meta, live } = useMeta()
  const [st, setSt] = useState<IngestStatus | null>(null)
  useEffect(() => {
    if (!live) return
    let alive = true
    fetchIngestStatus().then((s) => alive && setSt(s)).catch(() => {})
    return () => {
      alive = false
    }
  }, [live])

  if (!st) {
    // 快照 fallback：只有涵蓋交易日數與跨度
    return (
      <Panel title="資料擷取狀態" sub={live ? '載入中…' : '快照涵蓋'}>
        <table className="w-full text-[13px] mono">
          <tbody>
            {meta.coverage.map((c) => (
              <tr key={c.label} className="border-b border-[var(--line)] last:border-0">
                <td className="py-2 text-[var(--fg)]">{c.label}</td>
                <td className="py-2 tnum text-right text-[var(--muted)]">{c.days} 交易日</td>
                <td className="py-2 pl-3 text-right text-[11px] text-[var(--muted)]">{c.span}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    )
  }

  return (
    <Panel title="資料擷取狀態" sub={`以交易日曆判缺口 · 最後擷取 ${st.last_run ? st.last_run.slice(0, 16) : '—'}`}>
      <table className="w-full text-[13px] mono">
        <tbody>
          {st.sources.map((s) => (
            <tr key={s.label} className="border-b border-[var(--line)] last:border-0">
              <td className="py-2 text-[var(--fg)]">{s.label}</td>
              <td className="py-2 tnum text-right text-[var(--muted)]">{s.days} 交易日</td>
              <td className="py-2 pl-3 tnum text-right" style={{ color: s.gaps ? 'var(--warn)' : 'var(--good)' }}>
                {s.gaps ? `缺 ${s.gaps}` : '無缺口'}
              </td>
              <td className="py-2 pl-3 text-right text-[11px] text-[var(--muted)]">{s.span}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {st.recent_failures.length > 0 && (
        <details className="mt-3">
          <summary className="text-[11px] text-[var(--warn)] cursor-pointer">
            最近失敗 {st.recent_failures.length} 筆（擷取紀錄 {st.records} 筆 · 失敗 {st.failed} 筆）
          </summary>
          <div className="mt-2 max-h-40 overflow-y-auto text-[11px] mono text-[var(--muted)]">
            {st.recent_failures.map((f, i) => (
              <div key={i} className="py-0.5 border-b border-[var(--line)] last:border-0">
                <span className="text-[var(--fg)]">{f.attempt_ts.slice(0, 19)}</span>　{f.source}/{f.table} @ {f.date}
              </div>
            ))}
          </div>
        </details>
      )}
    </Panel>
  )
}

/* ---------- pages ---------- */
function Overview() {
  const { meta } = useMeta()
  const [ws, we] = meta.window.split('~')
  const market = useResource('market', fetchMarket, () => FALLBACK.market)
  const cap = useResource('capacity', fetchCapacity, () => FALLBACK.capacity)
  // 總覽只需 Sharpe，但回測較重 → 進度式：頁面即刻可用，KPI 就緒才填。與回測頁共用快取鍵。
  const bt = useResource(`backtest|${ws}|${we}`, () => fetchBacktest(ws, we), () => FALLBACK.backtest)
  const m = bt.data?.metrics
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="最新行情日" value={meta.as_of ?? '—'} />
        <Kpi label="回測 Sharpe（成本後）" value={m ? m.sharpe.toFixed(2) : '…'} tone={m ? sign(m.sharpe) : undefined} />
        <Kpi label="策略容量上限" value={cap.data ? oku(cap.data.twd) : '…'} tone="var(--accent)" />
        <Kpi label="目標持股" value={cap.data ? `${cap.data.n_positions} 檔` : '…'} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <IngestPanel />
        <Panel title="等權市場指數" sub={market.data ? `${market.data.length} 交易日` : '載入中…'}>
          {market.data ? <CandleChart data={market.data} height={230} /> : <Skeleton height={230} />}
        </Panel>
      </div>
    </div>
  )
}

function Market() {
  const { meta, live } = useMeta()
  const market = useResource('market', fetchMarket, () => FALLBACK.market)
  const indices = useResource('indices', fetchIndices, () => ({ taiex: [], tpex: [] }) as Indices)
  const [list, setList] = useState<string[]>(meta.top_stocks)
  const [sid, setSid] = useState<string>(() => meta.top_stocks[0] ?? '') // 空資料湖無個股時避免 undefined
  // OHLC 以 sid 標記：換股瞬間舊資料因 sid 不符而不渲染，杜絕「新標題配舊 K 線」殘影
  const [ohlc, setOhlc] = useState<{ sid: string; rows: Candle[] } | undefined>(() =>
    live || !sid ? undefined : { sid, rows: FALLBACK.stocks[sid] ?? [] },
  )
  const [loading, setLoading] = useState(false)

  // 即時模式：拉成交值前 150 檔完整選單（快照只內嵌前 8）
  useEffect(() => {
    if (!live) return
    let alive = true
    fetchStockList().then((s) => alive && s.length && setList(s)).catch(() => {})
    return () => {
      alive = false
    }
  }, [live])

  // 不快取：選到個股就即時重抓 OHLC（離線退回內嵌樣本）
  useEffect(() => {
    if (!sid) return // 無選定個股（空資料湖）時不打 /api/stocks/undefined
    if (!live) {
      setOhlc({ sid, rows: FALLBACK.stocks[sid] ?? [] })
      return
    }
    let alive = true
    setLoading(true)
    setOhlc(undefined)
    fetchStockOhlc(sid)
      .then((k) => alive && setOhlc({ sid, rows: k }))
      .catch(() => alive && setOhlc({ sid, rows: FALLBACK.stocks[sid] ?? [] }))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [sid, live])
  const series = ohlc?.sid === sid ? ohlc.rows : undefined

  const metaMap = useResource('stock-meta', fetchStockMeta, () => ({}) as StockMetaMap)
  const mm = metaMap.data ?? {}
  const chips = meta.top_stocks
  const taiex = indices.data?.taiex ?? []
  const otc = indices.data?.tpex ?? []
  const idxHint = (
    <div className="h-[300px] flex items-center justify-center text-center px-4 text-[13px] text-[var(--muted)]">
      尚無官方指數資料——請先{' '}
      <code className="mono mx-1 text-[var(--fg)]">twquant backfill-index --from 2025-07 --to 2026-07</code>
    </div>
  )
  return (
    <div className="flex flex-col gap-4">
      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="加權指數 TAIEX" sub="發行量加權股價指數 · 官方 · 紅漲綠跌">
          {taiex.length ? (
            <RangeCandle
              data={taiex.map((p) => ({ d: p.d, o: p.o ?? p.c, h: p.h ?? p.c, l: p.l ?? p.c, c: p.c }))}
              height={300}
            />
          ) : indices.loading ? (
            <Skeleton height={300} />
          ) : (
            idxHint
          )}
        </Panel>
        <Panel title="櫃買指數 TPEx" sub="上櫃指數 · 官方 · 收盤點數">
          {otc.length ? <IndexLine data={otc} height={300} /> : indices.loading ? <Skeleton height={300} /> : idxHint}
        </Panel>
      </div>
      <Panel title="等權市場指數 K 線" sub="自算基準 · 基期 1000 · 四碼普通股 · 紅漲綠跌 · 可拖曳縮放">
        {market.data ? <RangeCandle data={market.data} height={340} volume /> : <Skeleton height={340} />}
      </Panel>
      <Panel title="個股 K 線" sub={live ? `成交值前 ${list.length} 檔（即時）` : '成交值前 8 檔（快照）'}>
        <div className="flex flex-wrap items-center gap-1.5 mb-3">
          {chips.map((s) => (
            <button
              key={s}
              onClick={() => setSid(s)}
              className={`px-2.5 py-1 text-[12px] mono rounded border transition-colors ${
                sid === s ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--panel-2)]' : 'border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)]'
              }`}
            >
              {s}
              {mm[s]?.name && <span className="ml-1 text-[11px] opacity-75">{mm[s]?.name}</span>}
            </button>
          ))}
          {live && list.length > chips.length && (
            <select
              value={chips.includes(sid) ? '' : sid}
              onChange={(e) => e.target.value && setSid(e.target.value)}
              className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
            >
              <option value="">更多個股（前 {list.length}）…</option>
              {list.map((s) => (
                <option key={s} value={s}>{labelOf(mm, s)}</option>
              ))}
            </select>
          )}
        </div>
        {series && series.length > 0 ? (
          <RangeCandle key={sid} data={series} height={340} volume />
        ) : (
          <div className="h-[340px] flex items-center justify-center text-[13px] text-[var(--muted)]">
            {loading ? `載入 ${labelOf(mm, sid)} K 線…` : sid ? `無 ${labelOf(mm, sid)} 資料` : '資料湖尚無個股資料'}
          </div>
        )}
      </Panel>
    </div>
  )
}

/* ---------- factor display metadata (name → 中文名 + 說明) ---------- */
const FACTOR_META: Record<string, { zh: string; desc: string }> = {
  'fundamental.revenue_yoy': { zh: '月營收 YoY', desc: '最新已公布月營收的年增率。正向因子：營收年增動能延續（PEAD 效應基礎）。' },
  'fundamental.revenue_accel': { zh: '月營收 YoY 加速', desc: '最新公布 YoY 減前一月 YoY（成長的二階變化）。正向：營收成長正在加速。' },
  'chip.trust_net_10d': { zh: '投信 10 日買超占量比', desc: '投信近 10 日累計淨買超股數 ÷ 10 日成交量。正向先驗：投信作帳與群聚買進。' },
  'chip.foreign_net_10d': { zh: '外資 10 日買超占量比', desc: '外資近 10 日累計淨買超股數 ÷ 10 日成交量。訊號較投信弱，用於「投信買+外資賣」象限。' },
  'chip.trust_streak': { zh: '投信連買天數', desc: '截至當日投信連續淨買超的天數。正向：連買越久、作帳意圖越強。' },
  'chip.margin_velocity_5d': { zh: '5 日融資增速', desc: '近 5 日融資餘額淨增 ÷ 5 日成交量（散戶加碼密集度代理）。反向：散戶追買後股價傾向落後。' },
  'price.mom_20d': { zh: '20 日動能', desc: '還原收盤價相對 20 個交易日前的漲跌幅。正向：近月強勢股延續動能。' },
  'price.high_52w': { zh: '52 週高點接近度', desc: '收盤價 ÷ 近 52 週最高價（≤1，越接近 1 越貼近年高）。正向：貼近年高者續強。' },
  'price.reversal_5d': { zh: '5 日短期反轉', desc: '近 5 個交易日的累計報酬。反向因子：短線漲多者傾向回落。' },
  'volume.vol_ratio_5d': { zh: '5 日量比', desc: '今日成交量 ÷ 前 5 日均量（不含今日）。正向：異常放量偏多。' },
  'volume.turnover_z_20d': { zh: '20 日量能 Z 分數', desc: '(今日量 − 20 日均量) ÷ 20 日量的標準差。正向：相對放量偏多。' },
  'price.mom_60d': { zh: '60 日中期動能', desc: '還原收盤相對 60 個交易日前的漲跌幅。正向：中期強勢延續（連續期實測偏反轉，待更長樣本）。' },
  'price.vol_20d': { zh: '20 日波動率', desc: '日報酬的 20 日滾動標準差。反向因子：低波動股長期報酬較佳（低波動異象）。' },
  'price.rs_market_20d': { zh: '相對大盤強弱', desc: '個股 20 日報酬 − 加權指數 20 日報酬。正向：相對大盤強者續強（需 index_daily）。' },
  'chip.foreign_streak': { zh: '外資連買天數', desc: '截至當日外資連續淨買超天數。正向：外資連買越久、買盤越持續。' },
  'chip.inst_net_10d': { zh: '三大法人 10 日買超占量', desc: '外資+投信+自營 10 日累計淨買超 ÷ 10 日量。連續期實測強烈反向（t≈−10，法人擁擠出場）。' },
  'chip.short_margin_ratio': { zh: '券資比', desc: '融券餘額 ÷ 融資餘額。先驗正向（高券資比＝軋空燃料）；實際符號待更長樣本。' },
  'price.overnight_ret_20d': { zh: '20 日隔夜累計報酬', desc: 'Σln(開盤/前收) 近 20 日累計。反向：隔夜強者其後走弱（Lou/Polk/Skouras 2019 跨時段反轉；台股 OMOM 為負，Ho et al. 2023）。' },
  'price.intraday_ret_20d': { zh: '20 日盤中累計報酬', desc: 'Σln(收盤/開盤) 近 20 日累計。正向：盤中動能延續——投資人對盤中資訊反應不足（台股 IMOM 為正，Ho et al. 2023）。' },
  'price.residual_reversal_5d': { zh: '5 日產業殘差反轉', desc: '個股 5 日報酬減同產業等權平均。反向：濾掉產業共同走勢後只有殘差會反轉，訊號更純（Da/Liu/Schaumburg 2014）。' },
  'price.mom_1d': { zh: '1 日動能', desc: '前一交易日報酬。正向先驗：台股屬日動能市場——1 日續勢、第 2 日起反轉（Gao et al. NBER 31839）。預測窗在 1 日，5/10/20 日 IC 呈負屬預期。' },
}
const CAT_ZH: Record<string, string> = { price: '價格', volume: '量能', chip: '籌碼', fundamental: '基本面', market: '市場' }
const factorZh = (name: string) => FACTOR_META[name]?.zh ?? name
const factorCat = (name: string) => CAT_ZH[name.split('.')[0]] ?? name.split('.')[0]
const factorVerdict = (t: number) =>
  t > 2 ? { txt: '顯著正向', c: 'var(--up)' } : t < -2 ? { txt: '顯著反向', c: 'var(--down)' } : { txt: '無顯著', c: 'var(--muted)' }

/* ---------- hover tooltip: 因子全部細項（cursor-following, fixed → 不被表格 overflow 裁切）---------- */
function FactorTooltip({ f, x, y }: { f: Fac; x: number; y: number }) {
  const W = 340
  const left = Math.max(8, Math.min(x + 16, window.innerWidth - W - 8))
  const top = Math.max(8, Math.min(y + 16, window.innerHeight - 260))
  const t10 = f.horizons.find((h) => h.h === 10)?.t ?? 0
  const v = factorVerdict(t10)
  return (
    <div
      className="fixed z-50 pointer-events-none bg-[var(--panel-2)] border border-[var(--line)] rounded-lg p-3 shadow-xl"
      style={{ left, top, width: W }}
    >
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <span className="text-[13px] font-semibold text-[var(--fg)]">{factorZh(f.name)}</span>
        <span className="text-[10px] mono text-[var(--muted)]">{f.name}</span>
      </div>
      <div className="text-[11px] text-[var(--muted)] mb-2">
        類別 {factorCat(f.name)}　·　方向{' '}
        <span style={{ color: f.direction > 0 ? 'var(--up)' : 'var(--down)' }}>
          {f.direction > 0 ? '+1（值大看多）' : '−1（值大看空）'}
        </span>
      </div>
      <p className="text-[11px] text-[var(--fg)] leading-relaxed mb-2">{FACTOR_META[f.name]?.desc ?? '（無說明）'}</p>
      <table className="w-full text-[11px] mono tnum">
        <thead className="text-[var(--muted)]">
          <tr>
            <th className="text-left font-medium pr-2">視窗</th>
            <th className="text-right font-medium px-1">IC均值</th>
            <th className="text-right font-medium px-1">t值</th>
            <th className="text-right font-medium px-1">IR</th>
            <th className="text-right font-medium px-1">多空價差</th>
            <th className="text-right font-medium pl-1">樣本日</th>
          </tr>
        </thead>
        <tbody>
          {f.horizons.map((h) => (
            <tr key={h.h}>
              <td className="text-left text-[var(--fg)] pr-2">{h.h}日</td>
              <td className="text-right px-1">{fnum(h.ic, 4)}</td>
              <td className="text-right px-1" style={{ color: h.t != null && Math.abs(h.t) > 2 ? sign(h.t) : 'var(--muted)' }}>
                {fnum(h.t, 2)}
              </td>
              <td className="text-right px-1">{fnum(h.ir, 3)}</td>
              <td className="text-right px-1">{fnum(h.ls, 4)}</td>
              <td className="text-right pl-1 text-[var(--muted)]">{h.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-[10px] text-[var(--muted)] mt-2">
        判讀（10 日，|t|&gt;2 為 5% 顯著）：<span style={{ color: v.c }}>{v.txt}</span>
      </div>
    </div>
  )
}

function Factors() {
  const facs = useResource('factors', fetchFactors, () => FALLBACK.factors)
  const { meta } = useMeta()
  const [hover, setHover] = useState<{ f: Fac; x: number; y: number } | null>(null)
  const factors = facs.data
  const period = meta.window.split('~').join(' ~ ') // 因子 IC 資料區間，如 2025-08-01 ~ 2025-12-31
  if (!factors) {
    return (
      <div className="flex flex-col gap-4">
        <Panel title="因子健檢 · RankIC t 值（10 日）" sub={`連續期 ${period} · 計算中…`}>
          <Skeleton height={280} />
        </Panel>
        <Panel title="全因子 IC 明細">
          <Skeleton height={320} />
        </Panel>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <Panel title="因子健檢 · RankIC t 值（10 日）" sub={`連續期 ${period} · |t|>2 為 5% 顯著`}>
        <HBars
          height={factors.length * 26}
          rows={factors
            .map((f) => ({ f, h: f.horizons.find((h) => h.h === 10)! }))
            .sort((a, b) => (b.h.t ?? 0) - (a.h.t ?? 0))
            .map(({ f, h }) => ({ label: factorZh(f.name), value: h.t ?? 0, sub: `IC ${fnum(h.ic, 4)}` }))}
        />
      </Panel>
      <Panel title="全因子 IC 明細" sub={`資料區間 ${period} · 滑鼠移到因子看全部細項`}>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] mono tnum whitespace-nowrap">
            <thead className="text-[var(--muted)] text-left">
              <tr className="border-b border-[var(--line)]">
                <th className="py-2 pr-3 font-medium">因子</th>
                <th className="py-2 px-2 font-medium text-right">方向</th>
                {[1, 5, 10, 20].map((h) => (
                  <th key={h} className="py-2 px-2 font-medium text-right">{h}日 t</th>
                ))}
                <th className="py-2 pl-3 font-medium">判讀(10日)</th>
              </tr>
            </thead>
            <tbody>
              {factors.map((f) => {
                const v = factorVerdict(f.horizons.find((h) => h.h === 10)?.t ?? 0)
                return (
                  <tr
                    key={f.name}
                    className="border-b border-[var(--line)] last:border-0 cursor-help hover:bg-[var(--panel-2)]"
                    onMouseMove={(e) => setHover({ f, x: e.clientX, y: e.clientY })}
                    onMouseLeave={() => setHover((h) => (h?.f.name === f.name ? null : h))}
                  >
                    <td className="py-2 pr-3 text-[var(--fg)]">
                      <span className="underline decoration-dotted decoration-[var(--muted)] underline-offset-2">
                        {factorZh(f.name)}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-right text-[var(--muted)]">{f.direction > 0 ? '+1' : '−1'}</td>
                    {[1, 5, 10, 20].map((hh) => {
                      const t = f.horizons.find((x) => x.h === hh)?.t ?? null
                      return (
                        <td key={hh} className="py-2 px-2 text-right" style={{ color: t != null && Math.abs(t) > 2 ? sign(t) : 'var(--muted)' }}>
                          {fnum(t, 2)}
                        </td>
                      )
                    })}
                    <td className="py-2 pl-3" style={{ color: v.c }}>{v.txt}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-[var(--muted)] mt-3 leading-relaxed">
          月營收 YoY 最穩健；投信/外資買超占量比近期呈反向（追高反轉），方向先驗保留、待更長樣本 walk-forward 定奪。
        </p>
      </Panel>
      {hover && <FactorTooltip f={hover.f} x={hover.x} y={hover.y} />}
    </div>
  )
}

function Backtest() {
  const { meta, live } = useMeta()
  const [ws, we] = meta.window.split('~')
  const def = useResource(`backtest|${ws}|${we}`, () => fetchBacktest(ws, we), () => FALLBACK.backtest)
  const strats = useResource('strategies', fetchStrategies, () => [] as StrategyInfo[])
  const metaMap = useResource('stock-meta', fetchStockMeta, () => ({}) as StockMetaMap)
  const [override, setOverride] = useState<BacktestT | null>(null)
  const [win, setWin] = useState<{ start: string; end: string }>({ start: ws, end: we })
  const [strat, setStrat] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const apply = () => {
    setBusy(true)
    setErr(null)
    fetchBacktest(win.start, win.end, strat || undefined)
      .then(setOverride)
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false))
  }

  const bt = override ?? def.data
  if (!bt) {
    return (
      <div className="flex flex-col gap-4">
        <Panel title="策略回測" sub="跑回測…">
          <Skeleton height={420} />
        </Panel>
      </div>
    )
  }
  const m = bt.metrics
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
      <Panel title={`策略回測 · ${bt.name}`} sub={`${bt.window ?? meta.window} · 成本後淨值 · 成交 ${m.n_trades} 筆`}>
        {live && (
          <div className="flex flex-wrap items-end gap-2 mb-4">
            <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
              策略
              <select
                value={strat}
                onChange={(e) => setStrat(e.target.value)}
                className="px-2 py-1 text-[12px] rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
              >
                <option value="">預設（sample_revenue_trust）</option>
                {(strats.data ?? []).map((s) => (
                  <option key={s.id} value={s.id} title={s.description}>
                    {s.name}（{s.id}）
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
              起
              <input
                type="date"
                value={win.start}
                onChange={(e) => setWin((w) => ({ ...w, start: e.target.value }))}
                className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
              迄
              <input
                type="date"
                value={win.end}
                onChange={(e) => setWin((w) => ({ ...w, end: e.target.value }))}
                className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
              />
            </label>
            <button
              onClick={apply}
              disabled={busy}
              className="px-3 py-1.5 text-[12px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--panel-2)] disabled:opacity-50 transition-colors"
            >
              {busy ? '回測中…' : '重跑'}
            </button>
            {err && <span className="text-[11px] text-[var(--down)] self-center">{err}</span>}
          </div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
          {kpis.map(([l, v, t]) => (
            <div key={l} className="bg-[var(--panel-2)] border border-[var(--line)] rounded px-3 py-2">
              <div className="eyebrow">{l}</div>
              <div className="text-[17px] font-semibold mono tnum mt-0.5" style={{ color: t }}>{v}</div>
            </div>
          ))}
        </div>
        <EquityChart data={bt.equity} height={280} />
      </Panel>
      {bt.holdings && bt.holdings.length > 0 && (
        <Panel
          title={`期末持倉 · ${bt.holdings.length} 檔`}
          sub={`回測窗結束日仍持有 · 持倉市值 ${Math.round(
            bt.holdings.reduce((a, h) => a + h.value, 0),
          ).toLocaleString()} 元`}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] mono tnum">
              <thead>
                <tr className="text-[var(--muted)] text-left">
                  <th className="py-1 pr-3 font-normal">個股</th>
                  <th className="py-1 pr-3 font-normal text-right">買進日</th>
                  <th className="py-1 pr-3 font-normal text-right">均價</th>
                  <th className="py-1 pr-3 font-normal text-right">收盤</th>
                  <th className="py-1 pr-3 font-normal text-right">報酬率</th>
                  <th className="py-1 pr-3 font-normal text-right">最大虧損</th>
                  <th className="py-1 pr-3 font-normal text-right">股數</th>
                  <th className="py-1 pr-3 font-normal text-right">市值</th>
                  <th className="py-1 font-normal text-right">權重</th>
                </tr>
              </thead>
              <tbody>
                {bt.holdings.map((h) => (
                  <tr key={h.sid} className="border-t border-[var(--line)]">
                    <td className="py-1 pr-3">{labelOf(metaMap.data ?? {}, h.sid)}</td>
                    <td className="py-1 pr-3 text-right">{h.entry ?? '—'}</td>
                    <td className="py-1 pr-3 text-right">{h.cost != null ? h.cost.toFixed(2) : '—'}</td>
                    <td className="py-1 pr-3 text-right">{h.px.toFixed(2)}</td>
                    <td
                      className="py-1 pr-3 text-right"
                      style={{ color: (h.ret ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }}
                    >
                      {h.ret != null ? `${(h.ret * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="py-1 pr-3 text-right" style={{ color: 'var(--down)' }}>
                      {h.max_loss != null ? `${(h.max_loss * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="py-1 pr-3 text-right">{h.shares.toLocaleString()}</td>
                    <td className="py-1 pr-3 text-right">{Math.round(h.value).toLocaleString()}</td>
                    <td className="py-1 text-right">{(h.w * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      <p className="text-[11px] text-[var(--muted)] px-1 leading-relaxed">
        成交假設與 PaperBroker 同源；含手續費/證交稅/價格相依滑價；跨程序決定性。
        {(() => {
          const cur = (strats.data ?? []).find((s) => s.name === bt.name || s.id === bt.name)
          return cur?.description ? `策略：${cur.description}` : '策略權重非最終 alpha，僅供驗證。'
        })()}
      </p>
    </div>
  )
}

function Flows() {
  const { meta } = useMeta()
  const metaMap = useResource('stock-meta', fetchStockMeta, () => ({}) as StockMetaMap)
  const mm = metaMap.data ?? {}
  const inst = useResource('institutional', fetchInstitutional, () => FALLBACK.institutional as Inst[])
  const rev = useResource('revenue', fetchRevenue, () => ({
    rev_month: FALLBACK.meta.rev_month,
    revenue: FALLBACK.revenue as Rev[],
  }))
  const cap = useResource('capacity', fetchCapacity, () => FALLBACK.capacity)
  return (
    <div className="flex flex-col gap-4">
      <div className="grid md:grid-cols-2 gap-4">
        <Panel title="投信買超前 15" sub={meta.as_of ?? ''}>
          {inst.data ? (
            <HBars height={inst.data.length * 22} rows={inst.data.map((r) => ({ label: labelOf(mm, r.sid), value: r.trust / 1000, sub: `${num(r.px, 1)}` }))} />
          ) : (
            <Skeleton height={330} />
          )}
          <p className="text-[10px] text-[var(--muted)] mt-2">單位：張（千股）</p>
        </Panel>
        <Panel title="月營收 YoY 成長前 15" sub={rev.data?.rev_month ?? meta.rev_month ?? ''}>
          {rev.data ? (
            <HBars height={rev.data.revenue.length * 22} rows={rev.data.revenue.map((r) => ({ label: labelOf(mm, r.sid), value: r.yoy, sub: `${r.rev}億`, color: ACC }))} />
          ) : (
            <Skeleton height={330} />
          )}
          <p className="text-[10px] text-[var(--muted)] mt-2">單位：YoY %</p>
        </Panel>
      </div>
      <Panel title="策略容量分析">
        {cap.data ? (
          <>
            <div className="flex flex-wrap gap-6 items-end">
              <div>
                <div className="eyebrow">資金容量上限</div>
                <div className="text-[26px] font-semibold mono tnum" style={{ color: 'var(--accent)' }}>{oku(cap.data.twd)}</div>
              </div>
              <div>
                <div className="eyebrow">瓶頸標的</div>
                <div className="text-[26px] font-semibold mono">{cap.data.binding}</div>
              </div>
              <div>
                <div className="eyebrow">參與率上限</div>
                <div className="text-[26px] font-semibold mono tnum">5%</div>
              </div>
            </div>
            <p className="text-[11px] text-[var(--muted)] mt-3">容量 = min（參與率上限 × 20 日 ADV ÷ 權重）；瓶頸為組合中低流動性標的。</p>
          </>
        ) : (
          <Skeleton height={80} />
        )}
      </Panel>
    </div>
  )
}

/* ---------- 擷取後台（熱力圖 + 漏抓清單 + 一鍵補抓 / 引導指令）---------- */
// 運維色盤：綠=有／橙=擷取失敗／紅=漏抓／暗=尚未涵蓋。刻意不套台股紅漲綠跌（紅在此＝缺）。
const CELL: Record<CellState, string> = {
  present: 'var(--good)',
  missing: 'var(--crit)',
  failed: 'var(--warn)',
  none: 'var(--panel-2)',
}
const STATE_ZH: Record<CellState, string> = { present: '有資料', missing: '漏抓', failed: '擷取失敗', none: '未涵蓋' }

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setOk(true)
          setTimeout(() => setOk(false), 1200)
        })
      }}
      className="shrink-0 px-2 py-1 text-[11px] rounded border border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)] hover:border-[var(--accent)] transition-colors"
    >
      {ok ? '已複製 ✓' : '複製指令'}
    </button>
  )
}

function HeatLegend() {
  const items: [CellState, string][] = [
    ['present', '有資料'],
    ['missing', '漏抓'],
    ['failed', '擷取失敗'],
    ['none', '未涵蓋'],
  ]
  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px] text-[var(--muted)]">
      {items.map(([s, l]) => (
        <span key={s} className="flex items-center gap-1">
          <span className="inline-block rounded-[2px]" style={{ width: 11, height: 11, background: CELL[s] }} />
          {l}
        </span>
      ))}
      <span className="text-[10px]">（運維色，與台股紅漲綠跌無關）</span>
    </div>
  )
}

function Heatmap({ mx }: { mx: IngestMatrix }) {
  const { datasets, dates, cells } = mx
  if (dates.length === 0) return <div className="text-[13px] text-[var(--muted)]">資料湖尚無交易日</div>
  return (
    <div className="flex flex-col gap-2">
      <HeatLegend />
      <div className="overflow-x-auto">
        <div className="inline-flex flex-col gap-1">
          {datasets.map((ds) => (
            <div key={ds.key} className="flex items-center gap-2">
              <div className="w-20 shrink-0 text-[12px] text-[var(--fg)] whitespace-nowrap">{ds.label}</div>
              <div className="flex gap-[2px]">
                {dates.map((d) => {
                  const s = cells[ds.key]?.[d] ?? 'none'
                  return (
                    <div
                      key={d}
                      title={`${d}　${ds.label}　${STATE_ZH[s]}`}
                      className="rounded-[2px]"
                      style={{ width: 13, height: 13, background: CELL[s] }}
                    />
                  )
                })}
              </div>
              <div
                className="ml-2 shrink-0 text-[11px] mono tnum"
                style={{ color: ds.window_missing ? 'var(--crit)' : 'var(--muted)' }}
              >
                {ds.window_missing ? `缺 ${ds.window_missing}` : '齊'}
              </div>
            </div>
          ))}
          <div className="flex items-center gap-2 mt-0.5">
            <div className="w-20 shrink-0" />
            <div className="flex justify-between text-[10px] mono text-[var(--muted)]" style={{ width: dates.length * 15 }}>
              <span>{dates[0]}</span>
              <span>{dates[dates.length - 1]}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function GapList({ gaps, busy, onRun }: { gaps: IngestGap[]; busy: boolean; onRun: (g: IngestGap) => void }) {
  if (gaps.length === 0) return <div className="text-[13px] text-[var(--good)]">✓ 涵蓋區間內無漏抓</div>
  return (
    <div className="flex flex-col">
      {gaps.map((g, i) => (
        <div key={`${g.table}-${g.source}-${g.from}-${i}`} className="py-2.5 border-b border-[var(--line)] last:border-0">
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-[var(--fg)]">{g.label}</span>
              <span className="mono text-[var(--muted)]">
                {g.from}
                {g.to !== g.from ? ` ~ ${g.to}` : ''}
              </span>
              <span
                className="text-[11px] px-1.5 py-0.5 rounded bg-[var(--panel-2)]"
                style={{ color: g.kind === 'failed' ? 'var(--warn)' : 'var(--crit)' }}
              >
                {g.kind === 'failed' ? '失敗' : '漏抓'} {g.days} 日
              </span>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                onClick={() => onRun(g)}
                disabled={busy}
                className="px-2 py-1 text-[11px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] transition-colors disabled:opacity-40 disabled:pointer-events-none"
              >
                {busy ? '補抓中…' : '一鍵補抓'}
              </button>
              <CopyBtn text={g.command} />
            </div>
          </div>
          <code className="block mt-1.5 px-2 py-1.5 rounded bg-[var(--panel-2)] text-[11px] mono text-[var(--fg)] overflow-x-auto whitespace-nowrap">
            {g.command}
          </code>
          {g.warn_ban && (
            <div className="mt-1 text-[10px] text-[var(--warn)] leading-relaxed">
              ⚠ 跨度大：TWSE 免費端點連續大量回補易被暫時封鎖（D-015）→ 建議改走 FinMind 或分塊、加長間隔
            </div>
          )}
          {g.detail && <div className="mt-1 text-[10px] text-[var(--muted)]">失敗原因：{g.detail}</div>}
        </div>
      ))}
    </div>
  )
}

// 補抓工作狀態色：執行中=accent、成功=綠、失敗=紅（運維色）。
const JOB_TONE: Record<IngestJob['status'], string> = {
  running: 'var(--accent)',
  success: 'var(--good)',
  failed: 'var(--crit)',
}
const JOB_ZH: Record<IngestJob['status'], string> = { running: '執行中', success: '成功', failed: '失敗' }

function JobList({ jobs }: { jobs: IngestJob[] }) {
  return (
    <div className="flex flex-col">
      {jobs.map((j) => (
        <details key={j.id} open={j.status === 'running'} className="py-2 border-b border-[var(--line)] last:border-0">
          <summary className="flex flex-wrap items-center gap-2 cursor-pointer text-[13px]">
            <span
              className="text-[11px] px-1.5 py-0.5 rounded bg-[var(--panel-2)]"
              style={{ color: JOB_TONE[j.status] }}
            >
              {JOB_ZH[j.status]}
              {j.status === 'failed' && j.exit_code !== null ? ` (exit ${j.exit_code})` : ''}
            </span>
            <span className="text-[var(--fg)]">{j.label}</span>
            <code className="mono text-[11px] text-[var(--muted)]">{j.command}</code>
            <span className="ml-auto text-[10px] mono text-[var(--muted)]">
              {j.started.slice(11, 19)}
              {j.ended ? ` → ${j.ended.slice(11, 19)}` : ''}
            </span>
          </summary>
          <pre className="mt-1.5 px-2 py-1.5 rounded bg-[var(--panel-2)] text-[10px] mono text-[var(--muted)] leading-relaxed overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
            {j.tail.length ? j.tail.join('\n') : '（尚無輸出）'}
          </pre>
        </details>
      ))}
    </div>
  )
}

/* ---------- 手動抓取歷史資料（指定資料集 + 期間，伺服器端跑 twquant CLI）---------- */
const p2 = (n: number) => String(n).padStart(2, '0')
function todayISO(): string {
  const d = new Date() // 表單預設值用瀏覽器本地日期即可（非時間點語意）
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
}
function monthsBackISO(baseISO: string, n: number): string {
  const [y, m, d] = baseISO.split('-').map(Number)
  const back = new Date(y, m - 1 - n, d || 1)
  return `${back.getFullYear()}-${p2(back.getMonth() + 1)}-${p2(back.getDate())}`
}

/* ---------- 一鍵跑當日全部資料（排程錯過時的介面補跑，＝排程 16:40 同一 pipeline）---------- */
function PipelinePanel({
  hb,
  busy,
  onSubmitted,
}: {
  hb: Heartbeat | null
  busy: boolean
  onSubmitted: () => void
}) {
  const [date, setDate] = useState('')
  const [sending, setSending] = useState<'today' | 'date' | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)

  // 「指定交易日」欄預設今日；台灣使用者瀏覽器本地日即台北日（一鍵今日不看此欄）。
  useEffect(() => {
    setDate(todayISO())
  }, [])

  // watchdog：heartbeat 日 ≠ 最新行情日 → 排程可能錯過（未跑到最新交易日）。
  const never = hb != null && hb.heartbeat == null
  const missed = hb != null && hb.heartbeat != null && !hb.watchdog_ok
  const stale = never || missed

  // day 省略 → 後端以今日台北補跑（一鍵今日）；給定 → 補跑指定交易日。
  const run = (which: 'today' | 'date', day?: string) => {
    if (which === 'date' && !day) return
    setSending(which)
    setMsg(null)
    startPipelineJob(day)
      .then((j) => {
        setSending(null)
        setMsg({ kind: 'ok', text: `已排入：${j.label}（於下方「背景工作」看即時進度）` })
        onSubmitted()
      })
      .catch((e: unknown) => {
        setSending(null)
        setMsg({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
      })
  }

  const anyBusy = busy || sending != null
  return (
    <Panel
      title="一鍵跑當日全部資料"
      sub="擷取行情＋法人＋融資券 → 品質閘門 → 券商買賣超（富邦DJ 全市場分點）→ 訊號 → 供應層（因子物化）→ 選股池快照（＝排程 16:40 同一流程）"
    >
      {hb && (
        <div
          className="mb-3 px-3 py-2 rounded text-[12px] leading-relaxed"
          style={{ background: 'var(--panel-2)', color: stale ? 'var(--warn)' : 'var(--good)' }}
        >
          {never
            ? '⚠ 尚無 heartbeat：pipeline 從未執行，按下方按鈕跑一次建立每日狀態。'
            : missed
              ? `⚠ 排程可能已錯過：heartbeat 停在 ${hb.heartbeat?.day}，最新行情日 ${hb.latest ?? '—'}（${hb.watchdog_reason}）。按下方一鍵補上今日全部資料。`
              : `✓ pipeline 已跑到 ${hb.heartbeat?.day}（與最新行情日一致）。`}
        </div>
      )}
      {/* 主動作：一鍵跑今日（不需選日期，後端以台北今日補跑）。*/}
      <button
        onClick={() => run('today')}
        disabled={anyBusy}
        className="px-4 py-2 text-[13px] font-medium rounded border border-[var(--accent)] bg-[var(--accent)] text-[var(--bg)] hover:opacity-90 disabled:opacity-40 disabled:pointer-events-none transition-opacity"
      >
        {sending === 'today' ? '排入中…' : busy ? '有工作進行中…' : '▶ 一鍵跑今日全部資料'}
      </button>
      {/* 次動作：補跑過去某個交易日（如連假後補多天）。*/}
      <div className="flex flex-wrap items-end gap-2 mt-3 pt-3 border-t border-[var(--line)]">
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          指定交易日
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          />
        </label>
        <button
          onClick={() => run('date', date)}
          disabled={anyBusy || !date}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] disabled:opacity-40 disabled:pointer-events-none transition-colors"
        >
          {sending === 'date' ? '排入中…' : '補跑此日'}
        </button>
      </div>
      <p className="mt-2.5 text-[11px] text-[var(--muted)] leading-relaxed">
        含富邦DJ 全市場券商分點爬取，整趟約 10 分鐘（執行中於下方「背景工作」看即時進度）。
        非交易日或當日行情尚未公布時，品質閘門會擋下訊號（降級、沿用前日）、券商分點由探測快速跳過——與排程行為一致。
        重跑同一日冪等（券商續傳、供應層/選股池 upsert）。
      </p>
      {msg && (
        <div
          className="mt-2 text-[12px]"
          style={{ color: msg.kind === 'ok' ? 'var(--good)' : 'var(--crit)' }}
        >
          {msg.kind === 'ok' ? '✓ ' : '補跑失敗：'}
          {msg.text}
        </div>
      )}
    </Panel>
  )
}

/* ---------- 手動抓取歷史資料（指定資料集 + 期間，伺服器端跑 twquant CLI）---------- */
function FetchForm({
  tracks,
  latest,
  busy,
  onSubmitted,
}: {
  tracks: IngestTrack[]
  latest: string | null
  busy: boolean
  onSubmitted: () => void
}) {
  const [key, setKey] = useState('prices_daily:twse')
  const track = tracks.find((t) => `${t.table}:${t.source}` === key) ?? tracks[0]
  const unit = track?.unit ?? 'date'
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [sending, setSending] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)

  // 切資料集或首次拿到最新行情日 → 重設期間預設值（日軌預設近一個月、月軌預設當月）。
  useEffect(() => {
    const base = latest ?? todayISO()
    if (unit === 'month') {
      setFrom(base.slice(0, 7))
      setTo(base.slice(0, 7))
    } else {
      setFrom(monthsBackISO(base, 1))
      setTo(base)
    }
    setMsg(null)
  }, [key, unit, latest])

  const run = (force: boolean) => {
    if (!track || !from || !to) return
    setSending(true)
    setMsg(null)
    startIngestFetch({ table: track.table, source: track.source, from, to, force })
      .then((j) => {
        setSending(false)
        setMsg({ kind: 'ok', text: `已排入：${j.label}（於下方「背景工作」看進度）` })
        onSubmitted()
      })
      .catch((e: unknown) => {
        const text = e instanceof Error ? e.message : String(e)
        // D-015：TWSE 行情大區間需明示 force → 後端回 400，這裡確認後帶 force 重送。
        if (!force && /D-015|force/i.test(text) && window.confirm(`${text}\n\n仍要強制執行？`)) {
          run(true)
          return
        }
        setSending(false)
        setMsg({ kind: 'err', text })
      })
  }

  const disabled = busy || sending || !track || !from || !to
  return (
    <Panel
      title="手動抓取歷史資料"
      sub="指定資料集 + 期間 · 伺服器端跑 twquant CLI · 單一併發"
    >
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          資料集
          <select
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="px-2 py-1.5 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          >
            {tracks.map((t) => (
              <option key={`${t.table}:${t.source}`} value={`${t.table}:${t.source}`}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          起
          <input
            type={unit === 'month' ? 'month' : 'date'}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          迄
          <input
            type={unit === 'month' ? 'month' : 'date'}
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          />
        </label>
        <button
          onClick={() => run(false)}
          disabled={disabled}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] disabled:opacity-40 disabled:pointer-events-none transition-colors"
        >
          {sending ? '排入中…' : busy ? '有工作進行中…' : '抓取'}
        </button>
      </div>
      <p className="mt-2.5 text-[11px] text-[var(--muted)] leading-relaxed">
        台股行情為全市場擷取（單日含所有個股）——抓完某段期間後，即可到「個股因子 / 市場 K 線」頁查任一檔的歷史 K 線。
        此表單不受漏抓清單限制，可往前延伸歷史或初次建置資料湖。
      </p>
      {msg && (
        <div
          className="mt-2 text-[12px]"
          style={{ color: msg.kind === 'ok' ? 'var(--good)' : 'var(--crit)' }}
        >
          {msg.kind === 'ok' ? '✓ ' : '抓取失敗：'}
          {msg.text}
        </div>
      )}
    </Panel>
  )
}

/* ---------- 官方開休市日曆（TWSE market_holidays）---------- */
function HolidayPanel({
  hc,
  busy,
  onFetch,
}: {
  hc: HolidayCalendar | null
  busy: boolean
  onFetch: () => void
}) {
  const today = todayISO()
  const upcoming = (hc?.closed ?? []).filter((r) => r.date >= today).slice(0, 8)
  const cc = hc?.cross_check
  const nMiss = cc?.missing.length ?? 0
  const nUnexp = cc?.unexpected.length ?? 0
  return (
    <Panel
      title="官方開休市日曆"
      sub={hc?.available ? `${hc.sessions} 開市日 · ${hc.first}~${hc.last}` : 'TWSE 官方公布'}
    >
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          onClick={onFetch}
          disabled={busy}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] disabled:opacity-40 disabled:pointer-events-none transition-colors"
        >
          {busy ? '有工作進行中…' : '抓官方開休市日曆'}
        </button>
        <span className="text-[11px] text-[var(--muted)]">
          TWSE OpenAPI · 當前公布年度 · 平日−官方休市＝前瞻交易日曆
        </span>
      </div>
      {!hc?.available ? (
        <div className="text-[13px] text-[var(--muted)]">
          尚未擷取——按上方「抓官方開休市日曆」，或 CLI{' '}
          <code className="mono text-[var(--fg)]">twquant ingest-holidays</code>。
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="text-[12px]">
            官方 vs 觀測（{cc?.range ? `${cc.range[0]}~${cc.range[1]}` : '尚無行情'}）：
            {cc?.range == null ? (
              <span className="text-[var(--muted)]"> 尚無行情可核對</span>
            ) : nMiss === 0 && nUnexp === 0 ? (
              <span style={{ color: 'var(--good)' }}> ✓ 涵蓋年度內完全一致</span>
            ) : (
              <span style={{ color: 'var(--warn)' }}>
                {nMiss ? ` 官方開市卻無行情 ${nMiss} 日` : ''}
                {nUnexp ? ` 有行情卻官方判休市 ${nUnexp} 日` : ''}
              </span>
            )}
          </div>
          <div>
            <div className="eyebrow mb-1">近期休市（{upcoming.length}）</div>
            {upcoming.length === 0 ? (
              <div className="text-[12px] text-[var(--muted)]">今年後續無休市註記</div>
            ) : (
              <table className="w-full text-[12px] mono">
                <tbody>
                  {upcoming.map((r) => (
                    <tr key={r.date} className="border-b border-[var(--line)] last:border-0">
                      <td className="py-1 tnum text-[var(--fg)]">{r.date}</td>
                      <td className="py-1 pl-3 text-[var(--muted)]">{r.weekday ? `（${r.weekday}）` : ''}</td>
                      <td className="py-1 pl-3 text-[var(--fg)]">{r.name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}

function Ingest() {
  const { meta, live } = useMeta()
  const [mx, setMx] = useState<IngestMatrix | null>(null)
  const [gp, setGp] = useState<IngestGaps | null>(null)
  const [hb, setHb] = useState<Heartbeat | null>(null)
  const [jb, setJb] = useState<IngestJobs | null>(null)
  const [hc, setHc] = useState<HolidayCalendar | null>(null)
  const [tracks, setTracks] = useState<IngestTrack[]>([])
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [at, setAt] = useState('')

  useEffect(() => {
    if (!live) return
    let alive = true
    const load = () => {
      fetchIngestMatrix().then((d) => alive && setMx(d)).catch(() => {})
      fetchIngestGaps().then((d) => alive && setGp(d)).catch(() => {})
      fetchHeartbeat().then((d) => alive && setHb(d)).catch(() => {})
      fetchHolidayCalendar().then((d) => alive && setHc(d)).catch(() => {})
      if (alive) setAt(new Date().toLocaleTimeString('zh-TW', { hour12: false }))
    }
    load()
    const id = setInterval(load, 30000) // 每 30 秒即時更新（ops 看板）
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [live, tick])

  // 補抓工作獨立輪詢（3 秒：追執行中的輸出尾行）；工作一結束立即刷新矩陣/缺口。
  useEffect(() => {
    if (!live) return
    let alive = true
    let busyPrev = false
    const poll = () =>
      fetchIngestJobs()
        .then((d) => {
          if (!alive) return
          setJb(d)
          if (busyPrev && !d.busy) setTick((t) => t + 1)
          busyPrev = d.busy
        })
        .catch(() => {})
    poll()
    const id = setInterval(poll, 3000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [live])

  // 資料軌白名單（手動抓取表單的資料集選單）：載一次即可。
  useEffect(() => {
    if (!live) return
    let alive = true
    fetchIngestTracks()
      .then((t) => alive && setTracks(t))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [live])

  const busy = jb?.busy ?? false
  const refreshJobs = () => {
    fetchIngestJobs()
      .then((d) => setJb(d))
      .catch(() => {})
  }
  const runGap = (g: IngestGap) => {
    if (
      g.warn_ban &&
      !window.confirm(
        `跨度 ${g.days} 交易日：TWSE 免費端點連續大量回補易被暫時封鎖（D-015），建議分塊或改走 FinMind。仍要強制執行？`,
      )
    )
      return
    setErr('')
    startIngestJob(g, g.warn_ban)
      .then(refreshJobs)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
  }
  const runHolidayFetch = () => {
    setErr('')
    startHolidayFetch()
      .then(refreshJobs)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
  }

  if (!live) {
    return (
      <Panel title="資料擷取後台" sub="離線快照">
        <p className="text-[13px] text-[var(--muted)] leading-relaxed">
          後台熱力圖與漏抓清單需即時監控 API（<code className="mono">twquant serve</code>）；目前為內嵌快照、無法即時計算缺口。以下為快照涵蓋概況：
        </p>
        <table className="w-full text-[13px] mono mt-3">
          <tbody>
            {meta.coverage.map((c) => (
              <tr key={c.label} className="border-b border-[var(--line)] last:border-0">
                <td className="py-2 text-[var(--fg)]">{c.label}</td>
                <td className="py-2 tnum text-right text-[var(--muted)]">{c.days} 交易日</td>
                <td className="py-2 pl-3 text-right text-[11px] text-[var(--muted)]">{c.span}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="最新行情日" value={hb?.latest ?? mx?.latest ?? '—'} />
        <Kpi label="最後擷取" value={mx?.last_run ? mx.last_run.slice(0, 16).replace('T', ' ') : '—'} />
        <Kpi
          label="Heartbeat"
          value={hb?.heartbeat?.day ?? '—'}
          tone={hb ? (hb.watchdog_ok ? 'var(--good)' : 'var(--warn)') : undefined}
        />
        <Kpi
          label="漏抓合計"
          value={gp ? `${gp.total_missing} 日` : '…'}
          tone={gp ? (gp.total_missing ? 'var(--crit)' : 'var(--good)') : undefined}
        />
      </div>
      <div className="flex items-center justify-between">
        <div className="text-[12px] text-[var(--muted)]">一鍵補抓在伺服器端執行 twquant CLI（單一併發）· 每 30 秒自動更新</div>
        <button
          onClick={() => setTick((t) => t + 1)}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)] hover:border-[var(--accent)] transition-colors"
        >
          重新整理{at ? ` · ${at}` : ''}
        </button>
      </div>
      {err && <div className="text-[12px] text-[var(--crit)]">補抓失敗：{err}</div>}
      <PipelinePanel hb={hb} busy={busy} onSubmitted={refreshJobs} />
      {tracks.length > 0 && (
        <FetchForm
          tracks={tracks}
          latest={hb?.latest ?? mx?.latest ?? meta.as_of ?? null}
          busy={busy}
          onSubmitted={refreshJobs}
        />
      )}
      {jb && jb.jobs.length > 0 && (
        <Panel title="背景工作" sub="伺服器端執行 twquant CLI（補抓／每日 pipeline）· 單一併發 · 重啟 API 後清空">
          <JobList jobs={jb.jobs} />
        </Panel>
      )}
      <HolidayPanel hc={hc} busy={busy} onFetch={runHolidayFetch} />
      <Panel title="擷取涵蓋熱力圖" sub={mx ? `近 ${mx.dates.length} 交易日 · dataset × 日` : '載入中…'}>
        {mx ? <Heatmap mx={mx} /> : <Skeleton height={160} />}
      </Panel>
      <Panel
        title="漏抓清單 · 一鍵補抓"
        sub={gp ? `${gp.runs} 段 · 共 ${gp.total_missing} 交易日` : '載入中…'}
      >
        {gp ? <GapList gaps={gp.gaps} busy={busy} onRun={runGap} /> : <Skeleton height={120} />}
      </Panel>
    </div>
  )
}

/* ---------- 資金板塊（族群資金流 / 資金移動）---------- */
function Sectors() {
  const [win, setWin] = useState(20)
  const flows = useResource(`sector-flows:${win}`, () => fetchSectorFlows(win), () => null as unknown as SectorFlows)
  const f = flows.data
  if (flows.loading || !f) return <Panel title="資金板塊 · 族群資金流"><Skeleton height={420} /></Panel>
  if (!f.sectors.length) {
    return (
      <Panel title="資金板塊 · 族群資金流" sub="需 stock_meta（族群）+ 三大法人 + 行情">
        <div className="h-[140px] flex items-center justify-center text-center px-4 text-[13px] text-[var(--muted)]">
          尚無資料——請先 <code className="mono mx-1 text-[var(--fg)]">twquant ingest-meta</code>（族群）
        </div>
      </Panel>
    )
  }
  const byAbs = [...f.sectors].sort((a, b) => Math.abs(b.net_yi) - Math.abs(a.net_yi)).slice(0, 16)
  const maxDaily = Math.max(1, ...byAbs.flatMap((s) => f.dates.map((d) => Math.abs(f.daily[s.industry]?.[d] ?? 0))))
  const flowColor = (v: number) => {
    const a = Math.min(1, Math.abs(v) / maxDaily) * 0.8 + 0.06
    return v >= 0 ? `rgba(229,72,77,${a})` : `rgba(46,163,107,${a})` // 紅=流入、綠=流出
  }
  const sgn = (x: number) => (x >= 0 ? '+' : '')
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12px] text-[var(--muted)]">
          {f.as_of} · {f.sectors.length} 族群 · 三大法人淨買 × 收盤＝淨流入 ·{' '}
          <span style={{ color: 'var(--up)' }}>紅=資金流入</span> <span style={{ color: 'var(--down)' }}>綠=流出</span>
        </div>
        <div className="flex gap-1">
          {[5, 20, 60].map((w) => (
            <button
              key={w}
              onClick={() => setWin(w)}
              className={`px-2.5 py-1 text-[12px] rounded border transition-colors ${win === w ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)]'}`}
            >
              {w}日
            </button>
          ))}
        </div>
      </div>
      <Panel title="族群資金淨流入排行" sub={`近 ${f.window} 交易日 · 億元 · 副標=窗報酬`}>
        <HBars
          height={f.sectors.length * 22}
          rows={f.sectors.map((s) => ({ label: s.industry, value: s.net_yi, sub: `${sgn(s.ret_pct)}${s.ret_pct}%` }))}
        />
      </Panel>
      <Panel title="族群 × 日 資金流熱力圖" sub={`|淨流入| 前 ${byAbs.length} 族群 · 每格＝當日淨流入（億）`}>
        <div className="overflow-x-auto">
          <div className="inline-flex flex-col gap-[3px]">
            {byAbs.map((s) => (
              <div key={s.industry} className="flex items-center gap-2">
                <div className="w-24 shrink-0 text-[12px] text-[var(--fg)] whitespace-nowrap">{s.industry}</div>
                <div className="flex gap-[2px]">
                  {f.dates.map((d) => {
                    const v = f.daily[s.industry]?.[d] ?? 0
                    return (
                      <div
                        key={d}
                        title={`${d}　${s.industry}　${sgn(v)}${v} 億`}
                        className="rounded-[2px]"
                        style={{ width: 13, height: 13, background: flowColor(v) }}
                      />
                    )
                  })}
                </div>
                <div className="ml-2 shrink-0 text-[11px] mono tnum" style={{ color: s.net_yi >= 0 ? 'var(--up)' : 'var(--down)' }}>
                  {sgn(s.net_yi)}
                  {s.net_yi}
                </div>
              </div>
            ))}
            <div className="flex items-center gap-2 mt-0.5">
              <div className="w-24 shrink-0" />
              <div className="flex justify-between text-[10px] mono text-[var(--muted)]" style={{ width: f.dates.length * 15 }}>
                <span>{f.dates[0]}</span>
                <span>{f.dates[f.dates.length - 1]}</span>
              </div>
            </div>
          </div>
        </div>
      </Panel>
      <Panel title="族群明細" sub="淨流入排序">
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] mono tnum whitespace-nowrap">
            <thead className="text-[var(--muted)] text-left">
              <tr className="border-b border-[var(--line)]">
                <th className="py-2 pr-3 font-medium">族群</th>
                <th className="py-2 px-2 font-medium text-right">檔數</th>
                <th className="py-2 px-2 font-medium text-right">淨流入(億)</th>
                <th className="py-2 px-2 font-medium text-right">外資</th>
                <th className="py-2 px-2 font-medium text-right">投信</th>
                <th className="py-2 px-2 font-medium text-right">窗報酬</th>
                <th className="py-2 pl-2 font-medium text-right">成交值(億)</th>
              </tr>
            </thead>
            <tbody>
              {f.sectors.map((s) => (
                <tr key={s.industry} className="border-b border-[var(--line)] last:border-0">
                  <td className="py-1.5 pr-3 text-[var(--fg)]">{s.industry}</td>
                  <td className="py-1.5 px-2 text-right text-[var(--muted)]">{s.n}</td>
                  <td className="py-1.5 px-2 text-right" style={{ color: s.net_yi >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {sgn(s.net_yi)}
                    {s.net_yi}
                  </td>
                  <td className="py-1.5 px-2 text-right" style={{ color: s.frgn_yi >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {sgn(s.frgn_yi)}
                    {s.frgn_yi}
                  </td>
                  <td className="py-1.5 px-2 text-right" style={{ color: s.trust_yi >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {sgn(s.trust_yi)}
                    {s.trust_yi}
                  </td>
                  <td className="py-1.5 px-2 text-right" style={{ color: sign(s.ret_pct / 100) }}>
                    {sgn(s.ret_pct)}
                    {s.ret_pct}%
                  </td>
                  <td className="py-1.5 pl-2 text-right text-[var(--muted)]">{num(s.amt_yi)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

/* ---------- shell ---------- */
const PAGES = [
  { id: 'overview', label: '總覽', el: <Overview /> },
  { id: 'market', label: '市場 K 線', el: <Market /> },
  { id: 'stock', label: '個股因子', el: <StockFactorsPage /> },
  { id: 'factors', label: '因子健檢', el: <Factors /> },
  { id: 'backtest', label: '策略回測', el: <Backtest /> },
  { id: 'flows', label: '法人 · 營收 · 容量', el: <Flows /> },
  { id: 'sectors', label: '資金板塊', el: <Sectors /> },
  { id: 'ingest', label: '資料後台', el: <Ingest /> },
]

function Shell() {
  const { meta, live } = useMeta()
  // 預設落在個股因子頁（PG 直查、毫秒級）；總覽等讀湖頁面切到才算（分鐘級）
  const [page, setPage] = useState('stock')
  const cur = useMemo(() => PAGES.find((p) => p.id === page)!, [page])
  return (
    <div className="min-h-screen md:flex text-[var(--fg)]">
      {/* 桌面側欄（md 起）；手機改用下方頂欄 */}
      <aside className="hidden md:flex w-52 shrink-0 border-r border-[var(--line)] bg-[var(--panel)] flex-col sticky top-0 h-screen">
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
          <div>資料 {live ? '即時' : '快照'} {meta.as_of ?? '—'}</div>
          <div>TWSE · TPEX · MOPS</div>
          <div className="mt-1">
            <span style={{ color: 'var(--up)' }}>紅漲</span> · <span style={{ color: 'var(--down)' }}>綠跌</span>（台股慣例）
          </div>
        </div>
      </aside>

      {/* 手機頂欄（mobile-first）：品牌＋狀態列與橫向捲動頁籤，sticky 常駐 */}
      <div className="md:hidden sticky top-0 z-30 border-b border-[var(--line)] bg-[var(--bg)]/95 backdrop-blur">
        <div className="flex items-center gap-2 px-3 pt-2">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: 'var(--accent)' }} />
          <span className="font-semibold tracking-wide text-[14px]">twquant</span>
          <span className="ml-auto flex items-center gap-1.5 text-[10px] mono text-[var(--muted)] whitespace-nowrap">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{ background: live ? 'var(--good)' : 'var(--muted)' }}
              title={live ? '即時連線 API' : '離線快照 fallback'}
            />
            {live ? '即時' : '快照'} {meta.as_of ?? '—'}
          </span>
        </div>
        <nav className="flex gap-1 overflow-x-auto hscroll px-2 py-2">
          {PAGES.map((p) => (
            <button
              key={p.id}
              onClick={() => setPage(p.id)}
              className={`shrink-0 whitespace-nowrap px-2.5 py-1 text-[12px] rounded-[2px] border transition-colors ${
                page === p.id
                  ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--panel-2)]'
                  : 'border-transparent text-[var(--muted)]'
              }`}
            >
              {p.label}
            </button>
          ))}
        </nav>
      </div>

      <main className="flex-1 min-w-0">
        <header className="hidden md:flex px-6 py-4 border-b border-[var(--line)] items-baseline justify-between">
          <h1 className="text-[17px] font-semibold tracking-wide">{cur.label}</h1>
          <span className="text-[12px] mono text-[var(--muted)] flex items-center gap-2">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{ background: live ? 'var(--good)' : 'var(--muted)' }}
              title={live ? '即時連線 API' : '離線快照 fallback'}
            />
            {live ? '即時 · 讀資料湖' : '快照 · 內嵌 data.json'} · lightweight-charts
          </span>
        </header>
        <div className="p-3 md:p-6 max-w-[1180px]">{cur.el}</div>
      </main>
    </div>
  )
}

export default function App() {
  // 開頁零等待（使用者裁決，2026-07-10）：立即以內嵌 meta 渲染殼，/api/health（毫秒級）
  // 判定 live；meta 讀資料湖可能要分鐘級，改背景載入、到了再補 as_of/涵蓋等欄位。不快取。
  const [ctx, setCtx] = useState<MetaCtxT>({ meta: FALLBACK.meta, live: false })
  useEffect(() => {
    let alive = true
    fetchHealth()
      .then(() => alive && setCtx((c) => (c.live ? c : { ...c, live: true })))
      .catch(() => {}) // 探針失敗 → 維持快照模式；meta 若成功仍會翻 live
    fetchMeta()
      .then((m) => alive && setCtx({ meta: m, live: true }))
      .catch(() => {}) // API 不可達 → 維持內嵌 meta
    return () => {
      alive = false
    }
  }, [])
  return (
    <MetaCtx.Provider value={ctx}>
      <Shell />
    </MetaCtx.Provider>
  )
}
