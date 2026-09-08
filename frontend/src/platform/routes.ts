/** Canonical platform destinations shared by routing and navigation. */
export const platformPages = [
  { id: 'overview', label: '今日總覽', group: '每日投資', description: '每日候選與研究工作，從這裡開始。' },
  { id: 'recommendations', label: 'ML 選股', group: '每日投資', description: '查看模型選出的候選、推薦版本與歷史紀錄。' },
  { id: 'watchlists', label: '我的追蹤', group: '每日投資', description: '保存觀察理由，持續追蹤研究結果。' },
  { id: 'stocks', label: '個股研究', group: '市場研究', description: '價量、籌碼與基本面，一起檢視。' },
  { id: 'industries', label: '產業研究', group: '市場研究', description: '觀察產業資金流向、涵蓋範圍與相關股票。' },
  { id: 'insights', label: '洞悉與證據', group: '市場研究', description: '核對消息、產業與基本面支持或反對的理由。' },
  { id: 'models', label: '模型中心', group: '模型研究', description: '追溯模型版本、訓練資料與驗證紀錄。' },
  { id: 'backtests', label: '回測中心', group: '模型研究', description: '查看回測結果、成本假設與歷史持倉。' },
  { id: 'operations', label: '資料與工作', group: '營運', description: '檢查資料涵蓋、缺口與背景工作的執行狀態。' },
] as const
export type PageId = typeof platformPages[number]['id']
export type PlatformRoute = { page: PageId | 'not-found'; entityId?: string; query: URLSearchParams }

/** Decode a hash route without converting identifiers or trade dates. */
export function parseRoute(hash: string): PlatformRoute {
  const aliases: Record<string, string> = { '#stock': '#/stocks', '#ingest': '#/operations' }
  const normalized = aliases[hash] ?? hash
  const [path, search = ''] = (normalized.replace(/^#/, '') || '/overview').split('?')
  const parts = path.replace(/^\//, '').split('/')
  const page = parts[0] as PageId
  const query = new URLSearchParams(search)
  const invalid: PlatformRoute = { page: 'not-found', query }
  if (!platformPages.some(p => p.id === page) || parts.length > 2) return invalid
  if (parts.length === 1) return { page, query }
  if (!['stocks', 'models', 'backtests'].includes(page)) return invalid
  try {
    const entityId = decodeURIComponent(parts[1])
    return /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(entityId) ? { page, entityId, query } : invalid
  } catch (error) {
    if (error instanceof URIError) return invalid
    throw error
  }
}

/** Build an explicit-context deep link without inheriting unrelated page state. */
export function routeHref(page: PageId, entityId?: string, query = new URLSearchParams()): string {
  const search = query.toString()
  return `#/${page}${entityId ? `/${encodeURIComponent(entityId)}` : ''}${search ? `?${search}` : ''}`
}
