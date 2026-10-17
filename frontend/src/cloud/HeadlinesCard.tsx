// 首頁「昨晚國際大事・新聞頭條」（使用者 2026-10-17：「國際大事要縮起來輪播」）：預設收起，只留一則、每 6 秒換下一則
// （滑過時暫停），也可手動上一則、下一則；展開看全部。展開／收起記在瀏覽器。標題連到鉅亨網原文。
import { useEffect, useState } from 'react'
import { CAROUSEL_MS, carouselStep, headlineTime, type Headline } from './war'
import { HEADS_KEY, loadPref, openPref, savePref } from './prefs'

export function HeadlinesCard({ items, until }: { items: Headline[]; until: string | null }) {
  const [open, setOpenState] = useState(() => openPref(loadPref(HEADS_KEY), false))
  const setOpen = (on: boolean) => {
    setOpenState(on)
    savePref(HEADS_KEY, on ? '1' : '0')
  }
  const [slide, setSlide] = useState(0)
  const [hover, setHover] = useState(false)
  const at = Math.min(slide, items.length - 1)

  useEffect(() => {
    if (open || items.length < 2 || hover) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') setSlide((i) => carouselStep(i, items.length, 1))
    }, CAROUSEL_MS)
    return () => clearInterval(t)
  }, [open, items.length, hover])

  if (items.length === 0) return null

  const row = (h: Headline) => (
    <li key={h.news_id}>
      <span className="cq-war-newstime">{headlineTime(h.published_at)}</span>
      <a href={h.url} target="_blank" rel="noopener noreferrer">
        {h.title}
      </a>
      {h.category && <i className="cq-war-newstag">{h.category}</i>}
    </li>
  )

  return (
    <section className="cq-war-card cq-war-heads">
      <header>
        <button className="cq-news-head" aria-expanded={open} onClick={() => setOpen(!open)}>
          <h2>昨晚國際大事・新聞頭條</h2>
          <span className="cq-war-when">前一天 14:00 起{until ? `，截至 ${until}` : ''}</span>
          <span className="cq-mood-caret">{open ? '收起 ▲' : '展開 ▼'}</span>
        </button>
      </header>

      {open ? (
        <ul>{items.map(row)}</ul>
      ) : (
        <div className="cq-news-reel" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
          <button className="cq-news-nav" aria-label="上一則" onClick={() => setSlide((i) => carouselStep(i, items.length, -1))}>
            ‹
          </button>
          <ul className="cq-news-one">{row(items[at])}</ul>
          <button className="cq-news-nav" aria-label="下一則" onClick={() => setSlide((i) => carouselStep(i, items.length, 1))}>
            ›
          </button>
          <span className="cq-news-count">
            {at + 1} / {items.length}
          </span>
        </div>
      )}

      <p className="cq-war-note">來源：鉅亨網新聞（只列標題，點標題看原文）。「今日一句話」的【大事】由這些頭條整理。</p>
    </section>
  )
}
