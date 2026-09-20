const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const html = fs.readFileSync(path.join(__dirname, '../dist/index.html'), 'utf8');
    const errors = [];
    const modelId = 'model_0123456789abcdef0123';
    const backtestId = 'backtest_abcdef0123456789abcd';
    const models = {
      source: 'artifact_metadata', issues: [], items: [{
        artifact_id: modelId, artifact_state: 'active', model_id: 'lgbm_rank_v2', train_end: '2026-08-22',
        features: ['momentum_20d', 'revenue_yoy'], horizon_days: 20, label: 'forward_return_20d',
        artifact_fingerprint: 'a1b2c3d4', config_hash: 'cfg-123', data_hash: 'data-123', code_hash: 'code-123',
        oos_rank_ic: 0.0452, window_ics: [0.04, 0.05], started_at: null, ended_at: null,
        execution_status: 'unavailable', qualification: 'unreviewed',
      }],
    };
    const backtests = {
      source: 'result_bundle', issues: [], items: [{
        artifact_id: backtestId, artifact_state: 'active', source_name: 'ladder', strategy_name: 'monthly_top20',
        start: '2022-01-03', end: '2026-08-22', source: 'pitview', data_fingerprint: 'data-123',
        initial_capital: 1000000, max_participation: 0.1,
        metrics: { cagr: 0.153, sharpe: 1.32, mdd: -0.19, n_days: 1140 },
      }],
    };
    const backtestDetail = { item: { ...backtests.items[0], equity: [
      { date: '2026-08-20', equity: 1010000, cash: 120000, positions_value: 890000 },
      { date: '2026-08-21', equity: 1025000, cash: 130000, positions_value: 895000 },
    ], final_holdings: [{ stock_id: '0050', shares: 1000, value: 225000, weight: 0.22 }] }, issues: [] };
    const runBase = {
      // Older than the offline snapshot's market date, so the stale state is exercised.
      run_id: 'run_a', model_id: 'lgbm_rank_v2', as_of: '2026-06-20T20:00:00+08:00',
      generated_at: '2026-06-20T20:05:00+08:00', published_at: null, mode: 'research', artifact_fingerprint: 'fp-1',
    };
    const allCandidates = [
      { stock_id: '0050', rank: 1, score: 0.812, bucket: 'ml_top20', reason_ref: null, insight_run_id: null, data_status: 'pending',
        insight: { insight_run_id: 'ins_a', stance: 'conflict', recommended_bucket: 'watch', absent_reason: null, support_n: 2, contradict_n: 1, gap_n: 1 } },
      { stock_id: '2330', rank: 2, score: 0.744, bucket: 'ml_watch', reason_ref: null, insight_run_id: null, data_status: 'pending',
        insight: { insight_run_id: 'ins_b', stance: null, recommended_bucket: null, absent_reason: 'LLM 離線，未產生分析', support_n: 0, contradict_n: 0, gap_n: 3 } },
      { stock_id: '0056', rank: 3, score: 0.701, bucket: 'ml_top20', reason_ref: null, insight_run_id: null, data_status: 'pending', insight: null },
    ];
    // Mirrors the server contract: filters apply to the whole run before any page slice.
    const recommendations = url => {
      const [date, bucket, q] = ['date', 'bucket', 'q'].map(key => url.searchParams.get(key));
      const query = { date, model: url.searchParams.get('model'), run: url.searchParams.get('run'), bucket, q, page: 1, page_size: 200 };
      if (date === '2026-09-03') return { runs: [], issues: [], query };
      if (date === '2026-09-04') return { runs: [{ ...runBase, run_id: 'run_zero', as_of: '2026-09-04T20:00:00+08:00', candidates: [], candidate_total: 0, candidate_total_all: 0, bucket_counts: {} }], issues: [], query };
      // A pre-D-111 snapshot: it has candidates, but none carries a promotion bucket.
      if (date === '2026-09-05') return { runs: [{ ...runBase, run_id: 'run_legacy', as_of: '2026-09-05T20:00:00+08:00', candidates: [], candidate_total: 0, candidate_total_all: 1949, bucket_counts: { ml_candidate: 1949 } }], issues: [], query };
      const kept = allCandidates.filter(c => (!bucket || c.bucket === bucket) && (!q || c.stock_id.includes(q)));
      const bucketCounts = {};
      for (const c of allCandidates) bucketCounts[c.bucket] = (bucketCounts[c.bucket] ?? 0) + 1;
      return { runs: [{ ...runBase, candidates: kept, candidate_total: kept.length, candidate_total_all: allCandidates.length, bucket_counts: bucketCounts }], issues: [], query };
    };
    // A connected-mode fixture: /api/health succeeds so the stock page runs in live mode.
    // Deliberately different from the embedded snapshot's 2026-06-30 so the two are distinguishable.
    const marketAsOf = '2026-07-02';
    // Weekends are absent so a non-trading publication day has to map forward to a real session.
    const closedDays = new Set(['2026-06-06', '2026-06-07', '2026-06-13', '2026-06-14', '2026-06-20', '2026-06-21', '2026-06-27', '2026-06-28']);
    const candles = sid => Array.from({ length: 30 }, (_, i) => {
      const day = `2026-06-${String(i + 1).padStart(2, '0')}`;
      const base = sid === '0050' ? 200 : 900;
      return { date: day, open: base + i, high: base + i + 3, low: base + i - 3, close: base + i + 1, volume: 1000 * (i + 1) };
    }).filter(row => !closedDays.has(row.date));
    const stockEvents = {
      '0050': [
        { run_id: 'run_a', model_id: 'lgbm_rank_v2', stock_id: '0050', as_of: '2026-06-20T20:00:00+08:00', published_at: null, mode: 'research', rank: 1, score: 0.812, bucket: 'top_decile', reason_ref: null, insight_run_id: null, data_status: 'pending' },
        { run_id: 'run_b', model_id: 'lgbm_rank_v2', stock_id: '0050', as_of: '2026-06-25T20:00:00+08:00', published_at: null, mode: 'research', rank: 4, score: 0.655, bucket: 'watch', reason_ref: null, insight_run_id: null, data_status: 'pending' },
        // Same publication day as run_b, so the group must expand rather than draw a second arrow.
        { run_id: 'run_c', model_id: 'lgbm_rank_v2', stock_id: '0050', as_of: '2026-06-25T20:10:00+08:00', published_at: null, mode: 'research', rank: 7, score: 0.601, bucket: 'top_decile', reason_ref: null, insight_run_id: 'ins_1', data_status: 'ready' },
        // Published after the last available bar: listed as pending, never plotted.
        { run_id: 'run_d', model_id: 'lgbm_rank_v2', stock_id: '0050', as_of: '2026-07-10T20:00:00+08:00', published_at: null, mode: 'research', rank: 2, score: 0.702, bucket: 'top_decile', reason_ref: null, insight_run_id: null, data_status: 'pending' },
      ],
      '2330': [],
    };
    const stockMeta = {
      '0050': { name: '元大台灣50', industry: 'ETF', market: 'TWSE' },
      '2330': { name: '台積電', industry: '半導體', market: 'TWSE' },
      // 航運 appears in the flow rows but not here, so its coverage gap is exercised.
      '2454': { name: '聯發科', industry: '半導體', market: 'TWSE' },
    };
    const sectorFlows = window => ({
      as_of: marketAsOf, window, dates: [], daily: {},
      sectors: window === 60 ? [] : [
        { industry: '半導體', n: 2, net_yi: 12.5, frgn_yi: 9.1, trust_yi: 3.4, amt_yi: 880.2, ret_pct: 2.31 },
        { industry: 'ETF', n: 1, net_yi: -3.2, frgn_yi: -2.0, trust_yi: -1.2, amt_yi: 120.0, ret_pct: -0.44 },
        { industry: '航運', n: 4, net_yi: 1.1, frgn_yi: 0.5, trust_yi: 0.6, amt_yi: 55.5, ret_pct: 0.12 },
      ],
    });
    const evidence = (id, direction, title, available) => ({
      evidence_id: id, title, url: `https://source.example/${id}`,
      published_at: '2026-06-24T08:00:00+08:00', available_at: available ?? '2026-06-24T09:00:00+08:00',
      captured_at: '2026-06-24T10:00:00+08:00', stock_id: '0050', industry: null, direction, reason_ref: null,
    });
    const insightPacks = {
      packs: [
        {
          insight_run_id: 'ins_1', as_of: '2026-06-25T20:00:00+08:00', stock_id: '0050', industry: null,
          evidence: [{ ...evidence('ev_1', 'support', '月營收年增 18%'), availability: 'estimated', direction: 'neutral' }, evidence('ev_2', 'contradict', '投信連三日賣超'), evidence('ev_3', 'gap', '尚無法人持股明細')],
          analysis: {
            prompt_id: 'insight_v1', prompt_hash: 'abc123', analysis_schema_version: 1,
            model_version: 'claude-x-2026', as_of: '2026-06-25T20:00:00+08:00',
            summary: '營收成長支持基本面，但籌碼面出現反向訊號。', cited_evidence_ids: ['ev_1', 'ev_2'],
          },
          analysis_absent_reason: null,
        },
        {
          // The LLM was unavailable, so the evidence stands alone with a stated reason.
          insight_run_id: 'ins_2', as_of: '2026-06-24T20:00:00+08:00', stock_id: '0050', industry: null,
          evidence: [evidence('ev_9', 'support', '法說會釋出擴產計畫')],
          analysis: null, analysis_absent_reason: 'LLM 離線，未產生分析',
        },
      ],
      issues: [],
    };
    page.on('pageerror', error => errors.push(error.message));
    // All HTTP is fulfilled locally; the test never reaches data services.
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/sector-flows') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ flows: sectorFlows(Number(url.searchParams.get('window')) || 20) }) });
      if (url.pathname === '/api/platform/strategy-targets') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ snapshots: [{
        schema_version: 1, run_id: 'stgt_20260620_0123456789abcdef', as_of: '2026-06-20T20:00:00+08:00', generated_at: '2026-06-20T20:30:00+08:00', mode: 'research',
        strategies: [
          { code: 'LW-B2', config: 'lw_b2_broad_capw', label: '全市場多因子（市值加權）', note: '', status: 'ok', detail: null, exposure: 0.3, targets: [{ stock_id: '0050', target_weight: 0.25, rank: 1 }, { stock_id: '2330', target_weight: 0.05, rank: 2 }], vault: { total_return: 0.2578, max_drawdown: -0.1541, benchmark_total_return: 0.3223, benchmark_max_drawdown: -0.1588 } },
          { code: 'LW-M1', config: 'lw_m1_ml_c1', label: 'ML 訊號進載具', note: '', status: 'failed', detail: 'ConfigError: 串接閘門的因子在該日全為 null', exposure: 0, targets: [], vault: { total_return: 0.2268, max_drawdown: -0.3512, benchmark_total_return: 0.3223, benchmark_max_drawdown: -0.1588 } },
        ] }], issues: [], query: { date: null, limit: 1 } }) });
      if (url.pathname === '/api/platform/insights') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(url.searchParams.get('stock') === '2330' ? { packs: [], issues: [] } : insightPacks) });
      if (url.pathname === '/api/ingest-matrix') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        datasets: [{ key: 'prices', table: 'prices_daily', source: 'twse', label: '日行情', span: '2022-01-03~2026-06-30', days: 1100, window_missing: 0 }],
        dates: ['2026-06-29', '2026-06-30'],
        cells: { prices: { '2026-06-29': 'present', '2026-06-30': 'present' } },
        last_run: '2026-06-30T19:05:00', latest: marketAsOf,
      }) });
      if (url.pathname === '/api/ingest-gaps') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        runs: 1, total_missing: 2,
        gaps: [{ label: '日行情', table: 'prices_daily', source: 'twse', kind: 'missing', from: '2026-06-10', to: '2026-06-11', days: 2, command: 'twquant ingest --date 2026-06-10', warn_ban: false, detail: '' }],
      }) });
      if (url.pathname === '/api/heartbeat') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        heartbeat: { day: marketAsOf, git_hash: 'abc1234', ts: '2026-06-30T19:05:00' },
        latest: marketAsOf, watchdog_ok: true, watchdog_reason: '',
      }) });
      if (url.pathname === '/api/ingest-jobs') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ busy: false, jobs: [] }) });
      if (url.pathname === '/api/ingest-tracks') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ tracks: [] }) });
      if (url.pathname === '/api/holiday-calendar') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ available: true, years: [2026], sessions: 240, first: '2026-01-02', last: '2026-12-31', closed: [] }) });
      if (url.pathname === '/api/health') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'ok', as_of: marketAsOf }) });
      // /api/meta scans the data lake for minutes in production; the delay reproduces that gap.
      if (url.pathname === '/api/meta') return new Promise(resolve => setTimeout(resolve, 1500)).then(() => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ as_of: marketAsOf, window: '2022-01-01~2026-07-02', rev_month: null, top_stocks: ['0050', '2330'], coverage: [] }) }));
      if (url.pathname === '/api/stock-meta') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ meta: stockMeta }) });
      if (url.pathname === '/api/stocks') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ stocks: ['0050', '2330'] }) });
      {
        const ohlc = url.pathname.match(/^\/api\/stocks\/([^/]+)\/ohlc$/);
        if (ohlc) return decodeURIComponent(ohlc[1]) === 'FAILS'
          ? route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Fixture: quote source unreadable"}' })
          : route.fulfill({ contentType: 'application/json', body: JSON.stringify({ sid: decodeURIComponent(ohlc[1]), as_of: marketAsOf, source: 'pg', rows: candles(decodeURIComponent(ohlc[1])) }) });
        const events = url.pathname.match(/^\/api\/platform\/stocks\/([^/]+)\/recommendations$/);
        if (events) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ sid: decodeURIComponent(events[1]), events: stockEvents[decodeURIComponent(events[1])] ?? [] }) });
      }
      if (url.pathname === '/api/platform/recommendations') return url.searchParams.get('date') === '2026-09-02'
        ? route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Fixture: snapshot store unreadable"}' })
        : route.fulfill({ contentType: 'application/json', body: JSON.stringify(recommendations(url)) });
      if (url.pathname === '/api/platform/models') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(models) });
      if (url.pathname === `/api/platform/models/${modelId}`) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ item: models.items[0], issues: [] }) });
      if (url.pathname === '/api/platform/backtests') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(backtests) });
      if (url.pathname === `/api/platform/backtests/${backtestId}`) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(backtestDetail) });
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Fixture: offline"}' });
      return route.fulfill({ contentType: 'text/html', body: html });
    });
    await page.goto(`http://twquant.test/#/models/${modelId}?date=2026-08-24&run=run-a`);
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    // The data date comes from /api/health the moment it answers; the snapshot's date is never
    // shown as a connected data date while the slow /api/meta is still pending.
    await page.locator('.platform-connection').getByText('資料至 2026-07-02', { exact: true }).waitFor();
    assert.match(await page.locator('.platform-connection').innerText(), /API 已連線/);
    assert.doesNotMatch(await page.locator('.platform-connection').innerText(), /2026-06-30/);
    await page.getByRole('link', { name: 'lgbm_rank_v2', exact: true }).waitFor();
    await page.getByText('詳細執行過程未記錄', { exact: true }).waitFor();
    assert.match(await page.locator('.platform-context').innerText(), /2026-08-24/);
    await page.getByRole('link', { name: '回測中心', exact: true }).click();
    await page.getByRole('heading', { name: '回測中心', exact: true }).waitFor();
    assert.equal(new URL(page.url()).hash, '#/backtests');
    await page.getByRole('link', { name: 'monthly_top20', exact: true }).click();
    await page.getByText('權益曲線', { exact: true }).waitFor();
    await page.locator('.platform-equity-chart canvas').first().waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /15\.3%/);
    await page.goBack();
    await page.getByRole('heading', { name: '回測中心', exact: true }).waitFor();
    await page.goBack();
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    assert.match(page.url(), /date=2026-08-24&run=run-a/);
    await page.reload();
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    await page.goForward();
    await page.getByRole('heading', { name: '回測中心', exact: true }).waitFor();
    await page.goto('http://twquant.test/#/stocks/0050');
    await page.getByRole('heading', { name: '個股研究', exact: true }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /0050/);
    assert.equal(new URL(page.url()).hash, '#/stocks/0050');
    // AC-207-1: the K-line and the saved recommendation events are the first screen.
    await page.getByText('元大台灣50 · K 線', { exact: false }).waitFor();
    await page.getByRole('heading', { name: '推薦事件', exact: true }).waitFor();
    await page.getByText('2026-06-25', { exact: false }).first().waitFor();
    for (const [tab, marker] of [['基本面', '月營收'], ['因子', '因子體檢'], ['條件選股', '條件選股']]) {
      await page.getByRole('tab', { name: tab, exact: true }).click();
      await page.waitForFunction(() => location.hash.includes('tab='));
      await page.getByText(marker, { exact: false }).first().waitFor();
      await page.getByText('元大台灣50 · K 線', { exact: false }).waitFor(); // K-line never hides.
    }
    assert.match(new URL(page.url()).hash, /tab=screener/);
    await page.reload(); // AC-207-1: the selected tab survives a reload from the URL alone.
    assert.equal(await page.getByRole('tab', { name: '條件選股', exact: true }).getAttribute('aria-selected'), 'true');
    // AC-207-2: switching stocks leaves no residue from the previous quote.
    await page.goto('http://twquant.test/#/stocks/2330?tab=chips');
    await page.getByText('台積電 · K 線', { exact: false }).waitFor();
    await page.getByText('沒有任何保存的推薦事件', { exact: false }).waitFor();
    // The picker still lists other stocks by design; the quote panels must not show the old one.
    assert.equal(await page.getByText('元大台灣50 · K 線', { exact: false }).count(), 0);
    assert.doesNotMatch(await page.locator('#stock-tabpanel').innerText(), /元大台灣50/);
    // AC-207-2: a failed quote request is explicit and never falls back to offline rows.
    await page.goto('http://twquant.test/#/stocks/FAILS');
    await page.getByText('讀取失敗，未以離線快照替代', { exact: false }).waitFor();
    await page.goto('http://twquant.test/#/not-a-page');
    await page.getByRole('heading', { name: '找不到這個頁面' }).waitFor();
    await page.getByRole('link', { name: '回今日總覽 →' }).click();
    // AC-206-1/2: one saved run drives both the home page and the daily list.
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto('http://twquant.test/#/overview');
    await page.getByRole('heading', { name: 'lgbm_rank_v2', exact: true }).waitFor();
    await page.getByText('快照過期', { exact: false }).waitFor();
    await page.getByRole('link', { name: '展開完整清單 →' }).click();
    await page.getByRole('heading', { name: 'ML 選股', exact: true }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /0\.8120/);
    // D-111: with no bucket in the URL the list is the promoted top group only, not the whole universe.
    assert.equal(await page.getByRole('link', { name: '2330', exact: true }).count(), 0);
    assert.equal(await page.getByRole('link', { name: '前 20 名', exact: true }).getAttribute('aria-current'), 'true');
    await page.getByRole('link', { name: '觀察區（21~50）', exact: true }).click();
    await page.waitForFunction(() => location.hash.includes('bucket=ml_watch'));
    await page.getByRole('link', { name: '2330', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '0050', exact: true }).count(), 0);
    await page.reload(); // AC-206-1: the filtered view is restored from the URL alone.
    await page.getByRole('link', { name: '2330', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '0050', exact: true }).count(), 0);
    await page.getByRole('link', { name: '全部', exact: true }).click();
    await page.waitForFunction(() => location.hash.includes('bucket=all')); // 'all' is an explicit choice, not the default
    await page.getByRole('link', { name: '0050', exact: true }).waitFor();
    await page.getByRole('link', { name: '2330', exact: true }).waitFor();
    await page.getByRole('searchbox', { name: '搜尋股票代號' }).fill('233');
    await page.waitForFunction(() => location.hash.includes('q=233'));
    await page.getByRole('link', { name: '2330', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '0050', exact: true }).count(), 0);
    await page.getByRole('searchbox', { name: '搜尋股票代號' }).fill('');
    await page.getByRole('link', { name: '查看來源模型 →' }).click();
    await page.getByRole('heading', { name: '模型 metadata' }).waitFor(); // Resolved from model_id.
    await page.goBack();
    await page.getByRole('link', { name: '0050', exact: true }).click();
    await page.getByRole('heading', { name: '個股研究', exact: true }).waitFor();
    assert.match(new URL(page.url()).hash, /^#\/stocks\/0050\?/);
    // AC-208-1/2: markers, grouping, non-trading-day mapping, locate and reset.
    await page.goto('http://twquant.test/#/stocks/0050');
    await page.getByRole('heading', { name: '推薦事件', exact: true }).waitFor();
    await page.locator('table.platform-data-table tbody tr', { hasText: '2026-06-25' }).first().waitFor();
    // Two events share 06-25, so the row reports a count of 2 and expands to both run ids.
    const grouped = page.locator('table.platform-data-table tbody tr', { hasText: '2026-06-25' }).first();
    assert.match(await grouped.innerText(), /2/);
    await grouped.getByRole('button', { name: '展開', exact: true }).click();
    await page.getByText('run run_b', { exact: false }).waitFor();
    await page.getByText('run run_c', { exact: false }).waitFor();
    // A Saturday publication maps forward to the next session and says so.
    await page.getByText('前次非交易日發布', { exact: false }).waitFor();
    // A publication with no later bar stays pending instead of being plotted.
    await page.getByText('還沒有交易日行情', { exact: false }).waitFor();
    await page.getByRole('button', { name: '推薦標記', exact: true }).waitFor();
    await page.getByRole('button', { name: '2026-06-25', exact: true }).click(); // Locate that day.
    await page.getByRole('button', { name: '查看全區間', exact: true }).click();
    await page.getByRole('button', { name: '推薦標記', exact: true }).click(); // Toggle markers off.
    await page.getByRole('button', { name: '推薦標記', exact: true }).click();
    // AC-208-1: a stock with no events draws no markers and offers no toggle.
    await page.goto('http://twquant.test/#/stocks/2330');
    await page.getByText('沒有任何保存的推薦事件', { exact: false }).waitFor();
    assert.equal(await page.getByRole('button', { name: '推薦標記', exact: true }).count(), 0);
    // AC-240-4: the stock page is a numbered-panel dashboard fed only by what the API returned.
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto('http://twquant.test/#/stocks/0050');
    await page.getByText('元大台灣50 · K 線', { exact: false }).waitFor();
    assert.equal(await page.locator('.dash-no').count(), 18);
    assert.deepEqual(await page.locator('.dash-no').allInnerTexts(), Array.from({ length: 18 }, (_, i) => String(i + 1).padStart(2, '0')));
    // The KPI strip comes from the same fixture bars as the chart: last close 230 on 2026-06-30, 22 sessions.
    const kpis = await page.locator('.dash-kpis').innerText();
    assert.match(kpis, /230\.0/);
    assert.match(kpis, /2026-06-30/);
    assert.match(kpis, /22 日/);
    // Sources the fixture answers with 503 say so; nothing is filled in with a made-up figure.
    await page.getByRole('heading', { name: '法人行為計量', exact: true }).waitFor();
    const inst = page.locator('.dash-panel', { has: page.getByRole('heading', { name: '法人行為計量', exact: true }) });
    await inst.getByText('資料讀取失敗', { exact: false }).waitFor();
    assert.doesNotMatch(await inst.innerText(), /\d+ 張/);
    const coverage = page.locator('.dash-panel', { has: page.getByRole('heading', { name: '資料完整度', exact: true }) });
    assert.match(await coverage.innerText(), /日 K[\s\S]*22 筆[\s\S]*法人[\s\S]*讀取失敗/);
    // Too few bars for MA60 is stated rather than guessed.
    const readings = page.locator('.dash-panel', { has: page.getByRole('heading', { name: '技術摘要', exact: true }) });
    assert.match(await readings.innerText(), /資料不足（需 60 日）/);
    assert.match(await readings.innerText(), /非預測訊號/);
    // KD / MACD are opt-in panes on the same chart.
    await page.getByRole('button', { name: 'MACD', exact: true }).click();
    await page.getByRole('button', { name: 'KD', exact: true }).click();
    await page.getByText('元大台灣50 · K 線', { exact: false }).waitFor();
    await page.getByRole('button', { name: 'MACD', exact: true }).click();
    await page.getByRole('button', { name: 'KD', exact: true }).click();
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      assert(!await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), `${width}: stock dashboard overflow`);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    // AC-240-5: with no stock in the URL the page is a search entry; a code keeps its leading zero.
    await page.goto('http://twquant.test/#/stocks');
    await page.getByRole('heading', { name: '台股個股研究儀表板', exact: true }).waitFor();
    assert.equal(await page.locator('.dash-no').count(), 0);
    await page.getByRole('combobox', { name: '股票代號或名稱' }).fill('找不到的名字');
    await page.getByRole('button', { name: '開始分析', exact: true }).click();
    await page.getByRole('alert').getByText('找不到「找不到的名字」', { exact: false }).waitFor();
    assert.equal(new URL(page.url()).hash, '#/stocks');
    await page.getByRole('combobox', { name: '股票代號或名稱' }).fill('0050');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => location.hash === '#/stocks/0050');
    await page.getByText('元大台灣50 · K 線', { exact: false }).waitFor();
    // AC-209-1: the industry table mirrors the API and names its own window and coverage.
    await page.goto('http://twquant.test/#/industries');
    await page.getByRole('heading', { name: '產業資金流向', exact: true }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /窗口 20 交易日/);
    assert.match(await page.locator('#platform-content').innerText(), /12\.50/);
    await page.getByRole('link', { name: '半導體', exact: true }).click();
    await page.getByText('這是目前分類，不是歷史 PIT 分類', { exact: false }).waitFor();
    await page.getByRole('link', { name: '2454', exact: true }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /API 回報 2 檔.*可對應 2 檔/s);
    // An industry the classification table cannot name says so instead of showing nothing.
    await page.getByRole('link', { name: '航運', exact: true }).click();
    await page.getByText('目前分類表沒有屬於「航運」的股票', { exact: false }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /API 回報 4 檔；目前分類表沒有可對應的股票/);
    // A window with no rows is a legitimate zero, not a read failure.
    await page.getByRole('link', { name: '60 日', exact: true }).click();
    await page.getByText('零筆，不是讀取失敗', { exact: false }).waitFor();
    // AC-210-1: citations, cutoff, support/contradict/gap and an offline analysis are all visible.
    await page.goto('http://twquant.test/#/insights');
    await page.getByRole('heading', { name: '洞悉與證據', exact: true }).first().waitFor();
    await page.getByText('營收成長支持基本面', { exact: false }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /prompt insight_v1（abc123）/);
    assert.match(await page.locator('#platform-content').innerText(), /截點 2026-06-25 20:00/);
    for (const label of ['支持', '反對', '缺口']) {
      await page.getByText(label, { exact: true }).first().waitFor();
    }
    assert.match(await page.locator('#platform-content').innerText(), /有 1 項已知缺口/);
    // An absent analysis states why instead of being generated.
    await page.getByText('LLM 離線，未產生分析', { exact: false }).waitFor();
    await page.getByText('不會補寫分析', { exact: false }).waitFor();
    // A stock with no packs says so; it never falls back to another stock's insight.
    await page.goto('http://twquant.test/#/insights?stock=2330');
    await page.getByText('2330 尚未保存任何洞悉證據包。', { exact: false }).waitFor();
    await page.getByText('不會在沒有證據時生成分析', { exact: false }).waitFor();
    // AC-211-1: a legacy watchlist is readable, edits persist and corruption is never eaten.
    await page.goto('http://twquant.test/#/watchlists');
    await page.evaluate(() => localStorage.setItem('twquant.watchlist', '["0050","2330"]'));
    await page.reload();
    await page.getByRole('link', { name: '0050', exact: true }).waitFor();
    await page.getByText('讀到舊版自選格式', { exact: false }).first().waitFor();
    await page.getByRole('textbox', { name: '0050 的研究筆記' }).fill('等回踩月線');
    await page.reload();
    assert.equal(await page.getByRole('textbox', { name: '0050 的研究筆記' }).inputValue(), '等回踩月線');
    assert.equal(await page.getByRole('link', { name: '0050', exact: true }).count(), 1); // "0050" kept as a string.
    await page.getByRole('textbox', { name: '加入追蹤的股票代號' }).fill('2317');
    await page.getByRole('button', { name: '加入', exact: true }).click();
    await page.getByRole('link', { name: '2317', exact: true }).waitFor();
    await page.locator('tr', { hasText: '2330' }).getByRole('button', { name: '移除', exact: true }).click();
    assert.equal(await page.getByRole('link', { name: '2330', exact: true }).count(), 0);
    await page.reload();
    assert.equal(await page.getByRole('textbox', { name: '0050 的研究筆記' }).inputValue(), '等回踩月線');
    // Corrupt storage is reported and the raw value is left in place until the user resets it.
    await page.evaluate(() => localStorage.setItem('twquant.watchlist', '{not json'));
    await page.reload();
    await page.getByText('自選資料無法讀取', { exact: false }).first().waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('twquant.watchlist')), '{not json');
    await page.getByRole('button', { name: '清空並重建自選（會覆寫損毀資料）', exact: true }).click();
    await page.getByText('還沒有追蹤任何股票', { exact: false }).waitFor();
    await page.evaluate(() => localStorage.removeItem('twquant.watchlist'));
    // AC-212-1: legacy operations areas stay reachable, and polling never resets the tab.
    await page.goto('http://twquant.test/#/operations');
    await page.getByRole('tab', { name: '資料涵蓋', exact: true }).waitFor();
    await page.getByRole('heading', { name: '擷取涵蓋熱力圖', exact: true }).waitFor();
    await page.getByRole('tab', { name: '漏抓與補抓', exact: true }).click();
    await page.waitForFunction(() => location.hash.includes('tab=gaps'));
    await page.getByRole('heading', { name: /漏抓清單/ }).waitFor();
    await page.getByRole('tab', { name: '工作中心', exact: true }).click();
    await page.getByRole('heading', { name: '背景工作', exact: true }).waitFor();
    assert.match(new URL(page.url()).hash, /tab=jobs/);
    // The job poll runs every 3s; after more than one cycle the chosen tab must still be shown.
    await page.waitForTimeout(3500);
    assert.equal(await page.getByRole('tab', { name: '工作中心', exact: true }).getAttribute('aria-selected'), 'true');
    await page.getByRole('heading', { name: '背景工作', exact: true }).waitFor();
    await page.reload();
    assert.equal(await page.getByRole('tab', { name: '工作中心', exact: true }).getAttribute('aria-selected'), 'true');
    // No training or ordering control was introduced anywhere in operations.
    for (const forbidden of ['訓練', '下單', '買進', '賣出']) {
      assert.equal(await page.getByRole('button', { name: new RegExp(forbidden) }).count(), 0, forbidden);
    }
    // The legacy #ingest entry point still lands on operations.
    await page.goto('http://twquant.test/#ingest');
    await page.getByRole('heading', { name: '資料與工作', exact: true }).first().waitFor();
    // D-112: strategy targets never appear without their held-out record, and a failed strategy says why.
    await page.goto('http://twquant.test/#/strategies');
    await page.getByRole('heading', { name: '全市場多因子（市值加權）', exact: true }).waitFor();
    const strategies = await page.locator('#platform-content').innerText();
    assert.match(strategies, /研究輸出，不是買賣建議/);
    assert.match(strategies, /金庫期報酬[\s\S]*\+25\.8%[\s\S]*同期 0050 \+32\.2%/);
    assert.match(strategies, /未通過部署裁決/);
    assert.match(strategies, /25\.00%/); // 0050 keeps its leading zero and its weight
    assert.match(strategies, /這支策略今天算不出名單[\s\S]*串接閘門/); // failure is stated, not shown as a flat day
    // AC-220: the analysis reaches the daily list as a derived field, and its bucket stays a suggestion.
    await page.goto('http://twquant.test/#/recommendations?bucket=all');
    await page.getByRole('link', { name: '0050', exact: true }).waitFor();
    const table = await page.locator('#platform-content').innerText();
    assert.match(table, /矛盾/);
    assert.match(table, /支持 2／反對 1／缺口 1/);
    assert.match(table, /LLM 提議：觀察/); // Never rendered as the candidate's own bucket.
    assert.match(table, /LLM 離線，未產生分析/); // Absent analysis says why.
    assert.match(table, /尚未分析/); // No pack at all is a third, distinct state.
    // The stock page gains an insight tab reading the same saved packs.
    await page.goto('http://twquant.test/#/stocks/0050?tab=insights');
    await page.getByRole('tab', { name: '洞悉', exact: true }).waitFor();
    await page.getByText('營收成長支持基本面', { exact: false }).waitFor();
    await page.getByText('可得時間為估計', { exact: false }).first().waitFor();
    // AC-220b: the new pages show each stock's Chinese name beside its code.
    // 2330 sits in the watch group, which the default top-20 view leaves out (D-111).
    await page.goto('http://twquant.test/#/recommendations?bucket=all');
    await page.getByRole('link', { name: '0050', exact: true }).waitFor();
    const named = await page.locator('#platform-content').innerText();
    assert.match(named, /元大台灣50/);
    assert.match(named, /台積電/);
    // 0056 has no stock_meta entry in the fixture, so it degrades to the code alone.
    assert.doesNotMatch(named, /0056\s*\S*ETF/);
    await page.goto('http://twquant.test/#/insights');
    await page.getByText('元大台灣50', { exact: false }).first().waitFor();
    // The code stays its own link, so deep links and tests keep working.
    await page.getByRole('link', { name: '0050', exact: true }).first().waitFor();
    // AC-206-2: zero candidates, nothing generated and read failure stay distinguishable.
    await page.goto('http://twquant.test/#/recommendations?date=2026-09-04');
    await page.getByText('合法地沒有候選股票', { exact: false }).waitFor();
    // T-245: a snapshot saved before D-111 has candidates but no promotion bucket — a different state from an empty run.
    await page.goto('http://twquant.test/#/recommendations?date=2026-09-05');
    await page.getByText('D-111 之前產生的', { exact: false }).waitFor();
    assert.equal(await page.getByText('合法地沒有候選股票', { exact: false }).count(), 0);
    await page.goto('http://twquant.test/#/recommendations?date=2026-09-03');
    await page.getByText('2026-09-03 尚未產生推薦快照。', { exact: false }).waitFor();
    await page.goto('http://twquant.test/#/recommendations?date=2026-09-02');
    await page.getByText('推薦快照無法讀取', { exact: false }).waitFor();
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      for (const name of ['今日總覽', 'ML 選股', '策略持股', '我的追蹤', '產業研究', '洞悉與證據', '模型中心', '回測中心', '資料與工作']) {
        await page.getByRole('link', { name, exact: true }).click();
        // A page whose panel repeats the page title matches twice; the h1 is the first.
        await page.getByRole('heading', { name, exact: true }).first().waitFor();
        assert(!await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), `${width}: ${name} overflow`);
      }
    }
    await page.getByRole('link', { name: '模型中心', exact: true }).focus();
    await page.keyboard.press('Enter');
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    await page.getByRole('link', { name: '跳至內容' }).focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#platform-content').evaluate(el => el === document.activeElement), true);
    assert.equal(new URL(page.url()).hash, '#/models');
    // ── AC-215-1: one complete journey on fixed fixtures, then the same on mobile and keyboard.
    const journey = async label => {
      await page.goto('http://twquant.test/#/overview');
      await page.getByRole('heading', { name: 'lgbm_rank_v2', exact: true }).waitFor();
      // Daily pick → stock research, carrying the run context in the URL.
      await page.getByRole('link', { name: '0050', exact: true }).first().click();
      await page.getByRole('heading', { name: '個股研究', exact: true }).first().waitFor();
      assert.match(new URL(page.url()).hash, /^#\/stocks\/0050\?/, label);
      await page.getByText('元大台灣50 · K 線', { exact: false }).waitFor();
      // Stock → recommendation day → source model.
      await page.getByRole('button', { name: '2026-06-25', exact: true }).click();
      await page.goto(`http://twquant.test/#/models/${modelId}`);
      await page.getByRole('heading', { name: '模型 metadata' }).waitFor();
      // Source model → backtest evidence.
      await page.getByRole('link', { name: '回測中心', exact: true }).click();
      await page.getByRole('link', { name: 'monthly_top20', exact: true }).click();
      await page.getByText('權益曲線', { exact: true }).waitFor();
      // Track it, then walk back through history to the daily list.
      await page.goto('http://twquant.test/#/watchlists');
      await page.getByRole('textbox', { name: '加入追蹤的股票代號' }).fill('0050');
      await page.getByRole('button', { name: '加入', exact: true }).click();
      await page.getByRole('link', { name: '0050', exact: true }).waitFor();
      await page.reload();
      await page.getByRole('link', { name: '0050', exact: true }).waitFor(); // Survives a reload.
      await page.goBack();
      await page.getByText('權益曲線', { exact: true }).waitFor();
      await page.evaluate(() => localStorage.removeItem('twquant.watchlist'));
    };
    await page.setViewportSize({ width: 1280, height: 900 });
    await journey('desktop');
    await page.setViewportSize({ width: 375, height: 812 });
    await journey('mobile');
    assert(!await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), 'mobile journey overflow');
    await page.setViewportSize({ width: 1280, height: 900 });
    // Keyboard: every page is reachable by focusing a nav link and pressing Enter.
    for (const name of ['今日總覽', 'ML 選股', '策略持股', '我的追蹤', '個股研究', '產業研究', '洞悉與證據', '模型中心', '回測中心', '資料與工作']) {
      await page.getByRole('link', { name, exact: true }).focus();
      await page.keyboard.press('Enter');
      await page.getByRole('heading', { name, exact: true }).first().waitFor();
    }
    // AC-215-1: every page states a real source or an explicit not-generated state when data is gone.
    await page.unroute('**/*');
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Fixture: all sources down"}' });
      return route.fulfill({ contentType: 'text/html', body: html });
    });
    for (const [path, marker] of [
      ['#/overview', '推薦快照無法讀取'],
      ['#/recommendations', '推薦快照無法讀取'],
      ['#/industries', '產業資料無法讀取'],
      ['#/insights', '洞悉證據包讀取失敗'],
      ['#/models', '資料無法讀取'],
      ['#/backtests', '資料無法讀取'],
    ]) {
      await page.goto(`http://twquant.test/${path}`);
      await page.getByText(marker, { exact: false }).first().waitFor();
    }
    assert.deepEqual(errors, []);
    console.log('PASS AC-201-1/2/3, AC-204-1/2, AC-206-1/2, AC-207-1/2, AC-208-1/2, AC-240-4/5, AC-209-1, AC-210-1, AC-211-1, AC-212-1, AC-215-1, AC-220 and AC-220b: deep links, research artifacts, persisted equity chart, shared daily run with restorable filters, distinct empty/zero/error states, stock research tabs with a persistent K-line, quote identity isolation, recommendation-day markers with forward mapping, grouping, pending events, locate/reset/toggle, industry flows with explicit window and coverage, insight evidence with citations, gaps and stated absent analyses, legacy-compatible watchlist notes with explicit corruption handling, operations tabs that survive polling, derived insight fields whose suggested bucket stays a suggestion, Chinese stock names beside codes, responsive navigation and keyboard focus, plus the full desktop/mobile/keyboard journey and an all-sources-down pass (offline HTTP fixtures; no network).');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
