import assert from 'node:assert/strict'
import test from 'node:test'
import { dispatchRequest, taipeiDate } from '../../cron-trigger/src/dispatch.ts'

test('D-175：觸發時刻換成台北日期（UTC 16:00 以後已是台北隔天）', () => {
  assert.equal(taipeiDate(new Date('2026-10-12T09:33:00Z')), '2026-10-12') // 台北 17:33
  assert.equal(taipeiDate(new Date('2026-10-12T13:13:00Z')), '2026-10-12') // 台北 21:13
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

test('T-377：06:30 那條 cron 觸發晨報，其他照舊觸發每日 pipeline；工作流程檔名要像檔名', async () => {
  const { workflowFor } = await import('../../cron-trigger/src/dispatch.ts')
  const env = {
    GITHUB_TOKEN: 't0k',
    GITHUB_REPO: 'alexktchen/twquant',
    WORKFLOW: 'daily-pipeline.yml',
    MORNING_WORKFLOW: 'morning-brief.yml',
    MORNING_CRON: '30 22 * * SUN-THU',
  }
  assert.equal(workflowFor(env, '30 22 * * SUN-THU'), 'morning-brief.yml')
  assert.equal(workflowFor(env, '33 9 * * MON-FRI'), 'daily-pipeline.yml')
  assert.equal(workflowFor(env, undefined), 'daily-pipeline.yml')
  assert.equal(workflowFor({ ...env, MORNING_WORKFLOW: undefined }, '30 22 * * SUN-THU'), 'daily-pipeline.yml')
  const { url } = dispatchRequest(env, '2026-10-20', 'morning-brief.yml')
  assert.equal(url, 'https://api.github.com/repos/alexktchen/twquant/actions/workflows/morning-brief.yml/dispatches')
  assert.throws(() => dispatchRequest(env, '2026-10-20', '../x.yml'), /檔名/)
})

test('T-409：21:45 那條 cron 也觸發晨報工作流程，但多帶 edition=evening；其他班不帶', async () => {
  const { inputsFor, workflowFor } = await import('../../cron-trigger/src/dispatch.ts')
  const env = {
    GITHUB_TOKEN: 't0k',
    GITHUB_REPO: 'alexktchen/twquant',
    WORKFLOW: 'daily-pipeline.yml',
    MORNING_WORKFLOW: 'morning-brief.yml',
    MORNING_CRON: '30 22 * * SUN-THU',
    EVENING_CRON: '45 13 * * MON-FRI',
  }
  assert.equal(workflowFor(env, '45 13 * * MON-FRI'), 'morning-brief.yml')
  assert.equal(workflowFor(env, '30 22 * * SUN-THU'), 'morning-brief.yml')
  assert.equal(workflowFor(env, '13 13 * * MON-FRI'), 'daily-pipeline.yml')
  assert.deepEqual(inputsFor(env, '45 13 * * MON-FRI', '2026-10-20'), { date: '2026-10-20', edition: 'evening' })
  assert.deepEqual(inputsFor(env, '30 22 * * SUN-THU', '2026-10-20'), { date: '2026-10-20' })
  assert.deepEqual(inputsFor({ ...env, EVENING_CRON: undefined }, '45 13 * * MON-FRI', '2026-10-20'), { date: '2026-10-20' })
  const { init } = dispatchRequest(env, '2026-10-20', 'morning-brief.yml', inputsFor(env, '45 13 * * MON-FRI', '2026-10-20'))
  assert.deepEqual(JSON.parse(String(init.body)), { ref: 'main', inputs: { date: '2026-10-20', edition: 'evening' } })
  assert.equal(taipeiDate(new Date('2026-10-20T13:45:00Z')), '2026-10-20') // 台北 21:45 還是當天
})
