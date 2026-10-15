// 市場氛圍卡（D-181）：每日名單最上面一張小卡。盤中每 30 秒更新類股強弱，收盤後的族群資金算好後自動換成資金版。
// 只給登入的人（API 在 /api 底下）；兩份都拿不到就不顯示，不擋名單。
import { useEffect, useState } from 'react'
import { fetchMoodClose, fetchMoodLive } from './api'
import { pct, trend } from './format'
import { liveBackoff, shouldPoll, taipeiNow } from './live'
import { CLOSE_POLL_MS, MOOD_POLL_MS, moodView, pp, type CloseMood, type LiveMood, type SectorMove, type FlowMove } from './mood'

const MOBILE = '(max-width: 820px)'

function useMood(enabled: boolean): { live: LiveMood | null; close: CloseMood | null } {
  const [live, setLive] = useState<LiveMood | null>(null)
  const [close, setClose] = useState<CloseMood | null>(null)
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
    const tick = () => {
      if (document.visibilityState === 'visible' && Date.now() >= pauseUntil && shouldPoll(taipeiNow(), dates)) loadLive()
    }
    loadLive()
    loadClose()
    const t1 = setInterval(tick, MOOD_POLL_MS)
    const t2 = setInterval(() => document.visibilityState === 'visible' && loadClose(), CLOSE_POLL_MS)
    return () => {
      gone = true
      clearInterval(t1)
      clearInterval(t2)
    }
  }, [enabled])
  return { live, close }
}

function Sectors({ items }: { items: SectorMove[] }) {
  return (
    <>
      {items.map((m) => (
        <span key={m.code} className={trend(m.chg)} title={`比大盤${m.rel >= 0 ? '強' : '弱'} ${Math.abs(m.rel * 100).toFixed(1)} 個百分點`}>
          {m.name} {pct(m.chg, 1)}
        </span>
      ))}
    </>
  )
}

function Flows({ items }: { items: FlowMove[] }) {
  return (
    <>
      {items.map((m) => (
        <span key={m.grp} className={trend(m.diff)} title={`成交比重 ${(m.share * 100).toFixed(1)}%，比前 20 日平均${m.diff >= 0 ? '多' : '少'} ${Math.abs(m.diff * 100).toFixed(1)} 個百分點`}>
          {m.grp} {pp(m.diff)}
        </span>
      ))}
    </>
  )
}

export function MoodCard({ enabled }: { enabled: boolean }) {
  const { live, close } = useMood(enabled)
  // 桌機預設展開；手機收起（展開時蓋在名單上，不擠掉名單的高度），點標題切換
  const [open, setOpen] = useState(() => !window.matchMedia(MOBILE).matches)
  const v = moodView(live, close, taipeiNow())
  if (!enabled || !v) return null
  return (
    <section className="cq-mood" aria-label="市場氛圍">
      <button className="cq-mood-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <b>市場氛圍</b>
        <span className="cq-mood-when">{v.when}</span>
        <span className="cq-mood-idx">
          加權 <i className={trend(v.taiex)}>{pct(v.taiex)}</i>
          {v.otc !== null && (
            <>
              {' '}櫃買 <i className={trend(v.otc)}>{pct(v.otc)}</i>
            </>
          )}
        </span>
        {v.label && <em className={`cq-mood-tag ${v.tone}`}>{v.label}</em>}
        <span className="cq-mood-caret" aria-hidden>
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && (
        <div className="cq-mood-body" onClick={() => window.matchMedia(MOBILE).matches && setOpen(false)}>
          {v.sectors && (
            <>
              <div className="cq-mood-row">
                <span>{v.sectors.words[0]}</span>
                <span className="cq-mood-vals">
                  <Sectors items={v.sectors.strong} />
                </span>
              </div>
              <div className="cq-mood-row">
                <span>{v.sectors.words[1]}</span>
                <span className="cq-mood-vals">
                  <Sectors items={v.sectors.weak} />
                </span>
              </div>
            </>
          )}
          {v.flow && (
            <>
              <div className="cq-mood-row">
                <span>資金流入</span>
                <span className="cq-mood-vals">
                  <Flows items={v.flow.inflow} />
                </span>
              </div>
              <div className="cq-mood-row">
                <span>資金流出</span>
                <span className="cq-mood-vals">
                  <Flows items={v.flow.outflow} />
                </span>
              </div>
              <p className="cq-mood-note">
                防禦族群（金融、電信、食品、油電燃氣）成交比重 {(v.flow.defShare * 100).toFixed(1)}%
                {v.flow.defAvg !== null && `（前 20 日平均 ${(v.flow.defAvg * 100).toFixed(1)}%）`}・上漲 {v.flow.up.toLocaleString()} 檔、下跌{' '}
                {v.flow.down.toLocaleString()} 檔。資金數字＝成交比重比前 20 日平均多幾個百分點。
              </p>
            </>
          )}
          {v.flowNote && <p className="cq-mood-note">{v.flowNote}；類股強弱來自證交所類股指數，盤中每 30 秒更新。</p>}
        </div>
      )}
    </section>
  )
}
