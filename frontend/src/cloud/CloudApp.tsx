// 雲端版網站（T-332／D-170）：每日推薦、歷史名單、追蹤股（T-345）與個股 K 線。介面仿富途牛牛：
// 深色底、橘色強調、左側自選股式名單、右側 K 線。漲紅跌綠（台股習慣）。
import { useEffect, useMemo, useRef, useState } from 'react'
import './cloud.css'
import {
  fetchBars,
  fetchMe,
  fetchPickDates,
  fetchPicks,
  fetchPublicDates,
  fetchQuotes,
  fetchScores,
  fetchStockPicks,
  fetchWatchlist,
  logoutTarget,
  saveWatchOrder,
  searchStocks,
  setTracked,
} from './api'
import { GUEST_LINES, LOGIN_URL, LoginRequired, guestCandles, guestDay, guestPicks } from './auth'
import { LINE_KEY, loadPref, savePref } from './prefs'
import { KChart, LINE_LEGEND, MA_LEGEND } from './KChart'
import { dateOption, dayCount, hits, pct, price, quoteBar, trend, type Bar, type DateCounts, type Pick, type PicksDay } from './format'
import { LIVE_POLL_MS, liveBar, liveLabel, rowPrice, shouldPoll, taipeiNow, type Quote } from './live'
import { edgeScroll, moveItem, rowAt } from './reorder'
import { modelView, scoreTags, sparkPoints, type SearchHit, type StockScores, type WatchItem, type WatchList } from './scores'

type Tab = 'today' | 'history' | 'watch'

// 追蹤股 API 的身分驗不過時（401）不切成沒登入的預覽：名單照常看，只有追蹤股區塊顯示這句
const WATCH_RELOGIN = '登入身分驗證失敗，請登出再登入一次'
const watchError = (e: Error) => (e instanceof LoginRequired ? WATCH_RELOGIN : e.message)

export default function CloudApp() {
  const [tab, setTab] = useState<Tab>('today')
  const [dates, setDates] = useState<string[]>([])
  // 日期選單旁的檔數（只有登入後的 /api 才給；公開日期清單沒有）
  const [dateCounts, setDateCounts] = useState<DateCounts | null>(null)
  const [date, setDate] = useState<string | undefined>(undefined)
  const [day, setDay] = useState<PicksDay | null>(null)
  // 上次看的策略（大盤精選／潛伏飆股）：重新整理、重開都停在那一條
  const [line, setLine] = useState<string>(() => loadPref(LINE_KEY) ?? '')
  const [sid, setSid] = useState<string>('')
  const [err, setErr] = useState<string>('')
  const [guest, setGuest] = useState(false)
  const [email, setEmail] = useState<string | null>(null)
  // 追蹤股：存在帳號裡（伺服器），手機和電腦同一份
  const [watch, setWatch] = useState<WatchList | null>(null)
  const [watchErr, setWatchErr] = useState('')
  // 追蹤分頁正在看的股票：清單裡的，或剛搜尋到、還沒加入的
  const [watchSel, setWatchSel] = useState<{ sid: string; name: string | null } | null>(null)
  // 追蹤清單的編輯模式（拖曳排序、置頂、刪除）
  const [editing, setEditing] = useState(false)
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

  useEffect(() => {
    // 身分只用來顯示頭像；本機開發的後端沒有 /api/me，那時不顯示即可。確定登入了才抓追蹤股
    fetchMe()
      .then((m) => {
        setEmail(m.email)
        loadWatch()
      })
      .catch((e: Error) => e instanceof LoginRequired && setGuest(true))
    fetchPickDates()
      .then((r) => {
        setDates(r.dates)
        setDateCounts(r.counts)
      })
      .catch((e: Error) => {
        if (!(e instanceof LoginRequired)) return setErr(e.message)
        setGuest(true)
        // 日期是公開資訊：沒登入也能切到歷史名單選日期
        fetchPublicDates().then(setDates).catch(onFail)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 從背景回到前景時重新確認登入：登入 24 小時就過期，iPhone 主畫面 App 常常隔天才回來，
  // 畫面卻還停在登入狀態（頭像、登出、舊名單）。過期就換成沒登入的畫面，不留舊資料
  useEffect(() => {
    if (guest) return
    const onShow = () => {
      if (document.visibilityState !== 'visible') return
      fetchMe().catch((e: Error) => {
        if (e instanceof LoginRequired) onFail(e)
      })
    }
    document.addEventListener('visibilitychange', onShow)
    return () => document.removeEventListener('visibilitychange', onShow)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guest])

  // 沒登入時「今日」用公開日期清單的最新一天；登入時這個值固定 undefined，不會多抓一次
  const guestToday = guest ? dates[0] : undefined

  // 歷史名單：日期清單晚到時（沒登入要多一趟），一到就選最新一天，否則選單會停在空值
  useEffect(() => {
    if (tab === 'history' && !date && dates[0]) setDate(dates[0])
  }, [tab, date, dates])

  useEffect(() => {
    // 追蹤分頁不看名單：留著上一份（K 線的推薦日圖例要用線別名稱）
    if (guest || tab === 'watch') return
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
    () => (guest ? guestDay((tab === 'history' ? date : undefined) ?? guestToday ?? '') : null),
    [guest, tab, date, guestToday],
  )
  const view = guest ? guestView : day

  // K 線推薦日的圖例：線別名稱；追蹤分頁還沒載過名單時用固定的三條線
  const lineNames = useMemo(
    () => Object.fromEntries((view?.lines ?? GUEST_LINES).map((l) => [l.code, l.name])),
    [view],
  )
  const current = view?.lines.find((l) => l.code === line) ?? view?.lines[0]
  const selected = current?.picks.find((p) => p.sid === sid) ?? null
  const idx = date ? dates.indexOf(date) : -1

  const tracked = useMemo(() => new Set((watch?.items ?? []).map((w) => w.sid)), [watch])
  const watchCurrent = watchSel ?? watch?.items[0] ?? null
  const toggleTrack = (s: string, on: boolean) => setTracked(s, on).then(loadWatch)
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
  // 追蹤清單的盤中價格（編輯時不抓）
  const watchSids = useMemo(() => (watch?.items ?? []).map((w) => w.sid), [watch])
  const watchLive = useLiveQuotes(watchSids, tab === 'watch' && !guest && !editMode)
  const today = taipeiNow().date
  const detailRef = useRef<HTMLElement>(null)

  return (
    <div className="cq">
      {/* 歷史名單的日期選單在手機上放不進分頁那一列：另起一列（cloud.css .cq-top-hist） */}
      <header className={tab === 'history' ? 'cq-top cq-top-hist' : 'cq-top'}>
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
          <button className={tab === 'watch' ? 'on' : ''} onClick={() => setTab('watch')}>
            追蹤
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
                    {dateOption(d, dayCount(dateCounts, d, current?.code ?? line))}
                  </option>
                ))}
              </select>
              <button disabled={idx <= 0} onClick={() => setDate(dates[idx - 1])}>
                ›
              </button>
            </>
          ) : tab === 'watch' ? (
            <span>
              {guest ? guestToday : watch?.date}
              <span className="cq-date-suffix"> 評分</span>
            </span>
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
                <span>代號／名稱／模型評分</span>
                <span>股價</span>
                <span>漲跌幅</span>
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
            <p className="cq-note">
              追蹤股存在你的帳號，手機和電腦看到同一份，最多 {watch?.max ?? 100} 檔。評分每個交易日收盤後更新；盤中價格來自證交所，約延遲 5 秒。非投資建議。
            </p>
          </aside>
        ) : (
          <aside className={guest ? 'cq-list cq-list-guest' : 'cq-list'}>
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
              {current && current.picks.length === 0 && <p className="cq-empty">今天沒有符合條件的股票。</p>}
              {guest && <Gate />}
            </div>
            {current?.note && <p className="cq-note cq-line-note">{current.note}</p>}
            <p className="cq-note">
              方格＝近 6 個交易日有沒有上榜（舊→新，空框＝那天沒打分）；6日＝近 6 日漲幅。模型每日收盤後產生，非投資建議。
            </p>
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
                lineNames={lineNames}
                tracked={tracked.has(watchCurrent.sid)}
                onTrack={watch ? toggleTrack : undefined}
              />
            ) : (
              <p className="cq-empty">輸入股號或名稱，看三個模型的評分。</p>
            )
          ) : selected ? (
            <Detail
              sid={selected.sid}
              name={selected.name}
              close={selected.close}
              chg={selected.chg}
              asOf={tab === 'history' ? view?.date : undefined}
              lineNames={lineNames}
              tracked={tracked.has(selected.sid)}
              onTrack={watch ? toggleTrack : undefined}
            />
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

/** 追蹤股一列：最新收盤、漲跌，和三個模型的重點（漲幅排名、在潛伏池才有的飆漲機率、回檔風險高才標）。 */
function WatchRow({
  w,
  live,
  on,
  onClick,
}: {
  w: WatchItem
  /** 要顯示的價格：盤中是即時價（tag＝盤中／今日收盤），否則是資料庫的收盤 */
  live: { close: number | null; chg: number | null; tag: string | null }
  on: boolean
  onClick: () => void
}) {
  const t = trend(live.chg)
  return (
    <li className={on ? 'on' : ''} onClick={onClick}>
      <div className="cq-name">
        <b>
          <em>{w.sid}</em>
          {w.name ?? ''}
        </b>
        <small>
          {scoreTags(w.models).map((g) => (
            <i key={g.text} className={`cq-tag ${g.tone}`}>
              {g.text}
            </i>
          ))}
        </small>
      </div>
      <span className="cq-pxcol">
        <span className={`cq-px ${t}`}>{price(live.close)}</span>
        {live.tag && <small className="cq-live-tag">{live.tag}</small>}
      </span>
      <span className={`cq-chg ${t}`}>{pct(live.chg)}</span>
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

function Detail({
  sid,
  name,
  close,
  chg,
  asOf,
  lineNames,
  tracked,
  onTrack,
}: {
  sid: string
  name: string | null
  /** 名單上的收盤與漲跌：歷史名單顯示名單那天的；其他分頁只在 K 線還沒到時先頂著 */
  close?: number | null
  chg?: number | null
  /** 歷史名單的日期：報價列停在那天，不顯示即時價 */
  asOf?: string
  lineNames: Record<string, string>
  tracked: boolean
  onTrack?: (sid: string, on: boolean) => Promise<void>
}) {
  // K 線資料記著是哪一檔：換股時舊圖先留著（不閃白），但報價列只用這一檔自己的資料
  const [chart, setChart] = useState<{ sid: string; bars: Bar[]; days: Record<string, string[]> }>({ sid: '', bars: [], days: {} })
  const [scores, setScores] = useState<StockScores | null>(null)
  const [scoreErr, setScoreErr] = useState('')
  const [err, setErr] = useState('')
  useEffect(() => {
    // stale：快速切換時，晚到的上一檔回應不能蓋到這一檔
    let stale = false
    setErr('')
    setScores(null)
    setScoreErr('')
    Promise.all([fetchBars(sid), fetchStockPicks(sid)])
      .then(([bars, days]) => !stale && setChart({ sid, bars, days }))
      .catch((e: Error) => !stale && setErr(e.message))
    fetchScores(sid)
      .then((s) => !stale && setScores(s))
      .catch((e: Error) => !stale && setScoreErr(e.message))
    return () => {
      stale = true
    }
  }, [sid])
  const bars = chart.sid === sid ? chart.bars : []
  // 分數同理：換股後到新分數回來前，不拿上一檔的分數與名稱
  const sc = scores?.sid === sid ? scores : null
  // 盤中：今天到目前為止的那根接在 K 線最後（資料庫傍晚才有今天）
  const today = taipeiNow().date
  const live = useLiveQuotes([sid], true)
  const quote = live.quotes[sid]
  const todayBar = liveBar(bars, quote, today)
  const all = todayBar ? [...bars, todayBar] : bars
  // 報價列：歷史名單停在名單那天（選 9/24 就顯示 9/24）；其他分頁是最後一根（盤中就是即時那根）
  const q = quoteBar(all, asOf, null)
  const bigClose = asOf ? (close ?? q?.bar.close ?? null) : (q?.bar.close ?? close ?? null)
  const bigChg = asOf ? (chg ?? q?.chg ?? null) : (q?.chg ?? chg ?? null)
  const label = asOf ? null : todayBar ? liveLabel(quote, today) : quote?.date === today && quote.last === null ? '尚未成交' : null
  const t = trend(bigChg)
  return (
    <>
      <div className="cq-quote">
        <div>
          <h1>
            <small>{sid}</small> {name ?? sc?.name ?? ''}
            {onTrack && <TrackButton sid={sid} on={tracked} onTrack={onTrack} />}
          </h1>
          <div className={`cq-big ${t}`}>
            {price(bigClose)} <span>{pct(bigChg)}</span>
            {label && (
              <em className="cq-live" title="證交所即時報價，約延遲 5 秒；盤中每 10 秒更新">
                {label}
              </em>
            )}
            {!asOf && live.error && shouldPoll(taipeiNow(), []) && <em className="cq-live off">即時報價暫時取不到</em>}
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
      <ModelPanel scores={sc} err={scoreErr} />
      <div className="cq-legend">
        {MA_LEGEND.map((m) => (
          <span key={m.n} style={{ color: m.color }}>
            MA{m.n}
          </span>
        ))}
        <span className="cq-legend-note">
          推薦日
          {Object.entries(lineNames).map(([code, n]) => (
            <b key={code} style={{ color: LINE_LEGEND[code] ?? '#FF7A00' }}>
              ▼{n}
            </b>
          ))}
        </span>
      </div>
      {err ? <div className="cq-err">{err}</div> : <KChart bars={chart.bars} live={chart.sid === sid ? todayBar : null} pickDays={chart.days} lineNames={lineNames} />}
    </>
  )
}

/** 盤中每 10 秒抓一次即時量價（畫面在背景、或不在盤中時不抓；打開時先抓一次）。代號換了就重抓。 */
function useLiveQuotes(sids: string[], enabled: boolean): { quotes: Record<string, Quote>; error: string } {
  // 排序後當鍵：追蹤清單只是換順序時不用重抓
  const key = enabled ? [...sids].sort().join(',') : ''
  const [state, setState] = useState<{ key: string; quotes: Record<string, Quote>; error: string }>({ key: '', quotes: {}, error: '' })
  useEffect(() => {
    if (!key) return
    let stale = false
    let dates: string[] = []
    const load = () =>
      fetchQuotes(key.split(','))
        .then((qs) => {
          if (stale) return
          dates = qs.map((x) => x.date)
          setState({ key, quotes: Object.fromEntries(qs.map((x) => [x.sid, x])), error: '' })
        })
        // 抓不到就沿用資料庫的收盤價，畫面標「即時報價暫時取不到」，下一輪再試
        .catch((e: Error) => !stale && setState((s) => ({ key, quotes: s.key === key ? s.quotes : {}, error: e.message })))
    const tick = () => {
      if (document.visibilityState === 'visible' && shouldPoll(taipeiNow(), dates)) load()
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
  return state.key === key ? state : { quotes: {}, error: '' }
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
function ModelPanel({ scores, err }: { scores: StockScores | null; err: string }) {
  const [help, setHelp] = useState(false)
  if (err) return <div className="cq-models cq-models-msg">模型評分：{err}</div>
  if (!scores) return <div className="cq-models cq-models-msg">模型評分計算中…</div>
  const views = scores.models.map((m) => ({ m, v: modelView(m) }))
  const date = scores.models.map((m) => m.date).find((d) => d !== null) ?? null
  return (
    <div className="cq-models">
      <div className="cq-models-head">
        <b>模型評分</b>
        {date && <span>{date} 收盤後</span>}
        {scores.lists.map((l) => (
          <i key={l.line} className="cq-tag new" title={`${l.date} 的名單`}>
            {l.name}第 {l.rank} 名
          </i>
        ))}
        <button className="cq-help" onClick={() => setHelp(!help)}>
          {help ? '收起說明' : '這些分數是什麼？'}
        </button>
      </div>
      <div className="cq-cards">
        {views.map(({ m, v }) => (
          <div key={m.code} className="cq-card" title={v.what}>
            <div className="cq-card-top">
              <b>{v.title}</b>
              <small>{v.who}</small>
            </div>
            <div className="cq-card-mid">
              <span className={`cq-card-big ${v.tone}`}>{v.big}</span>
              <Spark points={sparkPoints(m)} />
            </div>
            <div className="cq-card-sub">
              {v.sub}
              {m.date !== null && date !== null && m.date !== date && `（${m.date}）`}
            </div>
          </div>
        ))}
      </div>
      {help && (
        <ul className="cq-models-help">
          {views.map(({ m, v }) => (
            <li key={m.code}>
              <b>{v.title}</b>（{v.who}）：{v.what}
            </li>
          ))}
          <li>右上小折線是最近幾個交易日的變化，越往上越好。分數是模型的估計，不保證會發生，非投資建議。</li>
        </ul>
      )}
    </div>
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
