// 個股研究頁（SPEC-014 §5、§9）：儀表板式版面——指令列＋KPI 列＋18 個編號面板，
// 籌碼／基本面／因子／洞悉／條件選股的完整明細仍在下方頁籤。K 線永遠在首列、不進頁籤。
// 元件拆在 src/stock/；共用原件在 src/charts.tsx、src/ui.tsx。
import { useEffect, useMemo, useRef, useState } from 'react'
import type { BrokerFlowPoint, Candle, CompositePoint, CompositeTop, FactorOption, FactorOrder, FactorTop, StockMetaMap } from '../api'
import { fetchCompositeTop, fetchFactorTop, fetchStockMeta, fetchStockOhlc } from '../api'
import { FALLBACK } from '../fallback'
import { useMeta, useResource } from '../hooks'
import { labelOf } from '../theme'
import { CommandBar, KpiStrip, QuickRow, StockHero, ToolRow, type Pill } from './CommandBar'
import {
  BrokerCountPanel, BrokerFlowPanel, CompositeTrendPanel, CoveragePanel, EnergyPanel, FactorRadarPanel, HealthPanel, HeatPanel,
  InsightSummaryPanel, InstPanel, MarginStructurePanel, PoolPanel, RangeGaugePanel, ReadingsPanel, RevenueTrendPanel, RiskRadarPanel,
} from './DashPanels'
import { BrokerPanel, InstitutionalPanel, MarginPanel, RevenuePanel } from './DataPanels'
import { downloadBlob, ohlcCsv } from './exporting'
import { FactorScreener } from './FactorScreener'
import { ScorecardPanel } from './ScorecardPanel'
import { StockChartPanel } from './StockChartPanel'
import { StrategyPools } from './StrategyPools'
import { DEFAULT_SCREEN, MAX_FACTOR_PANES, loadSubFactors, loadWatch, saveSubFactors, saveWatch } from './store'
import { TPanel } from './ui'
import { DashGrid } from './DashGrid'
import { DrawAnalysisPanel } from './DrawAnalysisPanel'
import type { DrawAnalysis } from '../charts'
import { useStockData } from './useStockData'
import { DEFAULT_STOCK_TAB, parseStockTab, quoteIdentity, stockTabs, type StockTabId } from '../platform/stockTabs'
import { mapEventsToSessions } from '../platform/eventMarkers'
import { StockEventsPanel } from '../platform/StockEventsPanel'
import { StockInsightsPanel } from '../platform/StockInsightsPanel'
import type { StockEvent } from '../platform/stockRecommendations'
import { markerFor } from '../platform/markerStyle'

// 穩定的空陣列：圖表以陣列 identity 為 effect 依賴，每次 render 產生新 [] 會讓整座圖重建。
/** 個股儀表板的卡片順序與大小（T-322），跨股票共用；每個分頁（T-324）各存一份：`<key>.<分頁>`。 */
const DASH_LAYOUT_KEY = 'twquant.stockDashLayout'
const DASH_TAB_KEY = 'twquant.stockDashTab'
/** 卡片分頁（T-324）：一頁只放一類，資訊不會一次全攤開；'all' 保留完整儀表板。 */
const DASH_TABS: { id: string; label: string; cards: string[] | null }[] = [
  { id: 'tech', label: '技術面', cards: ['01', '19', '02', '04', '11', '14'] },
  { id: 'chip', label: '籌碼', cards: ['09', '18', '07', '10', '04'] },
  { id: 'factor', label: '因子／模型', cards: ['03', '05', '08', '12', '13', '06'] },
  { id: 'fund', label: '基本面／其他', cards: ['16', '17', '15'] },
  { id: 'all', label: '全部', cards: null },
]
function loadDashTab(): string {
  try {
    const v = localStorage.getItem(DASH_TAB_KEY)
    return DASH_TABS.some((t) => t.id === v) ? (v as string) : DASH_TABS[0].id
  } catch {
    return DASH_TABS[0].id
  }
}
const NO_COMPOSITE: CompositePoint[] = []
const NO_FLOW: BrokerFlowPoint[] = []

export function StockFactorsPage({ selectedSid, onSelectSid, tab, onSelectTab, dateContext = null, events = [], eventsLoading = false, eventsError = null }: {
  selectedSid?: string
  onSelectSid?: (sid: string) => void
  tab?: string | null
  onSelectTab?: (tab: StockTabId) => void
  dateContext?: string | null
  events?: StockEvent[]
  eventsLoading?: boolean
  eventsError?: string | null
} = {}) {
  const activeTab = parseStockTab(tab ?? null)
  const [localTab, setLocalTab] = useState<StockTabId>(DEFAULT_STOCK_TAB)
  const currentTab = onSelectTab ? activeTab : localTab
  const selectTab = (next: StockTabId) => (onSelectTab ? onSelectTab(next) : setLocalTab(next))
  const { meta, live } = useMeta()
  const metaMap = useResource('stock-meta', fetchStockMeta, () => ({}) as StockMetaMap)
  const mm = metaMap.data ?? {}
  // 由路由帶代號（平台用法）時，沒有代號就停在入口畫面；獨立使用時才退回追蹤股／熱門第一檔。
  const [localSid, setLocalSid] = useState<string>(() => loadWatch()[0] ?? meta.top_stocks[0] ?? '')
  const sid = onSelectSid ? (selectedSid ?? '') : (selectedSid ?? localSid)
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
  // 快速選擇列＝宇宙綜合排名前 10（最新物化日）；PG 未物化/離線退回快照熱門。
  // 一律嘗試 fetch（不依 live 閘）——開頁瞬間 live 尚未翻正，靠 key 的 useResource 不會重抓。
  const emptyTop: CompositeTop = { as_of: null, source: 'pg', rows: [] }
  const top = useResource('ctop', () => fetchCompositeTop(10), () => emptyTop)
  const topRows = top.data?.rows ?? []
  const hot = topRows.length
    ? topRows.map((r, i) => ({ sid: r.stock_id, name: r.name ?? mm[r.stock_id]?.name ?? '', rank: i + 1, pct: r.pct as number | null }))
    : meta.top_stocks.map((s) => ({ sid: s, name: mm[s]?.name ?? '', rank: 0, pct: null as number | null }))

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
  // 儀表板面板只在「有行情」時拿到 K 棒；失敗、載入中、零筆都傳 undefined，面板據此說明無法計算。
  const bars = series && series.length > 0 ? series : undefined
  // 事件→交易日的映射在此完成（圖表不自行貼棒）；換股時 series 換掉，標記自然清空。
  const mapping = useMemo(
    () => mapEventsToSessions(events, (series ?? []).map((k) => k.d)),
    [events, series],
  )
  const chartMarkers = useMemo(() => mapping.groups.map(markerFor), [mapping])
  const [focusDay, setFocusDay] = useState<string | null>(null)
  useEffect(() => {
    setFocusDay(null) // 換股或換日一律回到完整區間，不把上一檔的定位帶過來。
  }, [quoteKey])
  const focusSession = focusDay
    ? (mapping.groups.find((g) => g.day === focusDay)?.session ?? null)
    : null
  // 因子清單（名稱／方向）來自 factor-top；還沒回來傳 null，讀不到傳空陣列，因子卡據此分辨載入中與失敗。
  const data = useStockData(sid, ftop.loading ? null : (ftop.data?.available ?? []))
  const shot = useRef<(() => HTMLCanvasElement) | null>(null)
  const [analysis, setAnalysis] = useState<DrawAnalysis | null>(null)
  const [dashTab, setDashTabState] = useState(loadDashTab)
  const setDashTab = (t: string) => {
    setDashTabState(t)
    try {
      localStorage.setItem(DASH_TAB_KEY, t)
    } catch {
      /* 寫不進去：分頁只在本次會話有效 */
    }
  }
  const tabCards = DASH_TABS.find((t) => t.id === dashTab)?.cards ?? null

  if (!sid) {
    return <StockHero mm={mm} watch={watch} hot={hot} hotIsRanked={topRows.length > 0} live={live} onPick={setSid} />
  }

  const o = data.overview.data
  const stamp = bars?.[bars.length - 1]?.d ?? 'na'
  const pills: Pill[] = [
    { icon: '◉', label: 'API STATUS', value: live ? '已連線' : '未連線 · 快照', color: live ? 'var(--good)' : 'var(--warn)' },
    { icon: '▦', label: 'DATA AS OF', value: meta.as_of ?? '未知', color: 'var(--accent)' },
    { icon: '⚙', label: 'FACTOR AS OF', value: data.scorecard.data?.as_of ?? (data.scorecard.loading ? '…' : '無'), color: 'var(--accent-2)' },
    { icon: '◆', label: 'ML EVENTS', value: eventsError ? '讀取失敗' : eventsLoading ? '…' : `${events.length} 筆`, color: eventsError ? 'var(--crit)' : 'var(--warn)' },
  ]
  return (
    <div className="dash">
      <CommandBar sid={sid} name={o?.name ?? mm[sid]?.name ?? ''} industry={o?.industry ?? mm[sid]?.industry ?? null} market={o?.market ?? mm[sid]?.market ?? null} pills={pills} mm={mm} onPick={setSid} />
      <KpiStrip bars={bars} />
      <ToolRow
        bars={bars}
        watched={watch.includes(sid)}
        onToggleWatch={() => toggleWatch(sid)}
        onPng={() => shot.current?.().toBlob((blob) => blob && downloadBlob(`${sid}_K線_${stamp}.png`, blob))}
        onCsv={() => bars && downloadBlob(`${sid}_日K_${stamp}.csv`, new Blob([ohlcCsv(bars)], { type: 'text/csv;charset=utf-8' }))}
      />
      <QuickRow sid={sid} watch={watch} hot={hot} hotIsRanked={topRows.length > 0} mm={mm} onPick={setSid} />
      <DashGrid
        key={dashTab}
        // 「全部」沿用分頁前的舊鍵：使用者先前存的版面不會不見
        storageKey={dashTab === 'all' ? DASH_LAYOUT_KEY : `${DASH_LAYOUT_KEY}.${dashTab}`}
        toolbar={
          <div className="dash-cardtabs" role="tablist" aria-label="儀表板分類">
            {DASH_TABS.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={dashTab === t.id} className="dash-cardtab" onClick={() => setDashTab(t.id)}>
                {t.label}
              </button>
            ))}
          </div>
        }
        items={[
          {
            id: '01',
            w: 6,
            node: bars ? (
              <StockChartPanel
                no="01"
                sid={sid}
                title={`${labelOf(mm, sid)} · K 線`}
                series={bars}
                composite={data.comp.data?.rows ?? NO_COMPOSITE}
                brokerFlow={data.flow.data?.rows ?? NO_FLOW}
                subLoading={data.comp.loading || data.flow.loading}
                factors={subFactors}
                available={ftop.data?.available ?? []}
                onToggle={toggleSub}
                markers={chartMarkers.length ? chartMarkers : undefined}
                focusTime={focusSession}
                onResetView={() => setFocusDay(null)}
                onReady={(fn) => { shot.current = fn }}
                onAnalysis={setAnalysis}
              />
            ) : (
              <TPanel no="01" title={`${labelOf(mm, sid)} · K 線`}>
                <div className="dash-state" style={{ minHeight: 220 }}>
                  {ohlcError
                    ? `無法讀取 ${labelOf(mm, sid)} K 線：${ohlcError}（讀取失敗，未以離線快照替代）`
                    : !series || loading
                      ? `載入 ${labelOf(mm, sid)} K 線…`
                      : live
                        ? `無 ${labelOf(mm, sid)} K 線資料`
                        : `離線快照僅含熱門 ${meta.top_stocks.length} 檔，無 ${labelOf(mm, sid)} K 線`}
                </div>
              </TPanel>
            ),
          },
          { id: '19', w: 6, node: <DrawAnalysisPanel no="19" analysis={analysis} /> },
          { id: '02', w: 3, node: <ReadingsPanel no="02" bars={bars} data={data} /> },
          { id: '03', w: 3, node: <FactorRadarPanel no="03" data={data} /> },
          { id: '04', w: 3, node: <HeatPanel no="04" bars={bars} /> },
          { id: '05', w: 3, node: <RiskRadarPanel no="05" data={data} /> },
          {
            id: '06',
            w: 6,
            node: (
              <StockEventsPanel
                no="06"
                groups={mapping.groups}
                pending={mapping.pending}
                loading={eventsLoading}
                error={eventsError}
                selectedDay={focusDay ?? dateContext}
                onSelectDay={(d) => {
                  setFocusDay(d)
                  // 點推薦日要看 K 線定位：K 線不在目前分頁就切回技術面
                  if (d && tabCards !== null && !tabCards.includes('01')) setDashTab('tech')
                }}
              />
            ),
          },
          { id: '07', w: 6, node: <InstPanel no="07" data={data} /> },
          { id: '08', w: 3, node: <CompositeTrendPanel no="08" data={data} /> },
          { id: '09', w: 3, node: <BrokerFlowPanel no="09" data={data} /> },
          { id: '10', w: 3, node: <MarginStructurePanel no="10" data={data} /> },
          { id: '11', w: 3, node: <EnergyPanel no="11" bars={bars} /> },
          { id: '12', w: 3, node: <HealthPanel no="12" data={data} /> },
          { id: '13', w: 3, node: <PoolPanel no="13" sid={sid} /> },
          { id: '14', w: 3, node: <RangeGaugePanel no="14" bars={bars} /> },
          { id: '15', w: 3, node: <CoveragePanel no="15" bars={bars} data={data} events={{ count: events.length, failed: eventsError !== null, loading: eventsLoading }} /> },
          { id: '16', w: 3, node: <RevenueTrendPanel no="16" data={data} /> },
          { id: '17', w: 3, node: <InsightSummaryPanel no="17" data={data} onOpen={() => selectTab('insights')} /> },
          { id: '18', w: 6, node: <BrokerCountPanel no="18" data={data} /> },
        ].filter((it) => tabCards === null || tabCards.includes(it.id))
          .sort((a, b) => (tabCards === null ? 0 : tabCards.indexOf(a.id) - tabCards.indexOf(b.id)))}
      />
      <div className="dash-tabs" role="tablist" aria-label="個股資料頁籤">
        {stockTabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`stock-tab-${t.id}`}
            aria-selected={currentTab === t.id}
            aria-controls="stock-tabpanel"
            className="dash-tab"
            onClick={() => selectTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        <span className="dash-tabs-note">完整明細 · 條件選股為因子篩選，非 ML 推薦</span>
      </div>
      <div id="stock-tabpanel" role="tabpanel" aria-labelledby={`stock-tab-${currentTab}`} className="flex flex-col gap-3 md:gap-4">
        {currentTab === 'chips' &&
          (live ? (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 md:gap-4 lg:[&>:only-child]:col-span-2">
                <InstitutionalPanel sid={sid} />
                <MarginPanel sid={sid} />
              </div>
              <BrokerPanel sid={sid} />
            </>
          ) : (
            <TPanel title="籌碼">
              <div className="dash-state">需即時 API（twquant serve）</div>
            </TPanel>
          ))}
        {currentTab === 'fundamentals' &&
          (live ? (
            <RevenuePanel sid={sid} />
          ) : (
            <TPanel title="基本面">
              <div className="dash-state">需即時 API（twquant serve）</div>
            </TPanel>
          ))}
        {currentTab === 'factors' &&
          (live ? (
            <ScorecardPanel sid={sid} />
          ) : (
            <TPanel title="個股因子體檢">
              <div className="dash-state">需即時 API（twquant serve）</div>
            </TPanel>
          ))}
        {currentTab === 'insights' && <StockInsightsPanel sid={sid} />}
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
              <div className="dash-state">條件選股需即時 API（twquant serve）</div>
            </TPanel>
          ))}
      </div>
    </div>
  )
}
