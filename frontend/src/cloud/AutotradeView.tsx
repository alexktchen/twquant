// 程式交易（autotrade A-005）：獨立交易系統單向寫出來的狀態。這一頁只能看——沒有任何會動到交易程式的按鈕。
import { Activity, Bell, ReceiptText } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchAutotrade } from './api'
import {
  EVENT_LABEL,
  EVENT_SERIOUS,
  MODE_NAME,
  SCHEMA,
  SOURCE_NAME,
  STRATEGY_LABEL,
  assumptionText,
  clock,
  cumPath,
  cumulative,
  dayStats,
  eventText,
  lamp,
  lossRoom,
  positionText,
  refetchDue,
  type AutoDayRow,
  type AutoEnvelope,
  type AutoMode,
  type AutoStatus,
} from './autotrade'
import { PRODUCT_NAME, shouldPoll, tone, yuan } from './futures'
import { Kpi, Section } from './Section'
import './autotrade.css'

const POLL_MS = 15_000
const MODES: AutoMode[] = ['sim', 'live']
const EMPTY: Record<AutoMode, string> = {
  sim: '還沒有模擬紀錄：交易程式跑完一天、把狀態寫進來之後就會出現。',
  live: '還沒有真單紀錄：真單還沒上線。',
}
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const price = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 1 })
/** 期交所代號 → 平常的叫法（TMF＝微台）；不認得就照原樣。 */
const symbolName = (s: string) => (PRODUCT_NAME as Record<string, string | undefined>)[s] ?? s

/** 累計線的畫布（SVG 自己的座標；實際大小由 CSS 決定）。 */
const CHART_W = 300
const CHART_H = 100

function Days({ days, mode }: { days: AutoDayRow[]; mode: AutoMode }) {
  const st = dayStats(days)
  if (st.n === 0 || st.best === null || st.worst === null) return null
  const cum = cumulative(days)
  const path = cumPath(cum, CHART_W, CHART_H)
  return (
    <Section icon={Activity} title="每天的成績" meta={`${MODE_NAME[mode]}・${st.n} 天`}>
      <div className="cq-tiles cq-at-tiles cq-at-tiles-2">
        <Kpi label="累計損益" value={yuan(st.total)} tone={tone(st.total)} sub={`${cum[0].day} 起・${st.n} 天`} />
        <Kpi label="賺賠天數" value={`${st.wins} 賺 ${st.losses} 賠`} sub={`單日最好 ${yuan(st.best)}・最差 ${yuan(st.worst)}`} />
      </div>
      {path && (
        <>
          <svg
            className={`cq-at-cum ${tone(st.total)}`}
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={`累計損益 ${yuan(st.total)} 元`}
          >
            <line className="zero" x1={0} x2={CHART_W} y1={path.zeroY} y2={path.zeroY} />
            <polyline points={path.line} />
          </svg>
          <p className="cq-at-range">
            <span>{cum[0].day}</span>
            <span>累計損益（元）・虛線是 0</span>
            <span>{cum[cum.length - 1].day}</span>
          </p>
        </>
      )}
    </Section>
  )
}

function Status({ s, now }: { s: AutoStatus; now: Date }) {
  const l = lamp(s, now)
  const room = lossRoom(s.day_pnl_twd, s.limits.daily_loss_limit_twd)
  return (
    <>
      <div className={`cq-at-banner cq-at-mode-${s.mode}`}>
        <b className="cq-at-chip">{MODE_NAME[s.mode]}</b>
        <b className="cq-at-day">{s.day}</b>
        <span className="cq-cat cq-cat-mute">{symbolName(s.symbol)}</span>
        <span className="cq-cat cq-cat-mute">{STRATEGY_LABEL[s.strategy] ?? s.strategy}</span>
        <span className="cq-cat cq-cat-mute">{SOURCE_NAME[s.source]}</span>
      </div>
      <div className={`cq-at-lamp ${l.tone}`} role="status">
        <i aria-hidden="true" />
        <b>{l.label}</b>
        {l.detail && <span>{l.detail}</span>}
        <time>更新 {clock(s.updated_at)}</time>
      </div>
      <div className="cq-tiles cq-at-tiles">
        <Kpi
          label="部位"
          value={positionText(s.position)}
          tone={s.position === 0 ? 'plain' : 'hot'}
          sub={s.position === 0 ? `最新 ${price(s.last_price)}` : `均價 ${price(s.avg_price)}・最新 ${price(s.last_price)}`}
        />
        <Kpi
          label="當日損益"
          value={yuan(s.day_pnl_twd)}
          tone={tone(s.day_pnl_twd)}
          sub={`已實現 ${yuan(s.realized_twd)}・未實現 ${yuan(s.open_pnl_twd)}`}
        />
        <Kpi label="進場" value={`${s.entries} 次`} sub={`上限 ${s.limits.max_entries_per_day} 次・成本 ${Math.round(s.costs_twd).toLocaleString('en-US')} 元`} />
        <Kpi
          label="還能賠"
          value={room ? `${Math.round(room.left).toLocaleString('en-US')} 元` : '沒設上限'}
          tone={room === null ? 'na' : room.used >= 1 ? 'down' : room.used >= 0.5 ? 'warn' : 'plain'}
          sub={
            room ? (
              <span className="cq-at-bar" title={`每日虧損上限 ${s.limits.daily_loss_limit_twd} 元`}>
                <i style={{ width: `${Math.round(room.used * 100)}%` }} />
              </span>
            ) : (
              '每日虧損上限還沒設定'
            )
          }
        />
      </div>
      <Section icon={ReceiptText} title="今日成交" meta={`${s.fills.length} 筆`}>
        {s.fills.length === 0 ? (
          <p className="cq-empty">這天沒有成交。</p>
        ) : (
          <table className="cq-at-fills">
            <thead>
              <tr>
                <th>時間</th>
                <th>買賣</th>
                <th>價格</th>
                <th>損益</th>
              </tr>
            </thead>
            <tbody>
              {s.fills.map((f, i) => (
                <tr key={i}>
                  <td>{clock(f.ts)}</td>
                  <td className={f.side === 'BUY' ? 'up' : 'down'}>
                    {f.side === 'BUY' ? '買' : '賣'} {f.qty} 口
                  </td>
                  <td>{price(f.price)}</td>
                  <td className={tone(f.pnl_twd)}>{yuan(f.pnl_twd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {s.corrected && <p className="cq-at-note">這天的部位被修正過，逐筆損益只是估算，合計以上面的當日損益為準。</p>}
      </Section>
      <Section icon={Bell} title="今日大事" meta={s.events.length ? `${s.events.length} 則` : '沒有異常'}>
        {s.events.length === 0 ? (
          <p className="cq-empty">沒有停手、斷線、被拒絕這類事件。</p>
        ) : (
          <ul className="cq-at-events">
            {s.events.map((e, i) => (
              <li key={i} className={EVENT_SERIOUS.has(e.kind) ? 'bad' : ''}>
                <time>{clock(e.ts)}</time>
                <b>{EVENT_LABEL[e.kind] ?? e.kind}</b>
                <span>{eventText(e.text)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  )
}

export function AutotradeView({ refresh }: { refresh: number }) {
  const [mode, setMode] = useState<AutoMode>('sim')
  const [day, setDay] = useState<string | null>(null) // null＝最新一天
  const [data, setData] = useState<AutoEnvelope | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [now, setNow] = useState(() => new Date())
  const current = data?.current ?? null
  const live = useRef(false)
  live.current = current?.status === 'live'

  useEffect(() => {
    let stop = false
    let tick = 0
    const run = () =>
      fetchAutotrade(mode, day ?? undefined)
        .then((d) => {
          if (stop) return
          setData(d)
          setErr(null)
        })
        .catch((e) => !stop && setErr(errText(e)))
    run()
    const id = setInterval(() => {
      const t = new Date()
      setNow(t)
      tick += 1
      // 只有看最新一天、在交易時段、分頁在前景時才重抓；畫面上不是盤中狀態就抓得比較慢
      if (day === null && document.visibilityState === 'visible' && shouldPoll(t, null) && refetchDue(tick, live.current)) run()
    }, POLL_MS)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [mode, day, refresh])

  const pick = (m: AutoMode) => {
    if (m === mode) return
    setData(null)
    setDay(null)
    setMode(m)
  }
  return (
    <>
      <div className="cq-seg cq-at-modes">
        {MODES.map((m) => (
          <button key={m} className={`cq-at-mode-${m}${mode === m ? ' on' : ''}`} onClick={() => pick(m)}>
            {MODE_NAME[m]}
          </button>
        ))}
      </div>
      {err && <p className="cq-err">{err}</p>}
      {data && data.days.length > 1 && (
        <label className="cq-fu-pick">
          日期
          <select value={day ?? data.days[0].day} onChange={(e) => setDay(e.target.value === data.days[0].day ? null : e.target.value)}>
            {data.days.map((d) => (
              <option key={d.day} value={d.day}>
                {d.day}　{d.pnl === null ? '' : yuan(d.pnl)}　{d.fills ?? 0} 筆
              </option>
            ))}
          </select>
        </label>
      )}
      {data === null && !err && <p className="cq-empty">讀取中…</p>}
      {data !== null && current === null && <p className="cq-empty">{EMPTY[mode]}</p>}
      {current !== null && current.schema !== SCHEMA && (
        <p className="cq-err">交易程式寫來的狀態是第 {current.schema} 版，這一版網站只看得懂第 {SCHEMA} 版，要先更新網站。</p>
      )}
      {current !== null && current.schema === SCHEMA && <Status s={current} now={now} />}
      {data !== null && <Days days={data.days} mode={mode} />}
      {current !== null && current.schema === SCHEMA && <p className="cq-at-foot">{assumptionText(current)}</p>}
      <p className="cq-at-foot">這一頁只能看，不能對交易程式下指令。緊急停止要在跑交易程式的那台電腦上做。</p>
    </>
  )
}
