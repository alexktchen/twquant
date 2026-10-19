import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { cleanText, decodeEntities, parseCnyes, parseFeed, parseTime } from '../src/feed.ts'
import {
  contentSha,
  ingestSource,
  itemId,
  runIngest,
  validateConfig,
  type Deps,
  type FetchResult,
  type ItemRow,
  type NewsConfig,
  type NewsStore,
  type RunRow,
  type SourceState,
  type VersionRow,
} from '../src/ingest.ts'

const fixture = (name: string) => readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8')
const config = validateConfig(JSON.parse(readFileSync(new URL('../config/news.json', import.meta.url), 'utf8')))

// ── AC1：各來源的格式（錄好的真實回應，各留 3 則）──────────────────────────

test('T-400 AC1：RSS 2.0（Bloomberg、CNBC、FT、DigiTimes、Fed）解析出標題、網址、純文字摘要與時間', () => {
  for (const f of ['bloomberg-markets.xml', 'cnbc-top.xml', 'ft-markets.xml', 'digitimes.xml', 'fed-press.xml']) {
    const items = parseFeed(fixture(f))
    assert.equal(items.length, 3, f)
    for (const it of items) {
      assert.ok(it.title.length > 0 && !/<|CDATA|&amp;/.test(it.title), `${f} 標題：${it.title}`)
      assert.match(it.url, /^https?:\/\//, f)
      assert.ok(it.guid.length > 0, f)
      if (it.summary !== null) assert.ok(!/<[a-z]|CDATA/i.test(it.summary), `${f} 摘要還有標籤：${it.summary}`)
      assert.ok(it.publishedAt !== null && !Number.isNaN(Date.parse(it.publishedAt)), `${f} 時間`)
    }
  }
})

test('T-400 AC1：Nikkei Asia 是 RDF、沒有發布時間 → publishedAt 為 null，網址取 rdf:about／link', () => {
  const items = parseFeed(fixture('nikkei-asia.xml'))
  assert.equal(items.length, 3)
  for (const it of items) {
    assert.equal(it.publishedAt, null)
    assert.match(it.url, /^https:\/\/asia\.nikkei\.com\//)
  }
})

test('T-400 AC1：鉅亨網列表 API；格式不對就丟錯', () => {
  const items = parseCnyes(JSON.parse(fixture('cnyes-headline.json')))
  assert.equal(items.length, 3)
  assert.match(items[0].url, /^https:\/\/news\.cnyes\.com\/news\/id\/\d+$/)
  assert.throws(() => parseCnyes({ statusCode: 500 }), /鉅亨網/)
  assert.throws(() => parseCnyes({ statusCode: 200, items: { data: [{ title: 'x' }] } }), /缺代號/)
})

test('T-400 AC1：實體、CDATA、HTML 標籤；看不懂的時間丟錯、空的時間是 null；不是 RSS 丟錯', () => {
  assert.equal(decodeEntities('A &amp; B &#x2019;s &#8220;up&#8221; &nbsp;x'), 'A & B ’s “up”  x')
  assert.equal(cleanText('<![CDATA[<p>Chip <b>stocks</b> &amp; AI</p>]]>'), 'Chip stocks & AI')
  assert.equal(cleanText('&lt;p&gt;rally&lt;/p&gt;'), 'rally')
  assert.equal(parseTime('Mon, 19 Oct 2026 04:01 GMT'), '2026-10-19T04:01:00.000Z')
  assert.equal(parseTime('Mon, 27 Jan 2025 14:27:15 -0500'), '2025-01-27T19:27:15.000Z')
  assert.equal(parseTime('  '), null)
  assert.throws(() => parseTime('yesterday-ish'), /看不懂的時間/)
  assert.throws(() => parseFeed('<html>blocked</html>'), /不是 RSS/)
  assert.deepEqual(parseFeed('<rss><channel></channel></rss>'), [])
  const atom = '<feed><entry><title>T</title><link rel="alternate" href="https://a.example/x"/><id>tag:1</id><updated>2026-10-19T01:00:00Z</updated></entry></feed>'
  assert.deepEqual(parseFeed(atom), [{ guid: 'tag:1', url: 'https://a.example/x', title: 'T', summary: null, publishedAt: '2026-10-19T01:00:00.000Z' }])
})

test('T-400：設定檔鏡像合法；少欄位、代號重複就丟錯', () => {
  assert.ok(config.sources.length >= 8)
  assert.ok(config.sources.every((s) => s.lang === (s.kind === 'intl' ? 'en' : 'zh')))
  assert.throws(() => validateConfig({ ...config, per_run_limit: 0 }), /per_run_limit/)
  assert.throws(() => validateConfig({ ...config, sources: [config.sources[0], config.sources[0]] }), /來源代號/)
  assert.throws(() => validateConfig({ ...config, sources: [{ ...config.sources[0], url: 'http://x' }] }), /網址/)
})

// ── AC2～AC4：進料流程（假的來源、時鐘、資料庫）──────────────────────────

class MemStore implements NewsStore {
  items = new Map<string, ItemRow>()
  versions: VersionRow[] = []
  runs: RunRow[] = []
  states = new Map<string, SourceState>()
  async state(source: string) {
    return this.states.get(source) ?? null
  }
  async saveState(source: string, st: SourceState) {
    this.states.set(source, st)
  }
  async latestSha(ids: string[]) {
    const out = new Map<string, string>()
    for (const id of ids) {
      const it = this.items.get(id)
      if (!it) continue
      const last = this.versions.filter((v) => v.item_id === id).at(-1)
      out.set(id, last?.content_sha ?? it.content_sha)
    }
    return out
  }
  async insert(items: ItemRow[], versions: VersionRow[]) {
    for (const it of items) {
      assert.ok(!this.items.has(it.id), '只增不改：同一個 id 不得再寫一次')
      this.items.set(it.id, it)
    }
    this.versions.push(...versions)
  }
  async recordRun(run: RunRow) {
    this.runs.push(run)
  }
}

const rss = (items: { guid: string; title: string; desc?: string }[]) =>
  `<rss><channel>${items
    .map((i) => `<item><title>${i.title}</title><link>https://ex.test/${i.guid}</link><guid>${i.guid}</guid><description>${i.desc ?? ''}</description><pubDate>Mon, 19 Oct 2026 04:01 GMT</pubDate></item>`)
    .join('')}</channel></rss>`

const oneSource: NewsConfig = { ...config, sources: [config.sources[0]] }

function deps(store: MemStore, pages: Record<string, FetchResult | Error>, at = '2026-10-19T06:00:00.000Z'): Deps & { asked: (SourceState | null)[] } {
  const asked: (SourceState | null)[] = []
  return {
    asked,
    now: () => new Date(at),
    runId: 'r1',
    store,
    fetch: async (url, state) => {
      asked.push(state)
      const p = pages[url]
      if (p instanceof Error) throw p
      if (!p) throw new Error(`沒有假回應：${url}`)
      return p
    },
  }
}
const ok = (body: string, etag: string | null = null): FetchResult => ({ status: 200, body, etag, lastModified: null })

test('T-400 AC2：新的一則寫一列；同內容再抓不重寫；標題改了加一版；同一份裡重複的只算一次', async () => {
  const src = oneSource.sources[0]
  const store = new MemStore()
  const r1 = await ingestSource(src, oneSource, deps(store, { [src.url]: ok(rss([{ guid: 'a', title: 'Old' }, { guid: 'a', title: 'Old' }, { guid: 'b', title: 'B' }]), 'e1') }))
  assert.deepEqual([r1.ok, r1.fetched, r1.new_items, r1.new_versions], [true, 3, 2, 0])

  const d2 = deps(store, { [src.url]: ok(rss([{ guid: 'a', title: 'New title' }, { guid: 'b', title: 'B' }])) }, '2026-10-19T06:30:00.000Z')
  const r2 = await ingestSource(src, oneSource, d2)
  assert.deepEqual([r2.new_items, r2.new_versions], [0, 1])
  assert.deepEqual(d2.asked[0], { etag: 'e1', lastModified: null }, '帶上次的 ETag')
  const a = await itemId(src.id, 'a')
  assert.equal(store.items.get(a)?.title_orig, 'Old', '原本那列不被改')
  assert.deepEqual(store.versions.map((v) => [v.item_id, v.title_orig, v.seen_at]), [[a, 'New title', '2026-10-19T06:30:00.000Z']])
  assert.equal(store.versions[0].content_sha, await contentSha('New title', null))

  const r3 = await ingestSource(src, oneSource, deps(store, { [src.url]: ok(rss([{ guid: 'a', title: 'New title' }])) }))
  assert.deepEqual([r3.new_items, r3.new_versions], [0, 0], '跟最新一版相同就不再加')
})

test('T-400 AC3：一個來源壞掉不擋其他；錯誤記進執行紀錄；304 算成功、0 則', async () => {
  const [s1, s2, s3] = config.sources
  const cfg = { ...config, sources: [s1, s2, s3] }
  const store = new MemStore()
  const runs = await runIngest(cfg, deps(store, { [s1.url]: new Error('連線被重設'), [s2.url]: { status: 304, body: null, etag: null, lastModified: null }, [s3.url]: ok(rss([{ guid: 'x', title: 'X' }])) }))
  assert.deepEqual(
    runs.map((r) => [r.source, r.ok, r.new_items, r.error]),
    [
      [s1.id, false, 0, '連線被重設'],
      [s2.id, true, 0, null],
      [s3.id, true, 1, null],
    ],
  )
  assert.equal(store.runs.length, 3)
  const bad = await ingestSource(s1, cfg, deps(store, { [s1.url]: { status: 429, body: 'slow down', etag: null, lastModified: null } }))
  assert.deepEqual([bad.ok, bad.http_status, bad.error], [false, 429, 'HTTP 429'])
  const html = await ingestSource(s1, cfg, deps(store, { [s1.url]: ok('<html>Access denied</html>') }))
  assert.match(html.error ?? '', /不是 RSS/)
})

test('T-400 AC4：first_seen_at 是我們的時鐘，不是來源的發布時間；每次最多收 per_run_limit 則', async () => {
  const src = oneSource.sources[0]
  const store = new MemStore()
  const many = Array.from({ length: 5 }, (_, i) => ({ guid: `g${i}`, title: `T${i}` }))
  await ingestSource(src, { ...oneSource, per_run_limit: 3 }, deps(store, { [src.url]: ok(rss(many)) }, '2026-10-19T22:05:00.000Z'))
  const rows = [...store.items.values()]
  assert.equal(rows.length, 3)
  assert.ok(rows.every((r) => r.first_seen_at === '2026-10-19T22:05:00.000Z' && r.published_at === '2026-10-19T04:01:00.000Z'))
  assert.deepEqual(rows.map((r) => r.guid), ['g0', 'g1', 'g2'])
})
