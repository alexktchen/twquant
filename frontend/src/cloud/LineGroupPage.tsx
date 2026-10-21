// 「LINE 群組」分頁（T-423／D-204）：發文帳號狀態、勾要發的群組、最近發出去的圖卡。
// 只有能用 line 功能的角色看得到（分頁按鈕由 /api/access 決定，資料端點自己再擋一次）。
import { useEffect, useState } from 'react'
import { Images, UserRound, Users } from 'lucide-react'
import './line-group.css'
import { fetchLineStatus, setLineGroup } from './api'
import { Section } from './Section'
import { KIND_LABEL, cardUrl, groupsMeta, shortTime, statusView, type LineStatus } from './linegroup'

export function LineGroupPage({ refresh }: { refresh: number }) {
  const [data, setData] = useState<LineStatus | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    let stale = false
    fetchLineStatus()
      .then((d) => {
        if (stale) return
        setData(d)
        setErr('')
      })
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [refresh])

  const toggle = (homeId: string, enabled: boolean) => {
    setBusy(homeId)
    setLineGroup(homeId, enabled)
      .then((row) =>
        setData((d) => (d ? { ...d, groups: d.groups.map((g) => (g.home_id === row.home_id ? row : g)) } : d)),
      )
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(null))
  }

  const a = data?.account ?? null
  return (
    <main className="cq-body cq-lg">
      <aside className="cq-list cq-lg-side">
        {err && <p className="cq-empty">{err}</p>}
        {!err && !data && <p className="cq-empty">讀取中…</p>}
        {data && (
          <>
            <Section icon={UserRound} title="發文帳號" meta={a ? `憑證更新 ${shortTime(a.updated_at)}` : undefined}>
              {a ? (
                <div className="cq-lg-acct">
                  <b>{a.display_name ?? '（沒有名字）'}</b>
                  {a.paused_until && <p className="cq-lg-warn">LINE 要求暫緩，停到 {shortTime(a.paused_until)}</p>}
                  {a.last_error && <p className="cq-lg-bad">上次失敗：{a.last_error}</p>}
                </div>
              ) : (
                <p className="cq-lg-hint">
                  還沒登入。用備用 LINE 帳號在本機跑 <code>twquant line-group login</code> 掃 QR（步驟見 line-poster/README.md）。
                </p>
              )}
            </Section>
            <Section icon={Users} title="要發的群組" meta={groupsMeta(data.groups)}>
              {data.groups.length === 0 ? (
                <p className="cq-lg-hint">
                  把發文帳號拉進群組，下一班排程（或本機 <code>twquant line-group groups</code>）就會列在這裡；新群組預設不發。
                </p>
              ) : (
                <ul className="cq-lg-groups">
                  {data.groups.map((g) => (
                    <li key={g.home_id} className={g.present ? '' : 'gone'}>
                      <label>
                        <input
                          type="checkbox"
                          checked={g.enabled}
                          disabled={busy !== null || !g.present}
                          onChange={(e) => toggle(g.home_id, e.target.checked)}
                        />
                        <span>{g.name}</span>
                      </label>
                      {!g.present && <em>帳號已不在群組</em>}
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </>
        )}
      </aside>
      <section className="cq-detail">
        {data && (
          <Section icon={Images} title="最近發的圖卡" meta={data.posts.length ? `${data.posts.length} 則` : undefined}>
            {data.posts.length === 0 ? (
              <p className="cq-lg-hint">還沒有發過。勾好群組後，下一次名單、晨報、盤後快訊寫好就會發。</p>
            ) : (
              <ul className="cq-lg-posts">
                {data.posts.map((p) => {
                  const s = statusView(p)
                  return (
                    <li key={p.id}>
                      <a href={cardUrl(p.card_token)} target="_blank" rel="noreferrer">
                        <img src={cardUrl(p.card_token)} alt={p.alt_text} loading="lazy" />
                      </a>
                      <div className="cq-lg-meta">
                        <b>{KIND_LABEL[p.kind] ?? p.kind}</b>
                        <span>{p.group_name ?? p.home_id.slice(0, 8)}</span>
                        <span className={`cq-lg-st ${s.tone}`}>{s.text}</span>
                        <span>{shortTime(p.sent_at ?? p.enqueued_at)}</span>
                      </div>
                      {p.last_error && <p className="cq-lg-err">{p.last_error}</p>}
                    </li>
                  )
                })}
              </ul>
            )}
          </Section>
        )}
      </section>
    </main>
  )
}
