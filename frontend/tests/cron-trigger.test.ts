import assert from 'node:assert/strict'
import test from 'node:test'
import { dispatchRequest, taipeiDate } from '../../cron-trigger/src/dispatch.ts'

test('D-175：觸發時刻換成台北日期（UTC 16:00 以後已是台北隔天）', () => {
  assert.equal(taipeiDate(new Date('2026-10-12T09:33:00Z')), '2026-10-12') // 台北 17:33
  assert.equal(taipeiDate(new Date('2026-10-12T14:43:00Z')), '2026-10-12') // 台北 22:43
  assert.equal(taipeiDate(new Date('2026-10-12T16:00:00Z')), '2026-10-13') // 台北隔天 00:00
})

test('D-175：dispatch 請求帶 main 與日期；沒有 token 或 repo 格式錯就丟錯', () => {
  const env = { GITHUB_TOKEN: 't0k', GITHUB_REPO: 'alexktchen/twquant', WORKFLOW: 'daily-pipeline.yml' }
  const { url, init } = dispatchRequest(env, '2026-10-12')
  assert.equal(url, 'https://api.github.com/repos/alexktchen/twquant/actions/workflows/daily-pipeline.yml/dispatches')
  assert.equal(init.method, 'POST')
  assert.deepEqual(JSON.parse(String(init.body)), { ref: 'main', inputs: { date: '2026-10-12' } })
  assert.equal((init.headers as Record<string, string>).Authorization, 'Bearer t0k')
  assert.throws(() => dispatchRequest({ ...env, GITHUB_TOKEN: '' }, '2026-10-12'), /GITHUB_TOKEN/)
  assert.throws(() => dispatchRequest({ ...env, GITHUB_REPO: 'bad repo' }, '2026-10-12'), /GITHUB_REPO/)
})
