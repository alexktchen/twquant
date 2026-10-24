// 新聞情緒（D-205 §三 走 A，2026-10-24）：雲端 qwen3 對「這則新聞對這檔股票」判斷好／壞／中性。
// 本機要關機，情緒小模型（D-205 B）判定沒過（macro-F1 0.654 < 0.70），所以每則新聞進來就問一次雲端 AI：
// 一則新聞一次問完它提到的所有台股（省 token），只問近 window_hours 內的新聞（網站看近期；舊的不回補）。
// 額度自己記帳（task＝news_sentiment，上限 daily_neurons，D-218），不跟翻譯、向量搶。
// 判斷依據跟本機 Ollama 標籤同一套說法（src/twquant/data/news_sentiment.py build_prompt）：看新聞怎麼說，不是預測股價。

export type SentimentConfig = {
  model: string
  system_suffix: string
  prompt_version: string
  per_run_limit: number
  window_hours: number
  max_stocks: number
  max_tokens: number
  /** 情緒自己的額度（UTC 日）；只算 task＝news_sentiment 的用量 */
  daily_neurons: number
  input_neurons_per_1m: number
  output_neurons_per_1m: number
}

export function validateSentimentConfig(x: unknown): SentimentConfig {
  const c = x as Partial<SentimentConfig> | null
  const fail = (why: string): never => {
    throw new Error(`news 設定錯誤：sentiment.${why}`)
  }
  if (!c || typeof c.model !== 'string' || !c.model.startsWith('@cf/')) fail('model')
  if (typeof c!.system_suffix !== 'string') fail('system_suffix')
  if (typeof c!.prompt_version !== 'string' || !c!.prompt_version) fail('prompt_version')
  for (const k of ['per_run_limit', 'window_hours', 'max_stocks', 'max_tokens'] as const) if (!Number.isInteger(c![k]) || (c![k] as number) < 1) fail(k)
  for (const k of ['daily_neurons', 'input_neurons_per_1m', 'output_neurons_per_1m'] as const) if (typeof c![k] !== 'number' || (c![k] as number) <= 0) fail(k)
  return c as SentimentConfig
}

export type SentimentStock = { id: string; label: string }
export type SentimentItem = { item_id: string; content_sha: string; title: string; summary: string | null; stocks: SentimentStock[] }
/** −1 壞、0 中性、1 好 */
export type Label = -1 | 0 | 1
export type SentimentRow = { item_id: string; entity_id: string; content_sha: string; label: Label; model: string; prompt_version: string }
export type UsageRow = { ts: string; task: string; model_id: string; input_tokens: number; output_tokens: number; neurons: number }

const WORD: Record<string, Label> = { 好: 1, 好消息: 1, 正面: 1, 利多: 1, 壞: -1, 壞消息: -1, 負面: -1, 利空: -1, 中性: 0, 無關: 0 }

const SYSTEM =
  '你判斷一則財經新聞對「每一家指定的台灣公司」是好消息、壞消息、還是中性或無關。' +
  '依據是新聞怎麼說（營收、訂單、獲利、評等、監管、意外等），不是你對股價的預測。' +
  '同一則新聞對不同公司可能不一樣（例如搶下對手訂單：對得標者是好消息、對失標者是壞消息）。' +
  '只回 JSON：{"items":[{"id":"股票代號","label":"好|壞|中性"}]}，每家公司一筆。'

const SCHEMA = {
  name: 'news_sentiment',
  schema: {
    type: 'object',
    properties: {
      items: {
        type: 'array',
        items: { type: 'object', properties: { id: { type: 'string' }, label: { type: 'string', enum: ['好', '壞', '中性'] } }, required: ['id', 'label'] },
      },
    },
    required: ['items'],
  },
}

/** 一則新聞的 Workers AI 請求內容（同翻譯的格式：messages＋json_schema）。 */
export function requestBody(cfg: SentimentConfig, it: SentimentItem): Record<string, unknown> {
  const user = { 標題: it.title, 摘要: it.summary ?? '', 公司: it.stocks.slice(0, cfg.max_stocks).map((s) => ({ id: s.id.replace(/^tw:/, ''), 名稱: s.label })) }
  return {
    messages: [
      { role: 'system', content: SYSTEM + cfg.system_suffix },
      { role: 'user', content: JSON.stringify(user) },
    ],
    max_tokens: cfg.max_tokens,
    response_format: { type: 'json_schema', json_schema: SCHEMA },
  }
}

const THINK = /<think>[\s\S]*?<\/think>/g
const FENCE = /^```[a-zA-Z]*\s*|\s*```$/g

/** Workers AI 回應 → 每檔的標籤與 token 數；格式不對、問的股票沒回、回了沒問的 → 丟錯（不猜）。 */
export function parseReply(payload: unknown, asked: readonly string[]): { labels: Map<string, Label>; inputTokens: number; outputTokens: number } {
  const p = payload as { success?: boolean; errors?: unknown; result?: Record<string, unknown> } | null
  if (!p?.success) throw new Error(`Workers AI 回報失敗：${JSON.stringify(p?.errors ?? null)}`)
  const result = p.result ?? {}
  const choices = (result.choices as { message?: { content?: unknown } }[] | undefined) ?? []
  const content = result.response ?? choices[0]?.message?.content ?? null
  const usage = (result.usage as { prompt_tokens?: number; completion_tokens?: number } | undefined) ?? {}
  if (usage.prompt_tokens === undefined || usage.completion_tokens === undefined) throw new Error('Workers AI 回應沒有 usage，無法記帳')
  if (content === null || content === undefined) throw new Error('Workers AI 回應內容為空')
  const obj = typeof content === 'string' ? JSON.parse(content.replace(THINK, '').trim().replace(FENCE, '').trim()) : content
  const items = (obj as { items?: unknown }).items
  if (!Array.isArray(items)) throw new Error('情緒回應沒有 items')
  const want = new Set(asked.map((a) => a.replace(/^tw:/, '')))
  const labels = new Map<string, Label>()
  for (const it of items as Record<string, unknown>[]) {
    const id = String(it.id ?? '').trim()
    const word = String(it.label ?? '').trim()
    if (!want.has(id)) throw new Error(`情緒回應多了沒問的股票：${id}`)
    if (!(word in WORD)) throw new Error(`情緒回應的標籤看不懂：${word}`)
    labels.set(`tw:${id}`, WORD[word])
  }
  const missing = [...want].filter((id) => !labels.has(`tw:${id}`))
  if (missing.length > 0) throw new Error(`情緒回應少了：${missing.join('、')}`)
  return { labels, inputTokens: usage.prompt_tokens, outputTokens: usage.completion_tokens }
}

export const neurons = (cfg: SentimentConfig, inTok: number, outTok: number): number =>
  (inTok * cfg.input_neurons_per_1m + outTok * cfg.output_neurons_per_1m) / 1_000_000

export type SentimentDeps = {
  now: () => Date
  /** 近 window_hours 內、有台股提及、這一版內容還沒判斷過的新聞（英文要翻譯完，用譯文），新的先 */
  pending: (limit: number, windowHours: number, promptVersion: string) => Promise<SentimentItem[]>
  /** 這個 UTC 日 task＝news_sentiment 已用的 Neurons */
  usedToday: (utcDay: string) => Promise<number>
  ai: (body: Record<string, unknown>) => Promise<unknown>
  save: (rows: SentimentRow[], usage: UsageRow[]) => Promise<void>
}

export type SentimentResult = { items: number; labels: number; failed: number; budget_stop: boolean; by_label: Record<string, number>; errors: string[] }

/** ingest 第七步：一則一則問（一則一次問完它提到的台股）；壞回應那則這輪跳過、下一輪再問；額度用完就停。 */
export async function sentimentPending(cfg: SentimentConfig, deps: SentimentDeps): Promise<SentimentResult> {
  const day = deps.now().toISOString().slice(0, 10)
  let used = await deps.usedToday(day)
  const todo = await deps.pending(cfg.per_run_limit, cfg.window_hours, cfg.prompt_version)
  const res: SentimentResult = { items: 0, labels: 0, failed: 0, budget_stop: false, by_label: { '-1': 0, '0': 0, '1': 0 }, errors: [] }
  const note = (it: SentimentItem, e: unknown) => {
    if (res.errors.length < 5) res.errors.push(`${it.item_id}：${e instanceof Error ? e.message : String(e)}`.slice(0, 200))
  }
  for (const it of todo) {
    if (used >= cfg.daily_neurons) {
      res.budget_stop = true
      break
    }
    const stocks = it.stocks.slice(0, cfg.max_stocks)
    let payload: unknown
    try {
      payload = await deps.ai(requestBody(cfg, it))
    } catch (e) {
      note(it, e)
      res.failed += 1
      continue
    }
    let r: ReturnType<typeof parseReply>
    try {
      r = parseReply(payload, stocks.map((s) => s.id))
    } catch (e) {
      note(it, e)
      // 用量照記（模型有算），標籤不存，下一輪再問
      const p = payload as { result?: { usage?: { prompt_tokens?: number; completion_tokens?: number } } } | null
      const inT = p?.result?.usage?.prompt_tokens ?? 0
      const outT = p?.result?.usage?.completion_tokens ?? 0
      const n = neurons(cfg, inT, outT)
      used += n
      await deps.save([], [{ ts: deps.now().toISOString(), task: 'news_sentiment', model_id: cfg.model, input_tokens: inT, output_tokens: outT, neurons: n }])
      res.failed += 1
      continue
    }
    const n = neurons(cfg, r.inputTokens, r.outputTokens)
    used += n
    const rows: SentimentRow[] = [...r.labels].map(([entity_id, label]) => ({ item_id: it.item_id, entity_id, content_sha: it.content_sha, label, model: cfg.model, prompt_version: cfg.prompt_version }))
    await deps.save(rows, [{ ts: deps.now().toISOString(), task: 'news_sentiment', model_id: cfg.model, input_tokens: r.inputTokens, output_tokens: r.outputTokens, neurons: n }])
    res.items += 1
    res.labels += rows.length
    for (const row of rows) res.by_label[String(row.label)] += 1
  }
  return res
}
