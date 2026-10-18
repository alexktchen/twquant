// 首頁「今日一句話」（T-377／D-188）：模型晨報（數字都檢查過），沒有就用規則版。
// 使用者 2026-10-17：可選日期看歷史晨報；歷史那天另列當天大盤實際收盤對照。讀的是存好的晨報，不重叫模型。
// 2026-10-18 改版（地鐵告示）：標題一行大字；每一點前面是【標籤】的圖示；風險一行；「數字」按鈕才展開 45 個數字；說明移到 title。
import { ChevronLeft, ChevronRight, Hash, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchBriefDay } from './api'
import { CardFrame, HomeIconSvg } from './CardFrame'
import { pct } from './format'
import { chgSub, parseBullet, signedBig } from './home'
import type { Slide } from './Reel'
import { Kpi } from './Section'
import { briefNeighbors, dayLabel, tone, type Brief, type BriefActual } from './war'

const kt = (x: number | null | undefined): 'up' | 'down' | 'plain' => {
  const t = tone(x)
  return t === 'flat' ? 'plain' : t
}

export function BriefCard({
  latest,
  dates,
  latestActual,
  loading,
  onShown,
  collapsed,
  onToggle,
}: {
  latest: Brief | null
  /** 有晨報的日期，新到舊 */
  dates: string[]
  /** 最新那天若已收盤（首頁的大盤體檢同一天）就拿來對照 */
  latestActual: BriefActual | null
  loading: boolean
  /** 目前顯示的那份（新聞頭條卡跟著換） */
  onShown: (b: Brief | null) => void
  collapsed: boolean
  onToggle: () => void
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

  // 收起時輪播：標題一則、每一點一則、風險一則；沒有模型版就輪播規則版那一句
  const slides: Slide[] = !b
    ? []
    : b.headline && b.bullets
      ? [
          { key: 'h', node: <p className="cq-war-headline cq-reel-text">{b.headline}</p> },
          ...b.bullets.map((x, k) => ({ key: `b${k}`, node: <p className="cq-reel-text">{x}</p> })),
          ...(b.risk_note ? [{ key: 'r', node: <p className="cq-war-risk cq-reel-text">⚠ {b.risk_note}</p> }] : []),
        ]
      : [{ key: 'rule', node: <p className="cq-reel-text">{b.rule_text}</p> }]

  const bullets = (b?.bullets ?? []).map(parseBullet)

  return (
    <CardFrame
      id="brief"
      className="cq-war-brief"
      collapsed={collapsed}
      onToggle={onToggle}
      slides={slides}
      empty={loading || waiting ? '讀取中…' : '今天的晨報還沒產生（每個交易日早上 6:30 整理）。'}
      when={b ? `${viewing ? '歷史・' : ''}${b.generated_at.slice(5, 16).replace('-', '/')}` : undefined}
      extra={
        !collapsed &&
        current &&
        dates.length > 1 && (
          <div className="cq-brief-nav">
            <button aria-label="前一天" disabled={!older} onClick={() => go(older)}>
              <ChevronLeft size={18} aria-hidden="true" />
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
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          </div>
        )
      }
    >
      {err && <p className="cq-war-mute">讀不到 {viewing} 的晨報：{err}</p>}
      {(loading || waiting) && <p className="cq-war-mute">讀取中…</p>}
      {!loading && !waiting && !err && !b && (
        <p className="cq-war-mute">{viewing ? '這天沒有晨報。' : '今天的晨報還沒產生（每個交易日早上 6:30 整理）。'}</p>
      )}
      {b && b.headline && b.bullets ? (
        <>
          <p className="cq-brief-headline">{b.headline}</p>
          <ul className="cq-brief-points" title="AI 依下面的數字整理，每句裡的數字都檢查過出自這些數字">
            {bullets.map((x, k) => (
              <li key={k}>
                <span className="cq-brief-tag">
                  <HomeIconSvg name={x.icon} size={15} />
                  {x.tag ?? ''}
                </span>
                <span>{x.text}</span>
              </li>
            ))}
          </ul>
          {b.risk_note && (
            <p className="cq-brief-risk">
              <TriangleAlert size={16} aria-hidden="true" />
              <span>{b.risk_note}</span>
            </p>
          )}
        </>
      ) : (
        b && <p className="cq-brief-headline cq-war-rule">{b.rule_text}</p>
      )}

      {viewing && b && (
        <div className="cq-brief-actual" title="晨報寫在開盤前；這是那天收盤後的結果，拿來對照，不是晨報的一部分">
          {actual ? (
            <div className="cq-tiles cq-tiles-3">
              <Kpi label={`${dayLabel(actual.date)} 加權`} value={pct(actual.taiex_chg)} sub="實際收盤" tone={kt(actual.taiex_chg)} />
              {actual.otc_chg !== null && <Kpi label="櫃買" value={pct(actual.otc_chg)} sub="實際收盤" tone={kt(actual.otc_chg)} />}
              {actual.foreign_net !== null && <Kpi label="外資" value={signedBig(actual.foreign_net)} sub="實際買賣超" tone={kt(actual.foreign_net)} />}
            </div>
          ) : (
            <p className="cq-war-mute">{dayLabel(viewing)} 休市或還沒收盤，沒有實際收盤可對照。</p>
          )}
        </div>
      )}

      {b && (
        <div className="cq-brief-foot">
          {b.absent_reason && !b.headline && <span className="cq-war-mute">AI 摘要這次沒有產生：{b.absent_reason}</span>}
          {b.facts && b.facts.length > 0 && (
            <button className="cq-hc-link" aria-expanded={factsOpen} onClick={() => setFactsOpen(!factsOpen)}>
              <Hash size={15} aria-hidden="true" />
              {factsOpen ? '收起數字' : `${b.facts.length} 個數字`}
            </button>
          )}
          {!viewing && latestActual && chgSub(latestActual.taiex_chg) !== '—' && (
            <span className="cq-brief-today" title="今天已收盤：加權實際漲跌">
              今收 <b className={tone(latestActual.taiex_chg)}>{chgSub(latestActual.taiex_chg)}</b>
            </span>
          )}
        </div>
      )}
      {factsOpen && b?.facts && (
        <ul className="cq-war-facts">
          {b.facts.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      )}
    </CardFrame>
  )
}
