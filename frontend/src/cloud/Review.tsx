// 名單四面向評分與 AI 首選（T-394／D-196）的畫面：名單列的四根小直條、名單上方的首選卡、個股頁的四張分數卡。
import { Trophy } from 'lucide-react'
import { DIMS, MAX_SCORE, REVIEW_NOTE, scoreText, stanceTone, type Review } from './pickreview'

/** 四根小直條：顏色＝面向、高度＝分數。名單列用，滑過看數字。 */
export function ScoreBars({ r, size = 'sm' }: { r: Review; size?: 'sm' | 'md' }) {
  return (
    <span className={`cq-s4 cq-s4-${size}`} title={`AI 評分：${scoreText(r)}`} aria-label={`AI 評分 ${scoreText(r)}`}>
      {DIMS.map((d) => (
        <i key={d.key} style={{ height: `${(r[d.key] / MAX_SCORE) * 100}%`, background: d.color }} />
      ))}
    </span>
  )
}

/** 名單上方：這條線今天的 AI 首選。點了選那一檔。 */
export function TopPickCard({
  sid,
  name,
  r,
  on,
  onClick,
}: {
  sid: string
  name: string | null
  r: Review
  on: boolean
  onClick: () => void
}) {
  return (
    <button className={`cq-toppick${on ? ' on' : ''}`} onClick={onClick} title={REVIEW_NOTE}>
      <span className="cq-toppick-icon">
        <Trophy size={18} strokeWidth={2.2} aria-hidden="true" />
      </span>
      <span className="cq-toppick-body">
        <span className="cq-toppick-head">
          <b>{r.ai ? 'AI 首選' : '評分最高'}</b>
          <em>{sid}</em>
          <strong>{name ?? ''}</strong>
          <ScoreBars r={r} />
          <small>{r.total} 分</small>
        </span>
        {r.reason && <span className="cq-toppick-reason">{r.reason}</span>}
      </span>
    </button>
  )
}

/** 立場小標：偏多／中性／偏空。 */
export function StanceChip({ s }: { s: string | null }) {
  if (!s) return null
  return <i className={`cq-stance ${stanceTone(s)}`}>{s}</i>
}

/** 個股頁：結論（含立場）＋四張分數卡（分數、5 格量表、一句觀點）。 */
export function ReviewTiles({ r }: { r: Review }) {
  return (
    <div className="cq-rv">
      {r.conclusion && (
        <p className="cq-rv-concl">
          <StanceChip s={r.stance} />
          <span>{r.conclusion}</span>
        </p>
      )}
      <div className="cq-rv-tiles">
        {DIMS.map((d) => (
          <div key={d.key} className="cq-rv-tile" style={{ ['--c' as string]: d.color }}>
            <span className="cq-rv-name">{d.name}</span>
            <b>
              {r[d.key]}
              <small>／{MAX_SCORE}</small>
            </b>
            <span className="cq-rv-meter" aria-hidden="true">
              {Array.from({ length: MAX_SCORE }, (_, i) => (
                <i key={i} className={i < r[d.key] ? 'on' : ''} />
              ))}
            </span>
            {r.views[d.key] && <span className="cq-rv-view">{r.views[d.key]}</span>}
          </div>
        ))}
      </div>
      {r.top && r.reason && (
        <p className="cq-rv-top">
          <Trophy size={15} strokeWidth={2.2} aria-hidden="true" />
          <span>
            {r.ai ? '這條線今天的 AI 首選' : '這條線今天評分最高'}：{r.reason}
          </span>
        </p>
      )}
      <p className="cq-rv-note">{REVIEW_NOTE}</p>
    </div>
  )
}
