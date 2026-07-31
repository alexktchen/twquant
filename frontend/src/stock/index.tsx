// 個股因子頁：選股 + 因子選股 + 策略選股池 + 頁首 + K 線 + 法人/融資券/營收/主力 + 體檢卡。
// 元件拆在 src/stock/；跨頁共用的圖表/UI 原件在 src/charts.tsx、src/ui.tsx。
import { useEffect, useState } from 'react'
import type { Candle, CompositeTop, FactorOption, FactorOrder, FactorTop, StockMetaMap } from '../api'
import { fetchCompositeTop, fetchFactorTop, fetchStockList, fetchStockMeta, fetchStockOhlc } from '../api'
import { FALLBACK } from '../fallback'
import { useMeta, useResource } from '../hooks'
import { labelOf } from '../theme'
import { BrokerPanel, InstitutionalPanel, MarginPanel, RevenuePanel } from './DataPanels'
import { FactorScreener } from './FactorScreener'
import { ScorecardPanel } from './ScorecardPanel'
import { StockChartPanel } from './StockChartPanel'
import { StockHeader } from './StockHeader'
import { StockPicker } from './StockPicker'
import { StrategyPools } from './StrategyPools'
import { DEFAULT_SCREEN, MAX_FACTOR_PANES, loadSubFactors, loadWatch, saveSubFactors, saveWatch } from './store'
import { TPanel } from './ui'

export function StockFactorsPage() {
  const { meta, live } = useMeta()
  const metaMap = useResource('stock-meta', fetchStockMeta, () => ({}) as StockMetaMap)
  const mm = metaMap.data ?? {}
  const [list, setList] = useState<string[]>(meta.top_stocks)
  // 預設選股：優先第一檔追蹤股，其次熱門第一檔
  const [sid, setSid] = useState<string>(() => loadWatch()[0] ?? meta.top_stocks[0] ?? '')
  // OHLC 以 sid 標記：換股瞬間舊資料因 sid 不符而不渲染，杜絕「新標題配舊 K 線」殘影
  const [ohlc, setOhlc] = useState<{ sid: string; rows: Candle[] } | undefined>(() =>
    live || !sid ? undefined : { sid, rows: FALLBACK.stocks[sid] ?? [] },
  )
  const [loading, setLoading] = useState(false)
  const [watch, setWatch] = useState<string[]>(loadWatch)
  const toggleWatch = (s: string) =>
    setWatch((w) => {
      const next = w.includes(s) ? w.filter((x) => x !== s) : [...w, s]
      saveWatch(next)
      return next
    })
  // 因子選股狀態上提：篩選器選中的因子自動加入 K 線副圖（chips 可增減，上限 MAX_FACTOR_PANES）
  const [screen, setScreen] = useState<FactorOption>(DEFAULT_SCREEN)
  const [order, setOrder] = useState<FactorOrder>('top') // 訊號最強(top) / 最弱(bottom)
  const emptyFT = (): FactorTop => ({
    factor: screen.factor, zh: screen.zh, direction: screen.direction, order,
    as_of: null, source: 'pg', rows: [], available: [],
  })
  const ftop = useResource(`ftop:${screen.factor}:${order}`, () => fetchFactorTop(screen.factor, 10, order), emptyFT)
  const [subFactors, setSubFactors] = useState<FactorOption[]>(loadSubFactors)
  useEffect(() => {
    setSubFactors((prev) =>
      prev.some((f) => f.factor === screen.factor) ? prev : [...prev, screen].slice(-MAX_FACTOR_PANES),
    )
  }, [screen])
  useEffect(() => {
    saveSubFactors(subFactors)
  }, [subFactors])
  const toggleSub = (f: FactorOption) =>
    setSubFactors((prev) =>
      prev.some((x) => x.factor === f.factor)
        ? prev.filter((x) => x.factor !== f.factor)
        : [...prev, f].slice(-MAX_FACTOR_PANES),
    )
  // 熱門列＝宇宙綜合排名前 10（最新物化日）；PG 未物化/離線退回快照熱門。
  // 一律嘗試 fetch（不依 live 閘）——開頁瞬間 live 尚未翻正，靠 key 的 useResource 不會重抓。
  const emptyTop: CompositeTop = { as_of: null, source: 'pg', rows: [] }
  const top = useResource('ctop', () => fetchCompositeTop(10), () => emptyTop)
  const topRows = top.data?.rows ?? []
  const hot = topRows.length
    ? topRows.map((r, i) => ({ sid: r.stock_id, name: r.name ?? mm[r.stock_id]?.name ?? '', rank: i + 1, pct: r.pct as number | null }))
    : meta.top_stocks.map((s) => ({ sid: s, name: mm[s]?.name ?? '', rank: 0, pct: null as number | null }))

  useEffect(() => {
    if (!live) return
    let alive = true
    fetchStockList().then((s) => alive && s.length && setList(s)).catch(() => {})
    return () => {
      alive = false
    }
  }, [live])

  // 不快取：每次換股都即時重抓 OHLC（離線退回內嵌樣本；樣本沒有該股則空列→占位訊息）
  useEffect(() => {
    if (!sid) return
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

  return (
    <div className="flex flex-col gap-3 md:gap-4">
      <StockPicker
        sid={sid}
        setSid={setSid}
        mm={mm}
        watch={watch}
        toggleWatch={toggleWatch}
        hot={hot}
        hotIsRanked={topRows.length > 0}
        list={list}
        live={live}
      />
      {live && (
        <FactorScreener
          sid={sid}
          setSid={setSid}
          mm={mm}
          value={screen}
          onChange={setScreen}
          order={order}
          onOrderChange={setOrder}
          data={ftop.data}
          loading={ftop.loading}
        />
      )}
      {live && <StrategyPools sid={sid} setSid={setSid} mm={mm} />}
      {sid && live && <StockHeader sid={sid} fallbackName={mm[sid]?.name ?? ''} />}
      {sid && series && series.length > 0 ? (
        <StockChartPanel
          sid={sid}
          title={`${labelOf(mm, sid)} · K 線`}
          series={series}
          factors={subFactors}
          available={ftop.data?.available ?? []}
          onToggle={toggleSub}
        />
      ) : sid ? (
        <TPanel title={`${labelOf(mm, sid)} · K 線`}>
          <div className="h-[120px] flex items-center justify-center text-center text-[13px] text-[var(--muted)]">
            {!series || loading
              ? `載入 ${labelOf(mm, sid)} K 線…`
              : live
                ? `無 ${labelOf(mm, sid)} K 線資料`
                : `離線快照僅含熱門 ${meta.top_stocks.length} 檔，無 ${labelOf(mm, sid)} K 線`}
          </div>
        </TPanel>
      ) : null}
      {sid && live && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 md:gap-4 lg:[&>:only-child]:col-span-2">
          <InstitutionalPanel sid={sid} />
          <MarginPanel sid={sid} />
        </div>
      )}
      {sid && live && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 md:gap-4 lg:[&>:only-child]:col-span-2">
          <RevenuePanel sid={sid} />
          <BrokerPanel sid={sid} />
        </div>
      )}
      {sid && live ? (
        <ScorecardPanel sid={sid} />
      ) : (
        <TPanel title="個股因子體檢">
          <div className="h-[120px] flex items-center justify-center text-[13px] text-[var(--muted)]">
            {loading ? `載入 ${labelOf(mm, sid)}…` : live ? '選擇個股以檢視因子體檢' : '需即時 API（twquant serve）'}
          </div>
        </TPanel>
      )}
    </div>
  )
}
