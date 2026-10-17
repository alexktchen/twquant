// 首頁「昨日消息」（T-379／D-188；使用者 2026-10-17 修訂）：預設看名單股；點一則就地展開內文；
// 可收起——收起時只留一則，在名單股的公告間自動輪播（滑過或打開內文時暫停），也可手動上一則、下一則。展開／收起記在瀏覽器。
import { useEffect, useMemo, useState } from 'react'
import { CAROUSEL_MS, atLabel, carouselStep, dayLabel, newsId, type NewsItem } from './war'
import { NEWS_KEY, loadPref, openPref, savePref } from './prefs'

type Filter = 'listed' | 'all' | 'watch'

export function NewsCard({
  since,
  items,
  watchNews,
  watchSids,
  onStock,
}: {
  since: string | null
  /** 首頁那份：名單股的每一則＋全市場重要類別 */
  items: NewsItem[]
  /** 追蹤股的公告（另外查） */
  watchNews: NewsItem[]
  watchSids: string[]
  onStock: (sid: string, name: string | null) => void
}) {
  const [filter, setFilter] = useState<Filter>('listed')
  const [open, setOpenState] = useState(() => openPref(loadPref(NEWS_KEY), true))
  const setOpen = (on: boolean) => {
    setOpenState(on)
    savePref(NEWS_KEY, on ? '1' : '0')
  }
  const [expanded, setExpanded] = useState<string | null>(null)
  const [slide, setSlide] = useState(0)
  const [hover, setHover] = useState(false)
  const watchSet = useMemo(() => new Set(watchSids), [watchSids])

  const listed = useMemo(() => items.filter((n) => n.listed), [items])
  const all = useMemo(() => {
    const seen = new Set(items.map(newsId))
    return [...items, ...watchNews.filter((n) => !seen.has(newsId(n)))].sort((a, b) => b.ts - a.ts)
  }, [items, watchNews])
  const list = filter === 'listed' ? listed : filter === 'watch' ? watchNews : all
  // 收起時輪播名單股；名單股沒有公告就輪播全部
  const reel = listed.length > 0 ? listed : all
  const current = reel.length > 0 ? reel[Math.min(slide, reel.length - 1)] : null
  const paused = hover || (current !== null && expanded === newsId(current))

  useEffect(() => {
    if (open || reel.length < 2 || paused) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') setSlide((i) => carouselStep(i, reel.length, 1))
    }, CAROUSEL_MS)
    return () => clearInterval(t)
  }, [open, reel.length, paused])

  const toggle = (n: NewsItem) => setExpanded((k) => (k === newsId(n) ? null : newsId(n)))

  const body = (n: NewsItem) =>
    expanded === newsId(n) && (
      <div className="cq-news-body" onClick={(e) => e.stopPropagation()}>
        {n.content ? <pre>{n.content}</pre> : <p className="cq-war-mute">這則沒有內文（存託憑證的公告只有主旨，或內文還沒抓到）。</p>}
        <button className="cq-war-link" onClick={() => onStock(n.stock_id, n.name)}>
          看 {n.stock_id} {n.name ?? ''} 的 K 線與評分 ›
        </button>
      </div>
    )

  const row = (n: NewsItem) => (
    <li key={newsId(n)} className={expanded === newsId(n) ? 'on' : undefined} onClick={() => toggle(n)}>
      <div className="cq-news-row">
        <span className="cq-war-newstime">{atLabel(n.at)}</span>
        <span className="cq-war-newsco">
          <em>{n.stock_id}</em> {n.name ?? ''}
          {n.listed && <i className="cq-tag prob">名單</i>}
          {watchSet.has(n.stock_id) && <i className="cq-tag">追蹤</i>}
        </span>
        <span className="cq-war-newssub">
          {n.subject}
          {n.tags.map((tg) => (
            <i key={tg} className="cq-war-newstag">
              {tg}
            </i>
          ))}
        </span>
        <span className="cq-news-caret" aria-hidden>
          {expanded === newsId(n) ? '▴' : '▾'}
        </span>
      </div>
      {body(n)}
    </li>
  )

  return (
    <section className="cq-war-card cq-war-news">
      <header>
        <button className="cq-news-head" aria-expanded={open} onClick={() => setOpen(!open)}>
          <h2>昨日消息</h2>
          {since && <span className="cq-war-when">{dayLabel(since)} 起的重大訊息</span>}
          <span className="cq-mood-caret">{open ? '收起 ▲' : '展開 ▼'}</span>
        </button>
        {open && (
          <div className="cq-seg cq-seg-small">
            <button className={filter === 'listed' ? 'on' : ''} onClick={() => setFilter('listed')}>
              名單股 <em>{listed.length}</em>
            </button>
            <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>
              全部 <em>{all.length}</em>
            </button>
            {watchSids.length > 0 && (
              <button className={filter === 'watch' ? 'on' : ''} onClick={() => setFilter('watch')}>
                追蹤股 <em>{watchNews.length}</em>
              </button>
            )}
          </div>
        )}
      </header>

      {open ? (
        <>
          {list.length === 0 && (
            <p className="cq-war-mute">{filter === 'listed' ? '名單股昨天到今天沒有公告。' : '沒有符合的公告。'}</p>
          )}
          <ul className="cq-war-newslist">{list.map(row)}</ul>
        </>
      ) : current ? (
        <div className="cq-news-reel" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
          <button className="cq-news-nav" aria-label="上一則" onClick={() => setSlide((i) => carouselStep(i, reel.length, -1))}>
            ‹
          </button>
          <ul className="cq-war-newslist cq-news-one">{row(current)}</ul>
          <button className="cq-news-nav" aria-label="下一則" onClick={() => setSlide((i) => carouselStep(i, reel.length, 1))}>
            ›
          </button>
          <span className="cq-news-count">
            {listed.length > 0 ? '名單股' : '全部'} {Math.min(slide, reel.length - 1) + 1} / {reel.length}
          </span>
        </div>
      ) : (
        <p className="cq-war-mute">昨天到今天沒有公告。</p>
      )}

      <p className="cq-war-note">
        來源：公開資訊觀測站重大訊息，點一則看內文（太長的財報類公告會截斷）。「全部」＝名單股、追蹤股的每一則，加上全市場的處置、變更交易、減增資、併購、庫藏股、停工訴訟、董總異動。公告只說發生了什麼，不等於利多或利空。
      </p>
    </section>
  )
}
