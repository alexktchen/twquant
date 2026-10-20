// 核可頁（T-403／SPEC-017 §6、D-200 §三）：AI 依公開報導草擬的關係線（國外公司→台灣供應商、主題→族群與個股、同業），
// 擁有者核可後圖上變實線、開始算數；退回的不再出現。只有擁有者看得到（新聞服務比對祕密名單，網站只顯示 can_review 的結果）。
// 一組一張卡（例：輝達 → 台灣供應商 8 條），每條「退回／核可」，多條的組另有「全部」。按了就送，送成功才從清單拿掉。
import { ArrowRight, Check, X } from 'lucide-react'
import { useState } from 'react'
import { postNewsReview } from './api'
import { REVIEW_TYPE_LABEL, dropDecided, groupDecisions, reviewLeft, type ReviewDecision, type ReviewGroup } from './news'
import { classOf } from './news-graph'

export function NewsReview({ groups, onChange, onClose }: { groups: ReviewGroup[]; onChange: (g: ReviewGroup[]) => void; onClose: () => void }) {
  /** 正在送哪一批（組代號或單條代號）；送的時候其他按鈕先停 */
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [done, setDone] = useState({ approved: 0, rejected: 0 })

  const send = (id: string, ds: ReviewDecision[]) => {
    if (busy) return
    setBusy(id)
    setErr('')
    postNewsReview(ds)
      .then((r) => {
        setDone((d) => ({ approved: d.approved + r.approved, rejected: d.rejected + r.rejected }))
        onChange(dropDecided(groups, ds))
      })
      .catch((e: Error) => setErr(`沒送成功：${e.message}`))
      .finally(() => setBusy(null))
  }

  const left = reviewLeft(groups)
  return (
    <section className="cq-rv">
      <div className="cq-rv-head">
        <h2>待核可 {left} 條</h2>
        <button className="cq-rv-back" onClick={onClose}>
          回到新聞
        </button>
      </div>
      <p className="cq-rv-note">AI 依公開報導草擬的關係線，圖上是虛線。核可後變實線、開始算數；退回的不再出現。</p>
      {(done.approved > 0 || done.rejected > 0) && (
        <p className="cq-rv-done">
          這次已核可 {done.approved} 條、退回 {done.rejected} 條
        </p>
      )}
      {err && <div className="cq-err">{err}</div>}
      {left === 0 && <p className="cq-war-mute">沒有待核可的線了。</p>}
      {groups.map((g) => {
        const gid = `${g.type}|${g.src}`
        return (
          <div key={gid} className="cq-rv-card">
            <div className="cq-rv-title">
              <span className={`cq-news-ent ${g.src_type ? classOf(g.src_type) : ''}`}>{g.src_label}</span>
              <ArrowRight size={15} aria-hidden="true" />
              <span>{REVIEW_TYPE_LABEL[g.type]}</span>
              <small>{g.items.length} 條</small>
            </div>
            <ul>
              {g.items.map((i) => {
                const iid = `${gid}|${i.dst}`
                const one = (decision: ReviewDecision['decision']) => send(iid, [{ src: g.src, dst: i.dst, type: g.type, decision }])
                return (
                  <li key={i.dst}>
                    <span className="cq-rv-dst">
                      <b>
                        {i.dst_type === 'tw' && i.dst_sub && <em>{i.dst_sub}</em>}
                        {i.dst_label}
                      </b>
                      {i.label && <small>{i.label}</small>}
                    </span>
                    <button className="cq-rv-no" onClick={() => one('rejected')} disabled={busy !== null} aria-label={`退回 ${g.src_label} 到 ${i.dst_label}`}>
                      <X size={15} aria-hidden="true" />
                      退回
                    </button>
                    <button className="cq-rv-ok" onClick={() => one('approved')} disabled={busy !== null} aria-label={`核可 ${g.src_label} 到 ${i.dst_label}`}>
                      <Check size={15} aria-hidden="true" />
                      {busy === iid ? '送出中' : '核可'}
                    </button>
                  </li>
                )
              })}
            </ul>
            {g.items.length > 1 && (
              <div className="cq-rv-all">
                <button className="cq-rv-no" onClick={() => send(gid, groupDecisions(g, 'rejected'))} disabled={busy !== null}>
                  全部退回
                </button>
                <button className="cq-rv-ok" onClick={() => send(gid, groupDecisions(g, 'approved'))} disabled={busy !== null}>
                  {busy === gid ? '送出中' : `這組 ${g.items.length} 條全部核可`}
                </button>
              </div>
            )}
          </div>
        )
      })}
    </section>
  )
}
