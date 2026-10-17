import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { STOP_HIT, VOL_WINDOW, groupOf, stopHit, vol20 } from '../functions/_lib/stop-hit.ts'
import { stopHitLine, stopHitTitle } from '../src/cloud/stophit.ts'

// 與 tests/test_stop_hit_reference.py AC-4 共用：同一串收盤，Python 用 factors/price.vol_20d 的公式算出 VOL_REF
const CLOSES = [100, 101.5, 99.2, 100.4, 102.1, 101.0, 103.3, 102.2, 104.8, 103.9, 105.5, 104.1, 106.0, 107.2, 105.8, 108.3, 107.1, 109.0, 110.4, 108.9, 111.2]
const VOL_REF = 0.016033416427263993

test('D-191 C AC-1：波動度公式同 price.vol_20d（20 日簡單報酬樣本標準差）；收盤不足 21 筆、0 或缺值 → null', () => {
  assert.equal(VOL_WINDOW, 20)
  assert.ok(Math.abs(vol20(CLOSES)! - VOL_REF) < 1e-12)
  assert.ok(Math.abs(vol20([50, 50, ...CLOSES])! - VOL_REF) < 1e-12, '多給的舊收盤不影響（只用最後 21 筆）')
  assert.equal(vol20(CLOSES.slice(1)), null)
  assert.equal(vol20([...CLOSES.slice(0, 10), null, ...CLOSES.slice(11)]), null)
  assert.equal(vol20([...CLOSES.slice(0, 10), 0, ...CLOSES.slice(11)]), null)
})

test('D-191 C AC-2：找組——界線左閉右開；最低組無下界、最高組無上界；用凍結的表', () => {
  assert.deepEqual(groupOf(0.01, [0.005, 0.01, 0.02]), 2)
  assert.deepEqual(groupOf(0.0099, [0.005, 0.01, 0.02]), 1)
  assert.deepEqual(groupOf(0.0001, [0.005, 0.01, 0.02]), 0)
  assert.deepEqual(groupOf(1, [0.005, 0.01, 0.02]), 3)
  assert.equal(STOP_HIT.bounds.length, 9)
  assert.equal(STOP_HIT.groups.length, 10)
  assert.equal(groupOf(0.05), 9, '日波動 5% 在最高組')
  assert.equal(groupOf(0.005), 0)
  const down = STOP_HIT.groups.map((g) => g.down15)
  assert.deepEqual(down, [...down].sort((a, b) => a - b), '碰 −15% 隨組上升')
})

test('D-191 C AC-3：查表結果帶這檔的波動度、組、兩個比例、樣本數與界線；算不出波動度 → null', () => {
  const h = stopHit(CLOSES)!
  assert.ok(Math.abs(h.vol - VOL_REF) < 1e-12)
  const g = STOP_HIT.groups[groupOf(VOL_REF)]
  assert.deepEqual(h, { vol: h.vol, group: g.group, down15: g.down15, up20: g.up20, n: g.n, vol_lo: g.vol_lo, vol_hi: g.vol_hi })
  assert.equal(stopHit(CLOSES.slice(2)), null)
})

test('D-191 C AC-4：名單列的一行小字與滑過說明——寫兩個方向、寫明是基礎率不是預測、名單不刪減', () => {
  assert.equal(stopHitLine(null), null)
  assert.equal(stopHitTitle(undefined), '')
  const h = { vol: 0.0478, group: 9, down15: 0.224, up20: 0.2446, n: 349161, vol_lo: 0.040083, vol_hi: null }
  assert.equal(stopHitLine(h), '同波動股票 20 日內碰 −15%：22%、碰 +20%：24%')
  const t = stopHitTitle(h)
  assert.match(t, /日波動度 4\.78%.*第 10 組（4\.01% 以上）/)
  assert.match(t, /349,161 筆.*22\.4%.*24\.5%/)
  assert.match(t, /不是對這檔的預測/)
  assert.match(t, /名單不因此刪減/)
  const mid = stopHitTitle({ ...h, group: 4, vol_lo: 0.015261, vol_hi: 0.018182 })
  assert.match(mid, /第 5 組（1\.53% 以上、1\.82% 以下）/)
})

test('D-191 C AC-5（靜態）：名單 API 每檔附 stop_hit、個股頁端點存在、鏡像表不手改', () => {
  const picks = readFileSync(new URL('../functions/api/picks/index.ts', import.meta.url), 'utf8')
  assert.ok(picks.includes('readStopHit(db, target, sids)') && picks.includes('stop_hit: stopHit.get(r.stock_id) ?? null'))
  const api = readFileSync(new URL('../functions/api/stocks/[sid]/stophit.ts', import.meta.url), 'utf8')
  assert.ok(api.includes('SID_RE.test(sid)') && api.includes('readStopHit(db, today, [sid])'))
  const mirror = readFileSync(new URL('../functions/_lib/stop-hit-table.ts', import.meta.url), 'utf8')
  assert.ok(mirror.startsWith('// 鏡像自 configs/stop_hit_reference.json'))
})
