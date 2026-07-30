// 個股因子頁專屬 UI 原件（終端風精煉版）。
// 只影響個股頁：其他頁沿用 src/ui.tsx 的 Panel/chipCls，樣式演進互不牽動。
import { DOWN, UP } from '../theme'

/* ---------- 終端風面板：方角、標題列含 accent tick、細分隔線 ---------- */
export function TPanel({
  title,
  sub,
  children,
  className = '',
}: {
  title?: string
  sub?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={`bg-[var(--panel)] border border-[var(--line)] rounded-sm ${className}`}>
      {title && (
        <header className="flex items-center gap-2 min-w-0 px-3 md:px-4 py-2 border-b border-[var(--line)]">
          <span className="w-[3px] self-stretch my-[3px] shrink-0 bg-[var(--accent)]" aria-hidden />
          <h3 className="text-[12px] font-semibold tracking-[0.06em] whitespace-nowrap">{title}</h3>
          {sub && <span className="ml-auto min-w-0 truncate text-[10px] mono text-[var(--muted)]" title={sub}>{sub}</span>}
        </header>
      )}
      <div className="p-3 md:p-4">{children}</div>
    </section>
  )
}

/* ---------- chip：方角＋不換行（手機橫向捲動列用）---------- */
export const chip = (active: boolean) =>
  `shrink-0 whitespace-nowrap px-2.5 py-1 text-[12px] mono rounded-[2px] border transition-colors ${
    active
      ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--panel-2)]'
      : 'border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)]'
  }`

/* ---------- chip 列：mobile 單行橫向捲動、md 起換行 ---------- */
export function ChipRow({ label, title, children }: { label?: string; title?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto hscroll md:flex-wrap md:overflow-x-visible">
      {label && (
        <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--muted)] mr-0.5" title={title}>
          {label}
        </span>
      )}
      {children}
    </div>
  )
}

/* ---------- 中央零軸 diverging bar（取代固定 viewBox 的 SVG 條圖）----------
   div 佈局：標籤/數字維持實際字級不隨容器縮放，手機上仍可讀（SVG viewBox 縮放會糊成 4px）。 */
export function ZBars({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9)
  return (
    <div className="flex flex-col gap-[3px]">
      {rows.map((r) => {
        const w = (Math.abs(r.value) / max) * 50
        const pos = r.value >= 0
        return (
          <div key={r.label} className="flex items-center gap-2 text-[11px] mono">
            <span className="w-[7.5rem] md:w-36 shrink-0 truncate text-[var(--fg)]">{r.label}</span>
            <div className="relative flex-1 h-[9px] bg-[var(--panel-2)] rounded-[1px] overflow-hidden">
              <span className="absolute left-1/2 top-0 bottom-0 w-px bg-[var(--line)]" />
              <span
                className="absolute top-[1px] bottom-[1px]"
                style={{
                  background: pos ? UP : DOWN,
                  opacity: 0.85,
                  left: pos ? '50%' : `${50 - w}%`,
                  width: `${Math.max(0.4, w)}%`,
                }}
              />
            </div>
            <span className="w-12 shrink-0 text-right tnum" style={{ color: pos ? 'var(--up)' : 'var(--down)' }}>
              {pos ? '+' : ''}
              {r.value.toFixed(2)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/* ---------- 帶號小數字（法人/融資券/營收面板的摘要列）---------- */
export function Delta({ label, v, unit = '' }: { label: string; v: number; unit?: string }) {
  const c = v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'var(--muted)'
  return (
    <span className="text-[11px] mono whitespace-nowrap">
      <span className="text-[var(--muted)]">{label} </span>
      <span style={{ color: c }}>{v > 0 ? '+' : ''}{v.toLocaleString()}{unit}</span>
    </span>
  )
}

/* ---------- 迷你時序（棒＋疊線 inline SVG，紅漲綠跌）---------- */
export function MiniTS({ bars, line, height = 84 }: { bars: number[]; line?: number[]; height?: number }) {
  const n = bars.length
  if (!n) return <div className="h-[60px] flex items-center text-[11px] text-[var(--muted)]">無資料</div>
  const step = Math.max(2.5, Math.min(16, 900 / n))
  const W = n * step
  const bmax = Math.max(...bars.map((v) => Math.abs(v)), 1e-9)
  const zero = height / 2
  let pts = ''
  if (line && line.length === n) {
    const lo = Math.min(...line)
    const hi = Math.max(...line)
    const rng = hi - lo || 1
    pts = line.map((v, i) => `${i * step + step / 2},${height - ((v - lo) / rng) * (height - 6) - 3}`).join(' ')
  }
  return (
    <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      {bars.map((v, i) => {
        const h = (Math.abs(v) / bmax) * (height / 2 - 2)
        return (
          <rect key={i} x={i * step + 0.75} y={v >= 0 ? zero - h : zero} width={Math.max(1, step - 1.5)}
            height={Math.max(0.5, h)} fill={v >= 0 ? UP : DOWN} opacity={0.8} />
        )
      })}
      {pts && <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={1.3} vectorEffect="non-scaling-stroke" />}
    </svg>
  )
}

/* P7：因子 z 值 250 日迷你走勢（表格內欄位）。0 軸虛線 + 折線；資料不足畫點。 */
export function Spark({ zs }: { zs: number[] | undefined }) {
  const W = 110, H = 22
  if (!zs || zs.length === 0) return <span className="text-[10px] text-[var(--muted)]">—</span>
  const lo = Math.min(...zs, 0), hi = Math.max(...zs, 0)
  const rng = hi - lo || 1
  const y = (v: number) => H - 2 - ((v - lo) / rng) * (H - 4)
  const step = zs.length > 1 ? W / (zs.length - 1) : 0
  const pts = zs.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const last = zs[zs.length - 1]
  return (
    <svg width={W} height={H} className="block">
      <line x1={0} y1={y(0)} x2={W} y2={y(0)} stroke="var(--line)" strokeWidth={1} strokeDasharray="2,3" />
      {zs.length > 1 ? (
        <polyline points={pts} fill="none" stroke={last >= 0 ? UP : DOWN} strokeWidth={1.2} />
      ) : (
        <circle cx={W / 2} cy={y(last)} r={2} fill={last >= 0 ? UP : DOWN} />
      )}
    </svg>
  )
}
