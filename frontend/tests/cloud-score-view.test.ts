import assert from 'node:assert/strict'
import test from 'node:test'
import { benchDiff, cashRatio, heldText, statTiles, tone } from '../src/cloud/score-view.ts'

test('績效頁改版：報酬顏色、對照贏輸、現金比例、抱幾天', () => {
  assert.equal(tone(0.03), 'up')
  assert.equal(tone(-0.03), 'down')
  assert.equal(tone(0), 'plain')
  assert.equal(tone(null), 'na')
  assert.deepEqual(benchDiff(0.052, 0.02), { win: true, text: '贏 3.2 點' })
  assert.deepEqual(benchDiff(-0.01, 0.0), { win: false, text: '輸 1.0 點' })
  assert.equal(benchDiff(0.1, null), null)
  assert.equal(cashRatio(37, 100), 0.37)
  assert.equal(cashRatio(1, 0), null)
  assert.equal(heldText('closed', 21), '21 天')
  assert.equal(heldText('open', 3), '已 3 天')
  assert.equal(heldText('pending', 0), '')
})

test('逐筆統計 → 七張數字卡；沒結算任何一筆時全部是 —，不是 0', () => {
  const s = { n: 120, mean: 0.0123, median: -0.004, win_rate: 0.48, big_loss_rate: 0.05, best: 0.4, worst: -0.3 }
  const t = statTiles(s, -0.15)
  assert.equal(t.length, 7)
  assert.deepEqual(t.map((x) => x.key), ['n', 'mean', 'median', 'win', 'bigloss', 'best', 'worst'])
  assert.equal(t[0].value, '120')
  assert.equal(t[1].value, '+1.23%')
  assert.equal(t[1].tone, 'up')
  assert.equal(t[2].tone, 'down')
  assert.equal(t[4].label, '虧超過 15%')
  assert.equal(t[4].tone, 'warn')
  const empty = statTiles({ n: 0, mean: null, median: null, win_rate: null, big_loss_rate: null, best: null, worst: null }, -0.15)
  assert.ok(empty.slice(1).every((x) => x.value === '—' && x.tone !== 'up' && x.tone !== 'down'))
})
