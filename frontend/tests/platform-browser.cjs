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
    page.on('pageerror', error => errors.push(error.message));
    // All HTTP is fulfilled locally; the test never reaches data services.
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
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
    await page.getByText('離線快照僅含熱門', { exact: false }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /0050/);
    assert.equal(new URL(page.url()).hash, '#/stocks/0050');
    await page.goto('http://twquant.test/#/not-a-page');
    await page.getByRole('heading', { name: '找不到這個頁面' }).waitFor();
    await page.getByRole('link', { name: '回今日總覽 →' }).click();
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
    console.log('PASS AC-201-1/2/3 and AC-204-1/2: deep links, research artifacts, persisted equity chart, responsive navigation and keyboard focus (offline HTTP fixtures).');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
