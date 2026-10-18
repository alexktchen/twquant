// 個股頁「權證」（T-385）：最近幾天認購／認售成交金額、對標的成交金額的比、價平隱含波動、檔數。資料每天收盤後算一次。
import { useEffect, useState } from 'react'
import { fetchStockWarrants } from './api'
import { calDate } from './stockcal'
import { ivText, moneyText, putShareText, ratioText, warrantLine, type WarrantDay } from './warrants'

export function StockWarrants({ sid, refresh }: { sid: string; refresh: number }) {
  const [data, setData] = useState<{ sid: string; today: string; days: WarrantDay[] } | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let stale = false
    setErr('')
    fetchStockWarrants(sid)
      .then((r) => !stale && setData(r))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [sid, refresh])

  const mine = data?.sid === sid ? data : null
  if (err || !mine || mine.days.length === 0) return null // 沒權證或還沒算，不擋個股頁其他部分
  const latest = mine.days[0]
  return (
    <div className="cq-ref cq-stockcal cq-warrants">
      <h3>
        權證<small>這檔標的的認購／認售權證，{latest.n_call + latest.n_put} 檔有行情；每天收盤後算</small>
      </h3>
      <p className="cq-warrant-line">{warrantLine(latest)}</p>
      <ul>
        {mine.days.map((d, k) => (
          <li key={d.date} className={k === 0 ? 'soon' : 'past'}>
            <span className="cq-stockcal-date">{calDate(d.date, mine.today)}</span>
            <b>
              {moneyText(d.call_value)}／{moneyText(d.put_value)}
            </b>
            <span className="cq-stockcal-detail">
              {[putShareText(d), ratioText(d.value_ratio)].filter(Boolean).join('，')}
              <small>
                隱含波動 {ivText(d.iv_call)}／{ivText(d.iv_put)}
              </small>
            </span>
          </li>
        ))}
      </ul>
      <p className="cq-ref-note">
        金額欄是認購／認售成交金額。隱含波動只用距到期 20～120 天、履約價在標的價 ±15% 內的一般型權證，挑最價平那一檔反推（Black-Scholes）；
        沒有合格的或沒成交就是「—」。認售偏多、隱含波動突然升高，是權證市場在押方向或預期大波動，不是這檔會漲會跌的證據。
      </p>
    </div>
  )
}
