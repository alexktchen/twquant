// 首頁戰情室（T-379／D-188）：開盤前一頁看完——今日一句話（晨報）、昨晚國際大事、市場氛圍、國際行情、期貨籌碼、大盤體檢、
// 法人與融資、昨日消息、今天名單速覽、名單成績。行事曆與族群熱力圖 T-431／D-209 搬走（族群→「盤勢」分頁，法說會等→追蹤股與名單股的小標）。
// 使用者 2026-10-17：「每張卡可以自由移動位子，依照使用者帳號儲存擺置位置，卡也要可以縮起來，縮起來後內容要可以輪播」——
// 卡片照版面（useHomeLayout，登入後存在帳號）排；每張卡點標題收起，收起時輪播重點；「編輯版面」拖曳或 ▲▼ 換位置。
// 只供看盤：這一頁的任何數字都不進選股、不進模型。
// T-430：「今日名單」「名單成績」兩張卡只給有名單／績效權限的帳號（access.ts），其他人版面裡沒有這兩張、也不去抓。
import { Pencil } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { visibleLayout, type Feature } from './access'
import { fetchMarketLive, fetchScorecard, fetchWarroom, fetchWarroomNews } from './api'
import { BriefCard } from './BriefCard'
import { HeadlinesCard } from './HeadlinesCard'
import { ChipsCard, IntlCard, MarketCard, PicksCard, ScoreHomeCard, TaifexCard } from './HomeCards'
import { LayoutEditor } from './LayoutEditor'
import { HomeMoodCard } from './MoodCard'
import { NewsCard } from './NewsCard'
import { cardDef, type CardId } from './layout'
import type { Scorecard } from './scorecard'
import { liveBackoff, shouldPoll, taipeiNow } from './live'
import { useHomeLayout } from './useHomeLayout'
import { MARKET_POLL_MS, marketView, type Brief, type LiveMarket, type NewsItem, type WarRoomData } from './war'

/** 名單成績卡列哪幾條線（大盤精選、潛伏飆股；高分爆量還在驗證、筆數太少不列）。 */
const SCORE_LINES = ['CORE', 'QUIET']

/**
 * 盤中大盤體檢（T-397）：進首頁抓一次；週一到五 9:00～13:35 頁面看得到時每分鐘再抓（伺服器端每分鐘才更新一份）。
 * 取不到就留著手上那份，30 秒起加倍再試。
 */
function useMarketLive(refresh: number): LiveMarket | null {
  const [live, setLive] = useState<LiveMarket | null>(null)
  useEffect(() => {
    let gone = false
    let failures = 0
    let pauseUntil = 0
    let dates: string[] = []
    const load = () =>
      fetchMarketLive()
        .then((r) => {
          if (gone) return
          failures = 0
          dates = r.live ? [r.live.date] : []
          setLive(r.live)
        })
        .catch(() => {
          failures += 1
          pauseUntil = Date.now() + liveBackoff(failures, null)
        })
    const tick = () => {
      if (document.visibilityState === 'visible' && Date.now() >= pauseUntil && shouldPoll(taipeiNow(), dates)) load()
    }
    load()
    const t = setInterval(tick, MARKET_POLL_MS)
    // 手機解鎖回到頁面時馬上更新一次，不用等下一分鐘
    document.addEventListener('visibilitychange', tick)
    return () => {
      gone = true
      clearInterval(t)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [refresh])
  return live
}

export function WarRoom({
  refresh,
  features,
  watchSids,
  onPicks,
  onNews,
  onScore,
  onStock,
}: {
  refresh: number
  /** 這個帳號能用的功能：沒有 picks／score 就不顯示名單卡、名單成績卡 */
  features: Feature[]
  /** 追蹤股代號（登入後才有）：昨日消息多一個「追蹤」篩選 */
  watchSids: string[]
  onPicks: (line?: string) => void
  /** 切到新聞分頁（T-400） */
  onNews: () => void
  onScore: (line: string) => void
  onStock: (sid: string, name: string | null) => void
}) {
  const [data, setData] = useState<WarRoomData | null>(null)
  const [err, setErr] = useState('')
  const [watchNews, setWatchNews] = useState<NewsItem[]>([])
  const [scores, setScores] = useState<Record<string, Scorecard | null>>({})
  // 今日一句話目前顯示哪一天（切到歷史時，新聞頭條卡跟著換成那天的）
  const [shown, setShown] = useState<Brief | null>(null)
  const { layout: saved, toggle, setOrder, reset, error: layoutErr } = useHomeLayout()
  const layout = useMemo(() => visibleLayout(saved, features), [saved, features])
  const canScore = features.includes('score')
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    let stale = false
    setErr('')
    fetchWarroom()
      .then((d) => !stale && setData(d))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [refresh])

  // 名單成績卡：有績效權限才抓（權限晚一步讀回來，讀到了再抓）
  useEffect(() => {
    if (!canScore) return
    let stale = false
    for (const code of SCORE_LINES) {
      fetchScorecard(code)
        .then((s) => !stale && setScores((x) => ({ ...x, [code]: s })))
        .catch(() => !stale && setScores((x) => ({ ...x, [code]: null })))
    }
    return () => {
      stale = true
    }
  }, [refresh, canScore])

  const sidsKey = watchSids.join(',')
  useEffect(() => {
    if (!data || watchSids.length === 0) return setWatchNews([])
    let stale = false
    fetchWarroomNews(watchSids, data.news.since)
      .then((r) => !stale && setWatchNews(r.items))
      .catch(() => !stale && setWatchNews([]))
    return () => {
      stale = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, sidsKey])

  const m = data?.market ?? null
  const live = useMarketLive(refresh)
  const mv = useMemo(() => marketView(m, live, data?.market_split ?? null), [m, live, data])
  const b = data?.brief ?? null
  const shownBrief = shown ?? b

  const fold = (id: CardId) => ({ collapsed: layout.collapsed.includes(id), onToggle: () => toggle(id) })
  const card: Record<CardId, () => ReactNode> = {
    brief: () => (
      <BriefCard
        latest={b}
        dates={data?.brief_dates ?? (b ? [b.date] : [])}
        latestActual={m && b && m.date === b.date ? m : null}
        loading={!data && !err}
        onShown={setShown}
        {...fold('brief')}
      />
    ),
    heads: () => (
      <HeadlinesCard items={shownBrief?.headlines ?? []} until={shownBrief ? shownBrief.generated_at.slice(11, 16) : null} onNews={onNews} {...fold('heads')} />
    ),
    mood: () => <HomeMoodCard {...fold('mood')} />,
    intl: () => <IntlCard b={b} {...fold('intl')} />,
    taifex: () => <TaifexCard t={data?.taifex ?? null} {...fold('taifex')} />,
    market: () => <MarketCard v={mv} {...fold('market')} />,
    chips: () => <ChipsCard m={m} {...fold('chips')} />,
    news: () => (
      <NewsCard
        since={data?.news.since ?? null}
        items={data?.news.items ?? []}
        watchNews={watchNews}
        watchSids={watchSids}
        withList={features.includes('picks')}
        onStock={onStock}
        {...fold('news')}
      />
    ),
    picks: () => <PicksCard picks={data?.picks ?? null} onPicks={onPicks} {...fold('picks')} />,
    score: () => <ScoreHomeCard lines={SCORE_LINES} scores={scores} onScore={onScore} {...fold('score')} />,
  }

  return (
    <main className="cq-war">
      {err && <div className="cq-err">{err}</div>}
      <div className="cq-war-bar">
        {layoutErr && !editing && <span className="cq-war-mute">{layoutErr}</span>}
        {!editing && (
          <button className="cq-edit-btn" onClick={() => setEditing(true)}>
            <Pencil size={15} aria-hidden="true" /> 版面
          </button>
        )}
      </div>
      {editing ? (
        <LayoutEditor layout={layout} onOrder={setOrder} onToggle={toggle} onReset={reset} onDone={() => setEditing(false)} error={layoutErr} />
      ) : (
        <div className="cq-war-cards">
          {layout.order.map((id) => (
            <div key={id} className={cardDef(id).wide ? 'cq-war-cell cq-wide' : 'cq-war-cell'} data-card={id}>
              {card[id]()}
            </div>
          ))}
        </div>
      )}
    </main>
  )
}
