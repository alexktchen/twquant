// 首頁戰情室的小卡（使用者 2026-10-17：每張卡可移動、可收起，收起時輪播重點）。內容與改版前相同，只是包進 CardFrame，
// 並各自列出收起時要輪播的重點。只供看盤：這些數字都不進選股、不進模型。
import { useEffect, useState } from 'react'
import { fetchGroups } from './api'
import { CardFrame } from './CardFrame'
import { GroupsPage } from './GroupsPage'
import { pct } from './format'
import { ratioText, type RankRow } from './group'
import type { Slide } from './Reel'
import type { Scorecard } from './scorecard'
import { breadth, dayLabel, firstLiveExit, signedYi, streakText, tone, type Brief, type WarRoomData } from './war'

type Fold = { collapsed: boolean; onToggle: () => void }
type Market = NonNullable<WarRoomData['market']>
type Taifex = NonNullable<WarRoomData['taifex']>

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
    <CardFrame title="國際行情" when={b ? `${b.generated_at.slice(5, 16).replace('-', '/')} 快照` : undefined} slides={slides} empty="今早的快照還沒有。" {...fold}>
      {b?.extras && b.extras.length > 0 ? (
        <table className="cq-war-table">
          <tbody>
            {b.extras.map((q) => (
              <tr key={q.symbol}>
                <th>{q.name}</th>
                <td>{intlValue(q)}</td>
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
  return (
    <CardFrame title="期貨籌碼" when={t ? `${dayLabel(t.date)} 盤後` : undefined} slides={slides} empty="期交所盤後統計還沒有。" {...fold}>
      {t ? (
        <table className="cq-war-table">
          <tbody>
            <tr>
              <th>外資台指期淨未平倉</th>
              <td colSpan={2} className={tone(t.fut_foreign_net_oi)}>
                {oiText(t.fut_foreign_net_oi)}
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
              <td colSpan={2}>{ratio(t.pc_oi_ratio)}</td>
            </tr>
            <tr>
              <th>選擇權成交量買賣權比</th>
              <td colSpan={2}>{ratio(t.pc_vol_ratio)}</td>
            </tr>
          </tbody>
        </table>
      ) : (
        <p className="cq-war-mute">期交所盤後統計還沒有。</p>
      )}
      <p className="cq-war-note">淨空單＝外資在台指期貨的空單比多單多；買賣權比＝賣權÷買權，大於 100% 代表避險（賣權）比較多。</p>
    </CardFrame>
  )
}

export function MarketCard({ m, ...fold }: { m: Market | null } & Fold) {
  const br = m ? breadth(m.up, m.down, m.flat) : null
  const slides = m
    ? [
        kv('taiex', '加權', `${m.taiex_close?.toLocaleString('en-US', { maximumFractionDigits: 0 }) ?? '—'}　${pct(m.taiex_chg)}`, tone(m.taiex_chg)),
        kv('otc', '櫃買', `${m.otc_close?.toFixed(2) ?? '—'}　${pct(m.otc_chg)}`, tone(m.otc_chg)),
        kv('br', '漲跌家數', `${m.up} 漲／${m.flat} 平／${m.down} 跌`, tone(m.up - m.down)),
        kv('lim', '漲停／跌停', `${m.limit_up} ／ ${m.limit_down} 家`),
        kv('amt', '成交金額', `${Math.round(m.amount / 1e8).toLocaleString()} 億`, undefined, m.amount_ratio !== null ? `20 日平均的 ${m.amount_ratio.toFixed(2)} 倍` : null),
      ]
    : []
  return (
    <CardFrame title="大盤體檢" when={m ? dayLabel(m.date) : undefined} slides={slides} empty="大盤體檢還沒有（每個交易日收盤後約 17:40 算好）。" {...fold}>
      {m && br ? (
        <>
          <div className="cq-war-idx">
            <span>
              加權 <b>{m.taiex_close?.toLocaleString('en-US', { maximumFractionDigits: 0 }) ?? '—'}</b> <i className={tone(m.taiex_chg)}>{pct(m.taiex_chg)}</i>
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
                  {m.new_high === null ? '—' : <span className="up">{m.new_high}</span>} ／ {m.new_low === null ? '—' : <span className="down">{m.new_low}</span>} 家
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
    <CardFrame title="法人與融資" when={m ? `${dayLabel(m.date)}・估算` : undefined} slides={slides} {...fold}>
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
      <p className="cq-war-note">估算＝每檔買賣超（或融資）股數 × 當天收盤加總，會和證交所公布的金額差幾個百分點（不含 ETF、用收盤價）。看方向與量級用。</p>
    </CardFrame>
  )
}

export function PicksCard({ picks, onPicks, ...fold }: { picks: WarRoomData['picks'] | null; onPicks: (line?: string) => void } & Fold) {
  const row = (l: NonNullable<WarRoomData['picks']>['lines'][number]) => (
    <li key={l.code} onClick={() => onPicks(l.code)}>
      <b>{l.name}</b>
      <span>
        {l.n} 檔<small>（新增 {l.new}）</small>
      </span>
      <i>看名單 ›</i>
    </li>
  )
  const slides = (picks?.lines ?? []).map((l) => ({ key: l.code, node: <ul className="cq-war-picks cq-news-one">{row(l)}</ul> }))
  return (
    <CardFrame title="今日名單" when={picks ? `${dayLabel(picks.date)} 收盤後產生` : undefined} slides={slides} empty="還沒有名單。" {...fold}>
      {picks ? <ul className="cq-war-picks">{picks.lines.map(row)}</ul> : <p className="cq-war-mute">還沒有名單。</p>}
    </CardFrame>
  )
}

export function ScoreHomeCard({
  lines,
  scores,
  onScore,
  ...fold
}: { lines: string[]; scores: Record<string, Scorecard | null>; onScore: (line: string) => void } & Fold) {
  const waitText = (s: Scorecard) =>
    `還沒有抱滿 ${s.trades.hold_sessions} 天的${s.live_since ? `，約 ${dayLabel(firstLiveExit(s.live_since, s.trades.hold_sessions, s.nav.map((x) => x.d)))} 前後開始賣出` : ''}`
  const slides: Slide[] = lines.flatMap((code) => {
    const s = scores[code]
    if (!s) return []
    const st = s.trades.stats.live
    const a = s.account.live
    return [
      st.n > 0
        ? kv(`${code}t`, `${s.name} 公布後已賣出 ${st.n} 筆`, `每筆平均 ${pct(st.mean)}`, tone(st.mean), `中位數 ${pct(st.median)}、最差 ${pct(st.worst)}`)
        : kv(`${code}t`, s.name, waitText(s)),
      ...(a ? [kv(`${code}a`, `${s.name} 公布後模擬帳戶`, pct(a.ret, 1), tone(a.ret), `${dayLabel(a.start)} 起 ${a.n_days} 個交易日`)] : []),
    ]
  })
  return (
    <CardFrame title="名單成績" when="公布後的每一筆" slides={slides} empty="名單成績讀取中…" {...fold}>
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
          {lines.map((code) => {
            const s = scores[code]
            const st = s?.trades.stats.live
            // 2026-10-17 使用者：「名單成績沒有資料」——即時公布才幾天、還沒有抱滿天數賣出的，直接說哪天開始有
            const waiting = s && st && st.n === 0
            return (
              <tr key={code}>
                <th>{s?.name ?? code}</th>
                <td>{st ? st.n : s === null ? '—' : '…'}</td>
                {waiting ? (
                  <td colSpan={3} className="cq-war-score-wait">
                    {waitText(s)}
                  </td>
                ) : (
                  <>
                    <td className={tone(st?.mean ?? null)}>{st && st.n > 0 ? pct(st.mean) : '—'}</td>
                    <td className={tone(st?.median ?? null)}>{st && st.n > 0 ? pct(st.median) : '—'}</td>
                    <td className={st && st.n > 0 ? 'down' : undefined}>{st && st.n > 0 ? pct(st.worst) : '—'}</td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
      {lines.some((code) => scores[code]?.account.live) && (
        <p className="cq-war-score-acct">
          <span>公布後的模擬帳戶（含還抱著的）：</span>
          {lines.map((code) => {
            const s = scores[code]
            const a = s?.account.live
            return (
              a && (
                <span key={code}>
                  {s.name} <b className={tone(a.ret)}>{pct(a.ret, 1)}</b>
                  <small className="cq-war-mute">（{dayLabel(a.start)} 起 {a.n_days} 個交易日）</small>
                </span>
              )
            )
          })}
          {lines.some((code) => scores[code]?.account.live?.short) && <small className="cq-war-mute">期間還很短，看看就好。</small>}
        </p>
      )}
      <p className="cq-war-note">
        照名單規則當成真的買（含手續費、證交稅），只算公布之後已賣出的。看「每筆平均」不看勝率：台股大多時候在漲，隨便買的勝率也有七成多。
        這些成績只拿來看，不拿來挑股或調模型（2027 年 3 月才用新資料正式檢驗）。
        <button className="cq-war-link" onClick={() => onScore(lines[0])}>
          詳細績效 ›
        </button>
      </p>
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
      title="族群資金與熱力圖"
      className="cq-war-heat"
      when={fold.collapsed && date ? `${dayLabel(date)}・資金倍數最高的族群` : '每個族群的資金倍數、漲跌與成員'}
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
