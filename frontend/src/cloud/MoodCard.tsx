// 市場氛圍（D-181）：「盤勢」分頁最上面一條（族群熱力圖上方，橫跨兩欄）。盤中每 30 秒更新類股強弱，
// 收盤後的族群資金算好後自動換成資金版。期貨與美股（D-186）：台指期日盤／夜盤、期現價差、美股四大指數，每分鐘更新，
// 不分盤中收盤都顯示。只給登入的人（API 在 /api 底下）；三份都拿不到就不顯示，不擋族群資料。
import { useEffect, useState } from 'react'
import { fetchMoodClose, fetchMoodGlobal, fetchMoodLive } from './api'
import { CardFrame } from './CardFrame'
import { ptsSub } from './home'
import { Kpi } from './Section'
import type { Slide } from './Reel'
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

const kt = (x: number | null | undefined): 'up' | 'down' | 'plain' => (x === null || x === undefined || x === 0 ? 'plain' : x > 0 ? 'up' : 'down')

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

/** 期貨與美股（D-186）。2026-10-18 改版（地鐵告示）：每個價位一張數字卡（名稱、大數字、▲▼ 點數與幅度）；說明移到 title。 */
function Global({ state, now }: { state: GlobalState; now: { date: string; time: string } }) {
  const g = globalView(state?.global ?? null, now)
  if (!g) return null
  return (
    <div
      className="cq-mood-col cq-mood-wide"
      title={`台指期：期交所近月合約${g.fugle ? '（期交所沒回應，這次改用富果）' : ''}，日盤漲跌對前一天結算價、夜盤對當天日盤結算價；「比加權」＝最新一節的最後成交對加權最新點數（高＝正價差、低＝逆價差）。美股：四大指數最近一個交易日（盤中為即時）。每分鐘更新${state?.stale ? '；部分來源暫時沒更新到，顯示上一份' : ''}`}
    >
      <div className="cq-tiles">
        {g.txError && <Kpi label="台指期" value="—" sub={`取不到：${g.txError}`} tone="na" />}
        {g.tx.map((q) => (
          <Kpi
            key={q.session}
            label={`台指期 ${q.name}`}
            value={q.last !== null ? pts(q.last) : q.test !== null ? `試 ${pts(q.test)}` : '—'}
            sub={`${ptsSub(q.diff, q.chg)}・${q.when}`}
            tone={kt(q.chg)}
          />
        ))}
        {g.gap && (
          <Kpi label={`${g.gap.session === 'night' ? '夜盤' : '日盤'}比加權`} value={signedPts(g.gap.pts)} sub={`${g.gap.pts >= 0 ? '正價差' : '逆價差'}・${pct(g.gap.pct, 1)}`} tone={kt(g.gap.pts)} />
        )}
        {g.us.map((u) => (
          <Kpi key={u.name} label={u.name} value={usPrice(u.last)} sub={`${ptsSub(u.diff, u.chg)}${g.usWhen ? `・${g.usWhen}` : ''}`} tone={kt(u.chg)} />
        ))}
      </div>
    </div>
  )
}

type MoodView = ReturnType<typeof moodView>
type GlobalViewT = ReturnType<typeof globalView>

/** 市場氛圍的內容（期貨與美股、類股強弱、族群資金）；盤勢分頁與首頁共用。 */
function MoodBody({ v, global, now }: { v: MoodView; global: GlobalState; now: { date: string; time: string } }) {
  return (
    <div className="cq-mood-body">
      <Global state={global} now={now} />
      {v?.sectors && (
        <div className="cq-mood-col" title="類股強弱：證交所類股指數，跟加權比；盤中每 30 秒更新">
          <div className="cq-mood-row">
            <span className="cq-cat cq-cat-quote">{v.sectors.words[0]}</span>
            <Sectors items={v.sectors.strong} />
          </div>
          <div className="cq-mood-row">
            <span className="cq-cat cq-cat-quote">{v.sectors.words[1]}</span>
            <Sectors items={v.sectors.weak} />
          </div>
        </div>
      )}
      {v?.flow && (
        <div
          className="cq-mood-col"
          title={`資金數字＝成交比重比前 20 日平均多幾個百分點。防禦族群（金融、電信、食品、油電燃氣）成交比重 ${(v.flow.defShare * 100).toFixed(1)}%${v.flow.defAvg !== null ? `（前 20 日平均 ${(v.flow.defAvg * 100).toFixed(1)}%）` : ''}；上漲 ${v.flow.up.toLocaleString()} 檔、下跌 ${v.flow.down.toLocaleString()} 檔`}
        >
          <div className="cq-mood-row">
            <span className="cq-cat cq-cat-group">資金流入</span>
            <Flows items={v.flow.inflow} />
          </div>
          <div className="cq-mood-row">
            <span className="cq-cat cq-cat-group">資金流出</span>
            <Flows items={v.flow.outflow} />
          </div>
        </div>
      )}
      {v?.flowNote && <p className="cq-war-mute">{v.flowNote}</p>}
    </div>
  )
}

/** 標題旁的加權、櫃買漲跌與氛圍標籤。 */
function MoodTags({ v, g }: { v: MoodView; g: GlobalViewT }) {
  return (
    <>
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
    </>
  )
}

/** 收起時輪播的重點：台指期每一節、期現價差、美股每個指數、領漲／落後類股、資金流入／流出各一則。 */
function moodSlides(v: MoodView, g: GlobalViewT): Slide[] {
  const out: Slide[] = []
  const line = (key: string, label: string, value: string, tone?: string, note?: string | null) =>
    out.push({
      key,
      node: (
        <p className="cq-reel-text cq-reel-kv">
          <span>{label}</span>
          <b className={tone}>{value}</b>
          {note && <small className="cq-war-mute">{note}</small>}
        </p>
      ),
    })
  for (const q of g?.tx ?? [])
    line(`tx${q.session}`, `台指期 ${q.name}`, `${q.last !== null ? pts(q.last) : '—'}　${signedPts(q.diff)}　${q.chg !== null ? pct(q.chg) : '—'}`, trend(q.chg), q.when)
  if (g?.gap) line('gap', `${g.gap.session === 'night' ? '夜盤' : '日盤'}比加權`, `${signedPts(g.gap.pts)} 點（${pct(g.gap.pct, 1)}）`, trend(g.gap.pts), g.gap.pts >= 0 ? '正價差' : '逆價差')
  for (const u of g?.us ?? []) line(`us${u.name}`, `美股 ${u.name}`, `${usPrice(u.last)}　${signedPts(u.diff, 2)}　${pct(u.chg)}`, trend(u.chg), g?.usWhen)
  if (v?.sectors) {
    line('strong', v.sectors.words[0], v.sectors.strong.map((m) => `${m.name} ${pct(m.chg, 1)}`).join('、'))
    line('weak', v.sectors.words[1], v.sectors.weak.map((m) => `${m.name} ${pct(m.chg, 1)}`).join('、'))
  }
  if (v?.flow) {
    line('in', '資金流入', v.flow.inflow.map((m) => `${m.grp} ${pp(m.diff)}`).join('、'))
    line('out', '資金流出', v.flow.outflow.map((m) => `${m.grp} ${pp(m.diff)}`).join('、'))
  }
  return out
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
        <MoodTags v={v} g={g} />
        <span className="cq-mood-caret" aria-hidden>
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && <MoodBody v={v} global={global} now={now} />}
    </section>
  )
}

/** 首頁版（使用者 2026-10-17）：外框與收起輪播跟其他卡一樣，收起與否存在首頁版面（帳號）。 */
export function HomeMoodCard({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { live, close, global } = useMood(true)
  const now = taipeiNow()
  const v = moodView(live, close, now)
  const g = globalView(global?.global ?? null, now)
  return (
    <CardFrame
      id="mood"
      className="cq-war-moodcard"
      when={v?.when}
      extra={<span className="cq-war-moodtags"><MoodTags v={v} g={g} /></span>}
      collapsed={collapsed}
      onToggle={onToggle}
      slides={moodSlides(v, g)}
      empty="行情讀取中…"
    >
      {v || g ? <MoodBody v={v} global={global} now={now} /> : <p className="cq-war-mute">行情讀取中…</p>}
    </CardFrame>
  )
}
