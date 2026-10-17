// 首頁戰情室（T-379／D-188）：開盤前一頁看完——今日一句話（晨報）、市場氛圍（美股、台指期、類股／資金，沿用 MoodCard）、
// 其他國際行情、期貨籌碼、大盤體檢、法人與融資、昨日消息、今天名單速覽、名單成績、行事曆；族群熱力圖在最下面，預設打開。
// 只供看盤：這一頁的任何數字都不進選股、不進模型。
import { useEffect, useState } from 'react'
import { fetchScorecard, fetchWarroom, fetchWarroomNews } from './api'
import { GroupsPage } from './GroupsPage'
import { MoodCard } from './MoodCard'
import { NewsCard } from './NewsCard'
import { pct } from './format'
import { HEAT_KEY, loadPref, openPref, savePref } from './prefs'
import type { Scorecard } from './scorecard'
import {
  EVENT_NAME,
  breadth,
  dayLabel,
  headlineTime,
  signedYi,
  streakText,
  tone,
  type NewsItem,
  type WarRoomData,
} from './war'

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
  /** 追蹤股代號（登入後才有）：昨日消息多一個「追蹤」篩選 */
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
  // 使用者 2026-10-17：熱力圖預設打開，點過打開／收起記在這台瀏覽器
  const [heatOpen, setHeatOpenState] = useState(() => openPref(loadPref(HEAT_KEY), true))
  const setHeatOpen = (on: boolean) => {
    setHeatOpenState(on)
    savePref(HEAT_KEY, on ? '1' : '0')
  }
  const [factsOpen, setFactsOpen] = useState(false)

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
  const t = data?.taifex ?? null
  const b = data?.brief ?? null
  const br = m ? breadth(m.up, m.down, m.flat) : null

  return (
    <main className="cq-war">
      {err && <div className="cq-err">{err}</div>}

      {/* 今日一句話：模型晨報（數字都檢查過）；沒有就用規則版 */}
      <section className="cq-war-card cq-war-brief">
        <header>
          <h2>今日一句話</h2>
          {b && <span className="cq-war-when">{dayLabel(b.date)} 晨報・整理於 {b.generated_at.slice(5, 16).replace('-', '/')}</span>}
        </header>
        {!data && !err && <p className="cq-war-mute">讀取中…</p>}
        {data && !b && <p className="cq-war-mute">今天的晨報還沒產生（每個交易日早上 6:30 整理）。</p>}
        {b && b.headline && b.bullets ? (
          <>
            <p className="cq-war-headline">{b.headline}</p>
            <ul className="cq-war-bullets">
              {b.bullets.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
            {b.risk_note && <p className="cq-war-risk">⚠ {b.risk_note}</p>}
          </>
        ) : (
          b && <p className="cq-war-headline cq-war-rule">{b.rule_text}</p>
        )}
        {b && (
          <p className="cq-war-note">
            {b.headline ? 'AI 依下面的數字整理，每句裡的數字都檢查過出自這些數字；' : ''}
            {b.absent_reason && !b.headline ? `（AI 摘要這次沒有產生：${b.absent_reason}）` : ''}
            {b.facts && b.facts.length > 0 && (
              <button className="cq-war-link" onClick={() => setFactsOpen(!factsOpen)}>
                {factsOpen ? '收起數字' : `看全部 ${b.facts.length} 個數字`}
              </button>
            )}
          </p>
        )}
        {factsOpen && b?.facts && (
          <ul className="cq-war-facts">
            {b.facts.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        )}
      </section>

      {/* 昨晚國際大事・新聞頭條（使用者 2026-10-17）：晨報 06:30 抓的鉅亨網頭條快照 */}
      {b?.headlines && b.headlines.length > 0 && (
        <section className="cq-war-card cq-war-heads">
          <header>
            <h2>昨晚國際大事・新聞頭條</h2>
            <span className="cq-war-when">前一天 14:00 到今早 {b.generated_at.slice(11, 16)}</span>
          </header>
          <ul>
            {b.headlines.map((h) => (
              <li key={h.news_id}>
                <span className="cq-war-newstime">{headlineTime(h.published_at)}</span>
                <a href={h.url} target="_blank" rel="noopener noreferrer">
                  {h.title}
                </a>
                {h.category && <i className="cq-war-newstag">{h.category}</i>}
              </li>
            ))}
          </ul>
          <p className="cq-war-note">來源：鉅亨網新聞（只列標題，點標題看原文）。「今日一句話」的【大事】由這些頭條整理。</p>
        </section>
      )}

      {/* 美股、台指期、類股強弱／族群資金：沿用市場氛圍 */}
      <div className="cq-war-mood">
        <MoodCard enabled />
      </div>

      <div className="cq-war-grid">
        {/* 其他國際行情（06:30 的快照） */}
        <section className="cq-war-card">
          <header>
            <h2>國際行情</h2>
            {b && <span className="cq-war-when">{b.generated_at.slice(5, 16).replace('-', '/')} 快照</span>}
          </header>
          {b?.extras && b.extras.length > 0 ? (
            <table className="cq-war-table">
              <tbody>
                {b.extras.map((q) => (
                  <tr key={q.symbol}>
                    <th>{q.name}</th>
                    <td>{q.symbol === '^TNX' ? `${q.last.toFixed(2)}%` : q.last.toLocaleString('en-US', { maximumFractionDigits: 2 })}</td>
                    <td className={tone(q.chg)}>{pct(q.chg)}</td>
                  </tr>
                ))}
                {b.adr_premium !== null && (
                  <tr>
                    <th>台積電 ADR 折溢價</th>
                    <td colSpan={2} className={tone(b.adr_premium)}>
                      {b.adr_premium >= 0 ? '溢價' : '折價'} {Math.abs(b.adr_premium * 100).toFixed(1)}%
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          ) : (
            <p className="cq-war-mute">今早的快照還沒有。</p>
          )}
          <p className="cq-war-note">VIX＝美股恐慌指數；ADR 折溢價＝台積電 ADR 換算台幣後對台股收盤（1 張 ADR＝5 股）。</p>
        </section>

        {/* 期貨籌碼 */}
        <section className="cq-war-card">
          <header>
            <h2>期貨籌碼</h2>
            {t && <span className="cq-war-when">{dayLabel(t.date)} 盤後</span>}
          </header>
          {t ? (
            <table className="cq-war-table">
              <tbody>
                <tr>
                  <th>外資台指期淨未平倉</th>
                  <td colSpan={2} className={tone(t.fut_foreign_net_oi)}>
                    {t.fut_foreign_net_oi === null ? '—' : `${t.fut_foreign_net_oi >= 0 ? '淨多' : '淨空'} ${Math.abs(t.fut_foreign_net_oi).toLocaleString()} 口`}
                  </td>
                </tr>
                <tr>
                  <th>投信／自營</th>
                  <td colSpan={2}>
                    {t.fut_trust_net_oi?.toLocaleString() ?? '—'} ／ {t.fut_dealer_net_oi?.toLocaleString() ?? '—'} 口
                  </td>
                </tr>
                <tr>
                  <th>選擇權未平倉買賣權比</th>
                  <td colSpan={2}>{t.pc_oi_ratio === null ? '—' : `${t.pc_oi_ratio.toFixed(1)}%`}</td>
                </tr>
                <tr>
                  <th>選擇權成交量買賣權比</th>
                  <td colSpan={2}>{t.pc_vol_ratio === null ? '—' : `${t.pc_vol_ratio.toFixed(1)}%`}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="cq-war-mute">期交所盤後統計還沒有。</p>
          )}
          <p className="cq-war-note">淨空單＝外資在台指期貨的空單比多單多；買賣權比＝賣權÷買權，大於 100% 代表避險（賣權）比較多。</p>
        </section>

        {/* 大盤體檢 */}
        <section className="cq-war-card">
          <header>
            <h2>大盤體檢</h2>
            {m && <span className="cq-war-when">{dayLabel(m.date)}</span>}
          </header>
          {m && br ? (
            <>
              <div className="cq-war-idx">
                <span>
                  加權 <b>{m.taiex_close?.toLocaleString('en-US', { maximumFractionDigits: 0 }) ?? '—'}</b>{' '}
                  <i className={tone(m.taiex_chg)}>{pct(m.taiex_chg)}</i>
                </span>
                <span>
                  櫃買 <b>{m.otc_close?.toFixed(2) ?? '—'}</b> <i className={tone(m.otc_chg)}>{pct(m.otc_chg)}</i>
                </span>
              </div>
              <div className="cq-war-breadth" aria-label={`${m.up} 漲 ${m.down} 跌 ${m.flat} 平`}>
                <span className="up" style={{ width: `${br.up}%` }} />
                <span className="flat" style={{ width: `${br.flat}%` }} />
                <span className="down" style={{ width: `${br.down}%` }} />
              </div>
              <div className="cq-war-breadth-label">
                <span className="up">{m.up} 漲</span>
                <span className="flat">{m.flat} 平</span>
                <span className="down">{m.down} 跌</span>
              </div>
              <table className="cq-war-table">
                <tbody>
                  <tr>
                    <th>漲停／跌停</th>
                    <td colSpan={2}>
                      <span className="up">{m.limit_up}</span> ／ <span className="down">{m.limit_down}</span> 家
                    </td>
                  </tr>
                  <tr>
                    <th>創 20 日新高／新低</th>
                    <td colSpan={2}>
                      {m.new_high === null ? '—' : <span className="up">{m.new_high}</span>} ／{' '}
                      {m.new_low === null ? '—' : <span className="down">{m.new_low}</span>} 家
                    </td>
                  </tr>
                  <tr>
                    <th>成交金額</th>
                    <td colSpan={2}>
                      {Math.round(m.amount / 1e8).toLocaleString()} 億
                      {m.amount_ratio !== null && <small>（20 日平均的 {m.amount_ratio.toFixed(2)} 倍）</small>}
                    </td>
                  </tr>
                </tbody>
              </table>
            </>
          ) : (
            <p className="cq-war-mute">大盤體檢還沒有（每個交易日收盤後約 17:40 算好）。</p>
          )}
          <p className="cq-war-note">家數與成交金額只算上市櫃普通股，不含 ETF、權證。</p>
        </section>

        {/* 法人與融資 */}
        <section className="cq-war-card">
          <header>
            <h2>法人與融資</h2>
            {m && <span className="cq-war-when">{dayLabel(m.date)}・估算</span>}
          </header>
          {m ? (
            <table className="cq-war-table">
              <tbody>
                <tr>
                  <th>外資</th>
                  <td className={tone(m.foreign_net)}>{signedYi(m.foreign_net)}</td>
                  <td className="cq-war-mute">{streakText(m.foreign_streak)}</td>
                </tr>
                <tr>
                  <th>投信</th>
                  <td className={tone(m.trust_net)}>{signedYi(m.trust_net)}</td>
                  <td />
                </tr>
                <tr>
                  <th>自營商</th>
                  <td className={tone(m.dealer_net)}>{signedYi(m.dealer_net)}</td>
                  <td />
                </tr>
                <tr>
                  <th>融資餘額變化</th>
                  <td className={tone(m.margin_delta)}>{signedYi(m.margin_delta)}</td>
                  <td className="cq-war-mute">{m.margin_balance === null ? '' : `餘額約 ${Math.round(m.margin_balance / 1e8).toLocaleString()} 億`}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="cq-war-mute">還沒有資料。</p>
          )}
          <p className="cq-war-note">
            估算＝每檔買賣超（或融資）股數 × 當天收盤加總，會和證交所公布的金額差幾個百分點（不含 ETF、用收盤價）。看方向與量級用。
          </p>
        </section>
      </div>

      {/* 昨日消息（NewsCard：預設名單股、點開看內文、收起時輪播） */}
      <NewsCard since={data?.news.since ?? null} items={data?.news.items ?? []} watchNews={watchNews} watchSids={watchSids} onStock={onStock} />

      <div className="cq-war-grid">
        {/* 今日名單速覽 */}
        <section className="cq-war-card">
          <header>
            <h2>今日名單</h2>
            {data?.picks && <span className="cq-war-when">{dayLabel(data.picks.date)} 收盤後產生</span>}
          </header>
          {data?.picks ? (
            <ul className="cq-war-picks">
              {data.picks.lines.map((l) => (
                <li key={l.code} onClick={() => onPicks(l.code)}>
                  <b>{l.name}</b>
                  <span>
                    {l.n} 檔<small>（新增 {l.new}）</small>
                  </span>
                  <i>看名單 ›</i>
                </li>
              ))}
            </ul>
          ) : (
            <p className="cq-war-mute">還沒有名單。</p>
          )}
        </section>

        {/* 名單成績（只顯示，不拿來調模型） */}
        <section className="cq-war-card">
          <header>
            <h2>名單成績</h2>
            <span className="cq-war-when">公布後的每一筆</span>
          </header>
          <table className="cq-war-table cq-war-score">
            <thead>
              <tr>
                <th />
                <th>筆數</th>
                <th>每筆平均</th>
                <th>中位數</th>
                <th>最差</th>
              </tr>
            </thead>
            <tbody>
              {SCORE_LINES.map((code) => {
                const s = scores[code]
                const st = s?.trades.stats.live
                return (
                  <tr key={code}>
                    <th>{s?.name ?? code}</th>
                    <td>{st ? st.n : s === null ? '—' : '…'}</td>
                    <td className={tone(st?.mean ?? null)}>{st && st.n > 0 ? pct(st.mean) : '—'}</td>
                    <td className={tone(st?.median ?? null)}>{st && st.n > 0 ? pct(st.median) : '—'}</td>
                    <td className="down">{st && st.n > 0 ? pct(st.worst) : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="cq-war-note">
            照名單規則當成真的買（含手續費、證交稅），只算公布之後已賣出的。看「每筆平均」不看勝率：台股大多時候在漲，隨便買的勝率也有七成多。
            這些成績只拿來看，不拿來挑股或調模型（2027 年 3 月才用新資料正式檢驗）。
            <button className="cq-war-link" onClick={() => onScore(SCORE_LINES[0])}>
              詳細績效 ›
            </button>
          </p>
        </section>

        {/* 行事曆 */}
        <section className="cq-war-card">
          <header>
            <h2>行事曆</h2>
            {data && <span className="cq-war-when">今天起 3 天</span>}
          </header>
          {data ? (
            <ul className="cq-war-cal">
              <li>
                <b>月營收截止</b>
                <span>
                  {dayLabel(data.calendar.revenue.date)}
                  {data.calendar.revenue.days === 0 ? '（今天）' : `（還有 ${data.calendar.revenue.days} 天）`}
                </span>
              </li>
              {data.calendar.dividends.map((d) => (
                <li key={`${d.date}${d.stock_id}${d.event_type}`} onClick={() => onStock(d.stock_id, d.name)}>
                  <b>{EVENT_NAME[d.event_type] ?? d.event_type}</b>
                  <span>
                    {dayLabel(d.date)} <em>{d.stock_id}</em> {d.name ?? ''}
                  </span>
                </li>
              ))}
              {data.calendar.conferences.map((c) => (
                <li key={`${c.date}${c.stock_id}${c.conf_time}`} onClick={() => onStock(c.stock_id, c.name)} title={c.summary ?? ''}>
                  <b>法說會</b>
                  <span>
                    {dayLabel(c.date)} {c.conf_time} <em>{c.stock_id}</em> {c.name ?? ''}
                  </span>
                </li>
              ))}
              {data.calendar.dividends.length === 0 && data.calendar.conferences.length === 0 && (
                <li className="cq-war-mute">這三天沒有除權息或法說會。</li>
              )}
            </ul>
          ) : (
            <p className="cq-war-mute">讀取中…</p>
          )}
        </section>
      </div>

      {/* 族群熱力圖：預設折疊 */}
      <section className="cq-war-card cq-war-heat">
        <button className="cq-war-heat-head" aria-expanded={heatOpen} onClick={() => setHeatOpen(!heatOpen)}>
          <h2>族群資金與熱力圖</h2>
          <span className="cq-war-when">每個族群的資金倍數、漲跌與成員</span>
          <span className="cq-mood-caret">{heatOpen ? '收起 ▲' : '展開 ▼'}</span>
        </button>
        {heatOpen && (
          <div className="cq-war-heat-body">
            <GroupsPage refresh={refresh} listed={listed} onStock={onStock} withMood={false} />
          </div>
        )}
      </section>
    </main>
  )
}
