// 雲端版網站（T-332／D-170）：只有每日推薦、歷史名單、個股 K 線。介面仿富途牛牛：
// 深色底、橘色強調、左側自選股式名單、右側 K 線。漲紅跌綠（台股習慣）。
import { useEffect, useMemo, useState } from 'react'
import './cloud.css'
import { fetchBars, fetchIdentity, fetchPickDates, fetchPicks, fetchStockPicks, logoutTarget } from './api'
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

  useEffect(() => {
    fetchPickDates().then(setDates).catch((e: Error) => setErr(e.message))
  }, [])

  useEffect(() => {
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
      .catch((e: Error) => setErr(e.message))
    // line/sid 只用來保留使用者目前的選擇，不該觸發重抓
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, date])

  const lineNames = useMemo(
    () => Object.fromEntries((day?.lines ?? []).map((l) => [l.code, l.name])),
    [day],
  )
  const current = day?.lines.find((l) => l.code === line)
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
              {day?.date}
              <span className="cq-date-suffix"> 收盤後</span>
            </span>
          )}
        </div>
        <User />
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
function User() {
  const [email, setEmail] = useState<string | null>(null)
  useEffect(() => {
    fetchIdentity().then((id) => setEmail(id?.email ?? null))
  }, [])
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
