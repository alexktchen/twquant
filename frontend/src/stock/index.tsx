// 個股研究頁（SPEC-014 §5）：首屏是股票資訊 + K 線 + 推薦事件；籌碼／基本面／因子與
// 「條件選股」各自為頁籤，避免長頁。元件拆在 src/stock/；共用原件在 src/charts.tsx、src/ui.tsx。
import { useEffect, useState, type ReactNode } from 'react'
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
import { TPanel, chip } from './ui'
import { DEFAULT_STOCK_TAB, parseStockTab, quoteIdentity, stockTabs, type StockTabId } from '../platform/stockTabs'

export function StockFactorsPage({ selectedSid, onSelectSid, tab, onSelectTab, dateContext = null, recommendations }: {
  selectedSid?: string
  onSelectSid?: (sid: string) => void
  tab?: string | null
  onSelectTab?: (tab: StockTabId) => void
  dateContext?: string | null
  recommendations?: ReactNode
} = {}) {
  const activeTab = parseStockTab(tab ?? null)
  const [localTab, setLocalTab] = useState<StockTabId>(DEFAULT_STOCK_TAB)
  const currentTab = onSelectTab ? activeTab : localTab
  const selectTab = (next: StockTabId) => (onSelectTab ? onSelectTab(next) : setLocalTab(next))
  const { meta, live } = useMeta()
  const metaMap = useResource('stock-meta', fetchStockMeta, () => ({}) as StockMetaMap)
  const mm = metaMap.data ?? {}
  const [list, setList] = useState<string[]>(meta.top_stocks)
  // 預設選股：優先第一檔追蹤股，其次熱門第一檔
  const [localSid, setLocalSid] = useState<string>(() => loadWatch()[0] ?? meta.top_stocks[0] ?? '')
  const sid = selectedSid ?? localSid
  const setSid = (value: string) => { setLocalSid(value); onSelectSid?.(value) }
  // OHLC 以 sid＋日期上下文標記：換股／換日瞬間舊資料因身分不符而不渲染，杜絕殘影
  const quoteKey = sid ? quoteIdentity(sid, dateContext) : ''
  const [ohlc, setOhlc] = useState<{ key: string; rows: Candle[] } | undefined>(() =>
    live || !sid ? undefined : { key: quoteIdentity(sid, dateContext), rows: FALLBACK.stocks[sid] ?? [] },
  )
  const [ohlcError, setOhlcError] = useState<string | null>(null)
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

  // 不快取：每次換股/換日都即時重抓 OHLC。連線狀態下請求失敗一律明示錯誤——
  // 不退回離線快照，否則會把「讀取失敗」偽裝成「連線成功但這檔沒行情」（AC-207-2）。
  useEffect(() => {
    if (!sid) return
    setOhlcError(null)
    if (!live) {
      setOhlc({ key: quoteKey, rows: FALLBACK.stocks[sid] ?? [] })
      return
    }
    let alive = true
    setLoading(true)
    setOhlc(undefined)
    fetchStockOhlc(sid)
      .then((k) => alive && setOhlc({ key: quoteKey, rows: k }))
      .catch((e) => alive && setOhlcError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [quoteKey, live])
  const series = ohlc?.key === quoteKey ? ohlc.rows : undefined

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
            {ohlcError
              ? `無法讀取 ${labelOf(mm, sid)} K 線：${ohlcError}（讀取失敗，未以離線快照替代）`
              : !series || loading
                ? `載入 ${labelOf(mm, sid)} K 線…`
                : live
                  ? `無 ${labelOf(mm, sid)} K 線資料`
                  : `離線快照僅含熱門 ${meta.top_stocks.length} 檔，無 ${labelOf(mm, sid)} K 線`}
          </div>
        </TPanel>
      ) : null}
      {recommendations}
      <div className="flex items-center gap-1.5 overflow-x-auto hscroll" role="tablist" aria-label="個股資料頁籤">
        {stockTabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`stock-tab-${t.id}`}
            aria-selected={currentTab === t.id}
            aria-controls="stock-tabpanel"
            className={chip(currentTab === t.id)}
            onClick={() => selectTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        <span className="shrink-0 ml-1 text-[10px] text-[var(--muted)]">條件選股為因子篩選，非 ML 推薦</span>
      </div>
      <div id="stock-tabpanel" role="tabpanel" aria-labelledby={`stock-tab-${currentTab}`} className="flex flex-col gap-3 md:gap-4">
        {currentTab === 'chips' &&
          (sid && live ? (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 md:gap-4 lg:[&>:only-child]:col-span-2">
                <InstitutionalPanel sid={sid} />
                <MarginPanel sid={sid} />
              </div>
              <BrokerPanel sid={sid} />
            </>
          ) : (
            <TPanel title="籌碼">
              <div className="h-[120px] flex items-center justify-center text-[13px] text-[var(--muted)]">
                {live ? '選擇個股以檢視籌碼' : '需即時 API（twquant serve）'}
              </div>
            </TPanel>
          ))}
        {currentTab === 'fundamentals' &&
          (sid && live ? (
            <RevenuePanel sid={sid} />
          ) : (
            <TPanel title="基本面">
              <div className="h-[120px] flex items-center justify-center text-[13px] text-[var(--muted)]">
                {live ? '選擇個股以檢視月營收' : '需即時 API（twquant serve）'}
              </div>
            </TPanel>
          ))}
        {currentTab === 'factors' &&
          (sid && live ? (
            <ScorecardPanel sid={sid} />
          ) : (
            <TPanel title="個股因子體檢">
              <div className="h-[120px] flex items-center justify-center text-[13px] text-[var(--muted)]">
                {loading ? `載入 ${labelOf(mm, sid)}…` : live ? '選擇個股以檢視因子體檢' : '需即時 API（twquant serve）'}
              </div>
            </TPanel>
          ))}
        {currentTab === 'screener' &&
          (live ? (
            <>
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
              <StrategyPools sid={sid} setSid={setSid} mm={mm} />
            </>
          ) : (
            <TPanel title="條件選股">
              <div className="h-[120px] flex items-center justify-center text-[13px] text-[var(--muted)]">
                條件選股需即時 API（twquant serve）
              </div>
            </TPanel>
          ))}
      </div>
    </div>
  )
}
