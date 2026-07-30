// 個股因子體檢卡：全因子 signed-z 條圖 + 明細表（含 250 日 z 走勢 sparkline）。
import { useMemo } from 'react'
import type { Scorecard, StockFactorHistory } from '../api'
import { fetchStockFactorHistory, fetchStockFactors } from '../api'
import { useResource } from '../hooks'
import { Skeleton } from '../ui'
import { Spark, TPanel, ZBars } from './ui'

const verdict = (sz: number | null) =>
  sz == null
    ? { t: '—', c: 'var(--muted)' }
    : sz > 0.5
      ? { t: '強順', c: 'var(--up)' }
      : sz < -0.5
        ? { t: '逆勢', c: 'var(--down)' }
        : { t: '中性', c: 'var(--muted)' }

export function ScorecardPanel({ sid }: { sid: string }) {
  const empty: Scorecard = { sid, name: null, industry: null, market: null, as_of: null, factors: [], composite: null }
  const sc = useResource(`scorecard:${sid}`, () => fetchStockFactors(sid), () => empty)
  const names = (sc.data ?? empty).factors.map((f) => f.factor)
  const emptyFH: StockFactorHistory = { sid, as_of: null, source: 'pg', rows: [] }
  const fh = useResource(
    `fhist:${sid}:${names.length}`,
    () => (names.length ? fetchStockFactorHistory(sid, names, 250) : Promise.resolve(emptyFH)),
    () => emptyFH,
  )
  const zMap = useMemo(() => {
    const m = new Map<string, number[]>()
    for (const g of fh.data?.rows ?? []) m.set(g.factor, g.series.map((p) => p.z ?? 0))
    return m
  }, [fh.data])
  if (sc.loading) return <TPanel title="個股因子體檢"><Skeleton height={240} /></TPanel>
  const card = sc.data ?? empty
  const name = card.name ?? ''
  if (!card.factors.length) {
    return (
      <TPanel title="個股因子體檢" sub={sid}>
        <div className="h-[110px] flex items-center justify-center text-center px-4 text-[13px] text-[var(--muted)]">
          {sid}
          {name ? ` ${name}` : ''} 無足夠因子資料（非四碼普通股或史料不足）
        </div>
      </TPanel>
    )
  }
  const rows = [...card.factors].sort((a, b) => (b.signed_z ?? 0) - (a.signed_z ?? 0))
  const comp = card.composite
  return (
    <TPanel title="個股因子體檢" sub={card.as_of ?? ''}>
      <div className="flex flex-wrap items-baseline gap-2 mb-1">
        <span className="text-[16px] font-semibold text-[var(--fg)]">
          {sid} {name}
        </span>
        {card.industry && (
          <span className="text-[11px] px-1.5 py-0.5 rounded-[2px] bg-[var(--panel-2)]" style={{ color: 'var(--accent)' }}>
            {card.industry}
          </span>
        )}
      </div>
      {comp && (
        <div className="mb-3">
          <div className="text-[12px] text-[var(--muted)]">
            綜合因子傾向：宇宙前{' '}
            <span style={{ color: comp.pct >= 0.5 ? 'var(--up)' : 'var(--down)' }}>{Math.round((1 - comp.pct) * 100)}%</span>
            （signed-z 平均 {comp.signed_z_mean >= 0 ? '+' : ''}
            {comp.signed_z_mean}，共 {comp.n_universe} 檔）
          </div>
          <div className="text-[10px] text-[var(--muted)] mt-0.5 leading-relaxed">
            綜合分＝實證有效子集（短反轉＋產業殘差反轉＋月營收 YoY）的橫斷面相對定位，<b>非預測訊號</b>（D-020；因子預測力 RankIC 見健檢頁）
          </div>
        </div>
      )}
      <ZBars rows={rows.map((f) => ({ label: f.zh, value: f.signed_z ?? 0 }))} />
      <div className="overflow-x-auto mt-3">
        <table className="w-full min-w-[540px] text-[11px] mono tnum whitespace-nowrap">
          <thead className="text-[var(--muted)] text-left">
            <tr className="border-b border-[var(--line)]">
              <th className="py-1 pr-2 font-medium">因子</th>
              <th className="py-1 px-2 font-medium text-right">值</th>
              <th className="py-1 px-2 font-medium text-right">z</th>
              <th className="py-1 px-2 font-medium text-right">百分位</th>
              <th className="py-1 px-2 font-medium text-right">方向</th>
              <th className="py-1 pl-2 font-medium">傾向</th>
              <th className="py-1 pl-2 font-medium">250日 z 走勢</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => {
              const v = verdict(f.signed_z)
              return (
                <tr key={f.factor} className="border-b border-[var(--line)] last:border-0">
                  <td className="py-1 pr-2 text-[var(--fg)]">{f.zh}</td>
                  <td className="py-1 px-2 text-right text-[var(--muted)]">{f.value ?? '—'}</td>
                  <td
                    className="py-1 px-2 text-right"
                    style={{ color: f.z != null && Math.abs(f.z) > 1 ? (f.z > 0 ? 'var(--up)' : 'var(--down)') : 'var(--muted)' }}
                  >
                    {f.z == null ? '—' : `${f.z >= 0 ? '+' : ''}${f.z}`}
                  </td>
                  <td className="py-1 px-2 text-right text-[var(--muted)]">{f.pct == null ? '—' : `${Math.round(f.pct * 100)}%`}</td>
                  <td className="py-1 px-2 text-right text-[var(--muted)]">{f.direction > 0 ? '+1' : '−1'}</td>
                  <td className="py-1 pl-2" style={{ color: v.c }}>
                    [{v.t}]
                  </td>
                  <td className="py-1 pl-2"><Spark zs={zMap.get(f.factor)} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-[10px] text-[var(--muted)] leading-relaxed">
        紅=站多方、綠=站空方（direction 調整後 z）；百分位＝該因子橫斷面排名。此為「相對定位」，非個股預測力——因子預測力（RankIC）見因子健檢頁。
      </div>
    </TPanel>
  )
}
