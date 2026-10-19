// 產生新聞關係圖的示範資料（使用者 2026-10-19：「我沒有看到很酷炫的介面來做關聯性，有那種心智爆炸圖的方式嗎」）。
// 真實部分：三則 2026-10-19 的真實新聞（CNBC、日經亞洲）與新聞服務翻譯的標題；族群與同族群名單取自主庫 serving.stock_group
// （2026-10-19 快照，依當天成交金額排前幾名）；美股漲跌是 10/16 收盤（Yahoo）。
// 草擬部分：「國外公司 → 台灣供應商」與「主題 → 台股／族群」是 AI 依公開資訊草擬、還沒經使用者核可（D-200 §三）→ draft，網站畫虛線。
// 執行：node --experimental-strip-types news-service/scripts/demo-graph.ts（寫到 frontend/src/cloud/news-graph-demo.json）
import { writeFileSync } from 'node:fs'
import { parentOf, propagate, type Edge, type Entity, type GraphConfig } from '../src/graph.ts'

const cfg: GraphConfig = {
  weights: { supplier: 0.6, belongs: 0.5, member: 0.5, peer: 0.4, theme: 0.6 },
  fanout_power: 0.5,
  max_degree: 3,
  max_links: 26,
}

const E: Entity[] = [
  { id: 'tw:2330', type: 'tw', label: '台積電' },
  { id: 'tw:2303', type: 'tw', label: '聯電' },
  { id: 'tw:6488', type: 'tw', label: '環球晶' },
  { id: 'tw:3105', type: 'tw', label: '穩懋' },
  { id: 'tw:6770', type: 'tw', label: '力積電' },
  { id: 'tw:2382', type: 'tw', label: '廣達' },
  { id: 'tw:6669', type: 'tw', label: '緯穎' },
  { id: 'tw:3706', type: 'tw', label: '神達' },
  { id: 'tw:7711', type: 'tw', label: '永擎' },
  { id: 'tw:2317', type: 'tw', label: '鴻海' },
  { id: 'tw:2308', type: 'tw', label: '台達電' },
  { id: 'tw:2059', type: 'tw', label: '川湖' },
  { id: 'tw:3231', type: 'tw', label: '緯創' },
  { id: 'tw:2301', type: 'tw', label: '光寶科' },
  { id: 'tw:3017', type: 'tw', label: '奇鋐' },
  { id: 'tw:3324', type: 'tw', label: '雙鴻' },
  { id: 'tw:6831', type: 'tw', label: '邁科' },
  { id: 'tw:3008', type: 'tw', label: '大立光' },
  { id: 'tw:3406', type: 'tw', label: '玉晶光' },
  { id: 'tw:4938', type: 'tw', label: '和碩' },
  { id: 'tw:3711', type: 'tw', label: '日月光投控' },
  { id: 'tw:3264', type: 'tw', label: '欣銓' },
  { id: 'tw:2449', type: 'tw', label: '京元電子' },
  { id: 'tw:2408', type: 'tw', label: '南亞科' },
  { id: 'tw:2344', type: 'tw', label: '華邦電' },
  { id: 'tw:2337', type: 'tw', label: '旺宏' },
  { id: 'tw:8299', type: 'tw', label: '群聯' },
  { id: 'tw:3260', type: 'tw', label: '威剛' },
  { id: 'tw:2451', type: 'tw', label: '創見' },
  { id: 'tw:8271', type: 'tw', label: '宇瞻' },
  { id: 'tw:4973', type: 'tw', label: '廣穎' },
  { id: 'tw:2356', type: 'tw', label: '英業達' },
  { id: 'tw:2362', type: 'tw', label: '藍天' },
  { id: 'tw:2207', type: 'tw', label: '和泰車' },
  { id: 'tw:8255', type: 'tw', label: '朋程' },
  { id: 'tw:2237', type: 'tw', label: '華德動能' },
  { id: 'us:NVDA', type: 'us', label: '輝達' },
  { id: 'us:AAPL', type: 'us', label: '蘋果' },
  { id: 'us:AVGO', type: 'us', label: '博通' },
  { id: 'us:AMD', type: 'us', label: '超微' },
  { id: 'us:MU', type: 'us', label: '美光' },
  { id: 'co:samsung', type: 'company', label: '三星' },
  { id: 'co:skhynix', type: 'company', label: 'SK海力士' },
  { id: 'co:mitsubishi', type: 'company', label: '三菱電機' },
  { id: 'th:memory', type: 'theme', label: '記憶體' },
  { id: 'th:ev', type: 'theme', label: '電動車' },
  { id: 'grp:電動車輛產業', type: 'group', label: '電動車輛產業' },
]
const byId = new Map(E.map((e) => [e.id, e]))

/** 美股 10/16 收盤漲跌（%）、國外公司的國家 */
const SUB: Record<string, { sub: string; chg?: number }> = {
  'us:NVDA': { sub: 'NVDA', chg: -1.43 },
  'us:AAPL': { sub: 'AAPL', chg: 0.43 },
  'us:AVGO': { sub: 'AVGO', chg: -4.52 },
  'us:AMD': { sub: 'AMD', chg: -5.95 },
  'us:MU': { sub: 'MU', chg: -5.63 },
  'co:samsung': { sub: '韓國' },
  'co:skhynix': { sub: '韓國' },
  'co:mitsubishi': { sub: '日本' },
  'th:memory': { sub: '主題' },
  'th:ev': { sub: '主題' },
  'grp:電動車輛產業': { sub: '族群' },
}

// 同族群（真實，stock_group 2026-10-19）：兩兩連一條 peer 線、標族群名
const peers = (grp: string, ids: string[]): Edge[] =>
  ids.flatMap((a, i) => ids.slice(i + 1).map((b) => ({ src: `tw:${a}`, dst: `tw:${b}`, type: 'peer' as const, label: grp })))
const REAL: Edge[] = [
  ...peers('晶圓製造', ['2330', '6488', '2303', '3105', '6770']),
  ...peers('伺服器', ['6669', '3706', '7711']),
  ...peers('電腦及週邊設備', ['2317', '2308', '2059', '3231', '2301', '4938']),
  ...peers('散熱', ['3017', '3324', '6831']),
  ...peers('光學鏡頭', ['3008', '3406']),
  ...peers('封測', ['3711', '3264', '2449']),
  ...peers('半導體（記憶體）', ['2408', '2344', '2337', '8299']),
  ...peers('IC模組', ['3260', '2451', '8271', '4973']),
  ...peers('筆電', ['2382', '2356', '2362']),
  { src: 'tw:2207', dst: 'grp:電動車輛產業', type: 'belongs' },
  { src: 'tw:8255', dst: 'grp:電動車輛產業', type: 'belongs' },
  { src: 'tw:2237', dst: 'grp:電動車輛產業', type: 'belongs' },
]
// 草擬（AI 依公開資訊，待核可）
const sup = (from: string, to: string[]): Edge[] => to.map((t) => ({ src: from, dst: `tw:${t}`, type: 'supplier' as const, draft: true }))
const DRAFT: Edge[] = [
  ...sup('us:NVDA', ['2382', '6669', '2317', '3017']),
  ...sup('us:AAPL', ['2317', '3008', '4938']),
  ...sup('us:AVGO', ['3711']),
  ...sup('us:AMD', ['3711']),
  { src: 'co:samsung', dst: 'tw:2408', type: 'peer', draft: true, label: '同業' },
  { src: 'co:skhynix', dst: 'tw:2408', type: 'peer', draft: true, label: '同業' },
  { src: 'us:MU', dst: 'tw:2408', type: 'peer', draft: true, label: '同業' },
  { src: 'us:MU', dst: 'tw:2344', type: 'peer', draft: true, label: '同業' },
  ...['2408', '2344', '2337', '8299', '3260'].map((t) => ({ src: 'th:memory', dst: `tw:${t}`, type: 'theme' as const, draft: true })),
  { src: 'th:ev', dst: 'grp:電動車輛產業', type: 'theme', draft: true },
  { src: 'co:mitsubishi', dst: 'tw:2317', type: 'peer', draft: true, label: '結盟' },
]
const EDGES = [...REAL, ...DRAFT]

const ITEMS = [
  {
    id: 'demo-tsmc',
    source_name: 'CNBC',
    url: 'https://www.cnbc.com/',
    first_seen_at: '2026-10-19T03:40:00Z',
    title: "TSMC chip capacity is ‘fierce’ battleground for AI customers, amid record quarter",
    title_zh: '台積電晶片產能成AI客戶「激烈」競爭焦點，儘管創下紀錄季度',
    mentions: ['tw:2330', 'us:NVDA', 'us:AAPL', 'us:AVGO', 'us:AMD'],
  },
  {
    id: 'demo-memory',
    source_name: 'CNBC',
    url: 'https://www.cnbc.com/',
    first_seen_at: '2026-10-19T02:10:00Z',
    title: 'Memory stocks are getting hammered. China and a 2029 oversupply are the big worries',
    title_zh: '記憶體股遭重創。中國與2029年產能過剩成最大隱憂',
    mentions: ['th:memory', 'co:skhynix', 'co:samsung', 'us:MU'],
  },
  {
    id: 'demo-foxconn',
    source_name: 'Nikkei Asia',
    url: 'https://asia.nikkei.com/',
    first_seen_at: '2026-10-19T01:30:00Z',
    title: 'Foxconn, Mitsubishi Electric join forces for global EV push',
    title_zh: '鴻海、三菱電機合作推進全球電動車布局',
    mentions: ['tw:2317', 'co:mitsubishi', 'th:ev'],
  },
]

const graphs = ITEMS.map((it) => {
  const links = propagate(it.mentions, EDGES, cfg)
  return {
    item: { id: it.id, source_name: it.source_name, url: it.url, first_seen_at: it.first_seen_at, title: it.title, title_zh: it.title_zh },
    rule_version: 'demo-2026-10-19',
    nodes: links.map((l) => {
      const e = byId.get(l.entity)
      if (!e) throw new Error(`沒有定義的實體：${l.entity}`)
      const last = l.path[l.path.length - 1]
      return {
        id: e.id,
        type: e.type,
        label: e.label,
        sub: SUB[e.id]?.sub ?? e.id.split(':')[1],
        ...(SUB[e.id]?.chg !== undefined ? { chg: SUB[e.id].chg } : {}),
        degree: l.degree,
        score: Number(l.score.toFixed(4)),
        parent: parentOf(l),
        via: last.via,
        ...(last.label ? { via_label: last.label } : {}),
        draft: l.draft,
      }
    }),
  }
})
writeFileSync(new URL('../../frontend/src/cloud/news-graph-demo.json', import.meta.url), JSON.stringify(graphs, null, 1) + '\n')
console.log(graphs.map((g) => `${g.item.id}: ${g.nodes.length} 點（一度 ${g.nodes.filter((n) => n.degree === 1).length}、二度 ${g.nodes.filter((n) => n.degree === 2).length}、三度 ${g.nodes.filter((n) => n.degree === 3).length}）`).join('\n'))
