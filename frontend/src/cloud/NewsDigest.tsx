// 新聞三張卡（T-417；使用者 2026-10-20：「星雲圖當成可以切換的一個視角就好，要快速閱讀可以三張卡片」）：
// 熱門主題（點了就用這個字搜尋新聞）、多家同報（點了看原文）、股票熱度（點了開個股頁）。都看近 24 小時。
// 地鐵告示：每張卡一個顏色、一個圖示、字大；手機上三張左右滑，電腦並排。讀不到就整塊不顯示，新聞流照常。
import { Flame, Layers, TrendingUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchNewsDigest } from './api'
import { everywhereNote, hotBadge, hotLine, quietNote, topicDelta, type NewsDigest as DigestData } from './news'

export function NewsDigest({ refresh, onSearch, onStock }: { refresh: number; onSearch: (q: string) => void; onStock: (sid: string, name: string) => void }) {
  const [d, setD] = useState<DigestData | null>(null)
  useEffect(() => {
    let stale = false
    fetchNewsDigest()
      .then((x) => !stale && setD(x))
      .catch(() => !stale && setD(null))
    return () => {
      stale = true
    }
  }, [refresh])
  if (!d) return null
  const note = everywhereNote(d.topics.everywhere)
  return (
    <div className="cq-dg">
      <section className="cq-dg-card topic">
        <h3>
          <Flame size={17} aria-hidden="true" />
          熱門主題<small>近 24 小時</small>
        </h3>
        <ol>
          {d.topics.items.map((t) => {
            const delta = topicDelta(t, d.base_days)
            return (
              <li key={t.id}>
                <button onClick={() => onSearch(t.label)} title={`搜尋「${t.label}」的新聞`}>
                  <span className="cq-dg-main">
                    <b>{t.label}</b>
                    <em>{t.n} 則</em>
                    {delta && <i className={delta.startsWith('+') ? 'up' : delta.startsWith('−') ? 'down' : ''}>{delta}</i>}
                  </span>
                  {t.stocks.length > 0 && <small>{t.stocks.map((s) => s.label).join('・')}</small>}
                </button>
              </li>
            )
          })}
        </ol>
        {note && <p className="cq-dg-note">{note}</p>}
      </section>

      <section className="cq-dg-card multi">
        <h3>
          <Layers size={17} aria-hidden="true" />
          多家同報<small>越多家報越重要</small>
        </h3>
        {d.multi.length === 0 ? (
          <p className="cq-dg-note">近 24 小時沒有三家以上一起報的新聞</p>
        ) : (
          <ol>
            {d.multi.map((m) => (
              <li key={m.ev}>
                <a href={m.url} target="_blank" rel="noopener noreferrer">
                  <span className="cq-dg-main">
                    <i className="n">{m.outlets.length} 家</i>
                    <b className="t">{m.title}</b>
                  </span>
                  <small>
                    {m.outlets.join('・')}
                    {m.stocks.length > 0 && <span className="stk">｜{m.stocks.join('・')}</span>}
                  </small>
                </a>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="cq-dg-card hot">
        <h3>
          <TrendingUp size={17} aria-hidden="true" />
          股票熱度<small>{d.base_days < 1 ? '平常值累積中，先照則數排' : `比平常（前 ${d.base_days} 天）多`}</small>
        </h3>
        <ol>
          {d.hot.map((h) => {
            const badge = hotBadge(h)
            return (
              <li key={h.id}>
                <button onClick={() => h.sub && onStock(h.sub, h.label)} title="開個股頁">
                  <span className="cq-dg-main">
                    {h.sub && <em className="code">{h.sub}</em>}
                    <b>{h.label}</b>
                    {badge && <i className="x">{badge}</i>}
                    {quietNote(h.quiet) && <i className="q">久違</i>}
                  </span>
                  <small>
                    {hotLine(h)}
                    {quietNote(h.quiet) && `・${quietNote(h.quiet)}`}
                  </small>
                </button>
              </li>
            )
          })}
        </ol>
      </section>
    </div>
  )
}
