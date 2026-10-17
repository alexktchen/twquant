// 市場氛圍（D-181）：「盤勢」分頁最上面一條（族群熱力圖上方，橫跨兩欄）。盤中每 30 秒更新類股強弱，
// 收盤後的族群資金算好後自動換成資金版。期貨與美股（D-186）：台指期日盤／夜盤、期現價差、美股四大指數，每分鐘更新，
// 不分盤中收盤都顯示。只給登入的人（API 在 /api 底下）；三份都拿不到就不顯示，不擋族群資料。
import { useEffect, useState } from 'react'
import { fetchMoodClose, fetchMoodGlobal, fetchMoodLive } from './api'
import { pct, trend } from './format'
import { liveBackoff, shouldPoll, taipeiNow } from './live'
import { MOOD_KEY, loadPref, openPref, savePref } from './prefs'
import {
  CLOSE_POLL_MS,
  GLOBAL_POLL_MS,
  MOOD_POLL_MS,
  globalView,
  moodView,
  pp,
  pts,
  signedPts,
  usPrice,
  type CloseMood,
  type FlowMove,
  type GlobalMood,
  type LiveMood,
  type SectorMove,
} from './mood'

type GlobalState = { global: GlobalMood; stale: boolean } | null

function useMood(enabled: boolean): { live: LiveMood | null; close: CloseMood | null; global: GlobalState } {
  const [live, setLive] = useState<LiveMood | null>(null)
  const [close, setClose] = useState<CloseMood | null>(null)
  const [global, setGlobal] = useState<GlobalState>(null)
  useEffect(() => {
    if (!enabled) return
    let gone = false
    let dates: string[] = []
    let failures = 0
    let pauseUntil = 0
    const loadLive = () =>
      fetchMoodLive()
        .then((r) => {
          if (gone) return
          failures = 0
          pauseUntil = r.retry_after ? Date.now() + r.retry_after * 1000 : 0
          dates = r.live ? [r.live.date] : []
          setLive(r.live)
        })
        // 證交所擋下或請求太密：留著上一份，等一陣子再試（30 秒起加倍）
        .catch(() => {
          failures += 1
          pauseUntil = Date.now() + liveBackoff(failures, null)
        })
    const loadClose = () =>
      fetchMoodClose()
        .then((c) => !gone && setClose(c))
        .catch(() => undefined)
    // 期貨與美股：伺服器 60 秒暫存、來源沒拿到會給上一份（stale），取不到（503）就留著手上這份
    let globalFailures = 0
    let globalPauseUntil = 0
    const loadGlobal = () =>
      fetchMoodGlobal()
        .then((r) => {
          if (gone) return
          globalFailures = 0
          setGlobal({ global: r.global, stale: r.stale })
        })
        .catch(() => {
          globalFailures += 1
          globalPauseUntil = Date.now() + liveBackoff(globalFailures, null)
        })
    const tick = () => {
      if (document.visibilityState === 'visible' && Date.now() >= pauseUntil && shouldPoll(taipeiNow(), dates)) loadLive()
    }
    const tickGlobal = () => {
      if (document.visibilityState === 'visible' && Date.now() >= globalPauseUntil) loadGlobal()
    }
    loadLive()
    loadClose()
    loadGlobal()
    const t1 = setInterval(tick, MOOD_POLL_MS)
    const t2 = setInterval(() => document.visibilityState === 'visible' && loadClose(), CLOSE_POLL_MS)
    const t3 = setInterval(tickGlobal, GLOBAL_POLL_MS)
    return () => {
      gone = true
      clearInterval(t1)
      clearInterval(t2)
      clearInterval(t3)
    }
  }, [enabled])
  return { live, close, global }
}

/** 類股強弱：每格「名稱＋漲跌」，固定欄寬，領漲／落後兩列的格子上下對齊。 */
function Sectors({ items }: { items: SectorMove[] }) {
  return (
    <span className="cq-mood-cells">
      {items.map((m) => (
        <span key={m.code} className="cq-mood-cell" title={`比大盤${m.rel >= 0 ? '強' : '弱'} ${Math.abs(m.rel * 100).toFixed(1)} 個百分點`}>
          <span>{m.name}</span>
          <b className={trend(m.chg)}>{pct(m.chg, 1)}</b>
        </span>
      ))}
    </span>
  )
}

function Flows({ items }: { items: FlowMove[] }) {
  return (
    <span className="cq-mood-cells">
      {items.map((m) => (
        <span
          key={m.grp}
          className="cq-mood-cell"
          title={`成交比重 ${(m.share * 100).toFixed(1)}%，比前 20 日平均${m.diff >= 0 ? '多' : '少'} ${Math.abs(m.diff * 100).toFixed(1)} 個百分點`}
        >
          <span>{m.grp}</span>
          <b className={trend(m.diff)}>{pp(m.diff)}</b>
        </span>
      ))}
    </span>
  )
}

/** 期貨與美股（D-186）：一張對齊的表——名稱、價位、漲跌點、漲跌幅、時間；價位放大、紅漲綠跌。 */
function Global({ state, now }: { state: GlobalState; now: { date: string; time: string } }) {
  const g = globalView(state?.global ?? null, now)
  if (!g) return null
  return (
    <div className="cq-mood-col cq-mood-wide">
      <table className="cq-mood-quotes">
        <tbody>
          {g.txError && (
            <tr>
              <th>台指期</th>
              <td colSpan={5} className="cq-mood-gap">
                暫時取不到（{g.txError}），稍後自動再試
              </td>
            </tr>
          )}
          {g.tx.map((q, k) => (
            <tr key={q.session} title={q.session === 'night' ? '夜盤漲跌對當天日盤結算價' : '日盤漲跌對前一天結算價'}>
              <th>{k === 0 ? '台指期' : ''}</th>
              <td className="cq-q-name">{q.name}</td>
              <td className={`cq-q-px ${trend(q.chg)}`}>
                {q.last !== null ? pts(q.last) : q.test !== null ? <small>試撮 {pts(q.test)}</small> : '—'}
              </td>
              <td className={`cq-q-num ${trend(q.diff)}`}>{signedPts(q.diff)}</td>
              <td className={`cq-q-num ${trend(q.chg)}`}>{q.chg !== null ? pct(q.chg) : '—'}</td>
              <td className="cq-q-when">{q.when}</td>
            </tr>
          ))}
          {g.gap && (
            <tr className="cq-q-gap" title="最新一節期貨最後成交對加權最新點數（夜盤對加權收盤）">
              <th />
              <td className="cq-q-name">{g.gap.session === 'night' ? '夜盤' : '日盤'}比加權</td>
              <td className={`cq-q-px ${trend(g.gap.pts)}`}>{signedPts(g.gap.pts)}</td>
              <td className="cq-q-num cq-q-unit">點</td>
              <td className={`cq-q-num ${trend(g.gap.pct)}`}>{pct(g.gap.pct, 1)}</td>
              <td className="cq-q-when">{g.gap.pts >= 0 ? '正價差' : '逆價差'}</td>
            </tr>
          )}
          {g.us.map((u, k) => (
            <tr key={u.name} className={k === 0 ? 'cq-q-first' : undefined}>
              <th>{k === 0 ? '美股' : ''}</th>
              <td className="cq-q-name">{u.name}</td>
              <td className={`cq-q-px ${trend(u.chg)}`}>{usPrice(u.last)}</td>
              <td className={`cq-q-num ${trend(u.diff)}`}>{signedPts(u.diff, 2)}</td>
              <td className={`cq-q-num ${trend(u.chg)}`}>{pct(u.chg)}</td>
              <td className="cq-q-when">{k === 0 ? g.usWhen : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="cq-mood-note">
        台指期：期交所近月合約{g.fugle && '（期交所沒回應，這次改用富果）'}，日盤漲跌對前一天結算價、夜盤對當天日盤結算價；「比加權」是最新一節的最後成交對加權最新點數（夜盤對加權收盤），高＝正價差、低＝逆價差。
        美股：四大指數最近一個交易日（美股盤中為即時）。每分鐘更新，只供看盤{state?.stale && '；部分來源暫時沒更新到，顯示上一份'}。
      </p>
    </div>
  )
}

export function MoodCard({ enabled }: { enabled: boolean }) {
  const { live, close, global } = useMood(enabled)
  // 預設展開，點標題收起；展開／收起記在這台瀏覽器
  const [open, setOpenState] = useState(() => openPref(loadPref(MOOD_KEY), true))
  const setOpen = (on: boolean) => {
    setOpenState(on)
    savePref(MOOD_KEY, on ? '1' : '0')
  }
  const now = taipeiNow()
  const v = moodView(live, close, now)
  const g = globalView(global?.global ?? null, now)
  if (!enabled || (!v && !g)) return null
  return (
    <section className="cq-mood" aria-label="市場氛圍">
      <button className="cq-mood-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <b>市場氛圍</b>
        {v && <span className="cq-mood-when">{v.when}</span>}
        {v && (
          <span className="cq-mood-idx">
            加權 <i className={trend(v.taiex)}>{pct(v.taiex)}</i>
            {v.otc !== null && (
              <>
                {' '}櫃買 <i className={trend(v.otc)}>{pct(v.otc)}</i>
              </>
            )}
          </span>
        )}
        {v?.label && <em className={`cq-mood-tag ${v.tone}`}>{v.label}</em>}
        {g?.label && <em className={`cq-mood-tag ${g.tone}`}>{g.label}</em>}
        <span className="cq-mood-caret" aria-hidden>
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && (
        <div className="cq-mood-body">
          <Global state={global} now={now} />
          {v?.sectors && (
            <div className="cq-mood-col">
              <div className="cq-mood-row">
                <span>{v.sectors.words[0]}</span>
                <Sectors items={v.sectors.strong} />
              </div>
              <div className="cq-mood-row">
                <span>{v.sectors.words[1]}</span>
                <Sectors items={v.sectors.weak} />
              </div>
              <p className="cq-mood-note">類股強弱：證交所類股指數，跟加權比；盤中每 30 秒更新。</p>
            </div>
          )}
          {v?.flow && (
            <div className="cq-mood-col">
              <div className="cq-mood-row">
                <span>資金流入</span>
                <Flows items={v.flow.inflow} />
              </div>
              <div className="cq-mood-row">
                <span>資金流出</span>
                <Flows items={v.flow.outflow} />
              </div>
              <p className="cq-mood-note">
                防禦族群（金融、電信、食品、油電燃氣）成交比重 {(v.flow.defShare * 100).toFixed(1)}%
                {v.flow.defAvg !== null && `（前 20 日平均 ${(v.flow.defAvg * 100).toFixed(1)}%）`}・上漲 {v.flow.up.toLocaleString()} 檔、下跌{' '}
                {v.flow.down.toLocaleString()} 檔。資金數字＝成交比重比前 20 日平均多幾個百分點。
              </p>
            </div>
          )}
          {v?.flowNote && (
            <div className="cq-mood-col">
              <p className="cq-mood-note">{v.flowNote}。</p>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
