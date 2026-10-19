// 國外新聞翻譯（T-401／SPEC-017 §4）：Workers AI qwen3 把標題＋摘要翻成台灣繁中。
// 檢查分兩種：會「擋」的（數字換算後對不上、出現簡體字、譯文的英文字原文沒有、留著沒翻的英文字）→ 重試一次、仍不過就不用譯文、網站顯示原文標「未譯」；
// 只「記錄」的（漲跌方向詞相反、中文數字、對岸用語、多出原文沒有的數字）→ 照用，結果存在 checks，兩週後看誤擋率再決定。
// 指令在 system、資料在 user（SPEC-008 §0.3）。純函式；呼叫模型與資料庫由呼叫端注入。

export const PROMPT_VERSION = '2026-10-19.2'

export type TranslateConfig = {
  model: string
  /** qwen3 關推理（同 configs/llm.yaml cf_qwen3） */
  system_suffix: string
  batch: number
  /** 每次執行最多翻幾則（剩下的下次） */
  per_run_limit: number
  max_tokens: number
  /** 新聞這邊每天（UTC 日，Workers AI 免費額度的日界）最多用多少 Neurons（D-200 §五） */
  daily_neurons: number
  input_neurons_per_1m: number
  output_neurons_per_1m: number
  /** 英文 → 台灣慣用譯名 */
  glossary: Record<string, string>
  /** 簡體→繁體，兩字一組（OpenCC 算出的簡體專用字；按碼位切，有些字在 BMP 之外） */
  s2t_pairs: string
  /** 對岸用語 → 台灣用語（自動換掉，記在 checks.fixes） */
  mainland_terms: Record<string, string>
}

export type Source = { id: string; title: string; summary: string | null }
export type Translated = { id: string; title_zh: string; summary_zh: string }

/** configs/news.yaml 的 translate 段 → TranslateConfig；少欄位或型別不對 → 丟錯。 */
export function validateTranslateConfig(x: unknown): TranslateConfig {
  const c = x as Partial<TranslateConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：translate.${why}`)
  }
  if (!c || typeof c.model !== 'string' || !c.model.startsWith('@cf/')) fail('model')
  for (const k of ['batch', 'per_run_limit', 'max_tokens'] as const) if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  for (const k of ['daily_neurons', 'input_neurons_per_1m', 'output_neurons_per_1m'] as const) if (typeof c![k] !== 'number' || (c![k] as number) <= 0) fail(k)
  if (typeof c!.system_suffix !== 'string') fail('system_suffix')
  if (typeof c!.s2t_pairs !== 'string' || [...c!.s2t_pairs].length < 100 || [...c!.s2t_pairs].length % 2 !== 0) fail('s2t_pairs')
  for (const k of ['glossary', 'mainland_terms'] as const) {
    const m = c![k]
    if (!m || typeof m !== 'object' || Object.values(m).some((v) => typeof v !== 'string' || !v)) fail(k)
  }
  return c as TranslateConfig
}

// ── 請求與回應 ─────────────────────────────────────────────────────────────

export function systemPrompt(cfg: TranslateConfig): string {
  const terms = Object.entries(cfg.glossary)
    .map(([en, zh]) => `${en}＝${zh}`)
    .join('；')
  return [
    '你是財經新聞翻譯。把每則英文新聞的標題與摘要翻成台灣繁體中文（台灣用語、全形標點）。',
    '規則：',
    '1. 只翻譯：不加評論、不加原文沒有的字詞（原文沒說「無糖」「士兵」「起訴」就不能寫）、不推測、不省略原文的數字。',
    '   片語照整句意思翻，不逐字翻（turn on＝轉而看壞、look past＝不理會、beat forecasts＝優於預期）。',
    '2. 數字一律用阿拉伯數字（第3季、2026年、G7、5G 照原樣）。金額換成中文單位：1 billion＝10億、3.5 billion＝35億、500 million＝5億、25 million＝2,500萬、1 trillion＝1兆。百分比照抄（4.5%）。',
    '3. 公司、機構、人名、產品用詞彙表的台灣慣用譯名；不在詞彙表又不確定台灣譯名的，一律保留英文原文照抄（例：Huawei-JAC Maextro、Takaichi），不准自己音譯或猜中文名。',
    '4. 英文一般字詞都要翻成中文，不可留英文單字（專有名詞與縮寫除外）。',
    '5. 漲跌、升降方向照原文，不可相反。',
    '6. 標題 40 字以內；摘要照原意翻完。原文沒有摘要時 summary_zh 給空字串。',
    `詞彙表：${terms}`,
    '輸出 JSON：{"items":[{"id":"…","title_zh":"…","summary_zh":"…"}]}，每一則都要有，id 照抄。',
  ].join('\n')
}

export const RESPONSE_SCHEMA = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, title_zh: { type: 'string' }, summary_zh: { type: 'string' } },
        required: ['id', 'title_zh', 'summary_zh'],
      },
    },
  },
  required: ['items'],
}

/** Workers AI REST 的請求內容（同 src/twquant/insight/client.py cloudflare_request_body）。 */
export function requestBody(cfg: TranslateConfig, items: Source[]): Record<string, unknown> {
  const user = JSON.stringify({ items: items.map((s) => ({ id: s.id, title: s.title, summary: s.summary ?? '' })) })
  return {
    messages: [
      { role: 'system', content: systemPrompt(cfg) + cfg.system_suffix },
      { role: 'user', content: user },
    ],
    max_tokens: cfg.max_tokens,
    response_format: { type: 'json_schema', json_schema: RESPONSE_SCHEMA },
  }
}

const THINK = /<think>[\s\S]*?<\/think>/g
const FENCE = /^```[a-zA-Z]*\s*|\s*```$/g

/** Workers AI 回應 → 譯文與 token 數；success=false、沒內容、沒用量、格式不對 → 丟錯（同 Python parse_cloudflare_reply）。 */
export function parseReply(payload: unknown): { items: Translated[]; inputTokens: number; outputTokens: number } {
  const p = payload as { success?: boolean; errors?: unknown; result?: Record<string, unknown> } | null
  if (!p?.success) throw new Error(`Workers AI 回報失敗：${JSON.stringify(p?.errors ?? null)}`)
  const result = p.result ?? {}
  const choices = (result.choices as { message?: { content?: unknown } }[] | undefined) ?? []
  const content = result.response ?? choices[0]?.message?.content ?? null
  const usage = (result.usage as { prompt_tokens?: number; completion_tokens?: number } | undefined) ?? {}
  if (usage.prompt_tokens === undefined || usage.completion_tokens === undefined) throw new Error('Workers AI 回應沒有 usage，無法記帳')
  if (content === null || content === undefined) throw new Error(`Workers AI 回應內容為空（completion_tokens=${usage.completion_tokens}）`)
  const obj = typeof content === 'string' ? JSON.parse(content.replace(THINK, '').trim().replace(FENCE, '').trim()) : content
  const items = (obj as { items?: unknown }).items
  if (!Array.isArray(items)) throw new Error('Workers AI 回應沒有 items')
  const out: Translated[] = []
  for (const it of items as Record<string, unknown>[]) {
    if (typeof it.id !== 'string' || typeof it.title_zh !== 'string' || typeof it.summary_zh !== 'string') throw new Error(`譯文格式不對：${JSON.stringify(it).slice(0, 120)}`)
    out.push({ id: it.id, title_zh: it.title_zh.trim(), summary_zh: it.summary_zh.trim() })
  }
  return { items: out, inputTokens: usage.prompt_tokens, outputTokens: usage.completion_tokens }
}

export const neurons = (cfg: TranslateConfig, inTok: number, outTok: number): number =>
  (inTok * cfg.input_neurons_per_1m + outTok * cfg.output_neurons_per_1m) / 1_000_000

// ── 檢查 ───────────────────────────────────────────────────────────────────

export type Quantity = { value: number; pct: boolean; text: string }

const EN_SCALE: Record<string, number> = { trillion: 1e12, tn: 1e12, billion: 1e9, bn: 1e9, million: 1e6, mn: 1e6, m: 1e6, thousand: 1e3, k: 1e3 }
const EN_NUM = /(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:\s*(%|percent\b|per cent\b|trillion\b|tn\b|billion\b|bn\b|million\b|mn\b|m\b|thousand\b|k\b))?/gi

/** 英文裡的數量：3.5 billion → 3.5e9；4.5% → 4.5（百分比）；2026 → 2026。 */
export function englishQuantities(text: string): Quantity[] {
  const out: Quantity[] = []
  for (const m of text.matchAll(EN_NUM)) {
    const n = Number(m[1].replace(/,/g, '') + (m[2] ?? ''))
    const unit = (m[3] ?? '').toLowerCase()
    const pct = unit === '%' || unit === 'percent' || unit === 'per cent'
    out.push({ value: pct || unit === '' ? n : n * EN_SCALE[unit], pct, text: m[0] })
  }
  return out
}

const ZH_SCALE: Record<string, number> = { 兆: 1e12, 千億: 1e11, 百億: 1e10, 十億: 1e9, 億: 1e8, 千萬: 1e7, 百萬: 1e6, 萬: 1e4, 千: 1e3 }
const ZH_NUM = /(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|兆|千億|百億|十億|億|千萬|百萬|萬|千)?/g

/** 中文裡的數量：35億 → 3.5e9；4.5% → 4.5（百分比）；2,500萬 → 2.5e7。 */
export function chineseQuantities(text: string): Quantity[] {
  const out: Quantity[] = []
  for (const m of text.matchAll(ZH_NUM)) {
    const n = Number(m[1].replace(/,/g, '') + (m[2] ?? ''))
    const unit = m[3] ?? ''
    out.push({ value: unit === '%' || unit === '' ? n : n * ZH_SCALE[unit], pct: unit === '%', text: m[0] })
  }
  return out
}

const same = (a: Quantity, b: Quantity) => a.pct === b.pct && Math.abs(a.value - b.value) <= 1e-9 * Math.max(1, Math.abs(a.value))

/** 原文每個數量換算後都要在譯文找到；回傳找不到的（空＝通過）。 */
export function missingNumbers(orig: string, zh: string): string[] {
  const z = chineseQuantities(zh)
  return englishQuantities(orig)
    .filter((q) => !z.some((c) => same(q, c)))
    .map((q) => q.text.trim())
}

/** 譯文多出原文沒有的數量（只記錄：月份、季別這類換寫法會多出來）。 */
export function extraNumbers(orig: string, zh: string): string[] {
  const e = englishQuantities(orig)
  return chineseQuantities(zh)
    .filter((c) => !e.some((q) => same(q, c)))
    .map((c) => c.text.trim())
}

const s2tCache = new Map<string, Map<string, string>>()
/** s2t_pairs → 簡→繁對照（同一份設定只建一次）。 */
export function s2tMap(pairs: string): Map<string, string> {
  let m = s2tCache.get(pairs)
  if (!m) {
    const cps = [...pairs]
    m = new Map()
    for (let i = 0; i + 1 < cps.length; i += 2) m.set(cps[i], cps[i + 1])
    s2tCache.set(pairs, m)
  }
  return m
}

/** 文字裡的簡體字（去重、照出現順序）。 */
export const simplifiedIn = (text: string, pairs: string): string[] => {
  const m = s2tMap(pairs)
  return [...new Set([...text].filter((ch) => m.has(ch)))]
}

/** 譯文裡留著沒翻的英文一般字詞（全小寫、3 個字母以上；專有名詞與縮寫有大寫，不算）：2026-10-19 實測「日本首相立憲 rejects…」。 */
export function untranslatedWords(zh: string): string[] {
  return [...new Set([...zh.matchAll(/(?<![A-Za-z])[a-z]{3,}(?![A-Za-z])/g)].map((m) => m[0]))]
}

/** 譯文裡的英文字（3 個字母以上），原文沒有的（不分大小寫）：多半是公司名拼錯（2026-10-19 實測 Wooptix→Woopitx）。 */
export function foreignTokens(orig: string, zh: string): string[] {
  const src = orig.toLowerCase()
  return [...new Set([...zh.matchAll(/[A-Za-z][A-Za-z0-9&-]{2,}/g)].map((m) => m[0]))].filter((w) => !src.includes(w.toLowerCase()))
}

const EN_UP = /\b(rise|rises|rose|risen|rising|jump|jumps|jumped|surge|surges|surged|soar|soars|soared|gain|gains|gained|climb|climbs|climbed|rally|rallies|rallied|rebound|rebounds|rebounded|higher)\b/i
const EN_DOWN = /\b(fall|falls|fell|fallen|falling|drop|drops|dropped|slump|slumps|slumped|plunge|plunges|plunged|decline|declines|declined|tumble|tumbles|tumbled|sink|sinks|sank|slide|slides|slid|lower|rout|selloff|sell-off)\b/i
const ZH_UP = /[漲升揚增飆]|反彈|走高/
const ZH_DOWN = /[跌降挫滑減]|下修|重挫|走低/

/** 標題的漲跌方向：+1 漲、-1 跌、0 看不出來或兩者都有。 */
export function englishDirection(text: string): number {
  const up = EN_UP.test(text)
  const down = EN_DOWN.test(text)
  return up === down ? 0 : up ? 1 : -1
}
export function chineseDirection(text: string): number {
  const up = ZH_UP.test(text)
  const down = ZH_DOWN.test(text)
  return up === down ? 0 : up ? 1 : -1
}

const CN_NUMBER = /[一二三四五六七八九十百千]+[成倍]/g
const NORMAL_CN = new Set(['一倍', '兩倍', '一成', '兩成'])

const LATIN = /[A-Za-z][A-Za-z0-9&-]{2,}/g

/** 兩個字串差幾個字（插入、刪除、替換各算一）。 */
export function editDistance(a: string, b: string): number {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)])
  for (let j = 1; j <= b.length; j++) d[0][j] = j
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
  return d[a.length][b.length]
}

/**
 * 模型輸出的自動修正（每一項都記在 fixes，不靜默）：簡體字轉繁體、對岸用語換台灣用語、
 * 跟原文英文名只差一兩個字母（同首字母、5 個字母以上）的改回原文拼法。
 */
export function normalize(cfg: TranslateConfig, src: Source, t: Translated): { t: Translated; fixes: string[] } {
  const fixes: string[] = []
  const map = s2tMap(cfg.s2t_pairs)
  const words = [...new Set([...`${src.title} ${src.summary ?? ''}`.matchAll(LATIN)].map((m) => m[0]))]
  const lowerOrig = `${src.title} ${src.summary ?? ''}`.toLowerCase()
  const fix = (text: string): string => {
    let out = [...text]
      .map((ch) => {
        const tr = map.get(ch)
        if (tr) fixes.push(`${ch}→${tr}`)
        return tr ?? ch
      })
      .join('')
    // 季別一律阿拉伯數字（原文 Q3 → 第3季，數字檢查才對得上）
    out = out.replace(/第([一二三四])季/g, (m, n: string) => {
      const d = `第${'一二三四'.indexOf(n) + 1}季`
      fixes.push(`${m}→${d}`)
      return d
    })
    for (const [cn, tw] of Object.entries(cfg.mainland_terms)) {
      if (out.includes(cn)) {
        fixes.push(`${cn}→${tw}`)
        out = out.split(cn).join(tw)
      }
    }
    return out.replace(LATIN, (w) => {
      if (w.length < 5 || lowerOrig.includes(w.toLowerCase())) return w
      const near = words.filter((o) => o.length >= 5 && o[0].toLowerCase() === w[0].toLowerCase() && editDistance(o.toLowerCase(), w.toLowerCase()) <= 2)
      if (near.length !== 1) return w // 沒有或不只一個候選：不猜，交給檢查擋
      fixes.push(`${w}→${near[0]}`)
      return near[0]
    })
  }
  return { t: { id: t.id, title_zh: fix(t.title_zh), summary_zh: fix(t.summary_zh) }, fixes: [...new Set(fixes)] }
}

export type Checks = {
  /** 自動修正了什麼（簡體字、對岸用語、拼錯的英文名） */
  fixes: string[]
  /** 擋：原文數字在譯文找不到 */
  missing_numbers: string[]
  /** 擋：簡體字 */
  simplified: string[]
  /** 擋：譯文的英文字原文沒有（拼錯的公司名） */
  foreign_tokens: string[]
  /** 擋：留著沒翻的英文一般字詞 */
  untranslated: string[]
  /** 記錄：標題漲跌方向相反 */
  direction_flip: boolean
  /** 記錄：中文數字（七成、三倍） */
  chinese_numbers: string[]
  /** 記錄：修正後還在的對岸用語（應該是空的） */
  mainland_terms: string[]
  /** 記錄：譯文多出的數字 */
  extra_numbers: string[]
}

/** 修正後的檢查；fixes 由 normalize 給。 */
export function check(cfg: TranslateConfig, src: Source, t: Translated, fixes: string[] = []): Checks {
  const orig = `${src.title}\n${src.summary ?? ''}`
  const zh = `${t.title_zh}\n${t.summary_zh}`
  const dirEn = englishDirection(src.title)
  const dirZh = chineseDirection(t.title_zh)
  return {
    fixes,
    missing_numbers: missingNumbers(orig, zh),
    simplified: simplifiedIn(zh, cfg.s2t_pairs),
    foreign_tokens: foreignTokens(orig, zh),
    untranslated: untranslatedWords(zh),
    direction_flip: dirEn !== 0 && dirZh !== 0 && dirEn !== dirZh,
    chinese_numbers: [...zh.matchAll(CN_NUMBER)].map((m) => m[0]).filter((s) => !NORMAL_CN.has(s)),
    mainland_terms: Object.keys(cfg.mainland_terms).filter((w) => zh.includes(w)),
    extra_numbers: extraNumbers(orig, zh),
  }
}

/** 會擋的四項都沒問題＝可以用。 */
export const passes = (c: Checks): boolean =>
  c.missing_numbers.length === 0 && c.simplified.length === 0 && c.foreign_tokens.length === 0 && c.untranslated.length === 0

// ── 流程（IO 注入）────────────────────────────────────────────────────────

export type TranslationRow = {
  item_id: string
  content_sha: string
  title_zh: string | null
  summary_zh: string | null
  status: 'ok' | 'failed'
  checks: Checks | { error: string }
  model_id: string
  prompt_version: string
  translated_at: string
}
export type UsageRow = { ts: string; task: string; model_id: string; input_tokens: number; output_tokens: number; neurons: number }
export type Pending = Source & { content_sha: string }

export type TranslateDeps = {
  now: () => Date
  /** 還沒翻過「目前這一版內容」的國際新聞，新的先 */
  pending: (limit: number) => Promise<Pending[]>
  /** 這個 UTC 日新聞這邊已用的 Neurons */
  usedToday: (utcDay: string) => Promise<number>
  /** 呼叫 Workers AI，回原始 JSON（HTTP 錯誤丟出） */
  ai: (body: Record<string, unknown>) => Promise<unknown>
  save: (rows: TranslationRow[], usage: UsageRow[]) => Promise<void>
}

export type TranslateResult = { translated: number; failed: number; budget_stop: boolean }

/** 一批一批翻；擋下的那則單獨重翻一次，還是不過就記 failed（網站顯示原文）。額度用完就停，剩下的下次。 */
export async function translatePending(cfg: TranslateConfig, deps: TranslateDeps): Promise<TranslateResult> {
  const day = deps.now().toISOString().slice(0, 10)
  let used = await deps.usedToday(day)
  const todo = await deps.pending(cfg.per_run_limit)
  const res: TranslateResult = { translated: 0, failed: 0, budget_stop: false }

  const call = async (items: Pending[]): Promise<Map<string, Translated> | null> => {
    if (used >= cfg.daily_neurons) {
      res.budget_stop = true
      return null
    }
    const payload = await deps.ai(requestBody(cfg, items))
    const r = parseReply(payload)
    const n = neurons(cfg, r.inputTokens, r.outputTokens)
    used += n
    await deps.save([], [{ ts: deps.now().toISOString(), task: 'news_translate', model_id: cfg.model, input_tokens: r.inputTokens, output_tokens: r.outputTokens, neurons: n }])
    return new Map(r.items.map((t) => [t.id, t]))
  }
  const row = (p: Pending, t: Translated | null, c: Checks | { error: string }, ok: boolean): TranslationRow => ({
    item_id: p.id,
    content_sha: p.content_sha,
    title_zh: ok && t ? t.title_zh : null,
    summary_zh: ok && t ? t.summary_zh || null : null,
    status: ok ? 'ok' : 'failed',
    checks: c,
    model_id: cfg.model,
    prompt_version: PROMPT_VERSION,
    translated_at: deps.now().toISOString(),
  })

  for (let i = 0; i < todo.length && !res.budget_stop; i += cfg.batch) {
    const batch = todo.slice(i, i + cfg.batch)
    let got: Map<string, Translated> | null
    try {
      got = await call(batch)
    } catch {
      got = new Map() // 整批失敗（逾時、格式壞）：每則各自單獨重試
    }
    if (got === null) break
    const rows: TranslationRow[] = []
    // 模型的輸出先自動修正（記在 checks.fixes）再檢查
    const review = (p: Pending, raw: Translated): { t: Translated; c: Checks } => {
      const n = normalize(cfg, p, raw)
      return { t: n.t, c: check(cfg, p, n.t, n.fixes) }
    }
    for (const p of batch) {
      const first = got.get(p.id)
      let t: Translated | null = null
      let c: Checks | null = null
      if (first) ({ t, c } = review(p, first))
      if (!t || !c || !passes(c)) {
        // 重試一次（只這一則）
        let retry: Map<string, Translated> | null = new Map()
        let error: string | null = null
        try {
          retry = await call([p])
        } catch (e) {
          error = e instanceof Error ? e.message : String(e)
        }
        if (retry === null) break // 額度用完：這則不寫，下次再翻
        const t2 = retry.get(p.id) ?? null
        if (t2) ({ t, c } = review(p, t2))
        if (!t || !c) {
          rows.push(row(p, null, { error: error ?? '模型沒有回這一則' }, false))
          res.failed += 1
          continue
        }
      }
      const ok = passes(c!)
      rows.push(row(p, t, c!, ok))
      if (ok) res.translated += 1
      else res.failed += 1
    }
    if (rows.length > 0) await deps.save(rows, [])
  }
  return res
}
