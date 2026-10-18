// 個股頁「籌碼與風險」：停損基礎率（D-191 C）、千張大戶（T-386）、權證（T-385）。
// 2026-10-18 改版（使用者：「一目瞭然…不要把一堆字丟出來」）：四張數字卡（標籤、大數字、一行小字），點卡片才展開細節表。
import { ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchStockHolders, fetchStockStopHit, fetchStockWarrants } from './api'
import { factTiles, type FactKey } from './facts'
import { ratioText as pctText, weekChange, type HoldersWeek } from './holders'
import { Kpi, Section } from './Section'
import { stopHitTitle, type StopHit } from './stophit'
import { calDate } from './stockcal'
import { ivText, moneyText, putShareText, ratioText, type WarrantDay } from './warrants'

type Data = { sid: string; today: string; hit: StopHit | null; weeks: HoldersWeek[]; days: WarrantDay[] }

export function StockFacts({ sid, refresh }: { sid: string; refresh: number }) {
  const [data, setData] = useState<Data | null>(null)
  const [open, setOpen] = useState<FactKey | null>(null)

  useEffect(() => {
    let stale = false
    setOpen(null)
    // 三個來源各自容錯：哪個讀不到就當沒有，不擋個股頁
    Promise.all([
      fetchStockStopHit(sid).catch(() => null),
      fetchStockHolders(sid).catch(() => null),
      fetchStockWarrants(sid).catch(() => null),
    ]).then(([h, b, w]) => {
      if (stale) return
      setData({ sid, today: h?.today ?? b?.today ?? w?.today ?? '', hit: h?.hit ?? null, weeks: b?.weeks ?? [], days: w?.days ?? [] })
    })
    return () => {
      stale = true
    }
  }, [sid, refresh])

  const mine = data?.sid === sid ? data : null
  if (!mine) return null
  const tiles = factTiles(mine)
  if (tiles.length === 0) return null

  const detail = (key: FactKey) => {
    if (key === 'hit' && mine.hit) return <p className="cq-fact-more">{stopHitTitle(mine.hit)}</p>
    if (key === 'big')
      return (
        <div className="cq-fact-more">
          <table className="cq-mini">
            <tbody>
              {[...mine.weeks].reverse().map((w, k) => (
                <tr key={w.date}>
                  <td>{calDate(w.date, mine.today)}</td>
                  <td className="num">{pctText(w.pct)}</td>
                  <td className="num">{weekChange(mine.weeks, mine.weeks.length - 1 - k)}</td>
                  <td className="num">{w.big_holders === null ? '' : `${w.big_holders.toLocaleString('zh-TW')} 人`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>持股 1,000 張以上的人占總股數，集保每週六公布。</p>
        </div>
      )
    if (key === 'wr')
      return (
        <div className="cq-fact-more">
          <table className="cq-mini">
            <thead>
              <tr>
                <th>日期</th>
                <th className="num">認購／認售</th>
                <th className="num">認售比</th>
                <th className="num">IV</th>
              </tr>
            </thead>
            <tbody>
              {mine.days.map((d) => (
                <tr key={d.date} title={ratioText(d.value_ratio)}>
                  <td>{calDate(d.date, mine.today)}</td>
                  <td className="num">
                    {moneyText(d.call_value)}／{moneyText(d.put_value)}
                  </td>
                  <td className="num">{putShareText(d).replace('認售 ', '')}</td>
                  <td className="num">{ivText(d.iv_call)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>IV＝最價平的一般型認購權證反推的隱含波動，只跟自己前幾天比。</p>
        </div>
      )
    return null
  }

  return (
    <Section icon={ShieldAlert} title="籌碼與風險" meta="點卡片看細節">
      <div className="cq-kpis cq-kpis-4">
        {tiles.map((t) => (
          <Kpi
            key={t.id}
            label={t.label}
            value={t.value}
            sub={t.sub}
            tone={t.tone}
            on={open === t.key}
            onClick={() => setOpen((k) => (k === t.key ? null : t.key))}
          />
        ))}
      </div>
      {open && detail(open)}
    </Section>
  )
}
