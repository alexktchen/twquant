// 個股 K 線 + 副圖（量 / 綜合因子分位 / 主力買賣超日條＋累計 / 可選因子z）。
// 副圖全部併進 K 線（v5 原生 pane、共用時間軸），每個 pane 左上以 watermark 標示指標名稱。
// factors＝要加開的因子副圖（一因子一 pane，方向化 z）；available＋onToggle 提供 chips 多選。
import { useMemo } from 'react'
import type { BrokerFlowPoint, Candle, CompositePoint, FactorOption, StockFactorHistory, StockSeries } from '../api'
import { fetchStockBrokerFlow, fetchStockCompositeHistory, fetchStockFactorHistory } from '../api'
import { RangeCandle, type ChartMarker } from '../charts'
import { useMediaQuery, useResource } from '../hooks'
import { MAX_FACTOR_PANES } from './store'
import { ChipRow, TPanel, chip } from './ui'

export function StockChartPanel({
  sid,
  title,
  series,
  factors = [],
  available = [],
  onToggle,
  markers,
  focusTime,
  onResetView,
}: {
  sid: string
  title: string
  series: Candle[]
  factors?: FactorOption[]
  available?: FactorOption[]
  onToggle?: (f: FactorOption) => void
  markers?: ChartMarker[]
  focusTime?: string | null
  onResetView?: () => void
}) {
  const emptyC: StockSeries<CompositePoint> = { sid, as_of: null, source: 'pg', rows: [] }
  const emptyB: StockSeries<BrokerFlowPoint> = { sid, as_of: null, source: 'pg', rows: [] }
  // 一律嘗試 fetch（API 不可達由 useResource catch 退回空列）——不依 live 閘，
  // 避免開頁瞬間 live=false 時掛載後永不重抓（useResource 以 key 為準）。
  const comp = useResource(`chist:${sid}`, () => fetchStockCompositeHistory(sid, 250), () => emptyC)
  const flow = useResource(`bflow:${sid}`, () => fetchStockBrokerFlow(sid, 250), () => emptyB)
  const codes = factors.map((f) => f.factor)
  const emptyFH: StockFactorHistory = { sid, as_of: null, source: 'pg', rows: [] }
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
  const cRows = comp.data?.rows ?? []
  const fRows = flow.data?.rows ?? []
  const hasComp = cRows.length > 1
  const hasFlow = fRows.length > 1
  const tall = hasComp || hasFlow || comp.loading || flow.loading
  // 手機直幅空間有限：主圖與各副圖 pane 等比縮小，避免整頁被單一圖表吃滿
  const desktop = useMediaQuery('(min-width: 768px)')
  const baseH = tall ? (desktop ? 540 : 430) : desktop ? 300 : 250
  const paneH = desktop ? 90 : 72
  const subs = [
    '量(股)',
    hasComp && '綜合因子分位',
    hasFlow && '主力買賣超(日條+累計，張)',
    ...factorPanes.map((fp) => fp.label),
  ].filter(Boolean).join(' / ')
  const compNote = hasComp ? ' · 綜合分位為相對定位、非預測訊號(D-020)' : ''
  return (
    <TPanel title={title} sub={`副圖：${subs} · 紅漲綠跌 · 可拖曳縮放${compNote}`}>
      {available.length > 0 && (
        <div className="mb-2">
          <ChipRow label={`副圖因子 ≤${MAX_FACTOR_PANES}`}>
            {available.map((a) => (
              <button
                key={a.factor}
                onClick={() => onToggle?.(a)}
                className={chip(codes.includes(a.factor))}
                title={`${a.factor}（direction ${a.direction > 0 ? '+1' : '−1'}）`}
              >
                {a.zh}
              </button>
            ))}
          </ChipRow>
        </div>
      )}
      <RangeCandle
        key={sid}
        data={series}
        height={baseH + factorPanes.length * paneH}
        volume
        composite={hasComp ? cRows : undefined}
        brokerFlow={hasFlow ? fRows : undefined}
        factorPanes={factorPanes.length ? factorPanes : undefined}
        markers={markers}
        focusTime={focusTime}
        onResetView={onResetView}
      />
    </TPanel>
  )
}
