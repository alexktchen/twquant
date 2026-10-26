// 雲端版網站（T-332／D-170）：每日名單（今天與過去每一天）、名單績效（D-176）、追蹤股（T-345）與個股 K 線。介面仿富途牛牛：
// 深色底、橘色強調、左側自選股式名單、右側 K 線。漲紅跌綠（台股習慣）。
import { useEffect, useMemo, useRef, useState } from 'react'
import './cloud.css'
import './cloud-v2.css'
import './home-v2.css'
import './score-v2.css'
import './news-v2.css'
import {
  fetchAccess,
  fetchBars,
  fetchMe,
  fetchPickDates,
  fetchPicks,
  fetchStockNewsCounts,
  fetchStockNewsDays,
  fetchPublicDates,
  fetchQuotes,
  fetchScores,
  fetchStockPicks,
  fetchWatchlist,
  fetchTrades,
  saveTrade,
  deleteTrade,
  saveWatchOrder,
  searchStocks,
  setTracked,
} from './api'
import { GUEST_LINES, LOGIN_PAGE, LoginRequired, guestCandles, guestDay, guestPicks } from './auth'
import { clearSession, completeOAuth, hasSession, signOut } from './session-client'
import { LINE_KEY, loadPref, savePref } from './prefs'
import { RISK_KEY, riskAccepted, riskRecord } from './risk'
import { RiskNotice } from './RiskNotice'
import { ScorecardPage } from './ScorecardPage'
import { FuturesPage } from './FuturesPage'
import { KChart, LINE_LEGEND, MA_LEGEND } from './KChart'
import { flowText, groupLine, groupTitle, sameGroups, type Group } from './group'
import { cardReasons, factorsNote, mark, primaryModel, type Reason } from './reasons'
import { dropNote, weakSummary } from './weak'
import { ArrowDown, ArrowUp, Banknote, CandlestickChart, Check, ChevronDown, CircleDot, CircleHelp, CircleMinus, CirclePlus, Gauge, History, Info, Layers, Lightbulb, MessageSquareText, ShoppingBag, Trophy } from 'lucide-react'
import { Kpi, Section } from './Section'
import { ScoreBars, ReviewTiles, StanceChip, TopPickCard } from './Review'
import { DIMS, REVIEW_NOTE, topOf, type Review } from './pickreview'
import { blurbTitle, type Blurb } from './blurb'
import { eventBadges, pickBadges, rowBadges, type Badge } from './badges'
import { BadgeChip } from './Icon'
import { BadgeLegend } from './Legend'
import { TradesPanel } from './TradesPanel'
import { boughtKeys, type Trade, type TradeList } from './trades'
import { StockFacts } from './StockFacts'
import { bucketText, refBar, refFor, refText, type RefRow } from './reference'
import { exampleText, similarBar, similarText, spanText, type Similar } from './similar'
import { WarRoom } from './WarRoom'
import { GroupsPage } from './GroupsPage'
import { NewsPage } from './NewsPage'
import { guestTab, tabFromUrl, withoutTab, type Tab } from './tabs'
import { LineGroupPage } from './LineGroupPage'
import { lockedText, tabAllowed, type Feature } from './access'
import { chooseDay, dateOption, dayCount, dayNav, hits, pct, price, quoteBar, sameBars, trend, type Bar, type DateCounts, type Pick, type PicksDay } from './format'
import { PUSH_HINT, type PushState } from './push'
import { currentPushState, disablePush, enablePush, registerServiceWorker } from './push-client'
import { LIVE_POLL_MS, limitPrices, liveBackoff, limitState, liveBar, liveLabel, pickPrice, quoteLimit, rowPrice, shouldPoll, taipeiNow, type Quote } from './live'
import { edgeScroll, moveItem, rowAt } from './reorder'
import { modelView, scoreTags, sparkPoints, type SearchHit, type StockScores, type WatchItem, type WatchList } from './scores'
import { StockCalendar } from './StockCalendar'
import { StockNews } from './StockNews'
import { newsMarkers, type NewsCount, type StockNewsDay } from './news'


// 追蹤股 API 的身分驗不過時（401）不切成沒登入的預覽：名單照常看，只有追蹤股區塊顯示這句
const WATCH_RELOGIN = '登入身分驗證失敗，請登出再登入一次'
// 從背景回到前景時，距離上次重抓至少隔多久才再抓
const REFRESH_MIN_MS = 30_000
// 追蹤股一列最多幾個行事曆小標（三個模型分數已經佔一排）
const WATCH_EVENTS_MAX = 2
const watchError = (e: Error) => (e instanceof LoginRequired ? WATCH_RELOGIN : e.message)

export default function CloudApp() {
  // 推播通知點開帶 ?tab=（族群資金通知帶 group → 首頁）就直接開那個分頁；預設是首頁戰情室（T-379）
  // 網址有沒有明寫 ?tab=：沒登入的人沒寫就落在公開的新聞頁（guestTab）
  const [explicitTab] = useState<Tab | null>(() => tabFromUrl(window.location.href))
  const [tab, setTab] = useState<Tab>(() => explicitTab ?? 'home')
  useEffect(() => {
    if (tabFromUrl(window.location.href)) window.history.replaceState(null, '', withoutTab(window.location.href))
    // 網站已開著時點通知：背景程式（sw.js）傳來要開的網址
    const onMessage = (e: MessageEvent) => {
      const data = e.data as { type?: string; url?: string } | null
      if (data?.type !== 'open' || typeof data.url !== 'string') return
      const t = tabFromUrl(new URL(data.url, window.location.href).href)
      if (t) setTab(t)
    }
    navigator.serviceWorker?.addEventListener('message', onMessage)
    return () => navigator.serviceWorker?.removeEventListener('message', onMessage)
  }, [])
  const [dates, setDates] = useState<string[]>([])
  // 日期選單旁的檔數（只有登入後的 /api 才給；公開日期清單沒有）
  const [dateCounts, setDateCounts] = useState<DateCounts | null>(null)
  // 每日名單看哪一天；undefined＝最新一天（資料進來新的一天就自動跟上）
  const [date, setDate] = useState<string | undefined>(undefined)
  const [day, setDay] = useState<PicksDay | null>(null)
  // 上次看的策略（大盤精選／潛伏飆股）：重新整理、重開都停在那一條
  const [line, setLine] = useState<string>(() => loadPref(LINE_KEY) ?? '')
  // 投資風險警語：這台裝置還沒同意過（或警語改版）就先擋在最前面
  const [riskOk, setRiskOk] = useState(() => riskAccepted(loadPref(RISK_KEY)))
  const [sid, setSid] = useState<string>('')
  const [err, setErr] = useState<string>('')
  const [guest, setGuest] = useState(false)
  const [email, setEmail] = useState<string | null>(null)
  // 這個帳號能用哪些功能（T-423：有 line 才顯示「LINE 群組」分頁；T-430：有 picks／score 才顯示每日名單／績效）
  const [features, setFeatures] = useState<Feature[]>([])
  // 權限讀回來了沒（讀回來之前，從網址開到要權限的分頁先空白，不閃「沒有權限」）
  const [accessKnown, setAccessKnown] = useState(false)
  const canPicks = features.includes('picks')
  // 追蹤股：存在帳號裡（伺服器），手機和電腦同一份
  const [watch, setWatch] = useState<WatchList | null>(null)
  const [watchErr, setWatchErr] = useState('')
  // 我的下單（T-388）：名單上勾的「我買了」與手動補的，存在帳號裡
  const [trades, setTrades] = useState<TradeList | null>(null)
  const [tradesErr, setTradesErr] = useState('')
  // 追蹤分頁正在看的股票：清單裡的，或剛搜尋到、還沒加入的
  const [watchSel, setWatchSel] = useState<{ sid: string; name: string | null } | null>(null)
  // 從背景回來時加一：名單、追蹤股、個股跟著重抓（見下方 visibilitychange）
  const [refresh, setRefresh] = useState(0)
  // 🔔 通知：這台裝置的狀態（登入後才算）與按下後的說明
  const [push, setPush] = useState<PushState | null>(null)
  const [notice, setNotice] = useState('')
  // 追蹤清單的編輯模式（拖曳排序、置頂、刪除）
  const [editing, setEditing] = useState(false)
  // 名單表頭的「圖示說明」
  const [legend, setLegend] = useState(false)
  // 沒登入（或登入過期）：資料 API 被 Access 擋下 → 版面照常可操作，只有股票列與 K 線換成模糊的假資料；其他錯誤照常顯示
  const onFail = (e: Error) => {
    if (!(e instanceof LoginRequired)) return setErr(e.message)
    setGuest(true)
    setDay(null) // 登入過期：先前的真名單不留在狀態裡
    setWatch(null)
  }
  const loadWatch = () =>
    fetchWatchlist()
      .then((w) => {
        setWatch(w)
        setWatchErr('')
      })
      .catch((e: Error) => setWatchErr(watchError(e)))
  const loadTrades = () =>
    fetchTrades()
      .then((t) => {
        setTrades(t)
        setTradesErr('')
      })
      .catch((e: Error) => setTradesErr(watchError(e)))

  useEffect(() => {
    // 先把 Google 登入回來的 ?code= 換成登入狀態（T-439），再問 /api/me 這是誰。
    // 身分只用來顯示頭像；本機開發的後端沒有 /api/me，那時不顯示即可。確定登入了才抓追蹤股
    completeOAuth()
      .catch((e: Error) => setNotice(e.message))
      .then(() => fetchMe())
      .then((m) => {
        setEmail(m.email)
        fetchAccess()
          .then((r) => {
            setFeatures(r.features)
            setAccessKnown(true)
          })
          .catch((e: Error) => console.warn('權限讀取失敗', e))
        currentPushState()
          .then(setPush)
          .catch((e: Error) => console.warn('通知狀態讀取失敗', e))
        loadWatch()
        loadTrades()
      })
      .catch((e: Error) => {
        if (!(e instanceof LoginRequired)) return
        if (hasSession()) {
          // 本機有 Supabase 登入卻被擋：被導走＝/api 還在 Cloudflare Access 後面（過渡期，管理者還沒關）；被拒＝權杖失效
          if (e.reason === 'redirect') setNotice('登入系統切換中：/api 還在 Cloudflare Access 保護下，管理者關掉後重新整理即可')
          else clearSession()
        }
        setGuest(true)
        setTab((t) => guestTab(t, explicitTab))
        // 日期是公開資訊（只有日期、沒有股票）：沒登入時追蹤分頁的評分日與預覽用
        fetchPublicDates().then(setDates).catch(onFail)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 從背景回到前景時重新確認登入：登入 24 小時就過期，iPhone 主畫面 App 常常隔天才回來，
  // 畫面卻還停在登入狀態（頭像、登出、舊名單）。過期就換成沒登入的畫面，不留舊資料
  // 還登入著就重抓名單、追蹤股與個股：網頁開著不會自己更新，下午打開、晚上回來（或點通知回來）會停在舊資料——
  // 追蹤股會一直顯示下午從證交所抓的價格，而資料庫 17:40 左右就有今天的收盤了。30 秒內重複切換不重抓
  const lastRefresh = useRef(Date.now())
  useEffect(() => {
    if (guest) return
    const onShow = () => {
      if (document.visibilityState !== 'visible') return
      fetchMe()
        .then(() => {
          if (Date.now() - lastRefresh.current < REFRESH_MIN_MS) return
          lastRefresh.current = Date.now()
          setRefresh((n) => n + 1)
          loadWatch()
        })
        .catch((e: Error) => {
          if (e instanceof LoginRequired) onFail(e)
        })
    }
    document.addEventListener('visibilitychange', onShow)
    return () => document.removeEventListener('visibilitychange', onShow)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guest])

  // 沒登入時「最新一天」用公開日期清單的第一天；登入時這個值固定 undefined，不會多抓一次
  const guestToday = guest ? dates[0] : undefined

  // 名單日期清單：有名單權限才抓（T-430）；從背景回來時也重抓，隔天回來選單才有新的一天（停在最新的人名單會自動換成新的一天）
  useEffect(() => {
    if (guest || !canPicks) return
    let stale = false
    fetchPickDates()
      .then((r) => {
        if (stale) return
        setDates(r.dates)
        setDateCounts(r.counts)
      })
      .catch((e: Error) => {
        if (stale) return
        if (refresh === 0) onFail(e)
        else console.warn('日期清單重抓失敗', e)
      })
    return () => {
      stale = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, guest, canPicks])

  useEffect(() => {
    // 追蹤、績效分頁不看名單：留著上一份（K 線的推薦日圖例要用線別名稱）；盤勢也抓（熱力圖標「在名單」，T-431 從首頁搬過來）；沒有名單權限不抓（T-430）
    if (guest || !canPicks || (tab !== 'picks' && tab !== 'group')) return
    // stale：切日期、切分頁或切成沒登入後，晚到的回應一律丟掉
    let stale = false
    setErr('')
    fetchPicks(date)
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
  }, [tab, date, guest, refresh, canPicks])

  // 沒登入時畫面只讀假資料：就算有真名單寫進 day，也不會出現在沒登入的畫面
  const guestView = useMemo(
    () => (guest ? guestDay(date ?? guestToday ?? '') : null),
    [guest, date, guestToday],
  )
  const view = guest ? guestView : day

  // K 線推薦日的圖例：線別名稱；追蹤分頁還沒載過名單時用固定的三條線
  const lineNames = useMemo(
    () => Object.fromEntries((view?.lines ?? GUEST_LINES).map((l) => [l.code, l.name])),
    [view],
  )
  const current = view?.lines.find((l) => l.code === line) ?? view?.lines[0]
  const topPick = current ? topOf(current.picks) : null
  const selected = current?.picks.find((p) => p.sid === sid) ?? null
  // 名單小標「相關新聞」（反向看圖）：新聞在另一個資料庫，名單抓好後另外問；問不到就沒有這個小標
  const [newsCounts, setNewsCounts] = useState<Record<string, NewsCount>>({})
  /** 「相關新聞」小標被點幾次（個股頁收到就捲到相關新聞那一塊；使用者 2026-10-20：「推薦股有新聞，但是點了沒反應」） */
  const [newsFocus, setNewsFocus] = useState(0)
  const pickSids = useMemo(() => [...new Set((view?.lines ?? []).flatMap((l) => l.picks.map((p) => p.sid)))].sort(), [view])
  useEffect(() => {
    if (guest || pickSids.length === 0) return
    let stale = false
    fetchStockNewsCounts(pickSids)
      .then((r) => !stale && setNewsCounts(Object.fromEntries(Object.entries(r.counts).map(([k, v]) => [k.replace(/^tw:/, ''), v]))))
      .catch(() => !stale && setNewsCounts({}))
    return () => {
      stale = true
    }
  }, [guest, pickSids, refresh])
  // 每日名單的日期列（date＝undefined 是最新一天）；past＝正在看過去某天：K 線停在那天、不抓即時價
  const nav = dayNav(dates, date)
  const drop = dropNote(view?.drop, !nav.past)
  const weakSum = current ? weakSummary(current.picks) : null

  const tracked = useMemo(() => new Set((watch?.items ?? []).map((w) => w.sid)), [watch])
  const watchCurrent = watchSel ?? watch?.items[0] ?? null
  const toggleTrack = (s: string, on: boolean) => setTracked(s, on).then(loadWatch)
  // 「我買了」：記名單日、那條線、代號（來源＝pick）；再按一次取消
  const bought = useMemo(() => boughtKeys(trades?.items ?? []), [trades])
  const toggleBuy = (s: string, on: boolean) => {
    if (!view) return
    const req = on
      ? saveTrade({ sid: s, date: view.date, side: 'buy', price: null, qty: null, source: 'pick', line: current?.code ?? null })
      : deleteTrade({ sid: s, date: view.date, side: 'buy' })
    req.then(loadTrades).catch((e: Error) => setNotice(watchError(e)))
  }
  // 追蹤分頁直接打開時還沒載過名單：用固定的線別名稱（同 K 線圖例），不顯示 CORE 這種代號
  const lineName = (code: string | null) => (code ? (lineNames[code] ?? null) : null)
  const addTrade = (t: Omit<Trade, 'created_at'>) => saveTrade(t).then(loadTrades)
  const removeTrade = (t: Trade) => deleteTrade({ sid: t.sid, date: t.date, side: t.side }).then(loadTrades)
  // 存順序：畫面先照新順序排，再一筆一筆依序送出（連拖幾次也不會後送先到）；失敗就顯示原因並重抓伺服器上的順序
  const orderChain = useRef<Promise<void>>(Promise.resolve())
  const reorderWatch = (order: string[]) => {
    setWatch((w) => w && { ...w, items: order.flatMap((sid) => w.items.filter((i) => i.sid === sid)) })
    orderChain.current = orderChain.current
      .then(() => saveWatchOrder(order))
      .then(() => undefined)
      .catch((e: Error) => {
        setWatchErr(watchError(e))
        loadWatch()
      })
  }
  const removeWatch = (sid: string) => {
    setWatch((w) => w && { ...w, items: w.items.filter((i) => i.sid !== sid) })
    setTracked(sid, false).catch((e: Error) => {
      setWatchErr(watchError(e))
      loadWatch()
    })
  }
  const editMode = editing && !guest && (watch?.items.length ?? 0) > 0
  // 按 🔔：不能開的狀態只顯示說明；能開的就開／關。開的時候第一步就要權限（iPhone 要在按下的當下要）
  const onBell = () => {
    if (!push) return
    if (push === 'needs-install' || push === 'unsupported' || push === 'denied') return setNotice(PUSH_HINT[push])
    const was = push
    ;(was === 'on' ? disablePush() : enablePush())
      .then((next) => {
        setPush(next)
        setNotice(was === 'on' ? '已關閉通知。' : PUSH_HINT[next])
      })
      .catch((e: Error) => setNotice(`通知設定失敗：${watchError(e)}`))
  }
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(''), 8000)
    return () => clearTimeout(t)
  }, [notice])
  useEffect(() => registerServiceWorker(), [])
  // 追蹤清單的盤中價格（編輯時不抓）
  const watchSids = useMemo(() => (watch?.items ?? []).map((w) => w.sid), [watch])
  const watchLive = useLiveQuotes(watchSids, tab === 'watch' && !guest && !editMode)
  const today = taipeiNow().date
  // 每日名單在盤中也跟著即時價（三條線的股票一起抓，切線不用等）；看過去某天的名單不抓
  const listSids = useMemo(() => [...new Set((day?.lines ?? []).flatMap((l) => l.picks.map((p) => p.sid)))], [day])
  const listLive = useLiveQuotes(listSids, tab === 'picks' && !guest && !nav.past)
  const rowPx = (p: Pick) => pickPrice(p, view?.date, listLive.quotes[p.sid], today, nav.past)
  const same = useMemo(() => sameGroups(current?.picks ?? []), [current])
  // 族群分頁標出「在名單上」：名單那天每個族群有哪些股票（三條線合起來）
  const listedGroups = useMemo(() => {
    if (!view) return null
    const sids = new Map<string, string[]>()
    for (const l of view.lines) for (const p of l.picks) if (p.group) sids.set(p.group.name, [...(sids.get(p.group.name) ?? []), p.sid])
    return { date: view.date, sids }
  }, [view])
  const anyLive = !guest && !nav.past && (current?.picks ?? []).some((p) => rowPx(p).rec !== null)
  const detailRef = useRef<HTMLElement>(null)

  return (
    <div className="cq">
      <header className="cq-top">
        <div className="cq-brand">
          <img className="cq-logo" src="/icon.svg" alt="" width="26" height="26" />
          twquant
        </div>
        <nav className="cq-tabs">
          <button className={tab === 'home' ? 'on' : ''} onClick={() => setTab('home')}>
            首頁
          </button>
          <button className={tab === 'news' ? 'on' : ''} onClick={() => setTab('news')}>
            新聞
          </button>
          <button className={tab === 'group' ? 'on' : ''} onClick={() => setTab('group')}>
            盤勢
          </button>
          {canPicks && (
            <button className={tab === 'picks' ? 'on' : ''} onClick={() => setTab('picks')}>
              每日名單
            </button>
          )}
          {features.includes('score') && (
            <button className={tab === 'score' ? 'on' : ''} onClick={() => setTab('score')}>
              績效
            </button>
          )}
          {features.includes('futures') && (
            <button className={tab === 'futures' ? 'on' : ''} onClick={() => setTab('futures')}>
              小台模擬
            </button>
          )}
          <button className={tab === 'watch' ? 'on' : ''} onClick={() => setTab('watch')}>
            追蹤
          </button>
          {features.includes('line') && (
            <button className={tab === 'line' ? 'on' : ''} onClick={() => setTab('line')}>
              LINE 群組
            </button>
          )}
        </nav>
        <div className="cq-date">
          {tab === 'watch' && (
            <span>
              {guest ? guestToday : watch?.date}
              <span className="cq-date-suffix"> 評分</span>
            </span>
          )}
        </div>
        {guest ? (
          <div className="cq-user">
            <a className="cq-login" href={LOGIN_PAGE}>
              登入
            </a>
          </div>
        ) : (
          <User email={email} push={push} onBell={onBell} />
        )}
      </header>

      {err && <div className="cq-err">{err}</div>}
      {notice && (
        <div className="cq-notice" role="status">
          <span>{notice}</span>
          <button aria-label="關閉" onClick={() => setNotice('')}>
            ✕
          </button>
        </div>
      )}

      {tab === 'home' ? (
        guest ? (
          <main className="cq-body">
            <aside className="cq-list cq-list-guest">
              <div className="cq-rows">
                <Gate title="登入後看今日戰情" text="開盤前一頁看完：昨晚美股與台指夜盤、昨天大盤漲跌家數與法人進出、重大訊息。" />
              </div>
            </aside>
            <section className="cq-detail" />
          </main>
        ) : (
          <WarRoom
            refresh={refresh}
            features={features}
            signedIn={email !== null}
            watchSids={watchSids}
            onPicks={(code) => {
              if (code) {
                setLine(code)
                savePref(LINE_KEY, code)
              }
              setTab('picks')
            }}
            onNews={() => setTab('news')}
            onScore={(code) => {
              setLine(code)
              savePref(LINE_KEY, code)
              setTab('score')
            }}
            onStock={(sid, name) => {
              setWatchSel({ sid, name })
              setTab('watch')
            }}
          />
        )
      ) : tab === 'news' ? (
        // 新聞公開（T-438／D-210）：沒登入也看得到；點股票會到要登入的追蹤分頁
        <NewsPage
          refresh={refresh}
          guest={guest}
          signedIn={email !== null}
          onStock={(sid, name) => {
            setWatchSel({ sid, name })
            setTab('watch')
          }}
        />
      ) : tab === 'group' ? (
        // 盤勢（T-431／D-209）：族群資金熱力圖從首頁搬來；有名單權限的人，名單上的族群標「名單 N」
        guest ? (
          <main className="cq-body">
            <aside className="cq-list cq-list-guest">
              <div className="cq-rows">
                <Gate title="登入後看族群資金" text="每個族群今天拿到多少資金、漲跌家數，點一個族群看成交金額最大的幾檔。" />
              </div>
            </aside>
            <section className="cq-detail" />
          </main>
        ) : (
          <GroupsPage
            refresh={refresh}
            listed={listedGroups}
            withMood={false}
            onStock={(sid, name) => {
              setWatchSel({ sid, name })
              setTab('watch')
            }}
          />
        )
      ) : !tabAllowed(tab, features) ? (
        // 要權限的分頁（每日名單、績效、LINE 群組）：沒權限、沒登入，或權限還沒讀回來（先空白）
        <main className="cq-body">
          {(guest || accessKnown) && <p className="cq-empty">{lockedText(tab, guest)}</p>}
        </main>
      ) : tab === 'line' ? (
        <LineGroupPage refresh={refresh} />
      ) : tab === 'futures' ? (
        <FuturesPage refresh={refresh} />
      ) : tab === 'score' ? (
        <ScorecardPage
          line={GUEST_LINES.some((l) => l.code === line) ? line : 'CORE'}
          onLine={(code) => {
            setLine(code)
            savePref(LINE_KEY, code)
          }}
          refresh={refresh}
        />
      ) : (
        <main className="cq-body">
          {tab === 'watch' ? (
            <aside className={guest ? 'cq-list cq-list-guest' : 'cq-list'}>
              <StockSearch
                disabled={guest}
                tracked={tracked}
                onPick={(h) => {
                  setWatchSel(h)
                  // 手機上名單在上、個股在下：查到就捲到個股
                  if (window.matchMedia('(max-width: 820px)').matches) detailRef.current?.scrollIntoView({ behavior: 'smooth' })
                }}
              />
              {!guest && watch && watch.items.length > 0 && (
                <div className="cq-watchbar">
                  <span>{editMode ? '按住右邊的 ≡ 上下拖曳排序' : `共 ${watch.items.length} 檔`}</span>
                  <button className="cq-edit-btn" onClick={() => setEditing(!editMode)}>
                    {editMode ? '完成' : '編輯'}
                  </button>
                </div>
              )}
              {!editMode && (
                <div className="cq-head">
                  <span>股票</span>
                  <span>現價</span>
                  <span>漲跌</span>
                </div>
              )}
              <div className="cq-rows">
                {editMode && watch ? (
                  <WatchEditor items={watch.items} onOrder={reorderWatch} onRemove={removeWatch} />
                ) : (
                  <ul className={guest ? 'cq-locked' : undefined} aria-hidden={guest || undefined}>
                    {guest
                      ? guestPicks(6, 20261013).map((p) => <Row key={p.sid} p={p} on={false} onClick={() => undefined} />)
                      : watch?.items.map((w) => (
                          <WatchRow
                            key={w.sid}
                            w={w}
                            live={rowPrice(w, watchLive.quotes[w.sid], today)}
                            limit={quoteLimit(watchLive.quotes[w.sid], today)}
                            on={w.sid === watchCurrent?.sid}
                            onClick={() => setWatchSel(w)}
                          />
                        ))}
                  </ul>
                )}
                {!guest && watchErr && <p className="cq-empty">{watchErr}</p>}
                {!guest && !watchErr && watch && watch.items.length === 0 && (
                  <p className="cq-empty">還沒有追蹤股。在上面輸入股號或名稱，查到後按「＋ 追蹤」。</p>
                )}
                {guest && <Gate title="登入後查個股評分" text="輸入任何股號，看三個模型怎麼評它，並存成自己的追蹤股。" />}
              </div>
              <details className="cq-note cq-fold">
                <summary>說明</summary>
                追蹤股存在你的帳號，手機和電腦看到同一份，最多 {watch?.max ?? 100} 檔。評分每個交易日收盤後更新；盤中價格來自證交所，約延遲 5 秒。藍色小標＝今天起 10 天內的法說會、除權息、月營收待公布（滑過或長按看細節）。非投資建議。
              </details>
              {!guest && <TradesPanel trades={trades} err={tradesErr} lineName={lineName} onAdd={addTrade} onDelete={removeTrade} />}
            </aside>
          ) : (
            <aside className={guest ? 'cq-list cq-list-guest' : 'cq-list'}>
              <div className="cq-day">
                <button aria-label="前一個交易日" disabled={nav.older === null} onClick={() => nav.older && setDate(chooseDay(dates, nav.older))}>
                  ‹
                </button>
                <select aria-label="名單日期" value={nav.shown ?? ''} onChange={(e) => setDate(chooseDay(dates, e.target.value))}>
                  {dates.map((d) => (
                    <option key={d} value={d}>
                      {dateOption(d, dayCount(dateCounts, d, current?.code ?? line), d === dates[0])}
                    </option>
                  ))}
                </select>
                <button aria-label="後一個交易日" disabled={nav.newer === null} onClick={() => nav.newer && setDate(chooseDay(dates, nav.newer))}>
                  ›
                </button>
                {nav.past && (
                  <button className="cq-day-latest" onClick={() => setDate(undefined)}>
                    回到最新
                  </button>
                )}
              </div>
              {drop && (
                <details className="cq-drop">
                  <summary>{drop.title}</summary>
                  <p>{drop.body}</p>
                </details>
              )}
              <div className="cq-seg">
                {view?.lines.map((l) => (
                  <button
                    key={l.code}
                    className={l.code === current?.code ? 'on' : ''}
                    onClick={() => {
                      setLine(l.code)
                      savePref(LINE_KEY, l.code)
                      setSid(l.picks[0]?.sid ?? '')
                    }}
                  >
                    {l.name}
                    <em>{l.picks.length}</em>
                  </button>
                ))}
              </div>
              {same.length > 0 && (
                <div className="cq-same" title="今天這條線的名單裡，同一個族群有兩檔以上">
                  <span>同族群</span>
                  {same.map((g) => (
                    <b key={g.name} className={g.flow?.hot ? 'hot' : undefined} title={g.flow ? flowText(g.flow) : undefined}>
                      {g.name} ×{g.count}
                      {g.flow?.hot && g.flow.ratio !== null && <em>資金 {g.flow.ratio.toFixed(1)} 倍</em>}
                    </b>
                  ))}
                </div>
              )}
              {weakSum && (
                <p className="cq-weak-sum" title="已還原除權息。只是提醒，名單照模型排名產生，不因此刪減。">
                  {weakSum}
                </p>
              )}
              {!guest && topPick?.review && (
                <TopPickCard sid={topPick.sid} name={topPick.name} r={topPick.review} on={topPick.sid === sid} onClick={() => setSid(topPick.sid)} />
              )}
              {!guest && current && current.picks.length > 0 && !current.picks.some((p) => p.review) && nav.shown === today && (
                <p className="cq-rv-eta">評分約 21:45 更新（籌碼要等當天的融資券與分點公布後才算，D-211）</p>
              )}
              <div className="cq-head">
                <span>
                  股票
                  <button className="cq-legend-btn" onClick={() => setLegend(!legend)} aria-expanded={legend}>
                    <Info size={13} aria-hidden="true" /> 圖示說明
                  </button>
                </span>
                <span>{anyLive ? '現價' : '收盤'}</span>
                <span>漲跌</span>
              </div>
              {legend && <BadgeLegend onClose={() => setLegend(false)} />}
              <div className="cq-rows">
                <ul className={guest ? 'cq-locked' : undefined} aria-hidden={guest || undefined}>
                  {current?.picks.map((p) => (
                    <Row
                      key={p.sid}
                      p={p}
                      px={rowPx(p)}
                      on={p.sid === sid}
                      onClick={() => setSid(p.sid)}
                      bought={view ? bought.has(`${view.date}|${p.sid}`) : false}
                      onBuy={!guest && view && trades ? toggleBuy : undefined}
                      line={current?.code ?? null}
                      news={newsCounts[p.sid] ?? null}
                      onNews={() => {
                        setSid(p.sid)
                        setNewsFocus((n) => n + 1)
                      }}
                    />
                  ))}
                </ul>
                {current && current.picks.length === 0 && <p className="cq-empty">今天沒有符合條件的股票。</p>}
                {guest && <Gate />}
              </div>
              {current?.note && <p className="cq-note cq-line-note">{current.note}</p>}
              <details className="cq-note cq-fold">
                <summary>說明</summary>
                方格＝近 6 個交易日有沒有上榜（舊→新，空框＝那天沒打分）；6日＝近 6 日漲幅。綠框＝破底提醒、黃框＝處置／除權息等風險，都不影響名單；滑過或長按小標看細節。「＋買」記下你在這天買了這檔。盤中價格每 10 秒更新，小字是推薦當天的收盤。模型每日收盤後產生，非投資建議。
              </details>
            </aside>
          )}

          <section className="cq-detail" ref={detailRef}>
            {guest ? (
              <div className="cq-locked cq-guest-detail" aria-hidden="true">
                <GuestChart />
              </div>
            ) : tab === 'watch' ? (
              watchCurrent ? (
                <Detail
                  sid={watchCurrent.sid}
                  name={watchCurrent.name}
                  showPicks={canPicks}
                  shared={watchSids.includes(watchCurrent.sid) && tab === 'watch' && !guest && !editMode ? watchLive : null}
                  lineNames={lineNames}
                  tracked={tracked.has(watchCurrent.sid)}
                  onTrack={watch ? toggleTrack : undefined}
                  refresh={refresh}
                />
              ) : (
                <p className="cq-empty">輸入股號或名稱，看三個模型的評分。</p>
              )
            ) : selected ? (
              <Detail
                sid={selected.sid}
                name={selected.name}
                showPicks={canPicks}
                group={selected.group}
                reasons={selected.reasons ?? null}
                line={current?.code ?? null}
                reference={current?.ref ?? null}
                score={selected.score}
                similar={selected.similar ?? null}
                shared={listSids.includes(selected.sid) && tab === 'picks' && !guest && !nav.past ? listLive : null}
                close={selected.close}
                chg={selected.chg}
                asOf={nav.past ? view?.date : undefined}
                lineNames={lineNames}
                tracked={tracked.has(selected.sid)}
                onTrack={watch ? toggleTrack : undefined}
                refresh={refresh}
                badges={pickBadges({ ...selected, line: current?.code ?? null, news: newsCounts[selected.sid] ?? null })}
                newsFocus={newsFocus}
                onNews={() => setNewsFocus((n) => n + 1)}
                blurb={selected.blurb ?? null}
                review={selected.review ?? null}
              />
            ) : null}
          </section>
        </main>
      )}
      {!riskOk && (
        <RiskNotice
          onAgree={() => {
            savePref(RISK_KEY, riskRecord(taipeiNow().date))
            setRiskOk(true)
          }}
        />
      )}
    </div>
  )
}

/** 登入身分與登出（Supabase Auth，T-439）。沒登入時整塊不顯示。 */
function User({ email, push, onBell }: { email: string | null; push: PushState | null; onBell: () => void }) {
  if (!email) return null
  return (
    <div className="cq-user" title={email}>
      {push && (
        <button className={push === 'on' ? 'cq-bell on' : 'cq-bell'} aria-label={PUSH_HINT[push]} title={PUSH_HINT[push]} onClick={onBell}>
          <BellMark on={push === 'on'} />
        </button>
      )}
      <span className="cq-avatar">{email.slice(0, 1).toUpperCase()}</span>
      <span className="cq-email">{email}</span>
      <a
        href="/"
        onClick={(e) => {
          e.preventDefault()
          signOut().finally(() => window.location.assign('/'))
        }}
      >
        登出
      </a>
    </div>
  )
}

function Row({
  p,
  px,
  on,
  onClick,
  bought,
  onBuy,
  line = null,
  news = null,
  onNews,
}: {
  p: Pick
  /** 要顯示的價格（pickPrice）：盤中是即時價、rec＝推薦當天的收盤；沒給（沒登入的假資料）就是名單那天的收盤 */
  px?: ReturnType<typeof pickPrice>
  on: boolean
  onClick: () => void
  /** 這條線的代號（大盤精選不顯示飆漲機率標記） */
  line?: string | null
  /** 「我買了」（T-388）：這檔在這天的名單有沒有被記下來；沒給（沒登入）就不顯示按鈕 */
  bought?: boolean
  onBuy?: (sid: string, on: boolean) => void
  /** 近 3 天相關新聞則數（反向看圖） */
  news?: NewsCount | null
  /** 點「相關新聞」小標：選這一檔並跳到個股頁的相關新聞 */
  onNews?: () => void
}) {
  px ??= pickPrice(p, undefined, undefined, '', true)
  const t = trend(px.chg)
  const h = hits(p.recent ?? [])
  // 2026-10-18 改版：每列兩行——上面代號名稱，下面「近 6 日上榜」方格＋圖示標記（最多 4 個，其餘 +N）。
  // 族群、選股理由、停損機率都搬到個股頁；名單只留掃一眼要知道的。
  const { shown, more } = rowBadges(pickBadges({ ...p, line, news }))
  return (
    <li className={on ? 'on cq-row' : 'cq-row'} onClick={onClick}>
      <div className="cq-name">
        <b>
          <em>{p.sid}</em>
          {p.name ?? ''}
        </b>
        <small className="cq-meta">
          <span className="cq-dots" title={`近 6 個交易日有 ${h.on} 天在名單上（舊→新）`}>
            {(p.recent ?? []).map((x, i) => (
              <i key={i} className={x === null ? 'na' : x ? 'in' : ''} />
            ))}
          </span>
          {p.review && <ScoreBars r={p.review} />}
          {shown.map((b) => (
            <BadgeChip key={b.key} b={b} compact onClick={b.key === 'news' ? onNews : undefined} />
          ))}
          {more > 0 && (
            <span className="cq-b cq-b-more" title="點進個股頁看全部">
              +{more}
            </span>
          )}
        </small>
        {p.review?.conclusion ? (
          <small className="cq-blurb" title={REVIEW_NOTE}>
            <StanceChip s={p.review.stance} split={p.review.split} />
            <span>{p.review.conclusion}</span>
          </small>
        ) : p.blurb && (
          <small className="cq-blurb" title={blurbTitle(p.blurb)}>
            <MessageSquareText size={13} strokeWidth={2.2} aria-hidden="true" />
            <span>{p.blurb.text}</span>
          </small>
        )}
      </div>
      <span className="cq-pxcol">
        <span className={`cq-px ${t}${px.limit ? ` limit-${px.limit}` : ''}`} title={px.limit === 'up' ? '漲停' : px.limit === 'down' ? '跌停' : undefined}>
          {price(px.price)}
        </span>
        {px.rec !== null ? (
          <small className="cq-rec" title="推薦當天的收盤">
            推 {price(px.rec)}
          </small>
        ) : (
          <small className={trend(p.chg6)} title="近 6 日漲幅">
            6日 {pct(p.chg6, 1)}
          </small>
        )}
      </span>
      <span className="cq-chgcol">
        <span className={`cq-chg cq-pill ${t}${px.limit ? ` limit-${px.limit}` : ''}`}>{pct(px.chg)}</span>
        {onBuy && (
          <button
            className={bought ? 'cq-buy on' : 'cq-buy'}
            aria-label={bought ? '取消「我買了」' : '記下我買了'}
            title={bought ? '已記下你在這天買了這檔；再按一次取消' : '記下你在這天買了這檔（追蹤分頁可以補價格張數）'}
            onClick={(e) => {
              e.stopPropagation()
              onBuy(p.sid, !bought)
            }}
          >
            {bought ? <Check size={13} strokeWidth={3} aria-hidden="true" /> : <ShoppingBag size={13} strokeWidth={2.2} aria-hidden="true" />}
            {bought ? '已買' : '買'}
          </button>
        )}
      </span>
    </li>
  )
}

/** 追蹤股一列：最新收盤、漲跌，和三個模型的重點（漲幅排名、在潛伏池才有的飆漲機率、回檔風險高才標）。 */
function WatchRow({
  w,
  live,
  limit = null,
  on,
  onClick,
}: {
  w: WatchItem
  /** 要顯示的價格：盤中是即時價（tag＝盤中／今日收盤），否則是資料庫的收盤 */
  live: { close: number | null; chg: number | null; tag: string | null }
  /** 盤中漲停／跌停（即時報價才判斷；收盤後的清單沒有前收，不判斷） */
  limit?: 'up' | 'down' | null
  on: boolean
  onClick: () => void
}) {
  const t = trend(live.chg)
  // 行事曆（T-431）：法說會、除權息、營收待公布跟名單列同一種小標，最多兩個、其餘 +N
  const { shown, more } = rowBadges(eventBadges(w.events), WATCH_EVENTS_MAX)
  return (
    <li className={on ? 'on cq-row' : 'cq-row'} onClick={onClick}>
      <div className="cq-name">
        <b>
          <em>{w.sid}</em>
          {w.name ?? ''}
        </b>
        <small className="cq-meta">
          {scoreTags(w.models).map((g) => (
            <span key={g.text} className={`cq-b cq-b-compact ${g.tone === 'hot' ? 'cq-b-good' : g.tone === 'warn' ? 'cq-b-warn' : 'cq-b-chip'}`}>
              {g.text}
            </span>
          ))}
          {shown.map((b) => (
            <BadgeChip key={b.key} b={b} compact />
          ))}
          {more > 0 && (
            <span className="cq-b cq-b-more" title="點進個股頁看行事曆">
              +{more}
            </span>
          )}
        </small>
      </div>
      <span className="cq-pxcol">
        <span className={`cq-px ${t}${limit ? ` limit-${limit}` : ''}`}>{price(live.close)}</span>
        {live.tag && <small className="cq-live-tag">{live.tag}</small>}
      </span>
      <span className="cq-chgcol">
        <span className={`cq-chg cq-pill ${t}${limit ? ` limit-${limit}` : ''}`}>{pct(live.chg)}</span>
      </span>
    </li>
  )
}

/** 編輯追蹤股：按住右邊 ≡ 上下拖曳排序（放開才存）、置頂、刪除（按一次變「確定刪除」，再按才刪）。 */
function WatchEditor({
  items,
  onOrder,
  onRemove,
}: {
  items: WatchItem[]
  onOrder: (order: string[]) => void
  onRemove: (sid: string) => void
}) {
  const list = useRef<HTMLUListElement>(null)
  // 拖曳中的暫時順序：放開才交給 onOrder 存
  const [dragOrder, setDragOrder] = useState<string[] | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const sids = items.map((i) => i.sid)
  const shown = (dragOrder ?? sids).flatMap((sid) => items.filter((i) => i.sid === sid))

  const startDrag = (sid: string) => (e: React.PointerEvent) => {
    const box = list.current
    if (!box || !e.isPrimary) return
    e.preventDefault()
    let order = sids
    let y = e.clientY
    let frame = 0
    setConfirm(null)
    setDragging(sid)
    setDragOrder(order)
    // 每一列高度相同（編輯模式只有代號名稱），第 i 個格子永遠是第 i 列，畫面還沒重畫也算得對
    const place = () => {
      const to = rowAt([...box.children].map((li) => li.getBoundingClientRect()), y)
      const from = order.indexOf(sid)
      if (to >= 0 && to !== from) {
        order = moveItem(order, from, to)
        setDragOrder(order)
      }
    }
    // 拖到清單上下緣時自動捲動（手指停著不動也要繼續捲）
    const tick = () => {
      const r = box.getBoundingClientRect()
      const dy = edgeScroll(y, r.top, r.bottom)
      if (dy !== 0 && box.scrollHeight > box.clientHeight) {
        box.scrollTop += dy
        place()
      }
      frame = requestAnimationFrame(tick)
    }
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== e.pointerId) return
      y = ev.clientY
      place()
    }
    const end = (ev: PointerEvent) => {
      if (ev.pointerId !== e.pointerId) return
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      cancelAnimationFrame(frame)
      setDragging(null)
      setDragOrder(null)
      if (order.join(',') !== sids.join(',')) onOrder(order)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
    frame = requestAnimationFrame(tick)
  }

  return (
    <ul ref={list} className="cq-editing">
      {shown.map((w, i) => (
        <li key={w.sid} className={w.sid === dragging ? 'cq-edit-row dragging' : 'cq-edit-row'}>
          <div className="cq-name">
            <b>
              <em>{w.sid}</em>
              {w.name ?? ''}
            </b>
          </div>
          <button className="cq-row-btn" disabled={i === 0} onClick={() => onOrder([w.sid, ...sids.filter((s) => s !== w.sid)])}>
            置頂
          </button>
          {confirm === w.sid ? (
            <button
              className="cq-row-btn danger"
              onClick={() => {
                setConfirm(null)
                onRemove(w.sid)
              }}
            >
              確定刪除
            </button>
          ) : (
            <button className="cq-row-btn" onClick={() => setConfirm(w.sid)}>
              刪除
            </button>
          )}
          <span className="cq-handle" role="button" aria-label={`拖曳 ${w.sid} 調整順序`} onPointerDown={startDrag(w.sid)}>
            ≡
          </span>
        </li>
      ))}
    </ul>
  )
}

/** 輸入股號或名稱：邊打邊找（停 0.2 秒才查），Enter 選第一筆；四碼數字直接查那一檔。 */
function StockSearch({ disabled, tracked, onPick }: { disabled: boolean; tracked: Set<string>; onPick: (h: SearchHit) => void }) {
  const [q, setQ] = useState('')
  const [found, setFound] = useState<{ q: string; hits: SearchHit[] } | null>(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState(false)
  const text = q.trim()
  useEffect(() => {
    if (!text) return setFound(null)
    let stale = false
    const timer = setTimeout(() => {
      searchStocks(text)
        .then((hits) => {
          if (stale) return
          setFound({ q: text, hits })
          setErr('')
        })
        .catch((e: Error) => !stale && setErr(watchError(e)))
    }, 200)
    return () => {
      stale = true
      clearTimeout(timer)
    }
  }, [text])
  const hits = found?.q === text ? found.hits : []
  const pick = (h: SearchHit) => {
    onPick(h)
    setQ('')
    setOpen(false)
  }
  return (
    <div className="cq-search">
      <input
        type="search"
        enterKeyHint="search"
        autoComplete="off"
        placeholder="輸入股號或名稱，例如 2330、台積電"
        value={q}
        disabled={disabled}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') return setQ('')
          if (e.key !== 'Enter') return
          if (hits[0]) pick(hits[0])
          else if (/^[0-9]{4}$/.test(text)) pick({ sid: text, name: null, industry: null })
        }}
      />
      {open && text && (
        <ul className="cq-hitlist">
          {hits.map((h) => (
            // mousedown 先於 blur：不攔的話清單在點到之前就關了
            <li key={h.sid} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(h)}>
              <em>{h.sid}</em>
              <span>{h.name ?? ''}</span>
              <small>{tracked.has(h.sid) ? '已追蹤' : h.industry ?? ''}</small>
            </li>
          ))}
          {err && <li className="cq-hit-none">{err}</li>}
          {!err && found?.q === text && hits.length === 0 && (
            <li className="cq-hit-none">找不到「{text}」：只收上市櫃普通股（ETF 沒有評分）</li>
          )}
        </ul>
      )}
    </div>
  )
}

/** 選股理由（D-180）：加分的在前，扣分與門檻收在「更多」；每條前面一個 ＋／－／・ 圖示。 */
function Factors({ reasons, note }: { reasons: Reason[]; note: string }) {
  const [all, setAll] = useState(false)
  const sorted = [...reasons].sort((a, b) => b.s - a.s)
  const shown = all ? sorted : sorted.slice(0, FACTORS_SHOWN)
  return (
    <Section icon={Lightbulb} title="為什麼選它" meta={<span className="cq-sec-note" title={`${note}・僅供參考，非投資建議`}>{note}</span>}>
      <ul className="cq-reasons">
        {shown.map((r) => {
          const m = mark(r.s)
          const I = r.s > 0 ? CirclePlus : r.s < 0 ? CircleMinus : CircleDot
          return (
            <li key={r.f} className={m.tone} title={m.label}>
              <I size={15} strokeWidth={2.2} aria-hidden="true" />
              <span>{r.t}</span>
            </li>
          )
        })}
      </ul>
      {sorted.length > FACTORS_SHOWN && (
        <button className="cq-more" onClick={() => setAll(!all)}>
          {all ? '收起' : `更多 ${sorted.length - FACTORS_SHOWN} 項`}
          <ChevronDown size={14} className={all ? 'flip' : undefined} aria-hidden="true" />
        </button>
      )}
    </Section>
  )
}

/** 選股理由預設列幾條。 */
const FACTORS_SHOWN = 4

/** 歷史同區間參考（D-179）：這一檔對到的區間在開發期／模型沒看過的期間，抱 20 天的報酬分布。 */
/** 歷史相似情境（D-182）：大盤狀態相近的日子裡，個股條件最像的 k 次，照名單規則買進抱 20 天的分布與最像的幾次。 */
function SimilarBlock({ s }: { s: Similar }) {
  const bar = similarBar(s)
  const span = spanText(s)
  // 2026-10-18 改版：收成一行（預設收起），點開才看分布條與最像的幾次
  return (
    <Section icon={History} title="歷史相似" fold summary={`最像的 ${s.n.toLocaleString()} 次・抱 20 天`}>
      <div className="cq-ref-row" title="比的是模型看的條件（波動、市值、營收、籌碼……）和當時的大盤狀態，只用 2026 年 8 月以前的資料；相似不代表會走一樣的路。">
        <span>{span}</span>
        <p>{similarText(s)}</p>
        {bar && (
          <div className="cq-ref-bar" title="灰色＝最差一成到最好一成的範圍（−30%～+30% 橫軸）；白線＝中位數；細線＝0">
            <i style={{ left: `${bar.p10}%`, width: `${Math.max(0, bar.p90 - bar.p10)}%` }} />
            <em style={{ left: `${bar.zero}%` }} />
            <b style={{ left: `${bar.median}%` }} />
          </div>
        )}
      </div>
      {s.examples.length > 0 && (
        <ul className="cq-sim-ex">
          {s.examples.map((e) => (
            <li key={`${e.d}-${e.sid}`} className={e.r > 0 ? 'up' : e.r < 0 ? 'down' : undefined}>
              {exampleText(e)}
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function Reference({ rows }: { rows: RefRow[] }) {
  if (rows.length === 0) return null
  return (
    <Section icon={History} title="歷史參考" fold summary={`過去${bucketText(rows[0])}・抱 20 天`}>
      {rows.map((r) => {
        const bar = refBar(r)
        return (
          <div className="cq-ref-row" key={r.period} title="分布來自同區間的所有股票，不是針對這一檔；開發期是規則在那段資料上試出來的，數字會偏好看。">
            <span>{r.label.replace(/（.*）/, '')}</span>
            <p>{refText(r)}</p>
            {bar && (
              <div className="cq-ref-bar" title="灰色＝最差一成到最好一成的範圍（−30%～+30% 橫軸）；白線＝中位數；細線＝0">
                <i style={{ left: `${bar.p10}%`, width: `${Math.max(0, bar.p90 - bar.p10)}%` }} />
                <em style={{ left: `${bar.zero}%` }} />
                <b style={{ left: `${bar.median}%` }} />
              </div>
            )}
          </div>
        )
      })}
    </Section>
  )
}

function Detail({
  sid,
  name,
  shared = null,
  group,
  reasons = null,
  line = null,
  reference = null,
  score = null,
  similar = null,
  close,
  chg,
  asOf,
  lineNames,
  showPicks,
  tracked,
  onTrack,
  refresh,
  badges = [],
  blurb = null,
  review = null,
  newsFocus = 0,
  onNews,
}: {
  sid: string
  name: string | null
  /** 清單（名單或追蹤股）已經在輪詢這一檔的即時報價：直接用，不另外再問一次（證交所會擋太密的請求） */
  shared?: LiveState | null
  /** 名單上帶來的族群（先頂著）；沒有就用評分 API 回來的 */
  group?: Group | null
  /** 選股依據（D-180）：名單上這一檔的白話理由與它在哪條線；不是從名單點進來（追蹤分頁）就沒有 */
  reasons?: Reason[] | null
  line?: string | null
  /** 這條線的歷史同區間參考（D-179）與這一檔的分數：過去同區間的股票抱 20 天的報酬分布；沒算就不顯示 */
  reference?: RefRow[] | null
  score?: number | null
  /** 歷史相似情境（D-182）：潛伏兩條線的這一檔，大盤相近、條件最像的情況之後 20 天；有它就不顯示同區間參考 */
  similar?: Similar | null
  /** 名單上的收盤與漲跌：看過去某天時顯示那天的；其他時候只在 K 線還沒到時先頂著 */
  close?: number | null
  chg?: number | null
  /** 看過去某天的名單時的日期：報價列停在那天，不顯示即時價 */
  asOf?: string
  lineNames: Record<string, string>
  /** 有名單權限（T-430）：K 線標推薦日；沒有就不問被推薦日、圖例也不列線別 */
  showPicks: boolean
  tracked: boolean
  onTrack?: (sid: string, on: boolean) => Promise<void>
  /** 從背景回來時加一：重抓 K 線、被推薦日與評分 */
  refresh: number
  /** 名單上這一檔的全部標記（處置、除權息、模型易跌…）；不是從名單點進來就沒有 */
  badges?: Badge[]
  /** 名單上這一檔的一句話（T-393）；不是從名單點進來就沒有 */
  blurb?: Blurb | null
  /** 名單上這一檔的四面向評分（T-394）；不是從名單點進來就沒有 */
  review?: Review | null
  /** 「相關新聞」小標被點的次數（變了就捲到相關新聞） */
  newsFocus?: number
  onNews?: () => void
}) {
  // K 線資料記著是哪一檔：換股時舊圖先留著（不閃白），但報價列只用這一檔自己的資料
  const [chart, setChart] = useState<{ sid: string; bars: Bar[]; days: Record<string, string[]> }>({ sid: '', bars: [], days: {} })
  const [scores, setScores] = useState<StockScores | null>(null)
  const [scoreErr, setScoreErr] = useState('')
  const [err, setErr] = useState('')
  // K 線上的新聞標記（T-419）：哪幾天有直接提到這檔的新聞；點了哪一天
  const [newsDays, setNewsDays] = useState<{ sid: string; days: StockNewsDay[] }>({ sid: '', days: [] })
  const [newsDay, setNewsDay] = useState<string | null>(null)
  useEffect(() => {
    let stale = false
    setNewsDay(null)
    fetchStockNewsDays(sid)
      .then((d) => !stale && setNewsDays({ sid, days: d.days }))
      .catch(() => !stale && setNewsDays({ sid, days: [] })) // 新聞服務沒設定或連不上：沒有標記，K 線照常
    return () => {
      stale = true
    }
  }, [sid, refresh])
  useEffect(() => {
    // stale：快速切換時，晚到的上一檔回應不能蓋到這一檔
    let stale = false
    setErr('')
    setScoreErr('')
    // 換股時分數靠 scores.sid 判斷（不先清空，重抓同一檔時不會閃「計算中」）；K 線內容沒變就不換，免得整張圖重畫
    Promise.all([fetchBars(sid), showPicks ? fetchStockPicks(sid) : Promise.resolve<Record<string, string[]>>({})])
      .then(([bars, days]) => {
        if (stale) return
        setChart((prev) =>
          prev.sid === sid && sameBars(prev.bars, bars) && JSON.stringify(prev.days) === JSON.stringify(days)
            ? prev
            : { sid, bars, days },
        )
      })
      .catch((e: Error) => !stale && setErr(e.message))
    fetchScores(sid)
      .then((s) => !stale && setScores(s))
      .catch((e: Error) => !stale && setScoreErr(e.message))
    return () => {
      stale = true
    }
  }, [sid, refresh, showPicks])
  const bars = chart.sid === sid ? chart.bars : []
  // 分數同理：換股後到新分數回來前，不拿上一檔的分數與名稱
  const sc = scores?.sid === sid ? scores : null
  // 盤中：今天到目前為止的那根接在 K 線最後（資料庫傍晚才有今天）
  const today = taipeiNow().date
  const own = useLiveQuotes([sid], shared === null)
  const live = shared ?? own
  const quote = live.quotes[sid]
  const todayBar = liveBar(bars, quote, today)
  const all = todayBar ? [...bars, todayBar] : bars
  // 報價列：看過去某天的名單時停在那天（選 9/24 就顯示 9/24）；其他時候是最後一根（盤中就是即時那根）
  const q = quoteBar(all, asOf, null)
  const bigClose = asOf ? (close ?? q?.bar.close ?? null) : (q?.bar.close ?? close ?? null)
  const bigChg = asOf ? (chg ?? q?.chg ?? null) : (q?.chg ?? chg ?? null)
  const label = asOf ? null : todayBar ? liveLabel(quote, today) : quote?.date === today && quote.last === null ? '尚未成交' : null
  const t = trend(bigChg)
  const grp = group ?? sc?.group ?? null
  const gl = grp ? groupLine(grp) : null
  // 漲停／跌停加框：盤中用證交所給的漲跌停價，其他時候由那根 K 棒的前一天收盤推
  const prevClose = !asOf && todayBar ? (quote?.prev ?? null) : q && q.diff !== null ? q.bar.close - q.diff : null
  const lim = limitPrices(prevClose)
  const bigLimit =
    !asOf && todayBar && quote
      ? limitState(bigClose, quote.limit_up ?? lim.up, quote.limit_down ?? lim.down)
      : limitState(bigClose, lim.up, lim.down)
  return (
    <>
      {/* 2026-10-18 改版：價格區（名稱、族群、大價格、開高低量）→ 標記 → 評分 → 理由 → 籌碼與風險 → 事件 → 歷史（收起）→ K 線 */}
      <header className="cq-hero">
        <div className="cq-hero-top">
          <h1>
            <small>{sid}</small>
            {name ?? sc?.name ?? ''}
          </h1>
          {onTrack && <TrackButton sid={sid} on={tracked} onTrack={onTrack} />}
        </div>
        {grp && gl && (
          <div className="cq-hero-group">
            <span className={`cq-b cq-b-chip${grp.official ? ' official' : ''}`} title={groupTitle(grp)}>
              <Layers size={13} aria-hidden="true" />
              <span className="cq-b-label">{gl.main}</span>
            </span>
            {grp.flow && grp.flow.ratio !== null && (
              <span className={`cq-b ${grp.flow.hot ? 'cq-b-good' : 'cq-b-chip'}`} title={`${grp.name}・${grp.flow.date}：${flowText(grp.flow)}`}>
                <Banknote size={13} aria-hidden="true" />
                <span className="cq-b-label">族群資金</span>
                <span className="cq-b-short">{grp.flow.ratio.toFixed(1)}倍</span>
              </span>
            )}
          </div>
        )}
        <div className={`cq-hero-price ${t}`}>
          <b className={bigLimit ? `cq-limit ${bigLimit}` : undefined} title={bigLimit === 'up' ? '漲停' : bigLimit === 'down' ? '跌停' : undefined}>
            {price(bigClose)}
          </b>
          <span className={`cq-pill ${t}`}>
            {bigChg !== null && bigChg > 0 ? <ArrowUp size={14} strokeWidth={3} aria-hidden="true" /> : bigChg !== null && bigChg < 0 ? <ArrowDown size={14} strokeWidth={3} aria-hidden="true" /> : null}
            {pct(bigChg)}
          </span>
          {label && (
            <em className="cq-live" title="證交所即時報價，約延遲 5 秒；盤中每 10 秒更新">
              {label}
            </em>
          )}
          {!asOf && (live.error || live.stale) && shouldPoll(taipeiNow(), []) && (
            <em className="cq-live off" title={live.error || undefined}>
              {quote ? '即時報價暫停（限流）' : '即時報價取不到'}
            </em>
          )}
        </div>
        {q && (
          <dl className="cq-hero-ohlc" title={q.bar.date}>
            <div>
              <dt>開</dt>
              <dd>{price(q.bar.open)}</dd>
            </div>
            <div>
              <dt>高</dt>
              <dd className="up">{price(q.bar.high)}</dd>
            </div>
            <div>
              <dt>低</dt>
              <dd className="down">{price(q.bar.low)}</dd>
            </div>
            <div>
              <dt>量</dt>
              <dd>{Math.round(q.bar.volume / 1000).toLocaleString()}張</dd>
            </div>
          </dl>
        )}
      </header>
      {review ? (
        <Section icon={Gauge} title="AI 分析師觀點" meta={`共 ${review.total}／${review.max * DIMS.length}`}>
          <ReviewTiles r={review} />
        </Section>
      ) : blurb && (
        <p className={`cq-blurb-full${blurb.ai ? ' ai' : ''}`} title={blurbTitle(blurb)}>
          <MessageSquareText size={16} strokeWidth={2.2} aria-hidden="true" />
          <span>{blurb.text}</span>
          <em>{blurb.ai ? 'AI' : '規則'}</em>
        </p>
      )}
      {badges.length > 0 && (
        <div className="cq-badges" aria-label="提醒">
          {badges.map((b) => (
            <BadgeChip key={b.key} b={b} onClick={b.key === 'news' ? onNews : undefined} />
          ))}
        </div>
      )}
      <ModelPanel scores={sc} err={scoreErr} reasons={reasons} line={line} />
      <StockFacts sid={sid} refresh={refresh} />
      <StockCalendar sid={sid} refresh={refresh} />
      <StockNews sid={sid} refresh={refresh} focus={newsFocus} />
      {similar ? <SimilarBlock s={similar} /> : <Reference rows={refFor(reference, score)} />}
      <Section
        icon={CandlestickChart}
        title="K 線"
        meta={
          <span className="cq-chart-legend">
            {MA_LEGEND.map((m) => (
              <span key={m.n} style={{ color: m.color }}>
                MA{m.n}
              </span>
            ))}
            {showPicks &&
              Object.entries(lineNames).map(([code, n]) => (
                <b key={code} style={{ color: LINE_LEGEND[code] ?? '#FF7A00' }} title={`▼＝${n}推薦日`}>
                  ▼{n}
                </b>
              ))}
            {newsDays.sid === sid && newsDays.days.length > 0 && (
              <b style={{ color: '#79B8FF' }} title="K 棒下方的藍點＝那天有直接提到這檔的新聞；點那一天列出來">
                ●新聞
              </b>
            )}
          </span>
        }
      >
        {err ? (
          <div className="cq-err">{err}</div>
        ) : (
          <KChart
            bars={chart.bars}
            live={chart.sid === sid ? todayBar : null}
            pickDays={chart.days}
            lineNames={lineNames}
            newsDays={newsDays.sid === sid ? newsMarkers(newsDays.days, chart.bars.map((b) => b.date)) : []}
            onDayClick={setNewsDay}
          />
        )}
        {newsDay && newsDays.sid === sid && <DayNews day={newsDays.days.find((d) => d.day === newsDay) ?? null} onClose={() => setNewsDay(null)} />}
      </Section>
    </>
  )
}

/** K 線上點了某一天：那天直接提到這檔的新聞（T-419）。 */
function DayNews({ day, onClose }: { day: StockNewsDay | null; onClose: () => void }) {
  if (!day) return null
  const d = new Date(`${day.day}T00:00:00+08:00`)
  return (
    <div className="cq-daynews">
      <div className="cq-daynews-h">
        <b>
          {d.getMonth() + 1}/{d.getDate()} 的新聞・{day.n} 則
        </b>
        <small>{day.outlets.join('・')}</small>
        <button onClick={onClose} aria-label="關閉">
          ×
        </button>
      </div>
      <ul>
        {day.items.map((it) => (
          <li key={it.id}>
            <a href={it.url} target="_blank" rel="noopener noreferrer">
              {it.title}
            </a>
            <small>{it.src}</small>
          </li>
        ))}
        {day.n > day.items.length && <li className="cq-daynews-more">還有 {day.n - day.items.length} 則，在下面「相關新聞」或新聞分頁看</li>}
      </ul>
    </div>
  )
}

/** 盤中每 10 秒抓一次即時量價（畫面在背景、或不在盤中時不抓；打開時先抓一次）。代號換了就重抓。 */
type LiveState = { quotes: Record<string, Quote>; error: string; stale: boolean }

function useLiveQuotes(sids: string[], enabled: boolean): LiveState {
  // 排序後當鍵：追蹤清單只是換順序時不用重抓
  const key = enabled ? [...sids].sort().join(',') : ''
  const [state, setState] = useState<LiveState & { key: string }>({ key: '', quotes: {}, error: '', stale: false })
  useEffect(() => {
    if (!key) return
    let stale = false
    let dates: string[] = []
    // 證交所會擋太密的請求：失敗後先停一陣子（30 秒起加倍、最多 5 分鐘），不要每 10 秒一直打
    let failures = 0
    let pauseUntil = 0
    const load = () =>
      fetchQuotes(key.split(','))
        .then((r) => {
          if (stale) return
          dates = r.quotes.map((x) => x.date)
          failures = 0
          pauseUntil = r.retry_after ? Date.now() + r.retry_after * 1000 : 0
          setState({ key, quotes: Object.fromEntries(r.quotes.map((x) => [x.sid, x])), error: '', stale: Boolean(r.stale) })
        })
        // 抓不到就沿用資料庫的收盤價，畫面標「即時報價暫時取不到」，等一陣子再試
        .catch((e: Error) => {
          if (stale) return
          failures += 1
          pauseUntil = Date.now() + liveBackoff(failures, null)
          setState((s) => ({ key, quotes: s.key === key ? s.quotes : {}, error: e.message, stale: s.key === key && Object.keys(s.quotes).length > 0 }))
        })
    const tick = () => {
      if (document.visibilityState === 'visible' && Date.now() >= pauseUntil && shouldPoll(taipeiNow(), dates)) load()
    }
    load()
    const timer = setInterval(tick, LIVE_POLL_MS)
    const onShow = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onShow)
    return () => {
      stale = true
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onShow)
    }
  }, [key])
  return state.key === key ? state : { quotes: {}, error: '', stale: false }
}

/** 加入／移除追蹤股。送出中停用，失敗把原因寫在按鈕旁。 */
function TrackButton({ sid, on, onTrack }: { sid: string; on: boolean; onTrack: (sid: string, on: boolean) => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  return (
    <>
      <button
        className={on ? 'cq-track on' : 'cq-track'}
        disabled={busy}
        onClick={() => {
          setBusy(true)
          setErr('')
          onTrack(sid, !on)
            .catch((e: Error) => setErr(watchError(e)))
            .finally(() => setBusy(false))
        }}
      >
        {on ? '✓ 已追蹤' : '＋ 追蹤'}
      </button>
      {err && <span className="cq-track-err">{err}</span>}
    </>
  )
}

/** 三個模型的評分卡：大字＋一行說明＋近幾日走勢；「這些分數是什麼」展開說明。 */
function ModelPanel({
  scores,
  err,
  reasons = null,
  line = null,
}: {
  scores: StockScores | null
  err: string
  /** 選股依據（D-180）：有的話掛在選出這檔的模型卡片裡，那張卡片放大、排第一 */
  reasons?: Reason[] | null
  line?: string | null
}) {
  const [help, setHelp] = useState(false)
  if (err) return <div className="cq-sec cq-models-msg">模型評分：{err}</div>
  if (!scores) return <div className="cq-sec cq-models-msg">模型評分計算中…</div>
  const views = scores.models.map((m) => ({ m, v: modelView(m) }))
  const date = scores.models.map((m) => m.date).find((d) => d !== null) ?? null
  const primaryCode = primaryModel(line, reasons)
  // 2026-10-18 改版：三張數字卡並排（標題、大數字、小折線），細節在滑過與「?」；選出這檔的模型那張加框。
  const tile = ({ m, v }: (typeof views)[number]) => (
    <Kpi
      key={m.code}
      label={v.title}
      value={v.big}
      tone={v.tone === 'hot' ? 'hot' : v.tone === 'warn' ? 'warn' : v.tone === 'na' ? 'na' : 'plain'}
      side={<Spark points={sparkPoints(m)} />}
      on={m.code === primaryCode}
      title={`${v.sub}${m.date !== null && date !== null && m.date !== date ? `（${m.date}）` : ''}——${v.what}`}
    />
  )
  return (
    <>
      <Section
        icon={Gauge}
        title="模型評分"
        meta={
          <>
            {scores.lists.map((l) => (
              <span key={l.line} className="cq-b cq-b-good" title={`${l.date} 的名單`}>
                <Trophy size={13} aria-hidden="true" />
                <span className="cq-b-label">{l.name}</span>
                <span className="cq-b-short">#{l.rank}</span>
              </span>
            ))}
            <button className="cq-icon-btn" aria-label="這些分數是什麼" aria-expanded={help} onClick={() => setHelp(!help)}>
              <CircleHelp size={17} aria-hidden="true" />
            </button>
          </>
        }
      >
        <div className="cq-kpis cq-kpis-3">{views.map(tile)}</div>
        {help && (
          <ul className="cq-models-help">
            {views.map(({ m, v }) => (
              <li key={m.code}>
                <b>{v.title}</b>：{v.what}
              </li>
            ))}
            <li>小折線是最近幾個交易日的變化，越往上越好。{date ? `${date} 收盤後算的。` : ''}分數是模型的估計，不保證會發生。</li>
          </ul>
        )}
      </Section>
      {primaryCode && reasons && <Factors reasons={cardReasons(reasons, primaryCode)} note={factorsNote(primaryCode)} />}
    </>
  )
}

/** 近幾日走勢的小折線（舊到新，越高越好）；少於兩天不畫。 */
function Spark({ points }: { points: { date: string; value: number }[] }) {
  if (points.length < 2) return null
  const W = 56
  const H = 20
  const vs = points.map((p) => p.value)
  const lo = Math.min(...vs)
  const hi = Math.max(...vs)
  const x = (i: number) => (i / (points.length - 1)) * (W - 4) + 2
  const y = (v: number) => (hi === lo ? H / 2 : H - 2 - ((v - lo) / (hi - lo)) * (H - 4))
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join('')
  const last = points[points.length - 1]
  return (
    <svg className="cq-spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden="true">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx={x(points.length - 1)} cy={y(last.value)} r="2" fill="currentColor" />
    </svg>
  )
}

/** 沒登入：蓋在模糊名單上的登入卡片。股票列與 K 線是假資料，真資料只從 /api 給已登入的人。 */
function Gate({
  title = '登入後查看名單',
  text = '每個交易日收盤後，AI 模型挑出的大盤精選與潛伏飆股。',
}: {
  title?: string
  text?: string
}) {
  return (
    <div className="cq-gate">
      <div className="cq-gate-card">
        <img src="/icon-192.png" alt="" width="52" height="52" />
        <h2>{title}</h2>
        <p>{text}</p>
        <a className="cq-google" href={LOGIN_PAGE}>
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

function BellMark({ on }: { on: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill={on ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
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
