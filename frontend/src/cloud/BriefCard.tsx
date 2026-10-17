// 首頁「今日一句話」（T-377／D-188）：模型晨報（數字都檢查過），沒有就用規則版。
// 使用者 2026-10-17：「做一個可以選日期看歷史的頁面」——標題列可選日期或按 ‹ › 換天，看過去的晨報；
// 晨報寫在開盤前，歷史那天另列當天大盤實際收盤對照。讀的是存好的晨報，不重叫模型。
import { useEffect, useState } from 'react'
import { fetchBriefDay } from './api'
import { pct } from './format'
import { briefNeighbors, dayLabel, signedYi, tone, type Brief, type BriefActual } from './war'

export function BriefCard({
  latest,
  dates,
  latestActual,
  loading,
  onShown,
}: {
  latest: Brief | null
  /** 有晨報的日期，新到舊 */
  dates: string[]
  /** 最新那天若已收盤（首頁的大盤體檢同一天）就拿來對照 */
  latestActual: BriefActual | null
  loading: boolean
  /** 目前顯示的那份（新聞頭條卡跟著換） */
  onShown: (b: Brief | null) => void
}) {
  const [day, setDay] = useState<string | null>(null) // null＝最新
  const [hist, setHist] = useState<{ date: string; brief: Brief | null; actual: BriefActual | null } | null>(null)
  const [err, setErr] = useState('')
  const [factsOpen, setFactsOpen] = useState(false)

  useEffect(() => {
    if (day === null) {
      setHist(null)
      return
    }
    let stale = false
    setErr('')
    fetchBriefDay(day)
      .then((r) => !stale && setHist(r))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [day])

  const viewing = day === null || day === latest?.date ? null : day
  const b = viewing ? (hist?.date === viewing ? hist.brief : null) : latest
  const actual = viewing ? (hist?.date === viewing ? hist.actual : null) : latestActual
  const waiting = viewing !== null && hist?.date !== viewing && !err

  useEffect(() => {
    onShown(viewing ? b : null)
  }, [viewing, b, onShown])

  const current = viewing ?? latest?.date ?? null
  const { older, newer } = current ? briefNeighbors(dates, current) : { older: null, newer: null }
  const go = (d: string | null) => {
    setFactsOpen(false)
    setDay(d === latest?.date ? null : d)
  }

  return (
    <section className="cq-war-card cq-war-brief">
      <header>
        <h2>今日一句話</h2>
        {viewing && <i className="cq-tag">歷史</i>}
        {b && <span className="cq-war-when">整理於 {b.generated_at.slice(5, 16).replace('-', '/')}</span>}
        {current && dates.length > 1 && (
          <div className="cq-brief-nav">
            <button aria-label="前一天" disabled={!older} onClick={() => go(older)}>
              ‹
            </button>
            <select aria-label="選日期" value={current} onChange={(e) => go(e.target.value)}>
              {dates.map((d) => (
                <option key={d} value={d}>
                  {dayLabel(d)}
                  {d === latest?.date ? ' 最新' : ''}
                </option>
              ))}
            </select>
            <button aria-label="後一天" disabled={!newer} onClick={() => go(newer)}>
              ›
            </button>
            {viewing && (
              <button className="cq-brief-latest" onClick={() => go(null)}>
                回到最新
              </button>
            )}
          </div>
        )}
      </header>

      {err && <p className="cq-war-mute">讀不到 {viewing} 的晨報：{err}</p>}
      {(loading || waiting) && <p className="cq-war-mute">讀取中…</p>}
      {!loading && !waiting && !err && !b && (
        <p className="cq-war-mute">{viewing ? '這天沒有晨報。' : '今天的晨報還沒產生（每個交易日早上 6:30 整理）。'}</p>
      )}
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

      {viewing && b && (
        <p className="cq-brief-actual">
          {actual ? (
            <>
              <span>{dayLabel(actual.date)} 收盤實際：</span>
              <span>
                加權 <b className={tone(actual.taiex_chg)}>{pct(actual.taiex_chg)}</b>
              </span>
              {actual.otc_chg !== null && (
                <span>
                  櫃買 <b className={tone(actual.otc_chg)}>{pct(actual.otc_chg)}</b>
                </span>
              )}
              {actual.foreign_net !== null && (
                <span>
                  外資 <b className={tone(actual.foreign_net)}>{signedYi(actual.foreign_net)}</b>
                </span>
              )}
            </>
          ) : (
            <span className="cq-war-mute">{dayLabel(viewing)} 休市或還沒收盤，沒有實際收盤可對照。</span>
          )}
          {actual && <small>晨報寫在開盤前；這是那天收盤後的結果，拿來對照，不是晨報的一部分。</small>}
        </p>
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
  )
}
