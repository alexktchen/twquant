// T-445／D-216：星雲圖「同時幾個人在看」。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { PRESENCE_BODY_MAX, PRESENCE_DDL, PRESENCE_PURGE_S, PRESENCE_WINDOW_S, parsePresence } from '../functions/_lib/presence.ts'
import { PRESENCE_BEAT_S, newViewerId, viewersText } from '../src/cloud/galaxy-presence.ts'

const ID = '0123456789abcdef0123456789abcdef'

test('T-445 AC2 心跳內容：只收 { id } 或 { id, leave: true }；id 是 16～32 位小寫十六進位；多欄位、太長、壞 JSON 都不收', () => {
  assert.deepEqual(parsePresence(JSON.stringify({ id: ID })), { id: ID, leave: false })
  assert.deepEqual(parsePresence(JSON.stringify({ id: ID, leave: true })), { id: ID, leave: true })
  assert.deepEqual(parsePresence(JSON.stringify({ id: ID.slice(0, 16) })), { id: ID.slice(0, 16), leave: false })
  for (const bad of [
    { id: ID.toUpperCase() },
    { id: ID.slice(0, 15) },
    { id: ID + 'ab' },
    { id: "'; drop table x; --" },
    { id: ID, leave: false },
    { id: ID, leave: 'yes' },
    { id: ID, email: 'a@b.c' },
    { id: 123 },
    [ID],
    null,
  ])
    assert.equal(parsePresence(JSON.stringify(bad)), null, JSON.stringify(bad))
  assert.equal(parsePresence('{'), null)
  assert.equal(parsePresence(JSON.stringify({ id: ID, pad: 'x'.repeat(PRESENCE_BODY_MAX) })), null)
})

test('T-445 AC3 漏一次心跳不會掉：在看的窗至少兩次心跳；過期列一定在窗外才刪；表只記 id 與時間', () => {
  assert.ok(PRESENCE_WINDOW_S >= 2 * PRESENCE_BEAT_S)
  assert.ok(PRESENCE_PURGE_S > PRESENCE_WINDOW_S)
  assert.deepEqual(PRESENCE_DDL, ['CREATE TABLE IF NOT EXISTS web.galaxy_presence (id text PRIMARY KEY, seen_at timestamptz NOT NULL)'])
  const ep = readFileSync(new URL('../functions/pub/galaxy-presence.ts', import.meta.url), 'utf-8')
  assert.ok(ep.includes('sameOrigin(request)'), '只收本站送的')
  assert.ok(!/userEmail|cf-connecting-ip|x-forwarded-for/i.test(ep), '不記身分與 IP')
})

test('T-445 AC4 分頁 id 是 32 位小寫十六進位、伺服器收得下；人數字樣', () => {
  const id = newViewerId((a) => a.map((_, i) => i * 17))
  assert.equal(id, '00112233445566778899aabbccddeeff')
  assert.deepEqual(parsePresence(JSON.stringify({ id })), { id, leave: false })
  assert.match(newViewerId(), /^[0-9a-f]{32}$/)
  assert.equal(viewersText(1), '1 人在看')
  assert.equal(viewersText(1234), '1,234 人在看')
})
