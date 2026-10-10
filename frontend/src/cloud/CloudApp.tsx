// 雲端版網站（T-332／D-170）：只有每日推薦、歷史名單、個股 K 線。介面仿富途牛牛：
// 深色底、橘色強調、左側自選股式名單、右側 K 線。漲紅跌綠（台股習慣）。
import { useEffect, useMemo, useState } from 'react'
import './cloud.css'
import { fetchBars, fetchMe, fetchPickDates, fetchPicks, fetchStockPicks, logoutTarget } from './api'
import { LOGIN_URL, LoginRequired, guestCandles, guestPicks } from './auth'
import { KChart, LINE_LEGEND, MA_LEGEND } from './KChart'
import { hits, pct, price, trend, type Bar, type Pick, type PicksDay } from './format'

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
  // 沒登入（或登入過期）：資料 API 被 Access 擋下 → 切成模糊預覽＋登入按鈕；其他錯誤照常顯示
  const onFail = (e: Error) => (e instanceof LoginRequired ? setGuest(true) : setErr(e.message))

  useEffect(() => {
    // 身分只用來顯示頭像；本機開發的後端沒有 /api/me，那時不顯示即可
    fetchMe()
      .then((m) => setEmail(m.email))
      .catch((e: Error) => e instanceof LoginRequired && setGuest(true))
    fetchPickDates().then(setDates).catch(onFail)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (guest) return
    const want = tab === 'today' ? undefined : date
    setErr('')
    fetchPicks(want)
      .then((d) => {
        setDay(d)
        const first = d.lines.find((l) => l.code === line) ?? d.lines[0]
        if (first) {
          setLine(first.code)
          if (!first.picks.some((p) => p.sid === sid)) setSid(first.picks[0]?.sid ?? '')
        }
      })
      .catch(onFail)
    // line/sid 只用來保留使用者目前的選擇，不該觸發重抓
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, date, guest])

  const lineNames = useMemo(
    () => Object.fromEntries((day?.lines ?? []).map((l) => [l.code, l.name])),
    [day],
  )
  const current = day?.lines.find((l) => l.code === line)
  const selected = current?.picks.find((p) => p.sid === sid) ?? null
  const idx = date ? dates.indexOf(date) : -1

  if (guest) return <GuestView />

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
              {day?.date}
              <span className="cq-date-suffix"> 收盤後</span>
            </span>
          )}
        </div>
        <User email={email} />
      </header>

      {err && <div className="cq-err">{err}</div>}

      <main className="cq-body">
        <aside className="cq-list">
          <div className="cq-seg">
            {day?.lines.map((l) => (
              <button
                key={l.code}
                className={l.code === line ? 'on' : ''}
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
          <ul>
            {current?.picks.map((p) => (
              <Row key={p.sid} p={p} on={p.sid === sid} onClick={() => setSid(p.sid)} />
            ))}
          </ul>
          <p className="cq-note">
            方格＝近 6 個交易日有沒有上榜（舊→新，空框＝那天沒打分）；6日＝近 6 日漲幅。模型每日收盤後產生，非投資建議。
          </p>
        </aside>

        <section className="cq-detail">{selected ? <Detail p={selected} lineNames={lineNames} /> : null}</section>
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

function Detail({ p, lineNames }: { p: Pick; lineNames: Record<string, string> }) {
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
  const last = bars.at(-1)
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
        {last && (
          <dl>
            <dt>開</dt>
            <dd>{price(last.open)}</dd>
            <dt>高</dt>
            <dd className="up">{price(last.high)}</dd>
            <dt>低</dt>
            <dd className="down">{price(last.low)}</dd>
            <dt>量（張）</dt>
            <dd>{Math.round(last.volume / 1000).toLocaleString()}</dd>
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

/** 沒登入：版面照常，名單與 K 線換成模糊的假資料，中間一張登入卡片。真資料只從 /api 給已登入的人。 */
function GuestView() {
  const picks = useMemo(() => guestPicks(), [])
  return (
    <div className="cq">
      <header className="cq-top cq-top-guest">
        <div className="cq-brand">
          <img className="cq-logo" src="/icon.svg" alt="" width="26" height="26" />
          twquant
        </div>
        <div className="cq-user">
          <a className="cq-login" href={LOGIN_URL}>
            登入
          </a>
        </div>
      </header>
      <div className="cq-guest">
        <div className="cq-guest-blur" aria-hidden="true">
          <main className="cq-body">
            <aside className="cq-list">
              <div className="cq-seg">
                <button className="on" tabIndex={-1}>
                  大盤精選<em>{picks.length}</em>
                </button>
                <button tabIndex={-1}>
                  潛伏飆股<em>{picks.length}</em>
                </button>
              </div>
              <div className="cq-head">
                <span>代號／名稱／近 6 日上榜</span>
                <span>收盤／6日</span>
                <span>漲跌幅</span>
              </div>
              <ul>
                {picks.map((p, i) => (
                  <Row key={i} p={p} on={i === 0} onClick={() => undefined} />
                ))}
              </ul>
            </aside>
            <section className="cq-detail">
              <GuestChart />
            </section>
          </main>
        </div>
        <div className="cq-gate">
          <div className="cq-gate-card">
            <img src="/icon-192.png" alt="" width="60" height="60" />
            <h2>登入後查看今日名單</h2>
            <p>每個交易日收盤後，AI 模型挑出的大盤精選與潛伏飆股。</p>
            <a className="cq-google" href={LOGIN_URL}>
              <GoogleMark />
              使用 Google 帳號登入
            </a>
          </div>
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
