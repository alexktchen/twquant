// 雲端版網站（T-332／D-170）：只有每日推薦、歷史名單、個股 K 線。介面仿富途牛牛：
// 深色底、橘色強調、左側自選股式名單、右側 K 線。漲紅跌綠（台股習慣）。
import { useEffect, useMemo, useState } from 'react'
import './cloud.css'
import { fetchBars, fetchMe, fetchPickDates, fetchPicks, fetchPublicDates, fetchStockPicks, logoutTarget } from './api'
import { LOGIN_URL, LoginRequired, guestCandles, guestDay } from './auth'
import { KChart, LINE_LEGEND, MA_LEGEND } from './KChart'
import { hits, pct, price, quoteBar, trend, type Bar, type Pick, type PicksDay } from './format'

type Tab = 'today' | 'history'

export default function CloudApp() {
  const [tab, setTab] = useState<Tab>('today')
  const [dates, setDates] = useState<string[]>([])
  const [date, setDate] = useState<string | undefined>(undefined)
  const [day, setDay] = useState<PicksDay | null>(null)
  const [line, setLine] = useState<string>('')
  const [sid, setSid] = useState<string>('')
  const [err, setErr] = useState<string>('')
  const [guest, setGuest] = useState(false)
  const [email, setEmail] = useState<string | null>(null)
  // 沒登入（或登入過期）：資料 API 被 Access 擋下 → 版面照常可操作，只有股票列與 K 線換成模糊的假資料；其他錯誤照常顯示
  const onFail = (e: Error) => {
    if (!(e instanceof LoginRequired)) return setErr(e.message)
    setGuest(true)
    setDay(null) // 登入過期：先前的真名單不留在狀態裡
  }

  useEffect(() => {
    // 身分只用來顯示頭像；本機開發的後端沒有 /api/me，那時不顯示即可
    fetchMe()
      .then((m) => setEmail(m.email))
      .catch((e: Error) => e instanceof LoginRequired && setGuest(true))
    fetchPickDates()
      .then(setDates)
      .catch((e: Error) => {
        if (!(e instanceof LoginRequired)) return setErr(e.message)
        setGuest(true)
        // 日期是公開資訊：沒登入也能切到歷史名單選日期
        fetchPublicDates().then(setDates).catch(onFail)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 沒登入時「今日」用公開日期清單的最新一天；登入時這個值固定 undefined，不會多抓一次
  const guestToday = guest ? dates[0] : undefined

  // 歷史名單：日期清單晚到時（沒登入要多一趟），一到就選最新一天，否則選單會停在空值
  useEffect(() => {
    if (tab === 'history' && !date && dates[0]) setDate(dates[0])
  }, [tab, date, dates])

  useEffect(() => {
    if (guest) return
    // stale：切日期、切分頁或切成沒登入後，晚到的回應一律丟掉
    let stale = false
    const want = tab === 'today' ? undefined : date
    setErr('')
    fetchPicks(want)
      .then((d) => {
        if (stale) return
        setDay(d)
        const first = d.lines.find((l) => l.code === line) ?? d.lines[0]
        if (first) {
          setLine(first.code)
          if (!first.picks.some((p) => p.sid === sid)) setSid(first.picks[0]?.sid ?? '')
        }
      })
      .catch((e: Error) => {
        if (!stale) onFail(e)
      })
    return () => {
      stale = true
    }
    // line/sid 只用來保留使用者目前的選擇，不該觸發重抓
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, date, guest])

  // 沒登入時畫面只讀假資料：就算有真名單寫進 day，也不會出現在沒登入的畫面
  const guestView = useMemo(
    () => (guest ? guestDay((tab === 'today' ? undefined : date) ?? guestToday ?? '') : null),
    [guest, tab, date, guestToday],
  )
  const view = guest ? guestView : day

  const lineNames = useMemo(
    () => Object.fromEntries((view?.lines ?? []).map((l) => [l.code, l.name])),
    [view],
  )
  const current = view?.lines.find((l) => l.code === line) ?? view?.lines[0]
  const selected = current?.picks.find((p) => p.sid === sid) ?? null
  const idx = date ? dates.indexOf(date) : -1

  return (
    <div className="cq">
      <header className="cq-top">
        <div className="cq-brand">
          <img className="cq-logo" src="/icon.svg" alt="" width="26" height="26" />
          twquant
        </div>
        <nav className="cq-tabs">
          <button className={tab === 'today' ? 'on' : ''} onClick={() => setTab('today')}>
            今日推薦
          </button>
          <button
            className={tab === 'history' ? 'on' : ''}
            onClick={() => {
              setTab('history')
              if (!date && dates[0]) setDate(dates[0])
            }}
          >
            歷史名單
          </button>
        </nav>
        <div className="cq-date">
          {tab === 'history' ? (
            <>
              <button disabled={idx < 0 || idx >= dates.length - 1} onClick={() => setDate(dates[idx + 1])}>
                ‹
              </button>
              <select value={date ?? ''} onChange={(e) => setDate(e.target.value)}>
                {dates.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              <button disabled={idx <= 0} onClick={() => setDate(dates[idx - 1])}>
                ›
              </button>
            </>
          ) : (
            <span>
              {view?.date}
              <span className="cq-date-suffix"> 收盤後</span>
            </span>
          )}
        </div>
        {guest ? (
          <div className="cq-user">
            <a className="cq-login" href={LOGIN_URL}>
              登入
            </a>
          </div>
        ) : (
          <User email={email} />
        )}
      </header>

      {err && <div className="cq-err">{err}</div>}

      <main className="cq-body">
        <aside className={guest ? 'cq-list cq-list-guest' : 'cq-list'}>
          <div className="cq-seg">
            {view?.lines.map((l) => (
              <button
                key={l.code}
                className={l.code === current?.code ? 'on' : ''}
                onClick={() => {
                  setLine(l.code)
                  setSid(l.picks[0]?.sid ?? '')
                }}
              >
                {l.name}
                <em>{l.picks.length}</em>
              </button>
            ))}
          </div>
          <div className="cq-head">
            <span>代號／名稱／近 6 日上榜</span>
            <span>收盤／6日</span>
            <span>漲跌幅</span>
          </div>
          <div className="cq-rows">
            <ul className={guest ? 'cq-locked' : undefined} aria-hidden={guest || undefined}>
              {current?.picks.map((p) => (
                <Row key={p.sid} p={p} on={p.sid === sid} onClick={() => setSid(p.sid)} />
              ))}
            </ul>
            {guest && <Gate />}
          </div>
          <p className="cq-note">
            方格＝近 6 個交易日有沒有上榜（舊→新，空框＝那天沒打分）；6日＝近 6 日漲幅。模型每日收盤後產生，非投資建議。
          </p>
        </aside>

        <section className="cq-detail">
          {guest ? (
            <div className="cq-locked cq-guest-detail" aria-hidden="true">
              <GuestChart />
            </div>
          ) : selected ? (
            <Detail p={selected} asOf={view?.date} lineNames={lineNames} />
          ) : null}
        </section>
      </main>
    </div>
  )
}

/** 登入身分與登出（Cloudflare Access）。沒開 Access 時整塊不顯示。 */
function User({ email }: { email: string | null }) {
  if (!email) return null
  return (
    <div className="cq-user" title={email}>
      <span className="cq-avatar">{email.slice(0, 1).toUpperCase()}</span>
      <span className="cq-email">{email}</span>
      <a
        href="/cdn-cgi/access/logout"
        onClick={(e) => {
          e.preventDefault()
          logoutTarget().then((to) => window.location.assign(to))
        }}
      >
        登出
      </a>
    </div>
  )
}

function Row({ p, on, onClick }: { p: Pick; on: boolean; onClick: () => void }) {
  const t = trend(p.chg)
  const h = hits(p.recent ?? [])
  return (
    <li className={on ? 'on' : ''} onClick={onClick}>
      <div className="cq-name">
        <b>
          <em>{p.sid}</em>
          {p.name ?? ''}
        </b>
        <small>
          <span className="cq-dots" title={`近 6 日上榜 ${h.on}/${h.of}`}>
            {(p.recent ?? []).map((x, i) => (
              <i key={i} className={x === null ? 'na' : x ? 'in' : ''} />
            ))}
          </span>
          <span className="cq-hits">
            {h.on}/{h.of}
          </span>
          {p.new && <i className="cq-tag new">新上榜</i>}
          {p.streak > (p.recent?.length ?? 0) && <i className="cq-tag">連{p.streak}天</i>}
          {p.score !== null && <i className="cq-tag prob">{Math.round(p.score * 100)}%</i>}
        </small>
      </div>
      <span className="cq-pxcol">
        <span className={`cq-px ${t}`}>{price(p.close)}</span>
        <small className={trend(p.chg6)}>{pct(p.chg6, 1)}</small>
      </span>
      <span className={`cq-chg ${t}`}>{pct(p.chg)}</span>
    </li>
  )
}

function Detail({ p, asOf, lineNames }: { p: Pick; asOf: string | undefined; lineNames: Record<string, string> }) {
  const [bars, setBars] = useState<Bar[]>([])
  const [days, setDays] = useState<Record<string, string[]>>({})
  const [err, setErr] = useState('')
  useEffect(() => {
    setErr('')
    Promise.all([fetchBars(p.sid), fetchStockPicks(p.sid)])
      .then(([b, d]) => {
        setBars(b)
        setDays(d)
      })
      .catch((e: Error) => setErr(e.message))
  }, [p.sid])
  const t = trend(p.chg)
  // 報價列：名單日期那根（歷史名單選 9/24 就顯示 9/24，不是最新一天）；長按看別天在 K 線上的浮框
  const q = quoteBar(bars, asOf, null)
  return (
    <>
      <div className="cq-quote">
        <div>
          <h1>
            <small>{p.sid}</small> {p.name ?? ''}
          </h1>
          <div className={`cq-big ${t}`}>
            {price(p.close)} <span>{pct(p.chg)}</span>
          </div>
        </div>
        {q && (
          <dl className="cq-ohlc">
            <dt>開</dt>
            <dd>{price(q.bar.open)}</dd>
            <dt>高</dt>
            <dd className="up">{price(q.bar.high)}</dd>
            <dt>低</dt>
            <dd className="down">{price(q.bar.low)}</dd>
            <dt>收</dt>
            <dd className={trend(q.chg)}>{price(q.bar.close)}</dd>
            <dt>量</dt>
            <dd>{Math.round(q.bar.volume / 1000).toLocaleString()} 張</dd>
            <dt>漲跌</dt>
            <dd className={trend(q.chg)}>{pct(q.chg)}</dd>
            <dd className="cq-ohlc-date">{q.bar.date}</dd>
          </dl>
        )}
      </div>
      <div className="cq-legend">
        {MA_LEGEND.map((m) => (
          <span key={m.n} style={{ color: m.color }}>
            MA{m.n}
          </span>
        ))}
        <span className="cq-legend-note">
          推薦日
          {Object.entries(lineNames).map(([code, name]) => (
            <b key={code} style={{ color: LINE_LEGEND[code] ?? '#FF7A00' }}>
              ▼{name}
            </b>
          ))}
        </span>
      </div>
      {err ? <div className="cq-err">{err}</div> : <KChart bars={bars} pickDays={days} lineNames={lineNames} />}
    </>
  )
}

/** 沒登入：蓋在模糊名單上的登入卡片。股票列與 K 線是假資料，真資料只從 /api 給已登入的人。 */
function Gate() {
  return (
    <div className="cq-gate">
      <div className="cq-gate-card">
        <img src="/icon-192.png" alt="" width="52" height="52" />
        <h2>登入後查看名單</h2>
        <p>每個交易日收盤後，AI 模型挑出的大盤精選與潛伏飆股。</p>
        <a className="cq-google" href={LOGIN_URL}>
          登入／註冊
        </a>
        <div className="cq-gate-ways">
          <GoogleMark />
          Google 帳號
          <span>·</span>
          <MailMark />
          Email 驗證碼
        </div>
      </div>
    </div>
  )
}

/** 模糊背景裡的假 K 線。 */
function GuestChart() {
  const bars = useMemo(() => guestCandles(), [])
  const hi = Math.max(...bars.map((b) => b.h))
  const lo = Math.min(...bars.map((b) => b.l))
  const W = 600
  const H = 300
  const step = W / bars.length
  const y = (v: number) => ((hi - v) / (hi - lo)) * (H - 20) + 10
  return (
    <svg className="cq-guest-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      {bars.map((b, i) => {
        const x = i * step + step / 2
        const color = b.c >= b.o ? '#F6465D' : '#0ECB81'
        return (
          <g key={i} stroke={color} fill={color}>
            <line x1={x} x2={x} y1={y(b.h)} y2={y(b.l)} strokeWidth={1.5} />
            <rect x={x - step * 0.3} width={step * 0.6} y={y(Math.max(b.o, b.c))} height={Math.max(2, Math.abs(y(b.o) - y(b.c)))} />
          </g>
        )
      })}
    </svg>
  )
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.6 13.2l7.8 6.1C12.3 13.6 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.5 5.8c4.4-4 6.8-10 6.8-17.2z" />
      <path fill="#FBBC05" d="M10.4 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C1 16.5 0 20.1 0 24s1 7.5 2.6 10.8l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.8 2.3-8.4 2.3-6.3 0-11.7-4.1-13.6-9.8l-7.8 6.1C6.6 42.6 14.6 48 24 48z" />
    </svg>
  )
}

function MailMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </svg>
  )
}
