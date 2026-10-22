// 追蹤股與個股的模型評分：型別與白話顯示文字（純函式，供 node 單元測試）。
// 後端資料形狀見 functions/_lib/scores.ts、watchlist.ts。
import type { Group } from './group'
import type { Risk } from './risks'

export type ModelCode = 'rank' | 'quiet' | 'risk'
export type ModelScore = {
  code: ModelCode
  date: string | null
  score: number | null
  rank_pct: number | null
  rank: number | null
  total: number | null
  avg: number | null
  history: { date: string; score: number; rank_pct: number | null }[]
}
export type StockScores = {
  sid: string
  name: string | null
  industry: string | null
  /** 族群（D-177）；舊後端沒有這欄 */
  group?: Group | null
  models: ModelScore[]
  /** 最新一天的名單上有這檔：哪條線第幾名 */
  lists: { date: string; line: string; name: string; rank: number }[]
}
export type SearchHit = { sid: string; name: string | null; industry: string | null }
export type WatchItem = {
  sid: string
  name: string | null
  added_at: string
  date: string | null
  close: number | null
  chg: number | null
  models: ModelScore[]
  /** 今天起 10 天內的除權息、法說會、月營收待公布（T-430）；沒有 → null；舊後端沒有這欄 → undefined */
  events?: Risk[] | null
}
export type WatchList = { max: number; date: string | null; items: WatchItem[] }

/** 每個模型的名稱、誰在用、在預測什麼（畫面上的說明）。 */
export const MODEL_TEXT: Record<ModelCode, { title: string; who: string; what: string }> = {
  rank: {
    title: '漲幅排名',
    who: '大盤精選用的模型',
    what: '預測未來 20 個交易日、扣掉交易成本後的報酬，在全市場排第幾。大盤精選先用它篩出前段的股票，再用 52 週高點、動能等規則挑 15 檔。',
  },
  quiet: {
    title: '飆漲機率',
    who: '潛伏飆股用的模型',
    what: '估計 20 個交易日內漲到 20% 的機率。只評「安靜」的股票：近 60 日漲不到 20%、波動偏低、成交金額在前 30%、股價 10 元以上；其他股票不打分。',
  },
  risk: {
    title: '回檔風險',
    who: '參考用，兩條線都沒用到',
    what: '預測未來 60 個交易日內最深會跌多少，在全市場排第幾。高＝最容易大跌的三分之一。',
  },
}

/** 名次換成「前幾 %」：第 1 名也是「前 1%」，不出現「前 0%」。 */
export function topPct(rank: number, total: number): number {
  return Math.max(1, Math.ceil((rank / total) * 100))
}

/** 回檔風險三等分：容易大跌的名次（1＝最容易）落在前三分之一＝高、中間＝中、後三分之一＝低。 */
export function riskLevel(m: ModelScore): { level: '高' | '中' | '低'; riskRank: number } | null {
  if (m.rank === null || m.total === null) return null
  const riskRank = m.total - m.rank + 1 // 分數越高＝預測跌得越淺，所以倒過來數
  const level = riskRank * 3 <= m.total ? '高' : riskRank * 3 <= m.total * 2 ? '中' : '低'
  return { level, riskRank }
}

/** 漲幅排名在前幾 % 以內用強調色（只是畫面標示；大盤精選的初篩也是留前 30%，見 configs/strategies/lw_e0_ctrl.yaml）。 */
const HOT_TOP_PCT = 30

const n = (v: number) => v.toLocaleString('en-US')
const prob = (v: number) => `${Math.round(v * 100)}%`

export type ModelView = { title: string; who: string; what: string; big: string; sub: string; tone: 'hot' | 'warn' | 'plain' | 'na' }

/** 一張模型卡片要顯示的字：大字、小字、顏色。沒分數時說明原因，不寫 0。 */
export function modelView(m: ModelScore): ModelView {
  const text = MODEL_TEXT[m.code]
  const na = (sub: string): ModelView => ({ ...text, big: '—', sub, tone: 'na' })
  if (m.date === null || m.total === null) return na('雲端還沒有這個模型的分數')
  if (m.code === 'quiet') {
    if (m.score === null || m.rank === null) return na(`不在潛伏池（今天評了 ${n(m.total)} 檔安靜股）`)
    const avg = m.avg === null ? '' : `，池內平均 ${prob(m.avg)}`
    return { ...text, big: prob(m.score), sub: `潛伏池 ${n(m.total)} 檔中第 ${n(m.rank)} 名${avg}`, tone: m.avg !== null && m.score > m.avg ? 'hot' : 'plain' }
  }
  if (m.rank === null) return na('模型今天沒有評到這檔')
  if (m.code === 'rank') {
    const p = topPct(m.rank, m.total)
    return { ...text, big: `前 ${p}%`, sub: `全市場 ${n(m.total)} 檔中第 ${n(m.rank)} 名`, tone: p <= HOT_TOP_PCT ? 'hot' : 'plain' }
  }
  const r = riskLevel(m)!
  return { ...text, big: r.level, sub: `${n(m.total)} 檔中第 ${n(r.riskRank)} 容易大跌`, tone: r.level === '高' ? 'warn' : 'plain' }
}

/** 近幾日走勢的點（舊到新）：漲幅排名與回檔風險用「贏過幾 %」，飆漲機率用機率本身；越高越好。 */
export function sparkPoints(m: ModelScore): { date: string; value: number }[] {
  return m.history
    .map((h) => ({ date: h.date, value: m.code === 'quiet' ? h.score : h.rank_pct }))
    .filter((p): p is { date: string; value: number } => p.value !== null && Number.isFinite(p.value))
}

/** 追蹤股清單每列的小標籤：漲幅排名一定有（沒分數寫「—」）；在潛伏池才標機率；回檔風險高才標。 */
export function scoreTags(models: ModelScore[]): { text: string; tone: 'hot' | 'warn' | 'plain' }[] {
  const out: { text: string; tone: 'hot' | 'warn' | 'plain' }[] = []
  for (const m of models) {
    const v = modelView(m)
    if (m.code === 'rank') out.push({ text: `漲幅 ${v.big}`, tone: v.tone === 'hot' ? 'hot' : 'plain' })
    if (m.code === 'quiet' && v.tone !== 'na') out.push({ text: `飆漲 ${v.big}`, tone: v.tone === 'hot' ? 'hot' : 'plain' })
    if (m.code === 'risk' && v.tone === 'warn') out.push({ text: '回檔風險高', tone: 'warn' })
  }
  return out
}
