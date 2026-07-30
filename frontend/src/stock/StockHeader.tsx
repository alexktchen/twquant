// 個股頁首：看盤式總覽（名稱/產業/最新價/漲跌/52週位置/綜合分位）。
import type { StockOverview } from '../api'
import { fetchStockOverview } from '../api'
import { useResource } from '../hooks'
import { TPanel } from './ui'

export function StockHeader({ sid, fallbackName }: { sid: string; fallbackName: string }) {
  const empty: StockOverview = {
    sid, name: fallbackName || null, industry: null, market: null, as_of: null,
    source: 'pg', close: null, chg: null, chg_pct: null, volume: null, pos_52w: null, composite: null,
  }
  const r = useResource(`overview:${sid}`, () => fetchStockOverview(sid), () => empty)
  const d = r.data ?? empty
  const up = (d.chg ?? 0) >= 0
  const col = up ? 'var(--up)' : 'var(--down)'
  return (
    <TPanel>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[17px] md:text-[18px] font-semibold text-[var(--fg)] whitespace-nowrap">
          {sid} {d.name ?? fallbackName}
        </span>
        {d.industry && (
          <span className="text-[11px] px-1.5 py-0.5 rounded-[2px] bg-[var(--panel-2)]" style={{ color: 'var(--accent)' }}>{d.industry}</span>
        )}
        {d.market && <span className="text-[11px] text-[var(--muted)]">{d.market}</span>}
        <span className="ml-auto text-[22px] md:text-[24px] font-semibold mono tnum" style={{ color: col }}>
          {d.close != null ? d.close.toLocaleString() : '—'}
        </span>
        {d.chg != null && (
          <span className="text-[13px] md:text-[14px] mono tnum whitespace-nowrap" style={{ color: col }}>
            {up ? '▲' : '▼'}{Math.abs(d.chg).toFixed(2)} ({up ? '+' : ''}{((d.chg_pct ?? 0) * 100).toFixed(2)}%)
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-x-5 md:gap-x-6 gap-y-1 mt-2 text-[12px] mono">
        {d.pos_52w != null && (
          <span className="text-[var(--muted)] whitespace-nowrap">52週位置 <span className="text-[var(--fg)]">{Math.round(d.pos_52w * 100)}%</span></span>
        )}
        {d.composite && (
          <span className="text-[var(--muted)] whitespace-nowrap">綜合因子分位 <span style={{ color: d.composite.pct >= 0.5 ? 'var(--up)' : 'var(--down)' }}>{Math.round(d.composite.pct * 100)}%</span>（{d.composite.n_universe} 檔）</span>
        )}
        {d.volume != null && (
          <span className="text-[var(--muted)] whitespace-nowrap">成交 {Math.round(d.volume / 1000).toLocaleString()} 張</span>
        )}
        <span className="ml-auto text-[10px] text-[var(--muted)] whitespace-nowrap">{d.as_of ?? ''} · {d.source}</span>
      </div>
    </TPanel>
  )
}
