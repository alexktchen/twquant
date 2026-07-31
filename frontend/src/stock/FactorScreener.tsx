// 因子選股（單因子最新物化日、方向化 z 由強到弱；點擊即選股）。
// 受控元件：factor 狀態由頁面持有（篩選器選中的因子同步進 K 線副圖）。
import type { FactorOption, FactorOrder, FactorTop, StockMetaMap } from '../api'
import { ChipRow, TPanel, chip } from './ui'

export function FactorScreener({
  sid, setSid, mm, value, onChange, order, onOrderChange, data, loading,
}: {
  sid: string
  setSid: (s: string) => void
  mm: StockMetaMap
  value: FactorOption
  onChange: (f: FactorOption) => void
  order: FactorOrder
  onOrderChange: (o: FactorOrder) => void
  data: FactorTop | null
  loading: boolean
}) {
  const d = data
  const rows = d?.rows ?? []
  const avail = d?.available ?? []
  const strongest = order === 'top'
  return (
    <TPanel
      title="因子選股"
      sub={
        d?.as_of
          ? `物化日 ${d.as_of} · 訊號${strongest ? '最強' : '最弱'}前 ${rows.length}`
          : '需 PG 物化因子'
      }
    >
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={value.factor}
            onChange={(e) => {
              const opt = avail.find((a) => a.factor === e.target.value)
              if (opt) onChange(opt)
            }}
            className="max-w-full min-w-0 px-2 py-1 text-[12px] mono rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          >
            {(avail.length ? avail : [value]).map((a) => (
              <option key={a.factor} value={a.factor}>
                {a.zh}（{a.factor}）
              </option>
            ))}
          </select>
          <div className="inline-flex rounded-[2px] border border-[var(--line)] overflow-hidden">
            {([['top', '訊號最強'], ['bottom', '訊號最弱']] as [FactorOrder, string][]).map(([o, lbl]) => (
              <button
                key={o}
                onClick={() => onOrderChange(o)}
                className={`px-2.5 py-1 text-[12px] mono whitespace-nowrap transition-colors ${
                  order === o ? 'bg-[var(--panel-2)] text-[var(--accent)]' : 'text-[var(--muted)] hover:text-[var(--fg)]'
                }`}
              >
                {lbl}
              </button>
            ))}
          </div>
          <span className="text-[11px] text-[var(--muted)]">
            {strongest
              ? '最順因子方向（如 5日反轉＝近5日跌深者）· 點擊即選股'
              : '最逆因子方向（如 5日反轉＝近5日漲多者）· 點擊即選股'}
          </span>
        </div>
        {rows.length > 0 ? (
          <ChipRow>
            {rows.map((r, i) => (
              <button
                key={r.stock_id}
                onClick={() => setSid(r.stock_id)}
                className={chip(sid === r.stock_id)}
                title={`值 ${r.value} · z ${r.z} · 宇宙分位 ${Math.round(r.pct * 100)}%`}
              >
                <span className="mr-1 opacity-60">{i + 1}</span>
                {r.stock_id}
                {(r.name ?? mm[r.stock_id]?.name) && (
                  <span className="ml-1 text-[11px] opacity-75">{r.name ?? mm[r.stock_id]?.name}</span>
                )}
                <span className="ml-1 text-[11px] mono opacity-75">
                  {r.signed_z >= 0 ? '+' : ''}
                  {r.signed_z.toFixed(2)}
                </span>
              </button>
            ))}
          </ChipRow>
        ) : (
          <div className="text-[12px] text-[var(--muted)]">
            {loading ? '載入中…' : '此因子尚無物化資料（PG 未物化或 API 離線）'}
          </div>
        )}
      </div>
    </TPanel>
  )
}
