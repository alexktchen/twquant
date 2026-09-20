// 儀表板的純呈現原件（inline SVG／div）：只畫傳進來的數字，自己不取資料、不算指標。
// value 為 null 一律畫成「—」，絕不當成 0 畫出來。
import type { VolumeProfile } from './indicators.ts'
import { ACC, DOWN, UP, WARN, priceFmt } from '../theme'
import { Skeleton } from '../ui'

/* ---------- 面板狀態：載入中／讀取失敗／零筆 三者分開（SPEC-014 §1）---------- */
export type LoadState = 'loading' | 'failed' | 'empty' | 'ok'
export function stateOf(res: { loading: boolean; live: boolean }, rows: number): LoadState {
  return res.loading ? 'loading' : !res.live ? 'failed' : rows === 0 ? 'empty' : 'ok'
}
export function PanelState({ state, empty = '這檔股票在此區間沒有資料（零筆）。', height = 120 }: { state: LoadState; empty?: string; height?: number }) {
  if (state === 'loading') return <Skeleton height={height} />
  return (
    <div className="dash-state" style={{ minHeight: height }}>
      {state === 'failed' ? '資料讀取失敗，未以其他來源替代；這不是「沒有資料」。' : empty}
    </div>
  )
}

/* ---------- 雷達圖：每軸 0~1，null 軸不連線、標「—」---------- */
export function Radar({ axes, color = ACC }: { axes: { label: string; value: number | null }[]; color?: string }) {
  const W = 250, H = 196, cx = W / 2, cy = 100, R = 64
  const n = axes.length
  const pt = (i: number, r: number): [number, number] => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)]
  }
  const ring = (f: number) => axes.map((_, i) => pt(i, R * f).map((v) => v.toFixed(1)).join(',')).join(' ')
  const shape = axes.map((a, i) => pt(i, R * Math.max(0, Math.min(1, a.value ?? 0))).map((v) => v.toFixed(1)).join(',')).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" style={{ maxHeight: 220 }} role="img" aria-label={axes.map((a) => `${a.label} ${a.value == null ? '無資料' : Math.round(a.value * 100)}`).join('、')}>
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon key={f} points={ring(f)} fill="none" stroke="var(--line)" strokeWidth={f === 1 ? 1.2 : 0.8} />
      ))}
      {axes.map((_, i) => {
        const [x, y] = pt(i, R)
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="var(--line)" strokeWidth={0.8} />
      })}
      <polygon points={shape} fill={color} fillOpacity={0.28} stroke={color} strokeWidth={1.6} strokeLinejoin="round" />
      {axes.map((a, i) => {
        const [x, y] = pt(i, R * Math.max(0, Math.min(1, a.value ?? 0)))
        return a.value == null ? null : <circle key={i} cx={x} cy={y} r={2.4} fill={color} />
      })}
      {axes.map((a, i) => {
        const [x, y] = pt(i, R + 13)
        const anchor = x > cx + 6 ? 'start' : x < cx - 6 ? 'end' : 'middle'
        const dy = y < cy - R / 2 ? -3 : y > cy + R / 2 ? 9 : 3
        return (
          <text key={i} x={x} y={y + dy} textAnchor={anchor} fontSize={10.5} fill="var(--fg)">
            {a.label}
            <tspan dx={4} fill={a.value == null ? 'var(--muted)' : color} fontFamily="ui-monospace,Consolas,monospace">
              {a.value == null ? '—' : Math.round(a.value * 100)}
            </tspan>
          </text>
        )
      })}
    </svg>
  )
}

/* ---------- 甜甜圈：0~1 的比例；null 畫空環與「—」---------- */
export function Donut({ value, label, color = ACC, size = 62 }: { value: number | null; label: string; color?: string; size?: number }) {
  const r = size / 2 - 5
  const c = 2 * Math.PI * r
  const v = value == null ? 0 : Math.max(0, Math.min(1, value))
  return (
    <div className="dash-donut">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label} ${value == null ? '無資料' : `${Math.round(v * 100)}%`}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--panel-2)" strokeWidth={6} />
        {value != null && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={6} strokeLinecap="round"
            strokeDasharray={`${(c * v).toFixed(2)} ${c.toFixed(2)}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        )}
        <text x="50%" y="50%" dy="0.35em" textAnchor="middle" fontSize={size * 0.24} fontWeight={700} fill={value == null ? 'var(--muted)' : 'var(--fg)'} fontFamily="ui-monospace,Consolas,monospace">
          {value == null ? '—' : `${Math.round(v * 100)}%`}
        </text>
      </svg>
      <span>{label}</span>
    </div>
  )
}

/* ---------- 半圓儀表：0~1 的位置，三段底色只分低／中／高，不帶多空判斷 ---------- */
export function Gauge({ value, caption }: { value: number | null; caption: string }) {
  const W = 220, H = 128, cx = W / 2, cy = 112, R = 84
  const at = (f: number, r = R): [number, number] => {
    const a = Math.PI * (1 - f)
    return [cx + r * Math.cos(a), cy - r * Math.sin(a)]
  }
  const arc = (f0: number, f1: number) => {
    const [x0, y0] = at(f0), [x1, y1] = at(f1)
    return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${R} ${R} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`
  }
  const v = value == null ? null : Math.max(0, Math.min(1, value))
  const [nx, ny] = at(v ?? 0.5, R - 16)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" style={{ maxHeight: 150 }} role="img" aria-label={`${caption} ${v == null ? '無資料' : `${Math.round(v * 100)}%`}`}>
      {([[0, 1 / 3, DOWN], [1 / 3, 2 / 3, WARN], [2 / 3, 1, UP]] as [number, number, string][]).map(([a, b, col]) => (
        <path key={col} d={arc(a + 0.006, b - 0.006)} fill="none" stroke={col} strokeWidth={13} opacity={0.85} />
      ))}
      {[0, 0.5, 1].map((f) => {
        const [x, y] = at(f, R + 14)
        return <text key={f} x={x} y={y + 3} textAnchor="middle" fontSize={9.5} fill="var(--muted)">{f * 100}</text>
      })}
      {v != null && <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="var(--fg)" strokeWidth={2.4} strokeLinecap="round" />}
      <circle cx={cx} cy={cy} r={5} fill="var(--fg)" />
      <text x={cx} y={cy - 26} textAnchor="middle" fontSize={20} fontWeight={700} fill={v == null ? 'var(--muted)' : 'var(--fg)'} fontFamily="ui-monospace,Consolas,monospace">
        {v == null ? '—' : `${Math.round(v * 100)}%`}
      </text>
    </svg>
  )
}

/* ---------- 籌碼熱區：量價分布的每個價格格子一列，越亮＝該價位成交量越大 ---------- */
const lerp = (a: number, b: number, t: number) => Math.round(a + (b - a) * t)
function heat(t: number): string {
  // 深藍 → 亮藍 → 黃：三個定點的線性內插（僅供視覺區分量的多寡）
  const stops: [number, number, number][] = [[22, 38, 74], [47, 109, 240], [245, 197, 66]]
  const [lo, hi, f] = t < 0.6 ? [stops[0], stops[1], t / 0.6] : [stops[1], stops[2], (t - 0.6) / 0.4]
  return `rgb(${lerp(lo[0], hi[0], f)},${lerp(lo[1], hi[1], f)},${lerp(lo[2], hi[2], f)})`
}
export function VolumeHeat({ profile, close }: { profile: VolumeProfile; close: number }) {
  const bins = profile.vol.length
  const rowH = 8.4, top = 6, left = 46, right = 8, W = 260
  const H = top * 2 + bins * rowH
  const max = Math.max(...profile.vol)
  const yOf = (price: number) => top + (1 - (price - profile.lo) / (profile.hi - profile.lo)) * bins * rowH
  const yc = Math.max(top, Math.min(H - top, yOf(close)))
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" style={{ maxHeight: 250 }} role="img" aria-label={`量價分布：最大量價位 ${priceFmt(profile.poc)}，價值區 ${priceFmt(profile.val)} 至 ${priceFmt(profile.vah)}`}>
      {profile.vol.map((v, i) => {
        const row = bins - 1 - i // 高價在上
        const t = max > 0 ? v / max : 0
        return (
          <g key={i}>
            <rect x={left} y={top + row * rowH + 0.6} width={W - left - right} height={rowH - 1.2} fill="var(--panel-2)" opacity={0.55} />
            <rect x={left} y={top + row * rowH + 0.6} width={Math.max(1, (W - left - right) * t)} height={rowH - 1.2} fill={heat(t)} rx={1} />
            {i % 4 === 0 && (
              <text x={left - 5} y={top + row * rowH + rowH - 1.4} textAnchor="end" fontSize={8.5} fill="var(--muted)" fontFamily="ui-monospace,Consolas,monospace">
                {priceFmt(profile.lo + (i + 0.5) * profile.binWidth)}
              </text>
            )}
          </g>
        )
      })}
      {/* 價值區（涵蓋 70% 成交量）以左側豎線標示 */}
      <line x1={left - 2} x2={left - 2} y1={top + (bins - 1 - profile.vaHiIndex) * rowH} y2={top + (bins - profile.vaLoIndex) * rowH} stroke="var(--accent-2)" strokeWidth={2.2} />
      <line x1={left} x2={W - right} y1={yc} y2={yc} stroke="var(--fg)" strokeWidth={1} strokeDasharray="3,3" />
      <text x={W - right - 2} y={yc - 3} textAnchor="end" fontSize={9.5} fontWeight={700} fill="var(--fg)" fontFamily="ui-monospace,Consolas,monospace">
        收 {priceFmt(close)}
      </text>
    </svg>
  )
}

/* ---------- 橫條量表：一列一個比例（0~1）；ratio 為 null 時不畫條 ---------- */
export function Meter({ label, ratio, text, color = ACC }: { label: string; ratio: number | null; text: string; color?: string }) {
  return (
    <div className="dash-meter">
      <span>{label}</span>
      <div className="dash-track">{ratio != null && <i style={{ width: `${Math.max(0, Math.min(1, ratio)) * 100}%`, background: color }} />}</div>
      <span>{text}</span>
    </div>
  )
}
