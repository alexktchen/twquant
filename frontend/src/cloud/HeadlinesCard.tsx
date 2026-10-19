// 首頁「新聞頭條」（使用者 2026-10-17：「國際大事要縮起來輪播」）：收起時一次一則、每 6 秒換下一則，展開看全部。
// 2026-10-18 改版（地鐵告示）：每則＝分類色塊＋標題＋時間；標題連到鉅亨網原文。
import { ArrowRight, ExternalLink } from 'lucide-react'
import { CardFrame } from './CardFrame'
import { headlineTone } from './home'
import { headlineTime, type Headline } from './war'

export function HeadlinesCard({
  items,
  until,
  onNews,
  collapsed,
  onToggle,
}: {
  items: Headline[]
  until: string | null
  /** 「看全部 →」：切到新聞分頁（T-400） */
  onNews: () => void
  collapsed: boolean
  onToggle: () => void
}) {
  const row = (h: Headline) => (
    <li key={h.news_id}>
      {h.category && <span className={`cq-cat cq-cat-${headlineTone(h.category)}`}>{h.category.replace(/新聞|雷達/g, '')}</span>}
      <a href={h.url} target="_blank" rel="noopener noreferrer">
        {h.title}
        <ExternalLink size={13} aria-hidden="true" />
      </a>
      <time>{headlineTime(h.published_at)}</time>
    </li>
  )

  return (
    <CardFrame
      id="heads"
      className="cq-war-heads"
      when={`前一天 14:00 起${until ? `～${until}` : ''}`}
      extra={
        <button className="cq-heads-all" onClick={onNews} title="新聞分頁：國內外財經新聞，每 30 分鐘更新">
          全部
          <ArrowRight size={15} aria-hidden="true" />
        </button>
      }
      collapsed={collapsed}
      onToggle={onToggle}
      slides={items.map((h) => ({ key: String(h.news_id), node: <ul className="cq-heads cq-news-one">{row(h)}</ul> }))}
      empty="這份晨報沒有新聞頭條。"
    >
      {items.length > 0 ? (
        <ul className="cq-heads" title="來源：鉅亨網新聞（只列標題，點標題看原文）；「今日一句話」的【大事】由這些頭條整理">
          {items.map(row)}
        </ul>
      ) : (
        <p className="cq-war-mute">這份晨報沒有新聞頭條。</p>
      )}
    </CardFrame>
  )
}
