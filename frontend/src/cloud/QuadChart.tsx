// 族群法人象限圖（D-177 修訂三／T-358）：每個族群一顆泡泡，x＝法人近 5 日買賣超、y＝加速、大小＝當天成交金額；
// 選中的族群畫出最近幾天怎麼走過來（軌跡）。點泡泡選族群。座標換算與刻度在 quadrant.ts。
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RankRow } from './group'
import { QUADRANT_NAME, YI, extent, md, quadrant, radius, scaler, ticks, yiText, type InstPoint } from './quadrant'

/** 繪圖區四周留給刻度與象限名稱的空間（px）。 */
const PAD = { left: 44, right: 12, top: 34, bottom: 40 }
/** 泡泡最小半徑（px）；最大的是繪圖區短邊的 7%。 */
const R_MIN = 4
const R_MAX_SHARE = 0.07

const sign = (v: number) => (v < 0 ? '−' : '+')
const tickLabel = (v: number) => (v === 0 ? '0' : `${sign(v)}${Math.abs(v)}`)

export function QuadChart({
  rows,
  inst,
  current,
  onPick,
}: {
  rows: RankRow[]
  inst: Record<string, InstPoint>
  current: string
  onPick: (grp: string) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const pts = useMemo(() => rows.filter((r) => inst[r.grp]).map((r) => ({ row: r, p: inst[r.grp] })), [rows, inst])
  const ext = useMemo(() => extent(pts.map((q) => q.p)), [pts])
  const area = { left: PAD.left, top: PAD.top, width: Math.max(0, size.w - PAD.left - PAD.right), height: Math.max(0, size.h - PAD.top - PAD.bottom) }
  const s = scaler(ext, area)
  const maxShare = Math.max(0, ...pts.map((q) => q.row.share))
  const rMax = Math.max(R_MIN * 2, Math.min(area.width, area.height) * R_MAX_SHARE)
  const sel = pts.find((q) => q.row.grp === current) ?? null
  // 大的先畫、小的疊在上面才點得到；選中的最後畫
  const order = [...pts].filter((q) => q !== sel).sort((a, b) => b.row.share - a.row.share)
  const xt = ticks(ext.x)
  const yt = ticks(ext.y)
  const x0 = s.x(0)
  const y0 = s.y(0)
  const right = area.left + area.width
  const bottom = area.top + area.height
  const bubble = (q: (typeof pts)[number], on: boolean) => {
    const { p, row } = q
    const quad = quadrant(p.x, p.y)
    const cls = `cq-bub ${p.x >= 0 ? 'up' : 'down'}${quad === 'cool' || quad === 'ease' ? ' weak' : ''}${on ? ' on' : ''}`
    return (
      <circle
        key={row.grp}
        className={cls}
        cx={s.x(p.x / YI)}
        cy={s.y(p.y / YI)}
        r={radius(row.share, maxShare, R_MIN, rMax)}
        onClick={() => onPick(row.grp)}
      >
        <title>{`${row.grp}：法人近 5 日 ${yiText(p.x)}，加速 ${yiText(p.y)}/天（${QUADRANT_NAME[quad]}）`}</title>
      </circle>
    )
  }
  const trail = sel ? sel.p.trail.map((t) => ({ ...t, cx: s.x(t.x / YI), cy: s.y(t.y / YI) })) : []
  const selR = sel ? radius(sel.row.share, maxShare, R_MIN, rMax) : 0
  const selX = sel ? s.x(sel.p.x / YI) : 0
  const selY = sel ? s.y(sel.p.y / YI) : 0
  const labelLeft = selX > area.left + area.width / 2 // 泡泡在右半邊：名字寫在左邊
  return (
    <div className="cq-quad-wrap">
      {sel && (
        <div className="cq-quad-head">
          <span>{sel.row.grp}｜法人近 5 日買賣超</span>
          <b className={sel.p.x >= 0 ? 'up' : 'down'}>{yiText(sel.p.x)}</b>
          <i className={`cq-quad-tag ${quadrant(sel.p.x, sel.p.y)}`}>{QUADRANT_NAME[quadrant(sel.p.x, sel.p.y)]}</i>
        </div>
      )}
      <div className="cq-quad" ref={box}>
        {area.width > 0 && area.height > 0 && (
          <svg width={size.w} height={size.h} role="img" aria-label="族群法人象限圖：橫軸法人近 5 日買賣超，縱軸加速">
            {[0, ...xt, ...xt.map((v) => -v)].map((v) => (
              <g key={`x${v}`}>
                <line className={v === 0 ? 'cq-quad-axis' : 'cq-quad-grid'} x1={s.x(v)} x2={s.x(v)} y1={area.top} y2={bottom} />
                <text className="cq-quad-tick" x={s.x(v)} y={bottom + 14} textAnchor="middle">
                  {tickLabel(v)}
                </text>
              </g>
            ))}
            {[0, ...yt, ...yt.map((v) => -v)].map((v) => (
              <g key={`y${v}`}>
                <line className={v === 0 ? 'cq-quad-axis' : 'cq-quad-grid'} x1={area.left} x2={right} y1={s.y(v)} y2={s.y(v)} />
                <text className="cq-quad-tick" x={area.left - 6} y={s.y(v) + 4} textAnchor="end">
                  {tickLabel(v)}
                </text>
              </g>
            ))}
            <text className="cq-quad-title" x={x0} y={area.top - 20} textAnchor="middle">
              加速流入（億/天）↑
            </text>
            <text className="cq-quad-title" x={area.left} y={bottom + 32} textAnchor="start">
              ← 資金流出
            </text>
            <text className="cq-quad-title" x={right} y={bottom + 32} textAnchor="end">
              法人近 5 日買賣超（億）→
            </text>
            <text className="cq-quad-name down" x={area.left + 6} y={area.top + 14}>
              {QUADRANT_NAME.ease}
            </text>
            <text className="cq-quad-name up strong" x={right - 6} y={area.top + 14} textAnchor="end">
              {QUADRANT_NAME.add}
            </text>
            <text className="cq-quad-name down strong" x={area.left + 6} y={bottom - 8}>
              {QUADRANT_NAME.cut}
            </text>
            <text className="cq-quad-name up" x={right - 6} y={bottom - 8} textAnchor="end">
              {QUADRANT_NAME.cool}
            </text>
            {order.map((q) => bubble(q, false))}
            {sel && trail.length > 1 && (
              <g className={`cq-quad-trail ${sel.p.x >= 0 ? 'up' : 'down'}`}>
                <polyline points={trail.map((t) => `${t.cx},${t.cy}`).join(' ')} />
                {trail.slice(0, -1).map((t) => (
                  <circle key={t.d} cx={t.cx} cy={t.cy} r={3} />
                ))}
                <text x={trail[0].cx} y={trail[0].cy + 16} textAnchor="middle">
                  {md(trail[0].d)}
                </text>
              </g>
            )}
            {sel && bubble(sel, true)}
            {sel && (
              <text
                className="cq-quad-label"
                x={labelLeft ? selX - selR - 6 : selX + selR + 6}
                y={selY + 4}
                textAnchor={labelLeft ? 'end' : 'start'}
              >
                {sel.row.grp}
              </text>
            )}
            {/* 0 軸畫在泡泡上面看得到 */}
            <line className="cq-quad-axis" x1={x0} x2={x0} y1={area.top} y2={bottom} pointerEvents="none" />
            <line className="cq-quad-axis" x1={area.left} x2={right} y1={y0} y2={y0} pointerEvents="none" />
          </svg>
        )}
      </div>
    </div>
  )
}
