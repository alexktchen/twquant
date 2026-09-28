// 個股 K 線 + 副圖（量 / 綜合因子分位 / 主力買賣超日條＋累計 / 可選因子z / KD / MACD）。
// 副圖全部併進 K 線（v5 原生 pane、共用時間軸），每個 pane 左上以 watermark 標示指標名稱。
// factors＝要加開的因子副圖（一因子一 pane，方向化 z）；available＋onToggle 提供 chips 多選。
// 綜合分位與主力序列由頁面讀一次後傳入（與儀表板面板共用），本元件不重複打 API。
import { useContext, useMemo, useState } from 'react'
import type { BrokerFlowPoint, Candle, CompositePoint, FactorOption, StockFactorHistory } from '../api'
import { fetchStockFactorHistory } from '../api'
import { RangeCandle, type ChartMarker, type DrawAnalysis } from '../charts'
import { useMediaQuery, useResource } from '../hooks'
import { MAX_FACTOR_PANES } from './store'
import { ChipRow, TPanel, chip } from './ui'
import { CardHeightContext } from './DashGrid'

// 卡片裡圖以外的高度（標題列＋副圖因子列＋自動畫線／均線開關列＋內距），與圖的下限
const CHART_CHROME_H = 230
const MIN_CHART_H = 200

export function StockChartPanel({
  no,
  sid,
  title,
  series,
  composite,
  brokerFlow,
  subLoading = false,
  factors = [],
  available = [],
  onToggle,
  markers,
  focusTime,
  onResetView,
  onReady,
  onAnalysis,
  className,
}: {
  no?: string
  sid: string
  title: string
  series: Candle[]
  composite: CompositePoint[]
  brokerFlow: BrokerFlowPoint[]
  subLoading?: boolean
  factors?: FactorOption[]
  available?: FactorOption[]
  onToggle?: (f: FactorOption) => void
  markers?: ChartMarker[]
  focusTime?: string | null
  onResetView?: () => void
  onReady?: (shot: (() => HTMLCanvasElement) | null) => void
  /** 判讀摘要／清單改由頁面放到獨立卡片（T-323）。 */
  onAnalysis?: (a: DrawAnalysis | null) => void
  className?: string
}) {
  const codes = factors.map((f) => f.factor)
  const [pickerOpen, setPickerOpen] = useState(false)
  const emptyFH: StockFactorHistory = { sid, as_of: null, source: 'pg', rows: [] }
  // 一律嘗試 fetch（API 不可達由 useResource catch 退回空列）——不依 live 閘，
  // 避免開頁瞬間 live=false 時掛載後永不重抓（useResource 以 key 為準）。
  const fh = useResource(
    `fpanes:${sid}:${codes.join(',')}`,
    () => (codes.length ? fetchStockFactorHistory(sid, codes, 250) : Promise.resolve(emptyFH)),
    () => emptyFH,
  )
  const factorPanes = useMemo(() => {
    const groups = new Map((fh.data?.rows ?? []).map((g) => [g.factor, g.series]))
    return factors
      .map((f) => ({ label: f.zh, direction: f.direction, series: groups.get(f.factor) ?? [] }))
      .filter((fp) => fp.series.length > 1)
    // codes.join 已涵蓋 factors 內容變化；fh.data 換股/換因子時更新
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fh.data, codes.join(',')])
  const hasComp = composite.length > 1
  const hasFlow = brokerFlow.length > 1
  const tall = hasComp || hasFlow || subLoading
  // 手機直幅空間有限：主圖與各副圖 pane 等比縮小，避免整頁被單一圖表吃滿
  const desktop = useMediaQuery('(min-width: 768px)')
  const baseH = tall ? (desktop ? 600 : 430) : desktop ? 320 : 250
  const paneH = desktop ? 90 : 72
  // 使用者把 K 線卡拉高（T-322）：圖跟著撐高，扣掉標題列與各開關列的高度
  const cardH = useContext(CardHeightContext)
  const naturalH = baseH + factorPanes.length * paneH
  const chartH = cardH === null ? naturalH : Math.max(MIN_CHART_H, cardH - CHART_CHROME_H)
  const subs = [
    '量(股)',
    hasComp && '綜合因子分位',
    hasFlow && '主力買賣超(日條+累計，張)',
    ...factorPanes.map((fp) => fp.label),
  ].filter(Boolean).join(' / ')
  const compNote = hasComp ? ' · 綜合分位為相對定位、非預測訊號(D-020)' : ''
  return (
    <TPanel no={no} title={title} sub={`副圖：${subs} · 紅漲綠跌 · 可拖曳縮放${compNote}`} className={className}>
      {available.length > 0 && (
        <div className="mb-2">
          {/* 因子清單很長（數十個）：預設只列已開的副圖，其餘收在開關後面，主圖才不會被擠到下面。 */}
          <ChipRow label={`副圖因子 ≤${MAX_FACTOR_PANES}`}>
            {(pickerOpen ? available : available.filter((a) => codes.includes(a.factor))).map((a) => (
              <button
                key={a.factor}
                onClick={() => onToggle?.(a)}
                className={chip(codes.includes(a.factor))}
                title={`${a.factor}（direction ${a.direction > 0 ? '+1' : '−1'}）`}
              >
                {a.zh}
              </button>
            ))}
            <button type="button" className={chip(false)} aria-expanded={pickerOpen} onClick={() => setPickerOpen((v) => !v)}>
              {pickerOpen ? '收合因子清單' : `＋ 選擇因子（${available.length}）`}
            </button>
          </ChipRow>
        </div>
      )}
      <RangeCandle
        key={sid}
        data={series}
        height={chartH}
        volume
        composite={hasComp ? composite : undefined}
        brokerFlow={hasFlow ? brokerFlow : undefined}
        factorPanes={factorPanes.length ? factorPanes : undefined}
        markers={markers}
        focusTime={focusTime}
        onResetView={onResetView}
        onReady={onReady}
        onAnalysis={onAnalysis}
      />
    </TPanel>
  )
}
