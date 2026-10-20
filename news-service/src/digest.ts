// 新聞三張卡（T-417；使用者 2026-10-20：「星雲圖當成可以切換的一個視角就好，要快速閱讀可以三張卡片」）：
// 熱門主題（近 24 小時哪些主題、族群被提到最多，比前 24 小時多或少，常一起提到的台股）、
// 多家同報（同一事件被幾家不同媒體報：同一家的兩個頻道只算一家）、
// 股票熱度（台股近 24 小時被提到幾則，比平常一天多幾倍；平常＝之前幾天的日平均，資料不到一天就先只排則數）。
// 純函式；SQL 在 supabase/functions/api。只是資訊，不是訊號（D-191 §一、D-200 §二）。

export type DigestConfig = {
  /** 熱門主題列幾個 */
  topics_n: number
  /** 每個主題附幾檔常一起提到的台股 */
  topic_stocks: number
  /** 至少幾家不同媒體報同一件事才列 */
  multi_min_outlets: number
  multi_n: number
  hot_n: number
  /** 近 24 小時至少被提到幾則才列（1、2 則的倍數沒意義） */
  hot_min_today: number
  /** 平常＝之前幾天的日平均（最多看幾天） */
  base_days: number
  /** 被超過這個比例的新聞提到的主題不列（「AI」佔一半，列了等於沒說），改在卡片底下註明 */
  everywhere_share: number
}

export function validateDigestConfig(x: unknown): DigestConfig {
  const c = x as Partial<DigestConfig> | null
  if (!c || typeof c !== 'object') throw new Error('news 設定錯誤：digest（整段缺少）')
  for (const k of ['topics_n', 'topic_stocks', 'multi_min_outlets', 'multi_n', 'hot_n', 'hot_min_today', 'base_days'] as const)
    if (!Number.isInteger(c[k]) || (c[k] as number) < 1) throw new Error(`news 設定錯誤：digest.${k}`)
  if (typeof c.everywhere_share !== 'number' || c.everywhere_share <= 0 || c.everywhere_share > 1) throw new Error('news 設定錯誤：digest.everywhere_share')
  if (c.multi_min_outlets! < 2) throw new Error('news 設定錯誤：digest.multi_min_outlets（至少 2 家才叫多家同報）')
  return c as DigestConfig
}

// ── 熱門主題 ──
export type TopicRow = { id: string; label: string; type: string; n_now: number; n_prev: number }
export type CoRow = { topic: string; stock: string; label: string; n: number }
export type Topic = { id: string; label: string; type: string; n: number; prev: number; stocks: { id: string; label: string; n: number }[] }
export type Topics = { items: Topic[]; everywhere: { label: string; share: number }[] }

const byStr = <T>(f: (x: T) => string) => (a: T, b: T) => (f(a) < f(b) ? -1 : f(a) > f(b) ? 1 : 0)

/**
 * 近 24 小時被提到最多的主題與族群；每個附常一起提到的台股。同數依代號排（結果穩定）。
 * total＝近 24 小時有提到任何實體的新聞則數；超過 everywhere_share 的主題不列、另回 everywhere。
 */
export function topTopics(rows: TopicRow[], co: CoRow[], total: number, cfg: DigestConfig): Topics {
  const tooCommon = (r: TopicRow) => total > 0 && r.n_now > cfg.everywhere_share * total
  const everywhere = rows
    .filter(tooCommon)
    .sort((a, b) => b.n_now - a.n_now)
    .map((r) => ({ label: r.label, share: Math.round((r.n_now / total) * 100) / 100 }))
  const items = rows
    .filter((r) => r.n_now > 0 && !tooCommon(r))
    .sort((a, b) => b.n_now - a.n_now || byStr<TopicRow>((x) => x.id)(a, b))
    .slice(0, cfg.topics_n)
    .map((r) => ({
      id: r.id,
      label: r.label,
      type: r.type,
      n: r.n_now,
      prev: r.n_prev,
      stocks: co
        .filter((c) => c.topic === r.id)
        .sort((a, b) => b.n - a.n || byStr<CoRow>((x) => x.stock)(a, b))
        .slice(0, cfg.topic_stocks)
        .map((c) => ({ id: c.stock, label: c.label, n: c.n })),
    }))
  return { items, everywhere }
}

// ── 多家同報 ──
export type EvRow = { ev: string; item_id: string; source: string; title: string; title_zh: string | null; url: string; first_seen_at: string; stocks: string[] }
export type Multi = { ev: string; outlets: string[]; items: number; title: string; url: string; first_seen_at: string; last_seen_at: string; stocks: string[] }

/** 同一事件群：幾家「不同媒體」報（outletOf 把來源代號換成媒體名，同一家的兩個頻道算一家）；標題用最早那則（有譯文用譯文）。 */
export function multiOutlet(rows: EvRow[], outletOf: (source: string) => string, cfg: DigestConfig): Multi[] {
  const by = new Map<string, EvRow[]>()
  for (const r of rows) by.set(r.ev, [...(by.get(r.ev) ?? []), r])
  const out: Multi[] = []
  for (const [ev, rs] of by) {
    const outlets = [...new Set(rs.map((r) => outletOf(r.source)))].sort()
    if (outlets.length < cfg.multi_min_outlets) continue
    const sorted = [...rs].sort(byStr<EvRow>((r) => r.first_seen_at))
    const first = sorted[0]
    out.push({
      ev,
      outlets,
      items: rs.length,
      title: first.title_zh ?? first.title,
      url: first.url,
      first_seen_at: first.first_seen_at,
      last_seen_at: sorted[sorted.length - 1].first_seen_at,
      stocks: [...new Set(rs.flatMap((r) => r.stocks))].sort().slice(0, 4),
    })
  }
  return out.sort((a, b) => b.outlets.length - a.outlets.length || byStr<Multi>((m) => m.last_seen_at)(b, a) || byStr<Multi>((m) => m.ev)(a, b)).slice(0, cfg.multi_n)
}

// ── 股票熱度 ──
export type HotRow = { id: string; label: string; sub: string | null; n_now: number; n_base: number }
export type Hot = { id: string; label: string; sub: string | null; n: number; base: number | null; ratio: number | null }

/** 平常值能用幾天：近 24 小時之前、最早一則新聞之後有幾整天（最多 base_days）。不到一天 → 0（還沒有平常值）。 */
export function baseDaysOf(firstSeenAt: string | null, now: Date, cfg: DigestConfig): number {
  if (!firstSeenAt) return 0
  const days = Math.floor((now.getTime() - 86_400_000 - Date.parse(firstSeenAt)) / 86_400_000)
  return Math.max(0, Math.min(cfg.base_days, days))
}

/**
 * 比平常熱的台股：近 24 小時至少 hot_min_today 則；倍數＝今天 ÷ 平常的日平均（平常是 0 當 0.5 算，免得除以零變無限大）。
 * 還沒有平常值（baseDays＝0）→ 只照則數排，base／ratio 都是 null（網站寫「平常值累積中」）。
 */
export function hotStocks(rows: HotRow[], baseDays: number, cfg: DigestConfig): Hot[] {
  const out = rows
    .filter((r) => r.n_now >= cfg.hot_min_today)
    .map((r) => {
      if (baseDays < 1) return { id: r.id, label: r.label, sub: r.sub, n: r.n_now, base: null, ratio: null }
      const base = r.n_base / baseDays
      return { id: r.id, label: r.label, sub: r.sub, n: r.n_now, base: Math.round(base * 10) / 10, ratio: Math.round((r.n_now / Math.max(base, 0.5)) * 10) / 10 }
    })
  return out.sort((a, b) => (b.ratio ?? 0) - (a.ratio ?? 0) || b.n - a.n || byStr<Hot>((h) => h.id)(a, b)).slice(0, cfg.hot_n)
}
