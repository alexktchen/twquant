import assert from 'node:assert/strict'
import test from 'node:test'
import { SHEET_FLICK, SHEET_PEEK, clampSheetOffset, settleSheet, sheetOffset, sheetVelocity } from '../src/cloud/sheet.ts'

// T-442 AC1：抽屜只有三個位置——全開（0）、只露出頭（高度 − 露頭高）、關掉（整張推出去）；高度固定，位置不改高度
test('T-442 AC1 sheetOffset：三個位置的 offset', () => {
  assert.equal(sheetOffset('open', 600), 0)
  assert.equal(sheetOffset('peek', 600), 600 - SHEET_PEEK)
  assert.equal(sheetOffset('closed', 600), 600)
  // 卡比露頭高還矮（不會發生，但不能變負數往上跑）
  assert.equal(sheetOffset('peek', 50), 0)
})

test('T-442 AC1 clampSheetOffset：往上拉頂住全開，往下沒有上限', () => {
  assert.equal(clampSheetOffset(-80), 0)
  assert.equal(clampSheetOffset(0), 0)
  assert.equal(clampSheetOffset(900), 900)
})

// T-442 AC2：放手停在最近的位置；拖過一半以上往下就關
test('T-442 AC2 settleSheet：慢慢放手停在最近的位置', () => {
  const h = 600
  assert.equal(settleSheet(40, 0, 'open', h), 'open')
  assert.equal(settleSheet(h - SHEET_PEEK - 30, 0, 'open', h), 'peek')
  assert.equal(settleSheet(h - SHEET_PEEK + 20, 0, 'peek', h), 'peek')
  assert.equal(settleSheet(h - 20, 0, 'peek', h), 'closed')
  // 露頭時往上拉回去
  assert.equal(settleSheet(100, 0, 'peek', h), 'open')
})

// T-442 AC3：甩得快照方向走一格——全開往下甩只到露頭（不會一甩就消失），露頭再往下甩才關；往上甩一律回全開
test('T-442 AC3 settleSheet：甩的方向優先', () => {
  const h = 600
  assert.equal(settleSheet(30, SHEET_FLICK, 'open', h), 'peek')
  assert.equal(settleSheet(h - 60, SHEET_FLICK + 1, 'open', h), 'peek')
  assert.equal(settleSheet(h - 60, SHEET_FLICK, 'peek', h), 'closed')
  assert.equal(settleSheet(h - 60, -SHEET_FLICK, 'peek', h), 'open')
  assert.equal(settleSheet(h - 60, -SHEET_FLICK, 'open', h), 'open')
})

test('T-442 AC3 sheetVelocity：px/ms；時間太短當 0', () => {
  assert.equal(sheetVelocity(50, 100), 0.5)
  assert.equal(sheetVelocity(-20, 40), -0.5)
  assert.equal(sheetVelocity(100, 0), 0)
})
