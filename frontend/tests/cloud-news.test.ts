import assert from 'node:assert/strict'
import test from 'node:test'
import { ALLOWED, proxy, SUMMARY_TIMEOUT_MS, UPSTREAM_TIMEOUT_MS, upstreamUrl } from '../functions/_lib/news-proxy.ts'
import { ageLabel, appendPage, dayTitle, groupByDay, healthLine, mentionChips, needsSummary, nextUntil, readerSummary, shown, shownTime, sentimentMark, splitSimilar, stockMood, taipeiHm, timeLabel, titleKey, type NewsHealth, type NewsRow } from '../src/cloud/news.ts'
import { tabFromUrl } from '../src/cloud/tabs.ts'

const BASE = 'https://abc.supabase.co/functions/v1/api/'
const env = { NEWS_API_URL: BASE, NEWS_API_KEY: 'k3y' }

test('T-400：轉給新聞服務只帶准許的參數（照字母排）；服務金鑰放標頭', async () => {
  assert.equal(
    upstreamUrl(BASE, '/v1/items', 'https://twquant.pages.dev/api/news/items?limit=60&evil=1&kind=intl&until=2026-10-19T00:00:00Z'),
    'https://abc.supabase.co/functions/v1/api/v1/items?kind=intl&limit=60&until=2026-10-19T00%3A00%3A00Z',
  )
  assert.equal(upstreamUrl(BASE, '/v1/health', 'https://x/api/news/health?limit=1'), 'https://abc.supabase.co/functions/v1/api/v1/health')
  assert.deepEqual(ALLOWED['/v1/health'], [])
  let seen: { url: string; key: string | null; region: string | null } | null = null
  const fake = (async (url: string, init?: RequestInit) => {
    seen = { url, key: new Headers(init?.headers).get('x-news-key'), region: new Headers(init?.headers).get('x-region') }
    return new Response('{"items":[]}', { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  const res = await proxy(env, '/v1/items', 'https://x/api/news/items?kind=tw', fake)
  assert.equal(res.status, 200)
  assert.equal(await res.text(), '{"items":[]}')
  // 指定在資料庫那一區（新加坡）執行（T-410 實測：不指定會在東京跑，每個查詢跨區）
  assert.deepEqual(seen, { url: 'https://abc.supabase.co/functions/v1/api/v1/items?kind=tw', key: 'k3y', region: 'ap-southeast-1' })
})

test('T-400：新聞服務沒設定、連不上、壞掉、回的不是 JSON → 503 不外洩上游；參數錯（400）照轉', async () => {
  const json = (status: number, body = '{"detail":"x"}') =>
    (async () => new Response(body, { status, headers: { 'content-type': 'application/json' } })) as unknown as typeof fetch
  const detail = async (r: Response) => ((await r.json()) as { detail: string }).detail
  const r0 = await proxy({}, '/v1/items', 'https://x/', json(200))
  assert.equal(r0.status, 503)
  assert.equal(await detail(r0), '新聞服務還沒設定')
  const r1 = await proxy(env, '/v1/items', 'https://x/', (async () => {
    throw new Error('ECONNRESET abc.supabase.co')
  }) as typeof fetch)
  assert.equal(r1.status, 503)
  assert.ok(!(await r1.text()).includes('supabase'))
  assert.equal((await proxy(env, '/v1/items', 'https://x/', json(500))).status, 503)
  assert.equal((await proxy(env, '/v1/items', 'https://x/', json(403))).status, 503, '金鑰錯也不轉 403（會被網站當成要登入）')
  const html = (async () => new Response('<html>', { status: 200, headers: { 'content-type': 'text/html' } })) as unknown as typeof fetch
  assert.equal((await proxy(env, '/v1/items', 'https://x/', html)).status, 503)
  const bad = await proxy(env, '/v1/items', 'https://x/', json(400, '{"detail":"limit 要是 1～200：999"}'))
  assert.equal(bad.status, 400)
  assert.equal(await detail(bad), 'limit 要是 1～200：999')
})

const row = (id: string, first: string, extra: Partial<NewsRow> = {}): NewsRow => ({
  id,
  source: 'cnbc-top',
  source_name: 'CNBC',
  kind: 'intl',
  lang: 'en',
  title: `T ${id}`,
  summary: null,
  url: 'https://x',
  published_at: null,
  first_seen_at: first,
  versions: 0,
  ...extra,
})

test('T-400：新聞流依抓到那天（台北）分組；今天／昨天／日期；時間顯示來源的發布時間，沒有才用抓到的', () => {
  const rows = [row('a', '2026-10-19T02:00:00Z'), row('b', '2026-10-18T17:30:00Z'), row('c', '2026-10-18T15:00:00Z'), row('d', '2026-10-17T01:00:00Z')]
  assert.deepEqual(
    groupByDay(rows).map((g) => [g.day, g.rows.map((r) => r.id)]),
    [
      ['2026-10-19', ['a', 'b']], // 10/18 17:30 UTC＝台北 10/19 01:30
      ['2026-10-18', ['c']],
      ['2026-10-17', ['d']],
    ],
  )
  assert.deepEqual(['2026-10-19', '2026-10-18', '2026-10-16'].map((d) => dayTitle(d, '2026-10-19')), ['今天', '昨天', '10/16（五）'])
  assert.equal(taipeiHm('2026-10-18T17:30:00Z'), '01:30')
  assert.equal(shownTime(row('x', '2026-10-19T02:00:00Z', { published_at: '2026-10-19T01:00:00Z' })), '2026-10-19T01:00:00Z')
  assert.equal(shownTime(row('x', '2026-10-19T02:00:00Z')), '2026-10-19T02:00:00Z')
  assert.equal(timeLabel(row('x', '2026-10-19T02:00:00Z', { published_at: '2026-10-19T01:00:00Z' }), '2026-10-19'), '09:00')
  assert.equal(timeLabel(row('x', '2026-10-19T02:00:00Z', { published_at: '2026-10-18T12:00:00Z' }), '2026-10-19'), '10/18 20:00', '昨晚發、今天才抓到')
})

test('T-400：最後抓取時間的說法；有來源壞掉、停擺、從沒抓到', () => {
  const now = new Date('2026-10-19T06:00:00Z')
  assert.equal(ageLabel('2026-10-19T05:59:40Z', now), '剛剛')
  assert.equal(ageLabel('2026-10-19T05:48:00Z', now), '12 分鐘前')
  assert.equal(ageLabel('2026-10-19T03:00:00Z', now), '3 小時前')
  assert.equal(ageLabel('2026-10-18T14:05:00Z', now), '15 小時前')
  assert.equal(ageLabel('2026-10-17T14:05:00Z', now), '10/17 22:05')
  const h = (over: Partial<NewsHealth>): NewsHealth => ({ now: now.toISOString(), last_ok_at: '2026-10-19T05:48:00Z', stale: false, sources: [], ...over })
  assert.deepEqual(healthLine(h({}), now), { text: '最後抓取 12 分鐘前', tone: 'ok' })
  assert.deepEqual(healthLine(h({ sources: [{ source: 'ft', name: 'Financial Times', failing: true, last_error: 'HTTP 403' }] }), now), {
    text: '最後抓取 12 分鐘前・Financial Times 暫停',
    tone: 'warn',
  })
  assert.equal(healthLine(h({ stale: true }), now).tone, 'bad')
  // 容量（2026-10-20）：超過七成狀態列變黃、寫用了幾成；沒超過或舊服務沒這欄照常
  assert.deepEqual(healthLine(h({ storage: { used_mb: 360, limit_mb: 500, warn: true } }), now), { text: '最後抓取 12 分鐘前・資料庫已用 72%', tone: 'warn' })
  assert.deepEqual(healthLine(h({ storage: { used_mb: 19, limit_mb: 500, warn: false } }), now), { text: '最後抓取 12 分鐘前', tone: 'ok' })
  assert.equal(healthLine(h({ last_ok_at: null }), now).text, '還沒抓到任何新聞')
  assert.equal(healthLine(null, now).tone, 'bad')
})

test('T-400：「更多」從最舊那則的時間（含）接著抓，重複的只留一次', () => {
  const page1 = [row('a', '2026-10-19T02:00:00Z'), row('b', '2026-10-19T01:00:00Z')]
  assert.equal(nextUntil(page1), '2026-10-19T01:00:00Z')
  assert.equal(nextUntil([]), null)
  const merged = appendPage(page1, [row('b', '2026-10-19T01:00:00Z'), row('c', '2026-10-19T01:00:00Z')])
  assert.deepEqual(merged.map((r) => r.id), ['a', 'b', 'c'])
})

test('T-400：網址 ?tab=news 打開新聞分頁', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=news'), 'news')
})

test('T-401：有過檢查的譯文才顯示譯文（標「譯」、附原文）；沒過→原文標「未譯」；還沒翻→「翻譯中」；中文新聞不標', () => {
  const en = row('e', '2026-10-19T02:00:00Z', { title: 'Nvidia jumps 4%', summary: 'Shares rose.' })
  assert.deepEqual(shown({ ...en, translation: 'ok', title_zh: '輝達大漲4%', summary_zh: '股價上漲。' }), {
    title: '輝達大漲4%',
    summary: '股價上漲。',
    lang: 'zh',
    tag: '譯',
    original: { title: 'Nvidia jumps 4%', summary: 'Shares rose.' },
  })
  assert.deepEqual([shown({ ...en, translation: 'failed' }).tag, shown({ ...en, translation: 'failed' }).title], ['未譯', 'Nvidia jumps 4%'])
  assert.equal(shown({ ...en, translation: 'pending' }).tag, '翻譯中')
  assert.equal(shown({ ...en, translation: 'ok', title_zh: null }).title, 'Nvidia jumps 4%', '狀態 ok 但沒有譯文字串：照原文顯示')
  assert.equal(shown(row('z', '2026-10-19T02:00:00Z', { kind: 'tw', lang: 'zh', translation: null })).tag, null)
  assert.equal(shown(en).tag, null, '舊服務沒有 translation 欄')
})

test('T-404：關係圖只轉 id（其餘參數丟掉）；沒有這則（404）照轉，不當成服務壞掉', async () => {
  assert.equal(
    upstreamUrl(BASE, '/v1/graph', 'https://x/api/news/graph?id=cnbc-top-0&limit=9&kind=tw'),
    'https://abc.supabase.co/functions/v1/api/v1/graph?id=cnbc-top-0',
  )
  assert.deepEqual(ALLOWED['/v1/graph'], ['id'])
  let seen = ''
  const notFound = (async (url: string) => {
    seen = url
    return new Response('{"detail":"沒有這則新聞"}', { status: 404, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  const res = await proxy(env, '/v1/graph', 'https://x/api/news/graph?id=a%2Fb', notFound)
  assert.equal(seen, 'https://abc.supabase.co/functions/v1/api/v1/graph?id=a%2Fb')
  assert.equal(res.status, 404)
  assert.equal(((await res.json()) as { detail: string }).detail, '沒有這則新聞')
})

test('T-402：列上的實體小標最多 3 個，其餘收成「+N」；沒有 mentions（舊服務）或空的 → 沒有小標', () => {
  const m = (id: string) => ({ id, type: 'tw' as const, label: id })
  const r = row('a', '2026-10-19T00:00:00Z', { mentions: ['tw:1', 'tw:2', 'tw:3', 'tw:4', 'tw:5'].map(m) })
  assert.deepEqual(mentionChips(r).chips.map((x) => x.id), ['tw:1', 'tw:2', 'tw:3'])
  assert.equal(mentionChips(r).more, 2)
  assert.deepEqual(mentionChips({ ...r, mentions: [m('tw:1')] }), { chips: [m('tw:1')], more: 0 })
  assert.deepEqual(mentionChips(row('b', '2026-10-19T00:00:00Z')), { chips: [], more: 0 })
  assert.deepEqual(mentionChips({ ...r, mentions: [] }), { chips: [], more: 0 })
})

test('反向看圖（2026-10-20）：proxy 只轉 id／ids；間接那則的路徑說明；個股頁標題右邊的計數', async () => {
  assert.deepEqual(ALLOWED['/v1/stock-news'], ['id'])
  assert.deepEqual(ALLOWED['/v1/stock-news-counts'], ['ids'])
  assert.equal(
    upstreamUrl(BASE, '/v1/stock-news-counts', 'https://x/api/news/stock-counts?ids=tw%3A2330%2Ctw%3A2382&days=99'),
    "https://abc.supabase.co/functions/v1/api/v1/stock-news-counts?ids=tw%3A2330%2Ctw%3A2382",
  )
  const { pathText, stockNewsSummary } = await import('../src/cloud/news.ts')
  const step = (label: string, via: string, via_label?: string) => ({ id: label, label, via, ...(via_label ? { via_label } : {}) })
  assert.equal(pathText({ path: [step('輝達', 'mention'), step('廣達', 'supplier')], draft: true }), '輝達 › 供應商・草擬')
  assert.equal(pathText({ path: [step('緯創', 'mention'), step('廣達', 'peer', '伺服器')], draft: false }), '緯創 › 同族群・伺服器')
  assert.equal(pathText({ path: [step('廣達', 'mention')], draft: false }), '', '直接提到不用說明')
  const item = (degree: number) => ({ id: String(degree), source_name: 'CNBC', url: 'https://x', first_seen_at: '2026-10-20T01:00:00Z', title: 't', title_zh: null, degree, score: 1, draft: false, path: [] })
  assert.equal(stockNewsSummary({ id: 'tw:2382', days: 7, items: [item(1), item(2), item(2)] }), '近 7 天・直接 1・間接 2')
})

test('T-410 相似新聞與語意搜尋：proxy 只轉 id／q；說明句；短時間', async () => {
  assert.deepEqual(ALLOWED['/v1/similar'], ['id'])
  assert.deepEqual(ALLOWED['/v1/search'], ['q'])
  assert.equal(
    upstreamUrl(BASE, '/v1/search', 'https://x/api/news/search?q=%E8%BC%9D%E9%81%94&limit=99'),
    'https://abc.supabase.co/functions/v1/api/v1/search?q=%E8%BC%9D%E9%81%94',
  )
  const { searchLine, shortWhen, similarNote } = await import('../src/cloud/news.ts')
  const it = { id: 'a', source_name: 'CNBC', kind: 'intl' as const, url: 'https://x', first_seen_at: '2026-10-20T02:00:00Z', title: 't', title_zh: null, score: 0.9, same_event: true }
  assert.equal(similarNote({ id: 'x', ready: false, items: [] }), '這則還在算（英文新聞翻譯完才算，通常 30 分鐘內）')
  assert.equal(similarNote({ id: 'x', ready: true, items: [] }), '沒有內容相近的新聞')
  assert.equal(similarNote({ id: 'x', ready: false, expired: true, retention_days: 45, items: [] }), '這則比較舊，相似新聞只找近 45 天的')
  assert.equal(similarNote({ id: 'x', ready: true, items: [it] }), null)
  assert.equal(searchLine({ q: '輝達', budget_stop: false, items: [it, it] }), '跟「輝達」相關的新聞 2 則，越相關越前面')
  assert.equal(searchLine({ q: '輝達', budget_stop: false, items: [] }), '找不到跟「輝達」相關的新聞')
  assert.equal(searchLine({ q: '輝達', budget_stop: true, items: [] }), '今天新聞的 AI 額度用完了，明天早上 8 點恢復')
  assert.equal(shortWhen('2026-10-20T02:00:00Z', '2026-10-20'), '10:00')
  assert.equal(shortWhen('2026-10-18T02:00:00Z', '2026-10-20'), '10/18')
})

test('T-403 核可頁：proxy 送 POST 內容與登入者；網站端擋別站送來的、沒登入的；送出後拿掉處理過的線', async () => {
  assert.deepEqual(ALLOWED['/v1/review'], [])
  let seen: { method: string; user: string | null; body: string | null; ctype: string | null } | null = null
  const fake = (async (_url: string, init?: RequestInit) => {
    const h = new Headers(init?.headers)
    seen = { method: init?.method ?? 'GET', user: h.get('x-news-user'), body: (init?.body as string) ?? null, ctype: h.get('content-type') }
    return new Response('{"approved":1,"rejected":0,"skipped":[]}', { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  const body = '{"decisions":[{"src":"us:NVDA","dst":"tw:2330","type":"supplier","decision":"approved"}]}'
  const res = await proxy(env, '/v1/review', 'https://x/api/news/review', fake, { method: 'POST', body, user: 'owner@example.com' })
  assert.equal(res.status, 200)
  assert.deepEqual(seen, { method: 'POST', user: 'owner@example.com', body, ctype: 'application/json' })
  await proxy(env, '/v1/review', 'https://x/api/news/review', fake, { user: null })
  assert.deepEqual(seen, { method: 'GET', user: null, body: null, ctype: null }, '沒登入身分就不帶 x-news-user（服務那邊回 can_review false）')

  const { reviewPost, REVIEW_BODY_MAX } = await import('../functions/_lib/news-proxy.ts')
  const req = (headers: Record<string, string>, text = body) => new Request('https://twquant.pages.dev/api/news/review', { method: 'POST', headers, body: text })
  const who = (e: string | null) => async () => e
  assert.equal((await reviewPost(req({ origin: 'https://evil.example' }), env, who('owner@example.com'), fake)).status, 403, '別站送來的不收')
  assert.equal((await reviewPost(req({ origin: 'https://twquant.pages.dev' }), env, who(null), fake)).status, 401, '沒登入不收')
  assert.equal((await reviewPost(req({}, 'x'.repeat(REVIEW_BODY_MAX + 1)), env, who('owner@example.com'), fake)).status, 413)
  const okRes = await reviewPost(req({ origin: 'https://twquant.pages.dev' }), env, who('owner@example.com'), fake)
  assert.equal(okRes.status, 200)
  assert.equal(seen!.user, 'owner@example.com')
  assert.equal(seen!.method, 'POST')

  const { dropDecided, groupDecisions, reviewLeft } = await import('../src/cloud/news.ts')
  const item = (dst: string) => ({ dst, dst_label: dst, dst_type: 'tw' as const, dst_sub: dst, label: null })
  const groups = [
    { src: 'us:NVDA', src_label: '輝達', src_type: 'us' as const, type: 'supplier' as const, items: [item('tw:2330'), item('tw:2382')] },
    { src: 'th:ai', src_label: 'AI', src_type: 'theme' as const, type: 'theme' as const, items: [item('tw:2330')] },
  ]
  assert.equal(reviewLeft(groups), 3)
  assert.deepEqual(groupDecisions(groups[0], 'approved'), [
    { src: 'us:NVDA', dst: 'tw:2330', type: 'supplier', decision: 'approved' },
    { src: 'us:NVDA', dst: 'tw:2382', type: 'supplier', decision: 'approved' },
  ])
  const after = dropDecided(groups, [{ src: 'th:ai', dst: 'tw:2330', type: 'theme' }, { src: 'us:NVDA', dst: 'tw:2330', type: 'supplier' }])
  assert.deepEqual(after.map((g) => [g.src, g.items.map((i) => i.dst)]), [['us:NVDA', ['tw:2382']]], '同一檔在別組的不受影響；空掉的組拿掉')
})

test('T-417 新聞三張卡：proxy 不帶參數；主題增減、到處都提的註明、股票熱度的小字與倍數', async () => {
  assert.deepEqual(ALLOWED['/v1/digest'], [])
  const { everywhereNote, hotBadge, hotLine, topicDelta } = await import('../src/cloud/news.ts')
  const t = (n: number, prev: number) => ({ id: 'th:x', label: 'X', type: 'theme', n, prev, stocks: [] })
  assert.equal(topicDelta(t(20, 8), 1), '+12')
  assert.equal(topicDelta(t(3, 8), 1), '−5')
  assert.equal(topicDelta(t(8, 8), 2), '持平')
  assert.equal(topicDelta(t(20, 0), 0), null, '還沒有前一天的資料不比')
  assert.equal(everywhereNote([]), null)
  assert.equal(everywhereNote([{ label: 'AI', share: 0.45 }]), '「AI」幾乎每則都提（45%），不列')
  const h = (n: number, base: number | null, ratio: number | null) => ({ id: 'tw:2330', label: '台積電', sub: '2330', n, base, ratio })
  assert.equal(hotLine(h(12, 2, 6)), '近 24 小時 12 則・平常 2／天')
  assert.equal(hotLine(h(12, null, null)), '近 24 小時 12 則')
  assert.equal(hotBadge(h(12, 2, 6)), '×6')
  assert.equal(hotBadge(h(3, 2, 1.5)), null, '不到 2 倍不標')
  assert.equal(hotBadge(h(12, null, null)), null)
})

test('T-419 新聞時間軸與久違的消息：K 線標記只標範圍內的日子、久違那句、個股頁摘要句、proxy 只轉 id', async () => {
  assert.deepEqual(ALLOWED['/v1/stock-news-days'], ['id'])
  const { newsMarkers, quietNote, stockNewsSummary } = await import('../src/cloud/news.ts')
  const day = (d: string, n: number) => ({ day: d, n, outlets: ['鉅亨網'], items: [] })
  assert.deepEqual(newsMarkers([day('2026-10-20', 3), day('2026-10-18', 1), day('2026-09-01', 2)], ['2026-10-17', '2026-10-18', '2026-10-20']), [
    { time: '2026-10-18', n: 1 },
    { time: '2026-10-20', n: 3 },
  ])
  assert.equal(quietNote({ days: 30, at_least: false }), '30 天沒消息後第一次')
  assert.equal(quietNote({ days: 120, at_least: true }), '至少 120 天沒消息後第一次')
  assert.equal(quietNote(null), null)
  const sn = { id: 'tw:2330', days: 7, items: [] as never[] }
  assert.equal(stockNewsSummary(sn), '近 7 天・直接 0・間接 0')
  assert.equal(stockNewsSummary({ ...sn, quiet: { days: 21, at_least: false } }), '近 7 天・直接 0・間接 0・21 天沒消息後第一次')
})

test('T-420 事件追蹤：proxy 只轉 id；至少兩步才顯示；每步的日期', async () => {
  assert.deepEqual(ALLOWED['/v1/thread'], ['id'])
  const { stepDay, threadShown } = await import('../src/cloud/news.ts')
  const step = (c: string, t: string, current = false) => ({ cluster_id: c, current, n: 3, first_seen_at: t, last_seen_at: t, item_id: 'x', title: 'T', url: 'u', outlets: ['鉅亨網'] })
  assert.equal(threadShown(null), false)
  assert.equal(threadShown({ id: 'x', cluster_id: 'a', steps: [step('a', '2026-10-18T01:00:00Z', true)] }), false, '只有自己那一群不畫')
  assert.equal(threadShown({ id: 'x', cluster_id: 'b', steps: [step('a', '2026-10-18T01:00:00Z'), step('b', '2026-10-20T01:00:00Z', true)] }), true)
  assert.equal(stepDay(step('a', '2026-10-18T17:00:00Z')), '10/19', '台北日期')
})

test('2026-10-21 閱讀卡：同一事件與相似新聞分開、同標題只留一則、不重列這則', () => {
  const it = (id: string, title: string, src: string, same: boolean, score: number) => ({
    id, title, title_zh: null, url: `u/${id}`, source_name: src, kind: 'tw' as const, first_seen_at: '2026-10-21T08:00:00Z', score, same_event: same,
  })
  const r = splitSimilar('加高績攻AI高速光通訊 明年展望謹慎樂觀', [
    it('a', '加高績攻 AI 高速光通訊，明年展望謹慎樂觀', '經濟日報', true, 0.95),
    it('b', '光通訊等 AI 貢獻增加 法人調高台表科獲利預估值及目標價', '經濟日報', false, 0.83),
    it('c', '光通訊等 AI 貢獻增加 法人調高台表科獲利預估值及目標價', '經濟日報', false, 0.82),
    it('d', '雲端AI將推動2027年晶片成長', 'DigiTimes Asia', false, 0.8),
    it('e', '加高績攻AI高速光通訊 明年展望謹慎樂觀', '中央社', false, 0.79),
  ])
  assert.deepEqual(r.sameEvent.map((x) => [x.id, x.sameTitle]), [['a', true]])
  assert.deepEqual(r.related.map((x) => x.id), ['b', 'd'], '同標題的 c 合併、跟這則同標題的 e 不列')
  assert.equal(titleKey('A, b！ c'), 'abc')
  const ex = splitSimilar('x', [it('d', '雲端AI將推動2027年晶片成長', 'DigiTimes Asia', false, 0.8)], ['雲端 AI 將推動 2027 年晶片成長'])
  assert.deepEqual(ex.related, [], '來龍去脈已列的不重列')
})

test('T-429 AC6：點開才翻摘要——proxy 只轉 id、逾時給長一點；英文、標題翻好、有摘要、還沒摘要譯文才另外翻', async () => {
  assert.deepEqual(ALLOWED['/v1/summary'], ['id'])
  assert.deepEqual(ALLOWED['/v1/evidence'], ['ids', 'entities'], 'T-437 證據句')
  assert.equal(upstreamUrl(BASE, '/v1/evidence', 'https://x/api/news/evidence?ids=a%2Cb&entities=tw%3A2330&x=1'), 'https://abc.supabase.co/functions/v1/api/v1/evidence?entities=tw%3A2330&ids=a%2Cb')
  assert.equal(upstreamUrl(BASE, '/v1/summary', 'https://x/api/news/summary?id=cnbc-top-0&limit=9'), 'https://abc.supabase.co/functions/v1/api/v1/summary?id=cnbc-top-0')
  assert.ok(SUMMARY_TIMEOUT_MS > UPSTREAM_TIMEOUT_MS, '要等模型翻譯')
  const en = row('e', '2026-10-22T02:00:00Z', { title: 'Nvidia jumps 4%', summary: 'Shares rose 4%.', translation: 'ok', title_zh: '輝達大漲4%', summary_zh: null })
  assert.equal(needsSummary(en), true)
  assert.equal(needsSummary({ ...en, summary_zh: '股價上漲4%。' }), false, '舊制已翻好')
  assert.equal(needsSummary({ ...en, summary: null }), false, '沒有摘要')
  assert.equal(needsSummary({ ...en, translation: 'pending', title_zh: null }), false, '標題還沒翻好')
  assert.equal(needsSummary(row('z', '2026-10-22T02:00:00Z', { kind: 'tw', lang: 'zh', translation: null, summary: '中文摘要' })), false)

  assert.deepEqual(readerSummary(en, 'loading'), { text: null, lang: 'zh', note: '摘要翻譯中…' })
  assert.deepEqual(readerSummary(en, { id: 'e', status: 'ok', summary_zh: '股價上漲4%。' }), { text: '股價上漲4%。', lang: 'zh', note: null })
  assert.deepEqual(readerSummary(en, { id: 'e', status: 'budget', summary_zh: null }), { text: 'Shares rose 4%.', lang: 'en', note: '今天的翻譯額度用完了，先顯示英文摘要' })
  assert.equal(readerSummary(en, { id: 'e', status: 'failed', summary_zh: null }).note, '摘要翻譯沒過檢查（數字或公司名對不上），先顯示英文摘要')
  assert.deepEqual(readerSummary(en, 'error'), { text: 'Shares rose 4%.', lang: 'en', note: '摘要暫時翻不了，先顯示英文摘要' })
  assert.deepEqual(readerSummary({ ...en, summary_zh: '股價上漲4%。' }, null), { text: '股價上漲4%。', lang: 'zh', note: null }, '不用另外翻的照 shown()')
})

test('D-205 A：情緒小標好＝紅、壞＝綠、中性與還沒判斷不標；個股風向只數好壞', () => {
  assert.equal(sentimentMark(1)?.cls, 'good')
  assert.equal(sentimentMark(-1)?.text, '壞')
  assert.equal(sentimentMark(0), null)
  assert.equal(sentimentMark(null), null)
  assert.equal(sentimentMark(undefined), null)
  assert.deepEqual(stockMood([{ sentiment: 1 }, { sentiment: -1 }, { sentiment: 1 }, { sentiment: 0 }, { sentiment: null }, {}]), { good: 2, bad: 1 })
})

