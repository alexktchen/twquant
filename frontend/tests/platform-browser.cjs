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
      { stock_id: '0050', rank: 1, score: 0.812, bucket: 'top_decile', reason_ref: null, insight_run_id: null, data_status: 'pending' },
      { stock_id: '2330', rank: 2, score: 0.744, bucket: 'watch', reason_ref: null, insight_run_id: null, data_status: 'pending' },
      { stock_id: '0056', rank: 3, score: 0.701, bucket: 'top_decile', reason_ref: null, insight_run_id: null, data_status: 'pending' },
    ];
    // Mirrors the server contract: filters apply to the whole run before any page slice.
    const recommendations = url => {
      const [date, bucket, q] = ['date', 'bucket', 'q'].map(key => url.searchParams.get(key));
      const query = { date, model: url.searchParams.get('model'), run: url.searchParams.get('run'), bucket, q, page: 1, page_size: 200 };
      if (date === '2026-09-03') return { runs: [], issues: [], query };
      if (date === '2026-09-04') return { runs: [{ ...runBase, run_id: 'run_zero', as_of: '2026-09-04T20:00:00+08:00', candidates: [], candidate_total: 0 }], issues: [], query };
      const kept = allCandidates.filter(c => (!bucket || c.bucket === bucket) && (!q || c.stock_id.includes(q)));
      return { runs: [{ ...runBase, candidates: kept, candidate_total: kept.length }], issues: [], query };
    };
    // A connected-mode fixture: /api/health succeeds so the stock page runs in live mode.
    const marketAsOf = '2026-06-30';
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
    page.on('pageerror', error => errors.push(error.message));
    // All HTTP is fulfilled locally; the test never reaches data services.
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/sector-flows') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ flows: sectorFlows(Number(url.searchParams.get('window')) || 20) }) });
      if (url.pathname === '/api/health') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'ok', as_of: marketAsOf }) });
      if (url.pathname === '/api/meta') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ as_of: marketAsOf, window: '2022-01-01~2026-06-30', rev_month: null, top_stocks: ['0050', '2330'], coverage: [] }) });
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
    await page.getByRole('link', { name: 'watch', exact: true }).click();
    await page.waitForFunction(() => location.hash.includes('bucket=watch'));
    await page.getByRole('link', { name: '2330', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '0050', exact: true }).count(), 0);
    await page.reload(); // AC-206-1: the filtered view is restored from the URL alone.
    await page.getByRole('link', { name: '2330', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '0050', exact: true }).count(), 0);
    await page.getByRole('link', { name: '全部', exact: true }).click();
    await page.getByRole('link', { name: '0050', exact: true }).waitFor();
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
    // AC-206-2: zero candidates, nothing generated and read failure stay distinguishable.
    await page.goto('http://twquant.test/#/recommendations?date=2026-09-04');
    await page.getByText('合法地沒有候選股票', { exact: false }).waitFor();
    await page.goto('http://twquant.test/#/recommendations?date=2026-09-03');
    await page.getByText('2026-09-03 尚未產生推薦快照。', { exact: false }).waitFor();
    await page.goto('http://twquant.test/#/recommendations?date=2026-09-02');
    await page.getByText('推薦快照無法讀取', { exact: false }).waitFor();
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      for (const name of ['今日總覽', 'ML 選股', '我的追蹤', '產業研究', '洞悉與證據', '模型中心', '回測中心', '資料與工作']) {
        await page.getByRole('link', { name, exact: true }).click();
        await page.getByRole('heading', { name, exact: true }).waitFor();
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
    assert.deepEqual(errors, []);
    console.log('PASS AC-201-1/2/3, AC-204-1/2, AC-206-1/2, AC-207-1/2, AC-208-1/2 and AC-209-1: deep links, research artifacts, persisted equity chart, shared daily run with restorable filters, distinct empty/zero/error states, stock research tabs with a persistent K-line, quote identity isolation, recommendation-day markers with forward mapping, grouping, pending events, locate/reset/toggle, industry flows with explicit window and coverage, responsive navigation and keyboard focus (offline HTTP fixtures).');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
