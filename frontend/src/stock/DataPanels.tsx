// 個股資料面板（法人 / 融資券 / 月營收 / 主力券商；讀 PG 供應層 v2 端點）。
import type { BrokerRow, InstRow, MarginRow, RevenueRow, StockBrokers, StockSeries } from '../api'
import { fetchStockBrokers, fetchStockInstitutional, fetchStockMargin, fetchStockRevenue } from '../api'
import { useResource } from '../hooks'
import { zhang } from '../theme'
import { Skeleton } from '../ui'
import { Delta, MiniTS, TPanel } from './ui'

/** 讀取失敗與「真的零筆」必須分開顯示，否則面板消失會被誤讀成沒有訊號（SPEC-014 §1）。 */
function PanelState({ title, live }: { title: string; live: boolean }) {
  return (
    <TPanel title={title}>
      <div className="h-[100px] flex items-center justify-center text-center text-[13px] text-[var(--muted)]">
        {live ? '這檔股票在此區間沒有資料（零筆）。' : '資料讀取失敗，未以其他來源替代；這不是「沒有資料」。'}
      </div>
    </TPanel>
  )
}

export function InstitutionalPanel({ sid }: { sid: string }) {
  const empty: StockSeries<InstRow> = { sid, as_of: null, source: 'pg', rows: [] }
  const r = useResource(`inst:${sid}`, () => fetchStockInstitutional(sid, 90), () => empty)
  if (r.loading) return <TPanel title="法人買賣超"><Skeleton height={100} /></TPanel>
  const rows = r.data?.rows ?? []
  if (!rows.length) return <PanelState title="法人買賣超" live={r.live} />
  const last = rows[rows.length - 1]
  return (
    <TPanel title="法人買賣超（張）" sub={`外資日買賣超＋累計 · ${r.data?.source}`}>
      <MiniTS bars={rows.map((x) => zhang(x.foreign_net))} line={rows.map((x) => zhang(x.cum_foreign))} />
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        <Delta label="外資" v={zhang(last.foreign_net)} />
        <Delta label="投信" v={zhang(last.trust_net)} />
        <Delta label="自營" v={zhang(last.dealer_net)} />
        <span className="text-[11px] mono text-[var(--muted)] whitespace-nowrap">累計外資 {zhang(last.cum_foreign).toLocaleString()} 張</span>
      </div>
    </TPanel>
  )
}

export function MarginPanel({ sid }: { sid: string }) {
  const empty: StockSeries<MarginRow> = { sid, as_of: null, source: 'pg', rows: [] }
  const r = useResource(`margin:${sid}`, () => fetchStockMargin(sid, 90), () => empty)
  if (r.loading) return <TPanel title="融資券"><Skeleton height={100} /></TPanel>
  const rows = r.data?.rows ?? []
  if (!rows.length) return <PanelState title="融資券" live={r.live} />
  const last = rows[rows.length - 1]
  const ratio = last.short_margin_ratio
  return (
    <TPanel title="融資券（張）" sub={`融資餘額(線)＋日增減(棒) · ${r.data?.source}`}>
      <MiniTS bars={rows.map((x) => zhang(x.margin_chg ?? 0))} line={rows.map((x) => zhang(x.margin_balance))} />
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        <span className="text-[11px] mono text-[var(--muted)] whitespace-nowrap">融資餘額 {zhang(last.margin_balance).toLocaleString()} 張</span>
        <span className="text-[11px] mono text-[var(--muted)] whitespace-nowrap">融券餘額 {zhang(last.short_balance).toLocaleString()} 張</span>
        <span className="text-[11px] mono text-[var(--muted)] whitespace-nowrap">券資比 {ratio == null ? '—' : `${(ratio * 100).toFixed(1)}%`}</span>
      </div>
    </TPanel>
  )
}

export function RevenuePanel({ sid }: { sid: string }) {
  const empty: StockSeries<RevenueRow> = { sid, as_of: null, source: 'pg', rows: [] }
  const r = useResource(`rev:${sid}`, () => fetchStockRevenue(sid, 36), () => empty)
  if (r.loading) return <TPanel title="月營收"><Skeleton height={100} /></TPanel>
  const rows = r.data?.rows ?? []
  if (!rows.length) return <PanelState title="月營收" live={r.live} />
  const last = rows[rows.length - 1]
  const yi = (v: number | null) => (v == null ? 0 : v / 1e8)
  return (
    <TPanel title="月營收（億元）" sub={`月營收(棒)＋YoY(線) · ${r.data?.source}`}>
      <MiniTS bars={rows.map((x) => yi(x.revenue))} line={rows.map((x) => (x.yoy ?? 0) * 100)} />
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        <span className="text-[11px] mono text-[var(--muted)] whitespace-nowrap">{last.month} 營收 {yi(last.revenue).toFixed(2)} 億</span>
        <Delta label="YoY" v={Math.round((last.yoy ?? 0) * 1000) / 10} unit="%" />
        <Delta label="MoM" v={Math.round((last.mom ?? 0) * 1000) / 10} unit="%" />
      </div>
    </TPanel>
  )
}

export function BrokerPanel({ sid }: { sid: string }) {
  const empty: StockBrokers = { sid, as_of: null, source: 'pg', window_days: 5, top_buy: [], top_sell: [], summary: [] }
  const r = useResource(`brokers:${sid}`, () => fetchStockBrokers(sid, 5), () => empty)
  if (r.loading) return <TPanel title="主力券商買賣超"><Skeleton height={120} /></TPanel>
  const d = r.data ?? empty
  if (!d.top_buy.length && !d.top_sell.length) return <PanelState title="主力券商買賣超" live={r.live} />
  const bcol = (rows: BrokerRow[], title: string, up: boolean) => (
    <div className="min-w-0">
      <div className="text-[12px] font-semibold mb-1" style={{ color: up ? 'var(--up)' : 'var(--down)' }}>{title}</div>
      <table className="w-full text-[11px] mono tnum">
        <tbody>
          {rows.slice(0, 10).map((b) => (
            <tr key={b.broker} className="border-b border-[var(--line)] last:border-0">
              <td className="py-0.5 pr-2 text-[var(--fg)] whitespace-nowrap">{b.broker}</td>
              <td className="py-0.5 text-right" style={{ color: up ? 'var(--up)' : 'var(--down)' }}>
                {up ? '+' : ''}{Math.round(b.net_shares / 1000).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
  return (
    <TPanel title="主力券商買賣超（張）" sub={`富邦DJ 母公司 · 近 ${d.window_days} 日彙總 · ${d.source}`}>
      <div className="grid grid-cols-2 gap-3 md:gap-4">
        {bcol(d.top_buy, '買超前 10', true)}
        {bcol(d.top_sell, '賣超前 10', false)}
      </div>
      <div className="mt-2 text-[10px] text-[var(--muted)]">來源為券商母公司層級 top-15，非分點明細、無成交均價。</div>
    </TPanel>
  )
}
