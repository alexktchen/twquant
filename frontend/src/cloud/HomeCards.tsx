// 首頁戰情室的小卡（使用者 2026-10-17：每張卡可移動、可收起，收起時輪播重點）。
// 2026-10-18 改版（地鐵告示）：表格改成數字卡（標籤、大數字、一行小字），說明文字移到滑過的 title；數字紅漲綠跌。
// 只供看盤：這些數字都不進選股、不進模型。
import { ArrowRight, ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchGroups } from './api'
import { CardFrame } from './CardFrame'
import { GroupsPage } from './GroupsPage'
import { pct } from './format'
import { ratioText, type RankRow } from './group'
import { bigMoney, chgSub, lots, signedBig } from './home'
import type { Slide } from './Reel'
import type { Scorecard } from './scorecard'
import { Kpi } from './Section'
import { SCOPE_LABEL, SESSION_MINUTES, breadth, dayLabel, firstLiveExit, scopeTrend, signedYi, sparkPoints, streakText, tone, type Breadth, type Brief, type MarketView, type Scope, type WarRoomData } from './war'

type Fold = { collapsed: boolean; onToggle: () => void }
type Market = NonNullable<WarRoomData['market']>
type Taifex = NonNullable<WarRoomData['taifex']>

/** 漲跌色 → 數字卡顏色。 */
const kt = (x: number | null | undefined): 'up' | 'down' | 'plain' => {
  const t = tone(x)
  return t === 'flat' ? 'plain' : t
}

/** 一則「名稱：數值（備註）」的輪播。 */
function kv(key: string, label: string, value: string, cls?: string, note?: string | null): Slide {
  return {
    key,
    node: (
      <p className="cq-reel-text cq-reel-kv">
        <span>{label}</span>
        <b className={cls}>{value}</b>
        {note && <small className="cq-war-mute">{note}</small>}
      </p>
    ),
  }
}

const intlValue = (q: { symbol: string; last: number }) =>
  q.symbol === '^TNX' ? `${q.last.toFixed(2)}%` : q.last.toLocaleString('en-US', { maximumFractionDigits: 2 })

export function IntlCard({ b, ...fold }: { b: Brief | null } & Fold) {
  const slides = [
    ...(b?.extras ?? []).map((q) => kv(q.symbol, q.name, `${intlValue(q)}　${pct(q.chg)}`, tone(q.chg))),
    ...(b && b.adr_premium !== null
      ? [kv('adr', '台積電 ADR 折溢價', `${b.adr_premium >= 0 ? '溢價' : '折價'} ${Math.abs(b.adr_premium * 100).toFixed(1)}%`, tone(b.adr_premium))]
      : []),
  ]
  return (
    <CardFrame id="intl" when={b ? `${b.generated_at.slice(5, 16).replace('-', '/')} 快照` : undefined} slides={slides} empty="今早的快照還沒有。" {...fold}>
      {b?.extras && b.extras.length > 0 ? (
        <div className="cq-tiles" title="VIX＝美股恐慌指數；ADR 折溢價＝台積電 ADR 換算台幣後對台股收盤（1 張 ADR＝5 股）">
          {b.extras.map((q) => (
            <Kpi key={q.symbol} label={q.name} value={intlValue(q)} sub={chgSub(q.chg)} tone={kt(q.chg)} />
          ))}
          {b.adr_premium !== null && (
            <Kpi label="ADR 折溢價" value={`${Math.abs(b.adr_premium * 100).toFixed(1)}%`} sub={b.adr_premium >= 0 ? '溢價' : '折價'} tone={kt(b.adr_premium)} />
          )}
        </div>
      ) : (
        <p className="cq-war-mute">今早的快照還沒有。</p>
      )}
    </CardFrame>
  )
}

const oiText = (x: number | null) => (x === null ? '—' : `${x >= 0 ? '淨多' : '淨空'} ${Math.abs(x).toLocaleString()} 口`)
const ratio = (x: number | null) => (x === null ? '—' : `${x.toFixed(1)}%`)

export function TaifexCard({ t, ...fold }: { t: Taifex | null } & Fold) {
  const slides = t
    ? [
        kv('f', '外資台指期淨未平倉', oiText(t.fut_foreign_net_oi), tone(t.fut_foreign_net_oi)),
        kv('td', '投信／自營', `${t.fut_trust_net_oi?.toLocaleString() ?? '—'} ／ ${t.fut_dealer_net_oi?.toLocaleString() ?? '—'} 口`),
        kv('pco', '選擇權未平倉買賣權比', ratio(t.pc_oi_ratio)),
        kv('pcv', '選擇權成交量買賣權比', ratio(t.pc_vol_ratio)),
      ]
    : []
  const f = lots(t?.fut_foreign_net_oi)
  const tr = lots(t?.fut_trust_net_oi)
  const d = lots(t?.fut_dealer_net_oi)
  return (
    <CardFrame id="taifex" when={t ? `${dayLabel(t.date)} 盤後` : undefined} slides={slides} empty="期交所盤後統計還沒有。" {...fold}>
      {t ? (
        <div className="cq-tiles" title="淨空＝外資在台指期貨的空單比多單多；買賣權比＝賣權÷買權，大於 100% 代表避險（賣權）比較多">
          <Kpi label="外資期貨" value={f.value} sub={`${f.label} 口`} tone={kt(t.fut_foreign_net_oi)} />
          <Kpi label="投信期貨" value={tr.value} sub={`${tr.label} 口`} tone={kt(t.fut_trust_net_oi)} />
          <Kpi label="自營期貨" value={d.value} sub={`${d.label} 口`} tone={kt(t.fut_dealer_net_oi)} />
          <Kpi label="買賣權比" value={ratio(t.pc_oi_ratio)} sub={`未平倉・成交量 ${ratio(t.pc_vol_ratio)}`} tone={t.pc_oi_ratio !== null && t.pc_oi_ratio > 100 ? 'warn' : 'plain'} />
        </div>
      ) : (
        <p className="cq-war-mute">期交所盤後統計還沒有。</p>
      )}
    </CardFrame>
  )
}

/** 分時走勢小圖（盤中、今日收盤速報才有）：虛線＝昨收，紅漲綠跌；左邊標今天最高、最低，底下標開收盤時間（使用者 2026-10-19 問「圖怎麼看？」）。 */
function Spark({ name, trend, prev, fmt }: { name: string; trend: [string, number][]; prev: number | null; fmt: (x: number) => string }) {
  const H = 64
  const p = sparkPoints(trend, prev, H)
  if (!p) return null
  const last = trend[trend.length - 1][1]
  const cls = prev === null ? 'flat' : last > prev ? 'up' : last < prev ? 'down' : 'flat'
  // 最高、最低靠太近（平盤日）只標最高，免得字疊在一起
  const showLow = p.low.y - p.high.y >= 16
  return (
    <div className="cq-spark-box">
      <div className="cq-spark-name">{name}今日走勢</div>
      <div className="cq-spark-wrap">
        <svg className={`cq-spark ${cls}`} viewBox={`0 0 ${SESSION_MINUTES} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${name}今天的分時走勢`}>
          {p.base !== null && <line className="base" x1="0" x2={SESSION_MINUTES} y1={p.base} y2={p.base} vectorEffect="non-scaling-stroke" />}
          <polyline points={p.line} vectorEffect="non-scaling-stroke" />
        </svg>
        {p.base !== null && prev !== null && (
          <span className="cq-spark-tag" style={{ top: p.base }}>
            昨收 {fmt(prev)}
          </span>
        )}
        <span className="cq-spark-v" style={{ top: p.high.y }}>
          高 {fmt(p.high.value)}
        </span>
        {showLow && (
          <span className="cq-spark-v" style={{ top: p.low.y }}>
            低 {fmt(p.low.value)}
          </span>
        )}
      </div>
      <div className="cq-spark-axis">
        <span>9:00</span>
        <span>13:30</span>
      </div>
    </div>
  )
}

const taiexFmt = (x: number) => x.toLocaleString('en-US', { maximumFractionDigits: 0 })
const otcFmt = (x: number) => x.toFixed(2)

/** 卡片標題旁：盤後體檢的日期；盤中、今日收盤速報一定是今天，只放橘色小框（窄卡片放不下日期）。 */
function marketWhen(v: MarketView) {
  if (v.kind === 'close') return dayLabel(v.date)
  return (
    <em className="cq-live cq-live-head" title="盤中每分鐘更新指數與成交金額；家數約 10 分鐘掃完全市場一次。盤後約 17:40 換成證交所正式收盤資料">
      {v.kind === 'live' ? `盤中 ${v.time}` : '今日收盤速報'}
    </em>
  )
}

/** 成交金額的小字：盤中＝到目前已達 20 日平均一整天的幾成；收盤後（含速報）＝對 20 日平均的倍數。 */
const amountSub = (v: MarketView, b: Breadth) =>
  b.amount_ratio === null ? '' : v.kind === 'live' ? `已達均量 ${Math.round(b.amount_ratio * 100)}%` : `20 日的 ${b.amount_ratio.toFixed(2)} 倍`

export function MarketCard({ v, ...fold }: { v: MarketView | null } & Fold) {
  const [scope, setScope] = useState<Scope>('all')
  const canSplit = v !== null && v.scopes.tse !== null && v.scopes.otc !== null
  const sc: Scope = canSplit ? scope : 'all'
  const b = v ? (v.scopes[sc] ?? v.scopes.all) : null
  const counted = b !== null && b.up !== null && b.down !== null && b.flat !== null
  const br = b && counted ? breadth(b.up ?? 0, b.down ?? 0, b.flat ?? 0) : null
  const all = v?.scopes.all ?? null
  const slides =
    v && all
      ? [
          kv('taiex', '加權', `${v.taiex_close !== null ? taiexFmt(v.taiex_close) : '—'}　${pct(v.taiex_chg)}`, tone(v.taiex_chg)),
          kv('otc', '櫃買', `${v.otc_close?.toFixed(2) ?? '—'}　${pct(v.otc_chg)}`, tone(v.otc_chg)),
          ...(all.up !== null
            ? [
                kv('br', '漲跌家數', `${all.up} 漲／${all.flat} 平／${all.down} 跌`, tone((all.up ?? 0) - (all.down ?? 0))),
                kv('lim', '漲停／跌停', `${all.limit_up} ／ ${all.limit_down} 家`),
              ]
            : []),
          kv('amt', '成交金額', `${Math.round(all.amount / 1e8).toLocaleString()} 億`, undefined, amountSub(v, all) || null),
        ]
      : []
  const st = v ? scopeTrend(v, sc) : null
  const empty = '大盤體檢還沒有（盤中每分鐘更新，收盤後約 17:40 換成正式資料）。'
  return (
    <CardFrame id="market" when={v ? marketWhen(v) : undefined} slides={slides} empty={empty} {...fold}>
      {v && b ? (
        <div className="cq-mkt" title="家數與成交金額只算上市櫃普通股，不含 ETF、權證">
          <div className="cq-tiles cq-tiles-2">
            <Kpi label="加權" value={v.taiex_close !== null ? taiexFmt(v.taiex_close) : '—'} sub={chgSub(v.taiex_chg)} tone={kt(v.taiex_chg)} />
            <Kpi label="櫃買" value={v.otc_close?.toFixed(2) ?? '—'} sub={chgSub(v.otc_chg)} tone={kt(v.otc_chg)} />
          </div>
          {canSplit && (
            <div className="cq-seg cq-seg-small cq-mkt-scope" role="tablist" aria-label="範圍">
              {(['all', 'tse', 'otc'] as Scope[]).map((k) => (
                <button key={k} className={sc === k ? 'on' : ''} role="tab" aria-selected={sc === k} onClick={() => setScope(k)}>
                  {SCOPE_LABEL[k]}
                </button>
              ))}
            </div>
          )}
          {st && st.trend.length > 1 && <Spark name={st.name} trend={st.trend} prev={st.prev} fmt={sc === 'otc' ? otcFmt : taiexFmt} />}
          {br ? (
            <div className="cq-breadth" aria-label={`${b.up} 漲 ${b.down} 跌 ${b.flat} 平`}>
              <span className="up" style={{ width: `${br.up}%` }}>
                {b.up} 漲
              </span>
              <span className="flat" style={{ width: `${br.flat}%` }}>
                {br.flat >= 12 ? `${b.flat} 平` : ''}
              </span>
              <span className="down" style={{ width: `${br.down}%` }}>
                {b.down} 跌
              </span>
            </div>
          ) : (
            <p className="cq-war-mute cq-breadth-wait">家數統計中（約 10 分鐘掃完全市場）</p>
          )}
          <div className="cq-tiles cq-tiles-3">
            <Kpi label="漲停 / 跌停" value={counted ? `${b.limit_up} / ${b.limit_down}` : '—'} sub="家" tone={counted ? kt((b.limit_up ?? 0) - (b.limit_down ?? 0)) : 'plain'} />
            <Kpi label="新高 / 新低" value={`${b.new_high ?? '—'} / ${b.new_low ?? '—'}`} sub="20 日・家" tone={kt((b.new_high ?? 0) - (b.new_low ?? 0))} />
            <Kpi label="成交" value={bigMoney(b.amount)} sub={amountSub(v, b)} tone={v.kind === 'close' && b.amount_ratio !== null && b.amount_ratio >= 1.3 ? 'warn' : 'plain'} />
          </div>
        </div>
      ) : (
        <p className="cq-war-mute">{empty}</p>
      )}
    </CardFrame>
  )
}

export function ChipsCard({ m, ...fold }: { m: Market | null } & Fold) {
  const slides = m
    ? [
        kv('f', '外資', signedYi(m.foreign_net), tone(m.foreign_net), streakText(m.foreign_streak)),
        kv('t', '投信', signedYi(m.trust_net), tone(m.trust_net)),
        kv('d', '自營商', signedYi(m.dealer_net), tone(m.dealer_net)),
        kv('mg', '融資餘額變化', signedYi(m.margin_delta), tone(m.margin_delta), m.margin_balance === null ? null : `餘額約 ${Math.round(m.margin_balance / 1e8).toLocaleString()} 億`),
      ]
    : []
  return (
    <CardFrame id="chips" when={m ? `${dayLabel(m.date)}・估算` : undefined} slides={slides} {...fold}>
      {m ? (
        <div className="cq-tiles" title="估算＝每檔買賣超（或融資）股數 × 當天收盤加總，會和證交所公布的金額差幾個百分點。看方向與量級用">
          <Kpi label="外資" value={signedBig(m.foreign_net)} sub={streakText(m.foreign_streak) || '買賣超'} tone={kt(m.foreign_net)} />
          <Kpi label="投信" value={signedBig(m.trust_net)} sub="買賣超" tone={kt(m.trust_net)} />
          <Kpi label="自營商" value={signedBig(m.dealer_net)} sub="買賣超" tone={kt(m.dealer_net)} />
          <Kpi label="融資" value={signedBig(m.margin_delta)} sub={m.margin_balance === null ? '餘額變化' : `餘額 ${bigMoney(m.margin_balance)}`} tone={kt(m.margin_delta)} />
        </div>
      ) : (
        <p className="cq-war-mute">還沒有資料。</p>
      )}
    </CardFrame>
  )
}

export function PicksCard({ picks, onPicks, ...fold }: { picks: WarRoomData['picks'] | null; onPicks: (line?: string) => void } & Fold) {
  const row = (l: NonNullable<WarRoomData['picks']>['lines'][number]) => (
    <button key={l.code} className="cq-tile-btn" onClick={() => onPicks(l.code)}>
      <span className="cq-kpi-label">{l.name}</span>
      <span className="cq-kpi-row">
        <b className="cq-kpi-value plain">
          {l.n}
          <small> 檔</small>
        </b>
        <ArrowRight size={18} aria-hidden="true" />
      </span>
      <span className="cq-kpi-sub">{l.new > 0 ? `新上榜 ${l.new} 檔` : '沒有新上榜'}</span>
    </button>
  )
  const slides = (picks?.lines ?? []).map((l) => kv(l.code, l.name, `${l.n} 檔`, undefined, `新上榜 ${l.new}`))
  return (
    <CardFrame id="picks" when={picks ? `${dayLabel(picks.date)} 收盤後` : undefined} slides={slides} empty="還沒有名單。" {...fold}>
      {picks ? <div className="cq-tiles cq-tiles-2">{picks.lines.map(row)}</div> : <p className="cq-war-mute">還沒有名單。</p>}
    </CardFrame>
  )
}

/** 分數還沒讀到時顯示的線別名稱（跟 K 線圖例同一套）。 */
const LINE_FALLBACK: Record<string, string> = { CORE: '大盤精選', QUIET: '潛伏飆股', VOLUME: '高分爆量' }

export function ScoreHomeCard({
  lines,
  scores,
  onScore,
  ...fold
}: { lines: string[]; scores: Record<string, Scorecard | null>; onScore: (line: string) => void } & Fold) {
  const waitDate = (s: Scorecard) => (s.live_since ? dayLabel(firstLiveExit(s.live_since, s.trades.hold_sessions, s.nav.map((x) => x.d))) : null)
  const slides: Slide[] = lines.flatMap((code) => {
    const s = scores[code]
    if (!s) return []
    const st = s.trades.stats.live
    const a = s.account.live
    return [
      st.n > 0
        ? kv(`${code}t`, `${s.name} 公布後已賣出 ${st.n} 筆`, `每筆平均 ${pct(st.mean)}`, tone(st.mean), `中位數 ${pct(st.median)}、最差 ${pct(st.worst)}`)
        : kv(`${code}t`, s.name, `還沒有抱滿 ${s.trades.hold_sessions} 天的`, undefined, waitDate(s) ? `約 ${waitDate(s)} 起賣出` : null),
      ...(a ? [kv(`${code}a`, `${s.name} 公布後模擬帳戶`, pct(a.ret, 1), tone(a.ret), `${dayLabel(a.start)} 起 ${a.n_days} 個交易日`)] : []),
    ]
  })
  return (
    <CardFrame
      id="score"
      when="公布後的每一筆"
      slides={slides}
      empty="名單成績讀取中…"
      extra={
        <button className="cq-hc-link" onClick={() => onScore(lines[0])}>
          詳細 <ChevronRight size={16} aria-hidden="true" />
        </button>
      }
      {...fold}
    >
      <div
        className="cq-score-lines"
        title="照名單規則當成真的買（含手續費、證交稅），只算公布之後已賣出的。看每筆平均不看勝率（台股大多時候在漲，隨便買的勝率也有七成多）。只拿來看，不拿來挑股或調模型"
      >
        {lines.map((code) => {
          const s = scores[code]
          const st = s?.trades.stats.live
          const a = s?.account.live
          const waiting = s && st && st.n === 0
          return (
            <div key={code} className="cq-score-line">
              <div className="cq-score-name">{s?.name ?? LINE_FALLBACK[code] ?? code}</div>
              {!s ? (
                <p className="cq-war-mute">{s === null ? '成績還沒有（雲端收盤後算）' : '讀取中…'}</p>
              ) : waiting ? (
                <div className="cq-tiles cq-tiles-2">
                  <Kpi label="已賣出" value="0" sub={`筆・要抱滿 ${s.trades.hold_sessions} 天`} tone="na" />
                  {a ? <Kpi label="模擬帳戶" value={pct(a.ret, 1)} sub={`${dayLabel(a.start)} 起 ${a.n_days} 日${a.short ? '・還很短' : ''}`} tone={kt(a.ret)} /> : <Kpi label="開始賣出" value={waitDate(s) ?? '—'} sub="前後" tone="na" />}
                </div>
              ) : (
                <div className="cq-tiles cq-tiles-3">
                  <Kpi label="每筆平均" value={pct(st!.mean)} sub={`已賣出 ${st!.n} 筆`} tone={kt(st!.mean)} />
                  <Kpi label="中位數" value={pct(st!.median)} sub={`最差 ${pct(st!.worst)}`} tone={kt(st!.median)} />
                  {a ? <Kpi label="模擬帳戶" value={pct(a.ret, 1)} sub={`${a.n_days} 個交易日${a.short ? '・還很短' : ''}`} tone={kt(a.ret)} /> : <Kpi label="模擬帳戶" value="—" tone="na" />}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </CardFrame>
  )
}

/** 收起時輪播幾個族群（資金倍數最高的幾個）。 */
const HEAT_SLIDES = 8

export function HeatCard({
  refresh,
  listed,
  onStock,
  ...fold
}: { refresh: number; listed: { date: string; sids: Map<string, string[]> } | null; onStock: (sid: string, name: string | null) => void } & Fold) {
  // 收起時才另外讀一份族群排行來輪播（展開時熱力圖自己會讀）
  const [rows, setRows] = useState<RankRow[] | null>(null)
  const [date, setDate] = useState<string | null>(null)
  useEffect(() => {
    if (!fold.collapsed) return
    let stale = false
    fetchGroups()
      .then((r) => {
        if (stale) return
        setRows(r.rows)
        setDate(r.date)
      })
      .catch(() => !stale && setRows([]))
    return () => {
      stale = true
    }
  }, [fold.collapsed, refresh])
  const top = (rows ?? [])
    .filter((r) => r.ratio !== null)
    .sort((a, b) => (b.ratio ?? 0) - (a.ratio ?? 0))
    .slice(0, HEAT_SLIDES)
  const slides = top.map((r) =>
    kv(r.grp, r.grp, `資金 ${ratioText(r.ratio) ?? '—'}　${pct(r.ret, 1)}`, tone(r.ret), `${r.up} 漲 ${r.down} 跌・${r.n} 檔`),
  )
  return (
    <CardFrame
      id="heat"
      className="cq-war-heat"
      when={fold.collapsed && date ? `${dayLabel(date)}・資金倍數最高` : '資金倍數・漲跌・成員'}
      slides={slides}
      empty={rows === null ? '族群資料讀取中…' : '還沒有族群資金資料。'}
      {...fold}
    >
      <div className="cq-war-heat-body">
        <GroupsPage refresh={refresh} listed={listed} onStock={onStock} withMood={false} />
      </div>
    </CardFrame>
  )
}
