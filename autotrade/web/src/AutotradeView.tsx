// 程式交易（autotrade A-005、A-011）：獨立交易系統寫出來的狀態，加上一顆遠端緊急停止（D-225）。
// 這個檔案裡除了緊急停止以外都只能看；沒有解除緊急停止的按鈕，也不要加。遙控按鈕（D-229）在 ./RemoteBar.tsx。
import { Activity, Bell, OctagonX, ReceiptText } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchAutotrade, sendAutotradeStop } from './api'
import {
  BROKER_HINT,
  EVENT_LABEL,
  EVENT_SERIOUS,
  LISTENER_NAME,
  MODE_NAME,
  NOBODY_LISTENING,
  DATA_STALE_S,
  POLL_MS,
  SCHEMA,
  SOURCE_NAME,
  STOP_FAST_MS,
  STOP_SCOPE,
  STRATEGY_LABEL,
  assumptionText,
  clock,
  cumPath,
  cumulative,
  dayStats,
  eventText,
  lamp,
  lossRoom,
  nobodyListening,
  pollMs,
  positionText,
  refetchDue,
  armedOf,
  armedText,
  remoteButtons,
  requestLines,
  requestsWaiting,
  stopLines,
  unknownLines,
  type AutoDayRow,
  type AutoEnvelope,
  type AutoMode,
  type AutoStatus,
  type StopLine,
} from './autotrade'
import { PRODUCT_NAME, shouldPoll, tone, yuan } from './helpers'
import { RemoteBar, type CommandSent } from './RemoteBar'
import { Kpi, Section } from './Section'
import './autotrade.css'

const MODES: AutoMode[] = ['sim', 'live']
const EMPTY: Record<AutoMode, string> = {
  sim: '還沒有模擬紀錄：交易程式跑完一天、把狀態寫進來之後就會出現。',
  live: '還沒有真單紀錄：真單還沒上線。',
}
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const price = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 1 })
/** 期交所代號 → 平常的叫法（TMF＝微台）；不認得就照原樣。 */
const symbolName = (s: string) => (PRODUCT_NAME as Record<string, string | undefined>)[s] ?? s

/** 送出停止時伺服器回來的東西：今天最新的停止指令與每支程式的心跳。 */
type StopSent = Pick<AutoEnvelope, 'stop' | 'listeners'>
/** 送出失敗後重讀一次，這麼多秒內按的停止算是剛才那一筆（請求可能在寫入之後才斷掉）。 */
const JUST_SENT_S = 60

/**
 * 遠端緊急停止：按一下只是打開確認，再按「確定停止」才送出。
 * 下面每支交易程式一行（真單在前）：在不在聽、收到了沒、最後平掉了沒。模擬的回報不會讓真單那一行變成「好了」。
 */
function StopBar({ lines, pressed, onSent, reread }: { lines: StopLine[]; pressed: boolean; onSent: (r: StopSent) => void; reread: () => Promise<StopSent> }) {
  const [step, setStep] = useState<'idle' | 'confirm' | 'sending'>('idle')
  const [err, setErr] = useState<string | null>(null)
  const send = () => {
    setStep('sending')
    setErr(null)
    sendAutotradeStop()
      .then(onSent)
      .catch((e) =>
        // 請求可能在寫進資料庫之後才斷掉：重讀一次，真的沒有剛按的那一筆才說沒送出去
        reread()
          .then((r) => (r.stop !== null && r.stop.age_s <= JUST_SENT_S ? onSent(r) : setErr(errText(e))))
          .catch(() => setErr(errText(e))),
      )
      .finally(() => setStep('idle'))
  }
  const nobody = nobodyListening(lines)
  const hand = lines.some((l) => l.tone === 'bad')
  return (
    <div className="cq-at-stop">
      {step === 'idle' ? (
        <button className="cq-at-stop-btn" onClick={() => setStep('confirm')}>
          <OctagonX size={20} aria-hidden="true" /> 緊急停止
        </button>
      ) : (
        <div className="cq-at-stop-confirm" role="alertdialog" aria-label="確認緊急停止">
          <b>確定要停止交易並平倉？</b>
          <span>模擬與真單都會停。{STOP_SCOPE}</span>
          <div>
            <button className="cq-at-stop-yes" disabled={step === 'sending'} onClick={send}>
              {step === 'sending' ? '送出中…' : '確定停止'}
            </button>
            <button className="cq-at-stop-no" disabled={step === 'sending'} onClick={() => setStep('idle')}>
              取消
            </button>
          </div>
        </div>
      )}
      {err && (
        <p className="cq-err">
          停止沒有送出去：{err}。{BROKER_HINT}
        </p>
      )}
      {nobody && <p className="cq-at-stop-nobody">{NOBODY_LISTENING}</p>}
      <ul className="cq-at-stop-lines" aria-label="每支交易程式的狀況">
        {lines.map((l) => (
          <li key={l.who} className={l.tone}>
            <i aria-hidden="true" />
            <b>{LISTENER_NAME[l.who]}</b>
            <strong>{l.label}</strong>
            {l.detail && <span>{l.detail}</span>}
          </li>
        ))}
      </ul>
      {hand && <p className="cq-at-stop-hand">{BROKER_HINT}</p>}
      {pressed && <p className="cq-at-stop-scope">{STOP_SCOPE}</p>}
    </div>
  )
}

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
  const [fetchedAt, setFetchedAt] = useState(0) // 這份資料是什麼時候拿到的（這台裝置自己的時鐘）
  const [loading, setLoading] = useState(true) // 換了分頁、新資料還沒到
  const [err, setErr] = useState<string | null>(null)
  const [now, setNow] = useState(() => new Date())
  const current = data?.current ?? null
  const live = useRef(false)
  live.current = current?.status === 'live'
  // 伺服器給的是「幾秒以前」；再加上拿到資料之後過的時間，就不用拿手機的時鐘去跟伺服器的時間比
  const elapsed = data === null ? 0 : Math.max(0, (now.getTime() - fetchedAt) / 1000)
  // 還沒讀到、或太久沒更新的資料不拿來判斷（不然會把「頁面沒更新」誤當成「程式沒回應」）
  const lines =
    data === null
      ? unknownLines(err ? `讀不到狀態：${err}` : '讀取中…')
      : elapsed > DATA_STALE_S
        ? unknownLines(`畫面上的狀態已經 ${Math.floor(elapsed)} 秒沒更新${err ? `（${err}）` : ''}`)
        : stopLines(data.stop, data.listeners, elapsed)
  // 遙控按鈕（A-016）：畫面上的狀態夠新才讓人按；暫停或恢復看今天這支程式的狀態
  const known = data !== null && elapsed <= DATA_STALE_S
  const requests = data?.requests ?? []
  const paused = day === null && current?.status === 'live' && current.paused === true
  // 待命／運作（A-019）：今天還在跑的那一份狀態說了算；還沒有狀態就是不知道
  const armed = known ? armedOf(current, day === null) : null
  const buttons = remoteButtons(mode, data?.listeners ?? {}, data?.remote, paused, known, armed)
  // 有停止或遙控指令還在等（等收到、等做完）時更新變快，不管是不是交易時段、看的是哪一天
  const fastMs = known && requestsWaiting(requests, elapsed) ? STOP_FAST_MS : pollMs(data?.stop ?? null, lines, elapsed)
  const fast = fastMs < POLL_MS

  const take = (d: AutoEnvelope) => {
    setFetchedAt(Date.now())
    setNow(new Date())
    setData(d)
    setLoading(false)
    setErr(null)
  }

  useEffect(() => {
    let stop = false
    let tick = 0
    const run = () =>
      fetchAutotrade(mode, day ?? undefined)
        .then((d) => !stop && take(d))
        .catch((e) => !stop && setErr(errText(e)))
    run()
    // 分頁回到前景時馬上重抓一次（不等下一輪）
    const onVisible = () => document.visibilityState === 'visible' && run()
    document.addEventListener('visibilitychange', onVisible)
    const id = setInterval(
      () => {
        tick += 1
        // 分頁在前景就一定重抓：緊急停止那一塊（每支程式在不在聽、停止的結果）不管看哪一天、什麼時段都要是新的。
        // 分頁在背景時：只有看最新一天、在交易時段、盤中狀態才抓（省流量）
        const visible = document.visibilityState === 'visible'
        if (fast || visible || (day === null && shouldPoll(new Date(), null) && refetchDue(tick, live.current))) run()
      },
      fast ? fastMs : POLL_MS,
    )
    return () => {
      stop = true
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // lines／elapsed 每次重畫都會變，這裡只在「快／慢」切換時重設計時器
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, day, refresh, fast])

  useEffect(() => {
    // 畫面上的「已經幾秒」：等結果的時候每秒動一次，平常跟著更新的節奏
    const id = setInterval(() => setNow(new Date()), fast ? 1000 : POLL_MS)
    return () => clearInterval(id)
  }, [fast])

  const sent = (r: Pick<AutoEnvelope, 'stop' | 'listeners'>) =>
    take({ days: data?.days ?? [], current, stop: r.stop, listeners: r.listeners, requests: data?.requests, remote: data?.remote })
  const sentCommand = (r: CommandSent) => take({ days: data?.days ?? [], current, stop: data?.stop ?? null, listeners: data?.listeners ?? {}, requests: r.requests, remote: r.remote })
  const pick = (m: AutoMode) => {
    if (m === mode) return
    // 緊急停止那一塊跟看哪個分頁無關：留著，等新資料來再換（不然切分頁的那一下會變成「不知道」）
    setData((d) => (d === null ? null : { days: [], current: null, stop: d.stop, listeners: d.listeners, requests: d.requests, remote: d.remote }))
    setDay(null)
    setLoading(true)
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
      <StopBar lines={lines} pressed={(data?.stop ?? null) !== null} onSent={sent} reread={() => fetchAutotrade(mode, day ?? undefined)} />
      <RemoteBar key={mode} mode={mode} buttons={buttons} lines={known ? requestLines(requests, mode, elapsed) : []} onSent={sentCommand} state={armedText(armed)} />
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
      {(data === null || loading) && !err && <p className="cq-empty">讀取中…</p>}
      {data !== null && !loading && current === null && <p className="cq-empty">{EMPTY[mode]}</p>}
      {current !== null && current.schema !== SCHEMA && (
        <p className="cq-err">交易程式寫來的狀態是第 {current.schema} 版，這一版網站只看得懂第 {SCHEMA} 版，要先更新網站。</p>
      )}
      {current !== null && current.schema === SCHEMA && <Status s={current} now={now} />}
      {data !== null && <Days days={data.days} mode={mode} />}
      {current !== null && current.schema === SCHEMA && <p className="cq-at-foot">{assumptionText(current)}</p>}
      <p className="cq-at-foot">除了緊急停止與遙控按鈕以外，這一頁都只能看。解除緊急停止、改設定，要在跑交易程式的那台電腦上做。</p>
    </>
  )
}
