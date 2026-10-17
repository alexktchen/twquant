// 首頁戰情室（T-379／D-188）：開盤前一頁看完——今日一句話（晨報）、昨晚國際大事、市場氛圍、國際行情、期貨籌碼、大盤體檢、
// 法人與融資、昨日消息、今天名單速覽、名單成績、行事曆、族群熱力圖。
// 使用者 2026-10-17：「每張卡可以自由移動位子，依照使用者帳號儲存擺置位置，卡也要可以縮起來，縮起來後內容要可以輪播」——
// 卡片照版面（useHomeLayout，登入後存在帳號）排；每張卡點標題收起，收起時輪播重點；「整理版面」拖曳或 ▲▼ 換位置。
// 只供看盤：這一頁的任何數字都不進選股、不進模型。
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { fetchScorecard, fetchWarroom, fetchWarroomNews } from './api'
import { BriefCard } from './BriefCard'
import { CalendarCard } from './CalendarCard'
import { HeadlinesCard } from './HeadlinesCard'
import { ChipsCard, HeatCard, IntlCard, MarketCard, PicksCard, ScoreHomeCard, TaifexCard } from './HomeCards'
import { LayoutEditor } from './LayoutEditor'
import { HomeMoodCard } from './MoodCard'
import { NewsCard } from './NewsCard'
import { cardDef, type CardId } from './layout'
import type { Scorecard } from './scorecard'
import { useHomeLayout } from './useHomeLayout'
import type { Brief, NewsItem, WarRoomData } from './war'

/** 名單成績卡列哪幾條線（大盤精選、潛伏飆股；高分爆量還在驗證、筆數太少不列）。 */
const SCORE_LINES = ['CORE', 'QUIET']

export function WarRoom({
  refresh,
  watchSids,
  listed,
  onPicks,
  onScore,
  onStock,
}: {
  refresh: number
  /** 追蹤股代號（登入後才有）：昨日消息多一個「追蹤」篩選、行事曆標「追蹤」 */
  watchSids: string[]
  /** 名單日期與各族群在名單上的股票（族群熱力圖標「在名單」用） */
  listed: { date: string; sids: Map<string, string[]> } | null
  onPicks: (line?: string) => void
  onScore: (line: string) => void
  onStock: (sid: string, name: string | null) => void
}) {
  const [data, setData] = useState<WarRoomData | null>(null)
  const [err, setErr] = useState('')
  const [watchNews, setWatchNews] = useState<NewsItem[]>([])
  const [scores, setScores] = useState<Record<string, Scorecard | null>>({})
  // 今日一句話目前顯示哪一天（切到歷史時，新聞頭條卡跟著換成那天的）
  const [shown, setShown] = useState<Brief | null>(null)
  const { layout, toggle, setOrder, reset, error: layoutErr } = useHomeLayout()
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    let stale = false
    setErr('')
    fetchWarroom()
      .then((d) => !stale && setData(d))
      .catch((e: Error) => !stale && setErr(e.message))
    for (const code of SCORE_LINES) {
      fetchScorecard(code)
        .then((s) => !stale && setScores((x) => ({ ...x, [code]: s })))
        .catch(() => !stale && setScores((x) => ({ ...x, [code]: null })))
    }
    return () => {
      stale = true
    }
  }, [refresh])

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
  const b = data?.brief ?? null
  const shownBrief = shown ?? b
  const listedSids = useMemo(() => new Set(data?.picks?.sids ?? []), [data])
  const watchSet = useMemo(() => new Set(watchSids), [watchSids])

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
      <HeadlinesCard items={shownBrief?.headlines ?? []} until={shownBrief ? shownBrief.generated_at.slice(11, 16) : null} {...fold('heads')} />
    ),
    mood: () => <HomeMoodCard {...fold('mood')} />,
    intl: () => <IntlCard b={b} {...fold('intl')} />,
    taifex: () => <TaifexCard t={data?.taifex ?? null} {...fold('taifex')} />,
    market: () => <MarketCard m={m} {...fold('market')} />,
    chips: () => <ChipsCard m={m} {...fold('chips')} />,
    news: () => (
      <NewsCard
        since={data?.news.since ?? null}
        items={data?.news.items ?? []}
        watchNews={watchNews}
        watchSids={watchSids}
        onStock={onStock}
        {...fold('news')}
      />
    ),
    picks: () => <PicksCard picks={data?.picks ?? null} onPicks={onPicks} {...fold('picks')} />,
    score: () => <ScoreHomeCard lines={SCORE_LINES} scores={scores} onScore={onScore} {...fold('score')} />,
    cal: () => <CalendarCard calendar={data?.calendar ?? null} listedSids={listedSids} watchSids={watchSet} onStock={onStock} {...fold('cal')} />,
    heat: () => <HeatCard refresh={refresh} listed={listed} onStock={onStock} {...fold('heat')} />,
  }

  return (
    <main className="cq-war">
      {err && <div className="cq-err">{err}</div>}
      <div className="cq-war-bar">
        {layoutErr && !editing && <span className="cq-war-mute">{layoutErr}</span>}
        {!editing && (
          <button className="cq-edit-btn" onClick={() => setEditing(true)}>
            整理版面
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
