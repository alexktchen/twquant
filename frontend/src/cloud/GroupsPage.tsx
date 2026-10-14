// 「族群」分頁（D-177 修訂）：每個族群今天拿到多少資金——資金倍數（成交比重對自己前 20 日平均）、漲跌家數，
// 點一個族群看成交金額最大的幾檔，以及其中哪些在今天的名單上。看資金是不是往同一個族群去。
import { useEffect, useMemo, useState } from 'react'
import { fetchGroups } from './api'
import { pct, trend } from './format'
import { flowText, rankRows, ratioText, yi, type RankRow } from './group'

/** 排行至少幾檔有成交才列（同 configs/stock_groups.yaml flow.hot_min_members）：兩三檔的族群一檔漲停就翻倍。 */
const MIN_MEMBERS = 3

export function GroupsPage({
  refresh,
  listed,
  onStock,
}: {
  refresh: number
  /** 名單日期與那天各族群在名單上的股票（只在跟族群資料同一天時標出來） */
  listed: { date: string; sids: Map<string, string[]> } | null
  /** 點成員：到「追蹤」分頁看那一檔的 K 線與評分 */
  onStock: (sid: string, name: string | null) => void
}) {
  const [data, setData] = useState<{ date: string; rows: RankRow[] } | null>(null)
  const [err, setErr] = useState('')
  const [hotOnly, setHotOnly] = useState(false)
  const [sel, setSel] = useState('')
  useEffect(() => {
    let stale = false
    setErr('')
    fetchGroups()
      .then((d) => !stale && setData(d))
      .catch((e: Error) => !stale && setErr(e.message))
    return () => {
      stale = true
    }
  }, [refresh])
  const rows = useMemo(() => rankRows(data?.rows ?? [], hotOnly, MIN_MEMBERS), [data, hotOnly])
  const onList = listed && data && listed.date === data.date ? listed.sids : null
  const current = rows.find((r) => r.grp === sel) ?? rows[0] ?? null
  const hotCount = (data?.rows ?? []).filter((r) => r.hot && r.n >= MIN_MEMBERS).length
  return (
    <main className="cq-body cq-groups">
      <aside className="cq-list">
        <div className="cq-groups-head">
          <span>
            族群資金 <b>{data?.date ?? ''}</b>
          </span>
          <div className="cq-seg cq-seg-small">
            <button className={!hotOnly ? 'on' : ''} onClick={() => setHotOnly(false)}>
              全部
            </button>
            <button className={hotOnly ? 'on' : ''} onClick={() => setHotOnly(true)}>
              資金流入 <em>{hotCount}</em>
            </button>
          </div>
        </div>
        <div className="cq-head">
          <span>族群／漲跌家數</span>
          <span>資金倍數</span>
          <span>平均漲幅</span>
        </div>
        <div className="cq-rows">
          {err && <p className="cq-empty">{err}</p>}
          {!err && !data && <p className="cq-empty">讀取中…</p>}
          {data && rows.length === 0 && <p className="cq-empty">{hotOnly ? '今天沒有資金流入的族群。' : '沒有族群資料。'}</p>}
          <ul>
            {rows.map((r) => {
              const mine = onList?.get(r.grp)?.length ?? 0
              return (
                <li key={r.grp} className={r.grp === current?.grp ? 'on' : ''} onClick={() => setSel(r.grp)}>
                  <div className="cq-name">
                    <b>
                      {r.grp}
                      {r.hot && <i className="cq-tag hot">資金流入</i>}
                      {mine > 0 && <i className="cq-tag prob">名單 {mine}</i>}
                    </b>
                    <small>
                      {r.n} 檔 {r.up} 漲 {r.down} 跌・比重 {(r.share * 100).toFixed(1)}%
                    </small>
                  </div>
                  <span className={`cq-ratio${r.hot ? ' hot' : ''}`}>{ratioText(r.ratio) ?? '—'}</span>
                  <span className={`cq-chg ${trend(r.ret)}`}>{pct(r.ret)}</span>
                </li>
              )
            })}
          </ul>
        </div>
        <p className="cq-note">
          資金倍數＝族群今天的成交金額佔全市場（上市櫃普通股）的比重，除以前 20 個交易日的平均比重；大於 1 是今天資金比平常多。
          倍數 ≥ 1.5 而且六成以上的股票上漲，標「資金流入」。族群來自櫃買中心產業價值鏈資訊平台，少於 {MIN_MEMBERS} 檔有成交的族群不列。觀察用，非投資建議。
        </p>
      </aside>
      <section className="cq-detail cq-group-detail">
        {current && (
          <>
            <div className="cq-quote">
              <div>
                <h1>
                  {current.grp}
                  {current.hot && <i className="cq-tag hot">資金流入</i>}
                </h1>
                <p className="cq-group">{flowText({ ...current, date: data?.date ?? '' })}</p>
              </div>
            </div>
            <h2 className="cq-sub">成交金額最大的 {current.members.length} 檔</h2>
            <ul className="cq-members">
              {current.members.map((m) => {
                const lines = onList?.get(current.grp)?.includes(m.sid)
                return (
                  <li key={m.sid} onClick={() => onStock(m.sid, m.name)}>
                    <span className="cq-name">
                      <b>
                        <em>{m.sid}</em>
                        {m.name ?? ''}
                        {lines && <i className="cq-tag prob">在名單</i>}
                      </b>
                    </span>
                    <span className="cq-amt">{yi(m.amount)}</span>
                    <span className={`cq-chg ${trend(m.chg)}`}>{pct(m.chg)}</span>
                  </li>
                )
              })}
            </ul>
          </>
        )}
      </section>
    </main>
  )
}
