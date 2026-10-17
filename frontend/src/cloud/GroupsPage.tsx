// 「族群」分頁（D-177 修訂）：每個族群今天拿到多少資金——資金倍數（成交比重對自己前 20 日平均）、漲跌家數，
// 點一個族群看成交金額最大的幾檔，以及其中哪些在今天的名單上。看資金是不是往同一個族群去。
// 預設熱力圖（T-355）：方塊大小＝成交金額，顏色＝平均漲跌或資金倍數；也可切回列表。
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { fetchGroups } from './api'
import { MoodCard } from './MoodCard'
import { pct, trend } from './format'
import { flowText, rankRows, ratioText, yi, type RankRow } from './group'
import { RATIO_HIGH, RATIO_LOW, RET_FULL, heatColor, squarify, tileLines, type HeatMode } from './heatmap'

/** 排行至少幾檔有成交才列（同 configs/stock_groups.yaml flow.hot_min_members）：兩三檔的族群一檔漲停就翻倍。 */
const MIN_MEMBERS = 3

export function GroupsPage({
  refresh,
  listed,
  onStock,
  withMood = true,
}: {
  refresh: number
  /** 名單日期與那天各族群在名單上的股票（只在跟族群資料同一天時標出來） */
  listed: { date: string; sids: Map<string, string[]> } | null
  /** 點成員：到「追蹤」分頁看那一檔的 K 線與評分 */
  onStock: (sid: string, name: string | null) => void
  /** 頂端的市場氛圍；首頁戰情室已經放在上面，折疊的熱力圖裡不再重複（T-379） */
  withMood?: boolean
}) {
  const [data, setData] = useState<{ date: string; rows: RankRow[] } | null>(null)
  const [err, setErr] = useState('')
  const [hotOnly, setHotOnly] = useState(false)
  const [sel, setSel] = useState('')
  const [view, setView] = useState<'heat' | 'list'>('heat')
  const [mode, setMode] = useState<HeatMode>('ret')
  const detailRef = useRef<HTMLElement>(null)
  // 手機上族群在上、成員在下：點了方塊捲到成員
  const pick = (grp: string) => {
    setSel(grp)
    if (window.matchMedia('(max-width: 820px)').matches) detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
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
    <main className={`cq-body cq-groups${view === 'heat' ? ' cq-groups-heat' : ''}`}>
      {withMood && <MoodCard enabled />}
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
        <div className="cq-groups-head cq-groups-view">
          <div className="cq-seg cq-seg-small">
            <button className={view === 'heat' ? 'on' : ''} onClick={() => setView('heat')}>
              熱力圖
            </button>
            <button className={view === 'list' ? 'on' : ''} onClick={() => setView('list')}>
              列表
            </button>
          </div>
          {view === 'heat' && (
            <div className="cq-seg cq-seg-small">
              <span className="cq-seg-label">顏色</span>
              <button className={mode === 'ret' ? 'on' : ''} onClick={() => setMode('ret')}>
                漲跌
              </button>
              <button className={mode === 'ratio' ? 'on' : ''} onClick={() => setMode('ratio')}>
                資金倍數
              </button>
            </div>
          )}
        </div>
        {err && <p className="cq-empty">{err}</p>}
        {!err && !data && <p className="cq-empty">讀取中…</p>}
        {data && rows.length === 0 && <p className="cq-empty">{hotOnly ? '今天沒有資金流入的族群。' : '沒有族群資料。'}</p>}
        {view === 'heat' && rows.length > 0 && <Heatmap rows={rows} mode={mode} current={current?.grp ?? ''} onList={onList} onPick={pick} />}
        {view === 'heat' && rows.length > 0 && <Legend mode={mode} />}
        {view === 'list' && (
          <div className="cq-head">
            <span>族群／漲跌家數</span>
            <span>資金倍數</span>
            <span>平均漲幅</span>
          </div>
        )}
        {view === 'list' && (
          <div className="cq-rows">
            <ul>
              {rows.map((r) => {
                const mine = onList?.get(r.grp)?.length ?? 0
                return (
                  <li key={r.grp} className={r.grp === current?.grp ? 'on' : ''} onClick={() => pick(r.grp)}>
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
        )}
        <p className="cq-note">
          {view === 'heat' && '方塊大小＝族群今天的成交金額，顏色＝平均漲跌（紅漲綠跌）或資金倍數（橘＝比平常多、藍＝比平常少），橘框＝資金流入。'}
          資金倍數＝族群今天的成交金額佔全市場（上市櫃普通股）的比重，除以前 20 個交易日的平均比重；大於 1 是今天資金比平常多。 倍數 ≥ 1.5
          而且六成以上的股票上漲，標「資金流入」。族群來自櫃買中心產業價值鏈資訊平台，少於 {MIN_MEMBERS} 檔有成交的族群不列。觀察用，非投資建議。
        </p>
      </aside>
      <section className="cq-detail cq-group-detail" ref={detailRef}>
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

/** 熱力圖：量容器大小、切方塊；點方塊選族群。 */
function Heatmap({
  rows,
  mode,
  current,
  onList,
  onPick,
}: {
  rows: RankRow[]
  mode: HeatMode
  current: string
  onList: Map<string, string[]> | null
  onPick: (grp: string) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const byKey = useMemo(() => new Map(rows.map((r) => [r.grp, r])), [rows])
  const tiles = useMemo(
    () =>
      squarify(
        rows.map((r) => ({ key: r.grp, value: r.share })),
        { x: 0, y: 0, w: size.w, h: size.h },
      ),
    [rows, size],
  )
  return (
    <div className="cq-heat" ref={box}>
      {tiles.map((t) => {
        const r = byKey.get(t.key)!
        const lines = tileLines(t)
        const mine = onList?.get(r.grp)?.length ?? 0
        const value = mode === 'ret' ? r.ret : r.ratio
        // 上色的那個數字寫在前面；夠高再寫另一個
        const nums = mode === 'ret' ? [pct(r.ret), ratioText(r.ratio) ?? '—'] : [ratioText(r.ratio) ?? '—', pct(r.ret)]
        return (
          <button
            key={t.key}
            className={`cq-tile${r.hot ? ' hot' : ''}${t.key === current ? ' on' : ''}`}
            style={{ left: t.x, top: t.y, width: t.w, height: t.h, background: heatColor(value, mode) }}
            title={`${r.grp}：${flowText({ ...r, date: '' })}${mine ? `；名單 ${mine} 檔` : ''}`}
            onClick={() => onPick(r.grp)}
          >
            {lines >= 1 && <b>{r.grp}</b>}
            {lines >= 2 && <span>{nums[0]}</span>}
            {lines >= 3 && <span className="cq-tile-sub">{nums[1]}</span>}
            {lines >= 1 && mine > 0 && <i>名單 {mine}</i>}
          </button>
        )
      })}
    </div>
  )
}

/** 顏色說明：漲跌 −5%～+5%，或資金倍數 0.4～2.5 倍。 */
function Legend({ mode }: { mode: HeatMode }) {
  const stops = mode === 'ret' ? [-RET_FULL, -RET_FULL / 2, 0, RET_FULL / 2, RET_FULL] : [RATIO_LOW, 0.7, 1, 1.75, RATIO_HIGH]
  const text = mode === 'ret' ? (v: number) => pct(v, 0) : (v: number) => `${v} 倍`
  return (
    <div className="cq-heat-legend">
      {stops.map((v) => (
        <span key={v} style={{ background: heatColor(v, mode) }}>
          {text(v)}
        </span>
      ))}
    </div>
  )
}
