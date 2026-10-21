// 「LINE 群組」分頁（T-423／D-204）：發文帳號狀態、勾要發的群組、最近發出去的圖卡。
// 只有能用 line 功能的角色看得到（分頁按鈕由 /api/access 決定，資料端點自己再擋一次）。
import { useEffect, useRef, useState } from 'react'
import { Images, Send, UserRound, Users } from 'lucide-react'
import './line-group.css'
import { fetchLineStatus, postLineCard, setLineGroup, startLineLogin, syncLineGroups } from './api'
import { Section } from './Section'
import {
  KIND_LABEL,
  LOGIN_POLL_MS,
  POST_WAIT_MS,
  GROUP_FIND_MIN,
  SYNC_WAIT_MS,
  cardUrl,
  findGroups,
  groupsMeta,
  latestPostId,
  latestSync,
  loginActive,
  loginStep,
  shortTime,
  statusView,
  taipeiToday,
  type LineLogin,
  type LineStatus,
} from './linegroup'

export function LineGroupPage({ refresh }: { refresh: number }) {
  const [data, setData] = useState<LineStatus | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [loginErr, setLoginErr] = useState('')
  const [tick, setTick] = useState(0)
  // 等群組同步：按下去時的「上次同步時間」（清單的同步時間變了就是同步好了）；null＝沒在等
  const [syncing, setSyncing] = useState<string | null>(null)
  const [syncNote, setSyncNote] = useState('')
  const [find, setFind] = useState('')
  // 手動發送：選哪一種、哪一天；posting＝按下去時最新一則貼文的 id（有更新的貼文送完了就是好了），null＝沒在等
  const [postKind, setPostKind] = useState('picks')
  const [postDate, setPostDate] = useState(() => taipeiToday())
  const [posting, setPosting] = useState<number | null>(null)
  const [postNote, setPostNote] = useState('')

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
  }, [refresh, tick])

  const lastSync = latestSync(data?.groups ?? [])
  useEffect(() => {
    if (syncing !== null && lastSync !== syncing) {
      setSyncing(null)
      setSyncNote(`已更新（${shortTime(lastSync)}）`)
    }
  }, [lastSync, syncing])
  useEffect(() => {
    if (syncing === null) return
    const t = window.setTimeout(() => {
      setSyncing(null)
      setSyncNote('等了 3 分鐘還沒更新：到 GitHub Actions 看 line-login 的執行紀錄')
    }, SYNC_WAIT_MS)
    return () => window.clearTimeout(t)
  }, [syncing])
  // 登入成功後工作流程會接著同步群組：跟著等，清單出來就顯示
  const loggingIn = loginActive(data?.login ?? null)
  const wasLoggingIn = useRef(false)
  useEffect(() => {
    if (wasLoggingIn.current && !loggingIn && data?.login?.status === 'done') setSyncing(lastSync)
    wasLoggingIn.current = loggingIn
  }, [loggingIn, data?.login?.status, lastSync])

  const sync = () => {
    setSyncNote('')
    setBusy('sync')
    syncLineGroups()
      .then(() => {
        setSyncing(lastSync)
        setSyncNote('已送出，約 1 分鐘後更新…')
      })
      .catch((e: Error) => setSyncNote(e.message))
      .finally(() => setBusy(null))
  }

  const lastPost = latestPostId(data?.posts ?? [])
  useEffect(() => {
    if (posting === null || !data) return
    const fresh = data.posts.filter((p) => p.id > posting)
    if (fresh.length === 0 || fresh.some((p) => p.status === 'queued')) return
    setPosting(null)
    setPostNote(`發好了：${fresh.map((p) => `${p.group_name ?? '群組'} ${statusView(p).text}`).join('、')}`)
  }, [data, posting])
  useEffect(() => {
    if (posting === null) return
    const t = window.setTimeout(() => {
      setPosting(null)
      setPostNote('等了 5 分鐘沒看到新的貼文：那天可能還沒有內容、或這一種那天已經發過（到 GitHub Actions 看 line-login 的紀錄）')
    }, POST_WAIT_MS)
    return () => window.clearTimeout(t)
  }, [posting])

  const send = () => {
    setPostNote('')
    setBusy('post')
    postLineCard(postKind, postDate)
      .then(() => {
        setPosting(lastPost)
        setPostNote('已送出，約 1～2 分鐘後出現在下面…')
      })
      .catch((e: Error) => setPostNote(e.message))
      .finally(() => setBusy(null))
  }
  const enabledCount = (data?.groups ?? []).filter((g) => g.enabled && g.present).length

  // 登入、同步、發送進行中：每幾秒重抓一次，QR、PIN、群組清單、新貼文一出來就顯示
  const polling = loggingIn || syncing !== null || posting !== null
  useEffect(() => {
    if (!polling) return
    const id = window.setInterval(() => setTick((n) => n + 1), LOGIN_POLL_MS)
    return () => window.clearInterval(id)
  }, [polling])

  const login = () => {
    setLoginErr('')
    setBusy('login')
    startLineLogin()
      .then((l) => setData((d) => (d ? { ...d, login: l } : d)))
      .catch((e: Error) => setLoginErr(e.message))
      .finally(() => setBusy(null))
  }

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
                <p className="cq-lg-hint">還沒登入。按下面的「登入」，再用備用 LINE 帳號的手機掃 QR 碼（不要用本人帳號）。</p>
              )}
              <LoginPanel login={data.login} />
              {loginErr && <p className="cq-lg-bad">{loginErr}</p>}
              <button className="cq-lg-btn" onClick={login} disabled={busy !== null || loggingIn}>
                {loggingIn ? '登入中…' : a ? '重新登入' : '登入'}
              </button>
            </Section>
            <Section icon={Users} title="要發的群組" meta={groupsMeta(data.groups)}>
              {data.groups.length === 0 ? (
                <p className="cq-lg-hint">把發文帳號拉進群組，再按「同步群組」就會列在這裡（每一班排程也會自動同步）；新群組預設不發。</p>
              ) : (
                <>
                  {data.groups.length > GROUP_FIND_MIN && (
                    <input
                      className="cq-lg-find"
                      type="search"
                      placeholder="找群組（勾了的一直留著）"
                      value={find}
                      onChange={(e) => setFind(e.target.value)}
                    />
                  )}
                  <ul className="cq-lg-groups">
                    {findGroups(data.groups, find).map((g) => (
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
                </>
              )}
              {syncNote && <p className="cq-lg-hint">{syncNote}</p>}
              {a && (
                <button className="cq-lg-btn" onClick={sync} disabled={busy !== null || syncing !== null}>
                  {syncing !== null ? '同步中…' : '同步群組'}
                </button>
              )}
            </Section>
          </>
        )}
      </aside>
      <section className="cq-detail">
        {data?.account && (
          <Section icon={Send} title="手動發送" meta="同一天同一種只發一次">
            <div className="cq-lg-post">
              <select value={postKind} onChange={(e) => setPostKind(e.target.value)} disabled={posting !== null}>
                {Object.entries(KIND_LABEL).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
              <input type="date" value={postDate} onChange={(e) => setPostDate(e.target.value)} disabled={posting !== null} />
              <button className="cq-lg-btn" onClick={send} disabled={busy !== null || posting !== null || enabledCount === 0 || !postDate}>
                {posting !== null ? '發送中…' : `發到 ${enabledCount} 個群組`}
              </button>
            </div>
            {enabledCount === 0 && <p className="cq-lg-hint">先在左邊勾要發的群組。</p>}
            {postNote && <p className="cq-lg-hint">{postNote}</p>}
          </Section>
        )}
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

/** 從網站登入的進度：等開機 → QR 碼 → PIN → 完成／失敗。 */
function LoginPanel({ login }: { login: LineLogin | null }) {
  if (!login || (login.status === 'done' && !login.fresh)) return null
  const step = loginStep(login)
  return (
    <div className={`cq-lg-login ${login.status}`}>
      <p>{step}</p>
      {login.status === 'scan' && login.qr_png && (
        <>
          <img className="cq-lg-qr" src={`data:image/png;base64,${login.qr_png}`} alt="LINE 登入 QR 碼" />
          {login.qr_url && (
            <a href={login.qr_url} target="_blank" rel="noreferrer">
              網頁開在備用帳號的手機上？直接點這裡
            </a>
          )}
        </>
      )}
      {login.status === 'pin' && login.pin && <b className="cq-lg-pin">{login.pin}</b>}
      {login.run_url && loginActive(login) && (
        <a className="cq-lg-run" href={login.run_url} target="_blank" rel="noreferrer">
          看 GitHub Actions 執行紀錄
        </a>
      )}
    </div>
  )
}
