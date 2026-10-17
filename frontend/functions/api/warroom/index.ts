// GET /api/warroom：首頁戰情室（T-379／D-188）——晨報、大盤體檢、期交所盤後、今天名單速覽、昨日重大訊息、行事曆。
// 每個人看到都一樣（追蹤股的消息另由 /api/warroom/news 查）：同一執行個體 60 秒暫存。任何一張表還沒建 → 那一塊是 null，不擋其他。
// 即時的美股、台指期、類股強弱與族群資金照舊走 /api/mood/*、/api/groups。
import { guarded, json, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { LINES } from '../../_lib/picks'
import { readNewColumn, readRows } from '../../_lib/scorecard'
import { CALENDAR_DAYS, addDays, attachContent, pickNews, revenueDeadline, taipeiDay, type NewsRow } from '../../_lib/warroom'

type Row = Record<string, unknown>

const handle: PagesFunction<Env> = async ({ env }) =>
  guarded(env, async (db) => {
    const today = taipeiDay(Date.now())
    const [brief] = await readRows(
      () => db<Row[]>`
        SELECT date::text AS date, to_char(generated_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS generated_at, rule_text, headline, bullets, risk_note,
               model_id, absent_reason, facts, inputs->'extras' AS extras, inputs->'adr_premium' AS adr_premium
        FROM serving.morning_brief ORDER BY date DESC LIMIT 1`,
    )
    // to_jsonb：日期變成 "YYYY-MM-DD"、數字照數字，不經過驅動程式的 Date
    const [m] = await readRows(
      () => db<{ j: Row }[]>`SELECT to_jsonb(t) - 'created_at' AS j FROM serving.market_daily t ORDER BY date DESC LIMIT 1`,
    )
    const market = m?.j ?? null
    const mdate = market ? String(market.date) : null
    const [tx] = mdate
      ? await readRows(
          () => db<{ j: Row }[]>`
            SELECT to_jsonb(t) - 'available_at' AS j FROM serving.taifex_daily t
            WHERE date <= ${mdate}::date ORDER BY date DESC LIMIT 1`,
        )
      : []
    const taifex = tx?.j ?? null

    // 今天名單（最新一天）與前一天比，算新增
    const pickDates = await readRows(
      () => db<{ d: string }[]>`SELECT DISTINCT date::text AS d FROM serving.daily_picks ORDER BY d DESC LIMIT 2`,
    )
    let picks: { date: string; lines: { code: string; name: string; n: number; new: number }[] } | null = null
    let listed = new Set<string>()
    if (pickDates.length > 0) {
      const rows = await db<{ d: string; line: string; stock_id: string }[]>`
        SELECT date::text AS d, line, stock_id FROM serving.daily_picks
        WHERE date::text = ANY(string_to_array(${pickDates.map((r) => r.d).join(',')}, ','))`
      const latest = pickDates[0].d
      const prev = new Set(rows.filter((r) => r.d !== latest).map((r) => `${r.line}:${r.stock_id}`))
      const now = rows.filter((r) => r.d === latest)
      listed = new Set(now.map((r) => r.stock_id))
      const codes = [...new Set(now.map((r) => r.line))]
      picks = {
        date: latest,
        lines: codes.map((code) => {
          const mine = now.filter((r) => r.line === code)
          return {
            code,
            name: LINES.find((l) => l.code === code)?.name ?? code,
            n: mine.length,
            new: mine.filter((r) => !prev.has(`${r.line}:${r.stock_id}`)).length,
          }
        }),
      }
    }

    // 昨日消息：最新交易日 00:00（台北）起的公告
    const since = mdate ?? addDays(today, -1)
    const raw = await readRows(
      () => db<NewsRow[]>`
        SELECT a.stock_id, a.name, a.subject, to_char(a.available_at AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD HH24:MI') AS at,
               extract(epoch FROM a.available_at)::bigint::int AS ts
        FROM serving.mops_announcements a
        WHERE a.available_at >= (${since}::date)::timestamp AT TIME ZONE 'Asia/Taipei'
        ORDER BY a.available_at DESC LIMIT 2000`,
    )

    // 內文只撈挑出來的那幾則（2026-10-17 使用者：點開要看到內文）——不把兩千則全文都拉進 Worker
    const picked = pickNews(raw, listed)
    const ids = [...new Set(picked.map((n) => n.stock_id))]
    // 內文欄是 2026-10-17 加的，排程第一次跑到之前不存在 → 當成沒有內文，首頁照常（readNewColumn）
    const bodies = ids.length
      ? await readNewColumn(
          () => db<{ stock_id: string; ts: number; subject: string; content: string | null }[]>`
            SELECT stock_id, extract(epoch FROM available_at)::bigint::int AS ts, subject, content
            FROM serving.mops_announcements
            WHERE stock_id = ANY(string_to_array(${ids.join(',')}, ','))
              AND available_at >= (${since}::date)::timestamp AT TIME ZONE 'Asia/Taipei'`,
        )
      : []
    const news = attachContent(picked, bodies)

    const until = addDays(today, CALENDAR_DAYS - 1)
    const conferences = await readRows(
      () => db<Row[]>`
        SELECT date::text AS date, stock_id, name, conf_time, summary FROM serving.conference_calendar
        WHERE date BETWEEN ${today}::date AND ${until}::date ORDER BY date, conf_time, stock_id`,
    )
    const dividends = await readRows(
      () => db<Row[]>`
        SELECT e.date::text AS date, e.stock_id, m.name, e.event_type FROM serving.corporate_events e
        LEFT JOIN serving.stock_meta m ON m.stock_id = e.stock_id
        WHERE e.date BETWEEN ${today}::date AND ${until}::date ORDER BY e.date, e.stock_id`,
    )

    return json({
      today,
      brief: brief ?? null,
      market,
      taifex,
      picks,
      news: { since, items: news },
      calendar: { conferences, dividends, revenue: revenueDeadline(today) },
    })
  })

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
