// 個股儀表板的編號面板（SPEC-014 §9）。每個面板只呈現 API 回來的真實資料或由它直接算出的描述統計；
// 沒有資料來源的東西（預測路徑、隔日沖、主力語意…）不做，也不以示範數字佔位。
import { useState } from 'react'
import type { Candle, Screener } from '../api'
import { fetchScreener } from '../api'
import { useResource } from '../hooks'
import { ACC, DOWN, UP, WARN, pct, priceFmt, zhang } from '../theme'
import { volumeProfile } from './indicators.ts'
import { READING, categoryScores, coverageRows, crossSummary, energyShare, poolSignals, riskAxes, technicalReadings } from './readings.ts'
import { Donut, Gauge, Meter, PanelState, Radar, VolumeHeat, stateOf } from './DashPrimitives'
import { DATA_WINDOW, type StockData } from './useStockData'
import { Delta, MiniTS, TPanel } from './ui'

const VIOLET = '#a78bfa', CYAN = '#22d3ee'
const YEAR_BARS = 252 // 52 週約當的交易日數
const HEALTH_DONUT = 50 // 五個甜甜圈要在四分之一寬的面板排成一列
const NO_QUOTE = '行情尚未載入，此面板無法計算。'
const NOT_UNIVERSE = '這檔不在因子宇宙（ETF／非四碼普通股），或因子尚未物化。'
const md = (d: string) => d.slice(5).replace('-', '/')
const lots = (v: number) => `${v > 0 ? '+' : ''}${zhang(v).toLocaleString()}`
const sumNet = (rows: { net: number }[], n: number) => rows.slice(-n).reduce((s, r) => s + r.net, 0)

/* 技術摘要：規則寫在 readings.ts，這裡只排版 */
export function ReadingsPanel({ no, bars, data, className }: { no: string; bars: Candle[] | undefined; data: StockData; className?: string }) {
  const rowsOf = <T,>(res: { loading: boolean; live: boolean; data: { rows: T[] } | null }) => (res.loading || !res.live ? null : (res.data?.rows ?? []))
  const o = data.overview.data
  return (
    <TPanel no={no} title="技術摘要" sub="規則式判讀 · RULE-BASED SUMMARY" className={className}>
      {!bars ? <PanelState state="empty" empty={NO_QUOTE} /> : (
        <>
          <div className="dash-banner">⚠ 規則式描述 · 非預測訊號、非投資建議</div>
          <div className="dash-list">
            {technicalReadings({ bars, brokerFlow: rowsOf(data.flow), institutional: rowsOf(data.inst), margin: rowsOf(data.margin) }).map((r) => (
              <div key={r.key} className="dash-row"><span>{r.label}</span><span className={`tone-${r.tone}`}>{r.value}</span></div>
            ))}
            <div className="dash-row">
              <span>綜合因子分位</span>
              <span className={o?.composite ? (o.composite.pct >= 0.5 ? 'tone-up' : 'tone-down') : 'tone-muted'}>
                {o?.composite ? `${Math.round(o.composite.pct * 100)}%（${o.composite.n_universe} 檔）` : data.overview.loading ? '…' : '無（非因子宇宙或未載入）'}
              </span>
            </div>
          </div>
          <CrossNote bars={bars} />
        </>
      )}
    </TPanel>
  )
}

const CROSS_DATES_SHOWN = 3 // 交叉日只列最近幾次，次數仍是整個回看窗的總數
function CrossNote({ bars }: { bars: Candle[] }) {
  const c = crossSummary(bars)
  const dates = (days: string[]) => (days.length ? `（最近 ${days.slice(-CROSS_DATES_SHOWN).map(md).join('、')}）` : '')
  const part = (name: string, s: { golden: string[]; death: string[] }) =>
    `${name} 黃金交叉 ${s.golden.length} 次${dates(s.golden)}、死亡交叉 ${s.death.length} 次${dates(s.death)}`
  return <div className="dash-note">近 {READING.crossLookback} 日：{part('KD', c.kd)}；{part('MACD', c.macd)}。</div>
}

/* 因子雷達／風險雷達／健康度：同一份因子體檢卡的三種讀法 */
function useScorecard(data: StockData) {
  const card = data.scorecard.data
  return { card, composite: data.overview.data?.composite ?? null, state: stateOf(data.scorecard, card?.factors.length ?? 0) }
}

export function FactorRadarPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const { card, composite, state } = useScorecard(data)
  return (
    <TPanel no={no} title="多維度因子雷達" sub="各類因子方向化百分位 · 相對定位" className={className}>
      {state !== 'ok' || !card ? <PanelState state={state} empty={NOT_UNIVERSE} height={180} /> : (
        <>
          <Radar axes={[...categoryScores(card.factors).map((s) => ({ label: s.label, value: s.value })), { label: '綜合', value: composite?.pct ?? null }]} />
          <div className="dash-note">
            數值＝該類因子「站在因子方向」的橫斷面百分位平均（0~100）。物化日 {card.as_of ?? '—'}
            {composite ? ` · 宇宙 ${composite.n_universe} 檔` : ''} · 相對定位，非預測訊號（D-020）。
          </div>
        </>
      )}
    </TPanel>
  )
}

export function RiskRadarPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const { card, state } = useScorecard(data)
  return (
    <TPanel no={no} title="風險因子雷達" sub="波動／量能／融資／券資比／高檔位置" className={className}>
      {state !== 'ok' || !card ? <PanelState state={state} empty={NOT_UNIVERSE} height={180} /> : (
        <>
          <Radar axes={riskAxes(card.factors)} color={WARN} />
          <div className="dash-note">各軸＝該因子原始值在全市場的百分位（越高＝數值越大），不加權、不合成單一分數。</div>
        </>
      )}
    </TPanel>
  )
}

export function HealthPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const { card, composite, state } = useScorecard(data)
  const colors: Record<string, string> = { price: WARN, volume: CYAN, chip: ACC, fundamental: VIOLET }
  return (
    <TPanel no={no} title="因子健康度" sub="各類因子分位 · 相對定位" className={className}>
      {state !== 'ok' || !card ? <PanelState state={state} empty={NOT_UNIVERSE} /> : (
        <>
          <div className="dash-donuts">
            {categoryScores(card.factors).map((s) => <Donut key={s.category} value={s.value} label={s.label} color={colors[s.category]} size={HEALTH_DONUT} />)}
            <Donut value={composite?.pct ?? null} label="綜合" color={(composite?.pct ?? 0) >= 0.5 ? UP : DOWN} size={HEALTH_DONUT} />
          </div>
          <div className="dash-note">百分位越高＝越站在因子方向；「—」表示該類沒有可用因子，不以 0 代替。</div>
        </>
      )}
    </TPanel>
  )
}

/* 籌碼熱區圖：量價分布（volume-at-price） */
export function HeatPanel({ no, bars, className }: { no: string; bars: Candle[] | undefined; className?: string }) {
  const window = bars?.slice(-READING.profileDays)
  const vp = window ? volumeProfile(window) : null
  return (
    <TPanel no={no} title="籌碼熱區圖" sub={`近 ${window?.length ?? READING.profileDays} 日量價分布 · VOLUME AT PRICE`} className={className}>
      {!bars ? <PanelState state="empty" empty={NO_QUOTE} height={180} /> : !vp ? <PanelState state="empty" empty="K 棒太少或沒有成交量，無法計算量價分布。" height={180} /> : (
        <>
          <VolumeHeat profile={vp} close={bars[bars.length - 1].c} />
          <div className="dash-list" style={{ marginTop: 4 }}>
            <div className="dash-row"><span>最大量價位 POC</span><span className="tone-warn">{priceFmt(vp.poc)}</span></div>
            <div className="dash-row"><span>價值區（70% 量）</span><span>{priceFmt(vp.val)} ~ {priceFmt(vp.vah)}</span></div>
          </div>
          <div className="dash-note">越亮＝該價位成交量越大；紫線＝價值區。視覺化確認層，非預測因子。</div>
        </>
      )}
    </TPanel>
  )
}

/* 綜合因子分位趨勢 */
export function CompositeTrendPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const rows = data.comp.data?.rows ?? []
  const state = stateOf(data.comp, rows.length)
  const last = rows[rows.length - 1]
  const pcts = rows.map((r) => r.pct)
  return (
    <TPanel no={no} title="綜合因子分位趨勢" sub={`近 ${DATA_WINDOW.historyDays} 日 · 歷史定位，非預測路徑`} className={className}>
      {state !== 'ok' ? <PanelState state={state} empty={NOT_UNIVERSE} /> : (
        <>
          <MiniTS bars={rows.map((r) => r.signed_z_mean)} line={rows.map((r) => r.pct * 100)} height={96} />
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[11px] mono">
            <span className="text-[var(--muted)]">最新分位 <span style={{ color: last.pct >= 0.5 ? 'var(--up)' : 'var(--down)' }}>{Math.round(last.pct * 100)}%</span></span>
            <span className="text-[var(--muted)]">區間 {Math.round(Math.min(...pcts) * 100)}%~{Math.round(Math.max(...pcts) * 100)}%</span>
            <span className="text-[var(--muted)]">{rows[0].date} ~ {last.date}</span>
          </div>
          <div className="dash-note">棒＝signed-z 平均、線＝宇宙分位。這是「過去站在哪裡」，不是未來走勢的推估。</div>
        </>
      )}
    </TPanel>
  )
}

/* 主力買賣超 */
export function BrokerFlowPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const all = data.flow.data?.rows ?? []
  const state = stateOf(data.flow, all.length)
  const rows = all.slice(-60)
  let cum = 0
  return (
    <TPanel no={no} title="主力買賣超" sub="富邦DJ 券商 top-15± · 日條＋累計（張）" className={className}>
      {state !== 'ok' ? <PanelState state={state} /> : (
        <>
          <MiniTS bars={rows.map((r) => zhang(r.net))} line={rows.map((r) => (cum += zhang(r.net)))} height={96} />
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
            {[5, 20, 60].map((n) => <Delta key={n} label={`${n}日`} v={zhang(sumNet(all, n))} unit=" 張" />)}
            <span className="text-[11px] mono text-[var(--muted)] whitespace-nowrap">至 {all[all.length - 1].date}</span>
          </div>
          <div className="dash-note">來源為券商母公司層級，無分點明細、無成交均價，故不估「主力成本」。</div>
        </>
      )}
    </TPanel>
  )
}

/* 法人行為計量 */
export function InstPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const all = data.inst.data?.rows ?? []
  const state = stateOf(data.inst, all.length)
  const rows = all.slice(-60)
  const total = (r: (typeof all)[number]) => r.foreign_net + r.trust_net + r.dealer_net
  const cell = (v: number) => <td style={{ color: v > 0 ? 'var(--up)' : v < 0 ? 'var(--down)' : 'var(--muted)' }}>{lots(v)}</td>
  let cum = 0
  return (
    <TPanel no={no} title="法人行為計量" sub="三大法人買賣超（張）" className={className}>
      {state !== 'ok' ? <PanelState state={state} /> : (
        <div className="dash-split">
          <div>
            <MiniTS bars={rows.map((r) => zhang(total(r)))} line={rows.map((r) => (cum += zhang(total(r))))} height={104} />
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2">
              <Delta label="5日" v={zhang(all.slice(-5).reduce((s, r) => s + total(r), 0))} unit=" 張" />
              <Delta label="20日" v={zhang(all.slice(-20).reduce((s, r) => s + total(r), 0))} unit=" 張" />
            </div>
          </div>
          <table className="dash-table">
            <thead><tr><th>日期</th><th>外資</th><th>投信</th><th>自營</th><th>合計</th></tr></thead>
            <tbody>{all.slice(-5).reverse().map((r) => (
              <tr key={r.date}><td>{md(r.date)}</td>{cell(r.foreign_net)}{cell(r.trust_net)}{cell(r.dealer_net)}{cell(total(r))}</tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </TPanel>
  )
}

/* 融資券結構 */
export function MarginStructurePanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const all = data.margin.data?.rows ?? []
  const state = stateOf(data.margin, all.length)
  const rows = all.slice(-60)
  const last = all[all.length - 1]
  return (
    <TPanel no={no} title="融資券結構" sub="融資餘額（線）＋日增減（棒）· 張" className={className}>
      {state !== 'ok' ? <PanelState state={state} /> : (
        <>
          <MiniTS bars={rows.map((r) => zhang(r.margin_chg ?? 0))} line={rows.map((r) => zhang(r.margin_balance))} height={96} />
          <div className="dash-list" style={{ marginTop: 6 }}>
            <div className="dash-row"><span>融資餘額</span><span>{zhang(last.margin_balance).toLocaleString()} 張</span></div>
            <div className="dash-row"><span>融券餘額</span><span>{zhang(last.short_balance).toLocaleString()} 張</span></div>
            <div className="dash-row"><span>券資比</span><span>{last.short_margin_ratio == null ? '—' : `${(last.short_margin_ratio * 100).toFixed(1)}%`}</span></div>
          </div>
        </>
      )}
    </TPanel>
  )
}

/* 多空能量條 */
export function EnergyPanel({ no, bars, className }: { no: string; bars: Candle[] | undefined; className?: string }) {
  const e = bars ? energyShare(bars) : null
  return (
    <TPanel no={no} title="多空能量條" sub={`近 ${READING.energyDays} 日上漲／下跌日成交量`} className={className}>
      {!bars ? <PanelState state="empty" empty={NO_QUOTE} /> : !e ? <PanelState state="empty" empty="近期沒有可比較的漲跌日成交量。" /> : (
        <>
          <Meter label="上漲日量" ratio={e.up} text={`${Math.round(e.up * 100)}%`} color={UP} />
          <Meter label="下跌日量" ratio={1 - e.up} text={`${Math.round((1 - e.up) * 100)}%`} color={DOWN} />
          <div className="dash-list" style={{ marginTop: 6 }}>
            <div className="dash-row"><span>量能多空比</span><span className={e.up >= 0.5 ? 'tone-up' : 'tone-down'}>{e.up >= 1 ? '僅上漲日有量' : `${(e.up / (1 - e.up)).toFixed(2)} 倍`}</span></div>
            <div className="dash-row"><span>上漲／下跌日數</span><span>{e.upDays}／{e.downDays}（共 {e.days} 日）</span></div>
          </div>
          <div className="dash-note">每日收盤與前一日收盤比較後，把當日成交量歸到上漲或下跌。描述性統計。</div>
        </>
      )}
    </TPanel>
  )
}

/* 選股池信號：全市場回算約需十秒，故不隨開頁自動讀，按下才讀；讀到後換股沿用同一份。 */
export function PoolPanel({ no, sid, className }: { no: string; sid: string; className?: string }) {
  const [armed, setArmed] = useState(false)
  const res = useResource(`screener:${armed ? 'latest' : 'idle'}`, (): Promise<Screener | null> => (armed ? fetchScreener(DATA_WINDOW.poolSize) : Promise.resolve(null)), () => null)
  const scr = res.data
  const state = stateOf(res, scr?.pools.length ?? 0)
  return (
    <TPanel no={no} title="選股池信號" sub="策略選股池成員狀態（SPEC-007 §6）" className={className}>
      {!armed ? (
        <div className="dash-state" style={{ flexDirection: 'column', gap: 10 }}>
          <span>五個策略選股池的成員狀態（全市場即時回算，約需 10 秒）。</span>
          <button type="button" className="dash-btn" onClick={() => setArmed(true)}>讀取選股池狀態</button>
        </div>
      ) : state !== 'ok' || !scr ? <PanelState state={state} empty="今日沒有任何選股池資料。" /> : (
        <>
          <div className="dash-list">
            {poolSignals(scr.pools, sid).map((s) => (
              <div key={s.key} className="dash-row" style={{ alignItems: 'center' }}>
                <span className="flex items-center gap-2"><i className={`dash-light ${s.state === 'in' ? (s.key === 'main_distrib' ? 'is-warn' : 'is-in') : ''}`} />{s.name}</span>
                <span className={s.state === 'in' ? (s.key === 'main_distrib' ? 'tone-warn' : 'tone-up') : 'tone-muted'}>
                  {s.state === 'in' ? `在池 · 連 ${s.streak} 日` : s.state === 'out' ? '不在池' : `前 ${DATA_WINDOW.poolSize} 名內無（共 ${s.total} 檔）`}
                </span>
              </div>
            ))}
          </div>
          <div className="dash-note">{scr.as_of ?? '—'} · 宇宙 {scr.universe_n} 檔 · 描述性選股池，非交易訊號。</div>
        </>
      )}
    </TPanel>
  )
}

/* 52 週區間位置 */
export function RangeGaugePanel({ no, bars, className }: { no: string; bars: Candle[] | undefined; className?: string }) {
  const window = bars?.slice(-YEAR_BARS) ?? []
  const hi = Math.max(...window.map((b) => b.h)), lo = Math.min(...window.map((b) => b.l))
  const close = window[window.length - 1]?.c
  const ok = window.length >= 2 && hi > lo
  return (
    <TPanel no={no} title="52 週區間位置" sub={`近 ${window.length || YEAR_BARS} 個交易日高低點`} className={className}>
      {!bars ? <PanelState state="empty" empty={NO_QUOTE} /> : !ok ? <PanelState state="empty" empty="K 棒不足，無法計算區間位置。" /> : (
        <>
          <Gauge value={(close - lo) / (hi - lo)} caption="52 週區間位置" />
          <div className="dash-list">
            <div className="dash-row"><span>區間高／低</span><span><span className="tone-up">{priceFmt(hi)}</span>／<span className="tone-down">{priceFmt(lo)}</span></span></div>
            <div className="dash-row"><span>距高點</span><span>{pct(close / hi - 1)}</span></div>
          </div>
          {window.length < YEAR_BARS && <div className="dash-note">史料只有 {window.length} 日，未滿 52 週。</div>}
        </>
      )}
    </TPanel>
  )
}

/* 資料完整度 */
export function CoveragePanel({ no, bars, data, events, className }: { no: string; bars: Candle[] | undefined; data: StockData; events: { count: number; failed: boolean; loading: boolean }; className?: string }) {
  const src = <T,>(label: string, res: { loading: boolean; live: boolean; data: { rows: T[]; as_of: string | null } | null }, target: number) =>
    ({ label, rows: res.data?.rows.length ?? 0, target, asOf: res.data?.as_of ?? null, live: res.live, loading: res.loading })
  const factors = data.scorecard.data?.factors.length ?? 0
  const inputs = [
    { label: '日 K', rows: bars?.length ?? 0, target: DATA_WINDOW.historyDays, asOf: bars?.[bars.length - 1]?.d ?? null, live: bars !== undefined, loading: false },
    src('法人', data.inst, DATA_WINDOW.chipDays),
    src('融資券', data.margin, DATA_WINDOW.chipDays),
    src('主力券商', data.flow, DATA_WINDOW.historyDays),
    src('綜合因子', data.comp, DATA_WINDOW.historyDays),
    src('月營收', data.revenue, DATA_WINDOW.revenueMonths),
    { label: '因子體檢', rows: factors, target: Math.max(factors, 1), asOf: data.scorecard.data?.as_of ?? null, live: data.scorecard.live, loading: data.scorecard.loading },
    { label: '推薦事件', rows: events.count, target: Math.max(events.count, 1), asOf: null, live: !events.failed, loading: events.loading },
  ]
  const rows = coverageRows(inputs)
  // 月營收的資料日是月份（YYYY-MM），其餘是交易日；各自照原樣顯示，不硬湊成同一格式。
  const asOfText = (asOf: string) => (asOf.length === 7 ? asOf : md(asOf))
  return (
    <TPanel no={no} title="資料完整度" sub="各來源實際讀到的筆數與資料日" className={className}>
      {rows.map((r, i) => (
        <Meter key={r.label} label={r.label} ratio={inputs[i].loading ? null : r.ratio} color={r.state === 'ok' ? ACC : 'var(--muted)'}
          text={inputs[i].loading ? '讀取中…' : r.state === 'failed' ? '讀取失敗' : r.state === 'empty' ? '零筆' : `${r.rows} 筆${r.asOf ? ` · 至 ${asOfText(r.asOf)}` : ''}`} />
      ))}
      <div className="dash-note">條長＝讀到筆數÷請求窗長；「讀取失敗」與「零筆」分開，不以 0% 混稱。來源：PG 供應層。</div>
    </TPanel>
  )
}

/* 月營收 */
export function RevenueTrendPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const rows = data.revenue.data?.rows ?? []
  const state = stateOf(data.revenue, rows.length)
  const last = rows[rows.length - 1]
  const yi = (v: number | null) => (v == null ? 0 : v / 1e8)
  return (
    <TPanel no={no} title="月營收" sub={`近 ${DATA_WINDOW.revenueMonths} 個月 · 營收（棒，億元）＋YoY（線）`} className={className}>
      {state !== 'ok' ? <PanelState state={state} /> : (
        <>
          <MiniTS bars={rows.map((r) => yi(r.revenue))} line={rows.map((r) => (r.yoy ?? 0) * 100)} height={96} />
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
            <span className="text-[11px] mono text-[var(--muted)] whitespace-nowrap">{last.month} 營收 {last.revenue == null ? '—' : `${yi(last.revenue).toFixed(2)} 億`}</span>
            {last.yoy != null && <Delta label="YoY" v={Math.round(last.yoy * 1000) / 10} unit="%" />}
            {last.mom != null && <Delta label="MoM" v={Math.round(last.mom * 1000) / 10} unit="%" />}
          </div>
        </>
      )}
    </TPanel>
  )
}

/* 主力券商力道：來源本來就是買、賣各取前 15 名，「家數」恆為 15:15 沒有資訊量，故比的是兩邊的張數 */
export function BrokerCountPanel({ no, data, className }: { no: string; data: StockData; className?: string }) {
  const b = data.brokers.data
  const state = stateOf(data.brokers, (b?.top_buy.length ?? 0) + (b?.top_sell.length ?? 0))
  const buy = b?.top_buy.reduce((sum, r) => sum + r.net_shares, 0) ?? 0
  const sell = b?.top_sell.reduce((sum, r) => sum - r.net_shares, 0) ?? 0
  const names = (rows: { broker: string; net_shares: number }[], up: boolean) => rows.slice(0, 4).map((r) => (
    <div key={r.broker} className="dash-row"><span className="truncate">{r.broker}</span><span className={up ? 'tone-up' : 'tone-down'}>{lots(r.net_shares)}</span></div>
  ))
  return (
    <TPanel no={no} title="主力券商力道" sub={`近 ${b?.window_days ?? DATA_WINDOW.brokerDays} 日彙總 · 富邦DJ 買／賣超各前 15 名（張）`} className={className}>
      {state !== 'ok' || !b ? <PanelState state={state} /> : (
        <div className="dash-split">
          <div className="dash-donuts" style={{ alignItems: 'center' }}>
            <Donut value={buy + sell > 0 ? buy / (buy + sell) : null} label="買超張數佔比" color={UP} size={74} />
            <div className="dash-list" style={{ minWidth: 128 }}>
              <div className="dash-row"><span>買超合計</span><span className="tone-up">{lots(buy)}</span></div>
              <div className="dash-row"><span>賣超合計</span><span className="tone-down">{lots(-sell)}</span></div>
              <div className="dash-row"><span>淨額</span><span className={buy - sell >= 0 ? 'tone-up' : 'tone-down'}>{lots(buy - sell)}</span></div>
              <div className="dash-row"><span>資料日</span><span>{b.as_of ? md(b.as_of) : '—'}</span></div>
            </div>
          </div>
          <div className="dash-list">{names(b.top_buy, true)}{names(b.top_sell, false)}</div>
        </div>
      )}
    </TPanel>
  )
}

/* 洞悉摘要：只給筆數與立場，內文留在「洞悉」頁籤，同一段分析不在頁面上出現兩次 */
const STANCE: Record<string, string> = { support: '支持', neutral: '中性', conflict: '矛盾', insufficient_data: '資料不足' }
export function InsightSummaryPanel({ no, data, onOpen, className }: { no: string; data: StockData; onOpen: () => void; className?: string }) {
  const packs = data.insights.data ?? []
  const state = stateOf(data.insights, packs.length)
  const latest = [...packs].sort((a, b) => (a.as_of < b.as_of ? 1 : -1))[0]
  const count = (direction: string) => latest?.evidence.filter((e) => e.direction === direction).length ?? 0
  return (
    <TPanel no={no} title="洞悉摘要" sub="已保存的證據包 · LLM 分析為提議" className={className}>
      {state !== 'ok' ? <PanelState state={state} empty="這檔沒有已保存的洞悉證據包；不會在沒有證據時生成分析。" /> : (
        <>
          <div className="dash-list">
            <div className="dash-row"><span>證據包</span><span>{packs.length} 份</span></div>
            <div className="dash-row"><span>最新截點</span><span>{latest.as_of.slice(0, 16).replace('T', ' ')}</span></div>
            <div className="dash-row"><span>立場</span><span className={latest.analysis ? 'tone-warn' : 'tone-muted'}>{latest.analysis ? (STANCE[latest.analysis.stance ?? ''] ?? '未標立場') : `無分析：${latest.analysis_absent_reason ?? '未記錄原因'}`}</span></div>
            <div className="dash-row"><span>證據方向</span><span>支持 {count('support')}／反對 {count('contradict')}／缺口 {count('gap')}</span></div>
          </div>
          <button type="button" className="dash-btn" style={{ marginTop: 10 }} onClick={onOpen}>查看證據與分析內文 →</button>
        </>
      )}
    </TPanel>
  )
}
