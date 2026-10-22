// 首頁「昨日消息」（T-379／D-188）：預設看名單股；點一則就地展開內文；可收起（收起時輪播名單股的公告）。
// 2026-10-18 改版（地鐵告示）：每則＝時間、代號名稱（名單／追蹤小標）、主旨一行、類別色塊；說明移到 title。
// T-430：沒有名單權限的帳號沒有「名單股」這一格，預設看全部。
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { CardFrame } from './CardFrame'
import { atLabel, dayLabel, newsId, type NewsItem } from './war'

type Filter = 'listed' | 'all' | 'watch'

export function NewsCard({
  since,
  items,
  watchNews,
  watchSids,
  withList,
  onStock,
  collapsed,
  onToggle,
}: {
  since: string | null
  /** 首頁那份：名單股的每一則＋全市場重要類別 */
  items: NewsItem[]
  /** 追蹤股的公告（另外查） */
  watchNews: NewsItem[]
  watchSids: string[]
  /** 有名單權限：多「名單股」一格並預設看它 */
  withList: boolean
  onStock: (sid: string, name: string | null) => void
  collapsed: boolean
  onToggle: () => void
}) {
  const [chosen, setFilter] = useState<Filter>('listed')
  // 權限晚一步讀回來：沒有名單權限時「名單股」那格不在，選到它就當全部
  const filter: Filter = chosen === 'listed' && !withList ? 'all' : chosen
  const open = !collapsed
  const [expanded, setExpanded] = useState<string | null>(null)
  const watchSet = useMemo(() => new Set(watchSids), [watchSids])

  const listed = useMemo(() => items.filter((n) => n.listed), [items])
  const all = useMemo(() => {
    const seen = new Set(items.map(newsId))
    return [...items, ...watchNews.filter((n) => !seen.has(newsId(n)))].sort((a, b) => b.ts - a.ts)
  }, [items, watchNews])
  const list = filter === 'listed' ? listed : filter === 'watch' ? watchNews : all
  // 收起時輪播名單股；名單股沒有公告就輪播全部
  const reel = listed.length > 0 ? listed : all

  const toggle = (n: NewsItem) => setExpanded((k) => (k === newsId(n) ? null : newsId(n)))

  const row = (n: NewsItem) => {
    const on = expanded === newsId(n)
    return (
      <li key={newsId(n)} className={on ? 'on' : undefined} onClick={() => toggle(n)}>
        <div className="cq-newsrow">
          <time>{atLabel(n.at).replace(/^\d+\/\d+\s*/, '')}</time>
          <span className="cq-newsco">
            <em>{n.stock_id}</em>
            {n.name ?? ''}
            {n.listed && <i className="cq-b cq-b-compact cq-b-good">名單</i>}
            {watchSet.has(n.stock_id) && <i className="cq-b cq-b-compact cq-b-chip">追蹤</i>}
          </span>
          <span className={on ? 'cq-newssub open' : 'cq-newssub'}>{n.subject}</span>
          <span className="cq-newstags">
            {n.tags.slice(0, 2).map((tg) => (
              <i key={tg} className="cq-cat cq-cat-event">
                {tg}
              </i>
            ))}
            <ChevronDown size={16} className={on ? 'flip' : undefined} aria-hidden="true" />
          </span>
        </div>
        {on && (
          <div className="cq-news-body" onClick={(e) => e.stopPropagation()}>
            {n.content ? <pre>{n.content}</pre> : <p className="cq-war-mute">這則沒有內文（存託憑證的公告只有主旨，或內文還沒抓到）。</p>}
            <button className="cq-hc-link" onClick={() => onStock(n.stock_id, n.name)}>
              看 {n.stock_id} {n.name ?? ''} <ChevronRight size={16} aria-hidden="true" />
            </button>
          </div>
        )}
      </li>
    )
  }

  return (
    <CardFrame
      id="news"
      className="cq-war-news"
      when={since ? `${dayLabel(since)} 起` : undefined}
      collapsed={collapsed}
      onToggle={onToggle}
      slides={reel.map((n) => ({ key: newsId(n), node: <ul className="cq-newslist cq-news-one">{row(n)}</ul> }))}
      reelLabel={listed.length > 0 ? '名單股' : '全部'}
      reelPaused={expanded !== null}
      empty="昨天到今天沒有公告。"
      extra={
        open && (
          <div className="cq-seg cq-seg-small">
            {withList && (
              <button className={filter === 'listed' ? 'on' : ''} onClick={() => setFilter('listed')}>
                名單股 <em>{listed.length}</em>
              </button>
            )}
            <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>
              全部 <em>{all.length}</em>
            </button>
            {watchSids.length > 0 && (
              <button className={filter === 'watch' ? 'on' : ''} onClick={() => setFilter('watch')}>
                追蹤股 <em>{watchNews.length}</em>
              </button>
            )}
          </div>
        )
      }
    >
      {list.length === 0 && <p className="cq-war-mute">{filter === 'listed' ? '名單股昨天到今天沒有公告。' : '沒有符合的公告。'}</p>}
      <ul
        className="cq-newslist"
        title={`來源：公開資訊觀測站重大訊息，點一則看內文。「全部」＝${withList ? '名單股、' : ''}追蹤股的每一則，加上全市場的處置、變更交易、減增資、併購、庫藏股、停工訴訟、董總異動。公告只說發生了什麼，不等於利多或利空`}
      >
        {list.map(row)}
      </ul>
    </CardFrame>
  )
}
