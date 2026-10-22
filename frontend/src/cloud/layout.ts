// 首頁版面（使用者 2026-10-17：「每張卡可以自由移動位子，依照使用者帳號儲存擺置位置，卡也要可以縮起來，縮起來後內容要可以輪播」）。
// 版面＝卡片由上到下的順序＋哪幾張收起。登入後存在帳號（/api/layout），換裝置也一樣；這台瀏覽器另存一份，打開時先照它排，
// 帳號那份讀回來再換上。純函式，供 node 單元測試。
// T-431／D-209：行事曆與族群資金搬出首頁（族群資金→「盤勢」分頁；法說會、除權息改標在追蹤股與名單股上）；
// 存過的版面裡的 cal、heat 讀回來時當不認得的代號丟掉。
import { HEADS_KEY, MOOD_KEY, NEWS_KEY, loadPref, openPref } from './prefs.ts'

export type CardId = 'brief' | 'heads' | 'mood' | 'intl' | 'taifex' | 'market' | 'chips' | 'news' | 'picks' | 'score'

/** 首頁的卡：預設順序、電腦版是否橫跨整排（wide）、預設收起與否。 */
export const HOME_CARDS: { id: CardId; title: string; wide: boolean; collapsed: boolean }[] = [
  { id: 'brief', title: '今日一句話', wide: true, collapsed: false },
  { id: 'heads', title: '昨晚國際大事・新聞頭條', wide: true, collapsed: true },
  { id: 'mood', title: '市場氛圍', wide: true, collapsed: false },
  { id: 'intl', title: '國際行情', wide: false, collapsed: false },
  { id: 'taifex', title: '期貨籌碼', wide: false, collapsed: false },
  { id: 'market', title: '大盤體檢', wide: false, collapsed: false },
  { id: 'chips', title: '法人與融資', wide: false, collapsed: false },
  { id: 'news', title: '昨日消息', wide: true, collapsed: false },
  { id: 'picks', title: '今日名單', wide: false, collapsed: false },
  { id: 'score', title: '名單成績', wide: false, collapsed: false },
]

export type Layout = { order: CardId[]; collapsed: CardId[] }

const KNOWN = new Set<string>(HOME_CARDS.map((c) => c.id))
const isCard = (x: unknown): x is CardId => typeof x === 'string' && KNOWN.has(x)

export function cardDef(id: CardId) {
  return HOME_CARDS.find((c) => c.id === id)!
}

export function defaultLayout(): Layout {
  return { order: HOME_CARDS.map((c) => c.id), collapsed: HOME_CARDS.filter((c) => c.collapsed).map((c) => c.id) }
}

/**
 * 存起來的版面 → 這一版網站的版面：不認得的代號丟掉、重複的只留第一個；以後新增的卡（存的時候還沒有）
 * 插在預設順序裡它前一張卡的後面（前面都沒有就放最上面），收起與否用預設。格式不對 → 預設。
 */
export function normalizeLayout(saved: unknown): Layout {
  const s = saved as { order?: unknown; collapsed?: unknown } | null
  if (!s || !Array.isArray(s.order)) return defaultLayout()
  const order: CardId[] = []
  for (const x of s.order) if (isCard(x) && !order.includes(x)) order.push(x)
  const seen = new Set(order)
  const added: CardId[] = []
  HOME_CARDS.forEach((c, i) => {
    if (seen.has(c.id)) return
    const before = HOME_CARDS.slice(0, i)
      .map((p) => p.id)
      .reverse()
      .find((p) => order.includes(p))
    order.splice(before ? order.indexOf(before) + 1 : 0, 0, c.id)
    added.push(c.id)
  })
  const collapsed = Array.isArray(s.collapsed) ? s.collapsed.filter(isCard) : []
  const out = new Set<CardId>(collapsed)
  for (const id of added) if (cardDef(id).collapsed) out.add(id)
  return { order, collapsed: order.filter((id) => out.has(id)) }
}

/** 收起↔展開一張卡。 */
export function toggleCollapsed(l: Layout, id: CardId): Layout {
  const on = !l.collapsed.includes(id)
  const set = new Set(l.collapsed)
  if (on) set.add(id)
  else set.delete(id)
  return { order: l.order, collapsed: l.order.filter((x) => set.has(x)) }
}

/** 換順序（收起的卡跟著）。 */
export function withOrder(l: Layout, order: CardId[]): Layout {
  return normalizeLayout({ order, collapsed: l.collapsed })
}

export function sameLayout(a: Layout, b: Layout): boolean {
  return a.order.join() === b.order.join() && a.collapsed.join() === b.collapsed.join()
}

/** 這台瀏覽器存的版面（帳號那份讀回來之前先用）。 */
export const LAYOUT_KEY = 'twquant.layout'

/**
 * 打開首頁時的版面：這台瀏覽器存過版面就用它；沒有的話用預設順序，收起與否沿用以前各卡分別記的展開／收起
 * （改版前各卡的鍵；行事曆、熱力圖兩張已搬走，只剩三個），免得改版後使用者原本收起的卡又全打開。
 */
export function initialLayout(stored: string | null, legacy: (key: string) => string | null): Layout {
  if (stored) {
    try {
      return normalizeLayout(JSON.parse(stored))
    } catch {
      // 壞掉的就當沒存過
    }
  }
  const base = defaultLayout()
  const legacyKeys: [CardId, string][] = [
    ['heads', HEADS_KEY],
    ['mood', MOOD_KEY],
    ['news', NEWS_KEY],
  ]
  const collapsed = new Set(base.collapsed)
  for (const [id, key] of legacyKeys) {
    const open = openPref(legacy(key), !cardDef(id).collapsed)
    if (open) collapsed.delete(id)
    else collapsed.add(id)
  }
  return { order: base.order, collapsed: base.order.filter((id) => collapsed.has(id)) }
}

/** 瀏覽器裡的初始版面（包一層，元件用）。 */
export function browserLayout(): Layout {
  return initialLayout(loadPref(LAYOUT_KEY), loadPref)
}
