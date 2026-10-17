// 首頁「昨晚國際大事・新聞頭條」（使用者 2026-10-17：「國際大事要縮起來輪播」）：收起時一次一則、每 6 秒換下一則，
// 展開看全部；收起與否存在首頁版面（帳號）。標題連到鉅亨網原文。
import { CardFrame } from './CardFrame'
import { headlineTime, type Headline } from './war'

export function HeadlinesCard({
  items,
  until,
  collapsed,
  onToggle,
}: {
  items: Headline[]
  until: string | null
  collapsed: boolean
  onToggle: () => void
}) {
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
    <CardFrame
      title="昨晚國際大事・新聞頭條"
      className="cq-war-heads"
      when={`前一天 14:00 起${until ? `，截至 ${until}` : ''}`}
      collapsed={collapsed}
      onToggle={onToggle}
      slides={items.map((h) => ({ key: String(h.news_id), node: <ul className="cq-news-one">{row(h)}</ul> }))}
      empty="這份晨報沒有新聞頭條（10/17 以前的晨報還沒有這一段）。"
    >
      {items.length > 0 ? <ul>{items.map(row)}</ul> : <p className="cq-war-mute">這份晨報沒有新聞頭條（10/17 以前的晨報還沒有這一段）。</p>}
      <p className="cq-war-note">來源：鉅亨網新聞（只列標題，點標題看原文）。「今日一句話」的【大事】由這些頭條整理。</p>
    </CardFrame>
  )
}
