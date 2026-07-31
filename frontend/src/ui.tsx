// 共用 UI 原件：面板、KPI、骨架、chip 樣式。
export function Panel({
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
    <section className={`bg-[var(--panel)] border border-[var(--line)] rounded-lg p-4 ${className}`}>
      {title && (
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-[13px] font-semibold tracking-wide">{title}</h3>
          {sub && <span className="text-[11px] mono text-[var(--muted)]">{sub}</span>}
        </div>
      )}
      {children}
    </section>
  )
}

export function Kpi({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="bg-[var(--panel)] border border-[var(--line)] rounded-lg px-4 py-3">
      <div className="eyebrow">{label}</div>
      <div className="text-[22px] font-semibold mono tnum mt-1" style={{ color: tone }}>{value}</div>
    </div>
  )
}

export function Skeleton({ height = 200, className = '' }: { height?: number; className?: string }) {
  return <div className={`animate-pulse rounded bg-[var(--panel-2)] ${className}`} style={{ height }} />
}

export const chipCls = (active: boolean) =>
  `px-2.5 py-1 text-[12px] mono rounded border transition-colors ${
    active ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--panel-2)]' : 'border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)]'
  }`
