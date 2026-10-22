// GET /api/warroom：首頁戰情室（T-379／D-188）——晨報（另回有晨報的日期，給歷史切換；某天內容走 /api/warroom/brief）、大盤體檢（D-199 起附上市／上櫃）、期交所盤後、今天名單速覽、昨日重大訊息。
// 行事曆 T-431／D-209 搬出首頁（法說會、除權息改標在追蹤股與名單股上），這裡不再查。
// 同一種權限看到都一樣（追蹤股的消息另由 /api/warroom/news 查）：同一執行個體 60 秒暫存。任何一張表還沒建 → 那一塊是 null，不擋其他。
// 即時的美股、台指期、類股強弱與族群資金照舊走 /api/mood/*、/api/groups。
// 名單速覽（picks）與昨日消息的「名單」標記只給 admin（T-430／D-208）：其他人 picks 是 null、消息照一般規則挑。
import { guarded, json, type Env } from '../../_lib/db'
import { cached, cacheKey } from '../../_lib/cache'
import { allowed, gatedKey } from '../../_lib/gate'
import { LINES } from '../../_lib/picks'
import { readNewColumn, readRows } from '../../_lib/scorecard'
import { briefKeys, selectLatestBrief } from '../../_lib/brief-db'
import { BRIEF_DATES_LIMIT, addDays, attachContent, pickNews, taipeiDay, type NewsRow } from '../../_lib/warroom'

type Row = Record<string, unknown>

const handle = async (env: Env, withPicks: boolean): Promise<Response> =>
  guarded(env, async (db) => {
    const today = taipeiDay(Date.now())
    const brief = await selectLatestBrief(db)
    // 歷史清單一天兩格（盤前、盤後）：「2026-10-20|evening」
    const briefDays = await briefKeys(db, BRIEF_DATES_LIMIT * 2)
    // to_jsonb：日期變成 "YYYY-MM-DD"、數字照數字，不經過驅動程式的 Date
    const [m] = await readRows(
      () => db<{ j: Row }[]>`SELECT to_jsonb(t) - 'created_at' AS j FROM serving.market_daily t ORDER BY date DESC LIMIT 1`,
    )
    const market = m?.j ?? null
    const mdate = market ? String(market.date) : null
    // 同一天分上市、上櫃（D-199，表 market_breadth_daily；新表還沒建或那天還沒算 → null，網站只顯示全部）
    const split = mdate
      ? await readRows(
          () => db<{ j: Row }[]>`
            SELECT to_jsonb(t) - 'created_at' - 'date' AS j FROM serving.market_breadth_daily t
            WHERE date = ${mdate}::date ORDER BY market`,
        )
      : []
    const marketSplit = split.length > 0 ? Object.fromEntries(split.map((r) => [String(r.j.market), r.j])) : null
    const [tx] = mdate
      ? await readRows(
          () => db<{ j: Row }[]>`
            SELECT to_jsonb(t) - 'available_at' AS j FROM serving.taifex_daily t
            WHERE date <= ${mdate}::date ORDER BY date DESC LIMIT 1`,
        )
      : []
    const taifex = tx?.j ?? null

    // 今天名單（最新一天）與前一天比，算新增；沒有名單權限就不讀
    const pickDates = withPicks
      ? await readRows(() => db<{ d: string }[]>`SELECT DISTINCT date::text AS d FROM serving.daily_picks ORDER BY d DESC LIMIT 2`)
      : []
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
        sids: [...listed],
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

    return json({
      today,
      brief,
      brief_dates: briefDays,
      market,
      market_split: marketSplit,
      taifex,
      picks,
      news: { since, items: news },
    })
  })

// 有沒有名單權限分開暫存（_lib/gate.ts）
export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const full = await allowed(ctx.request, 'picks')
  return cached(gatedKey(cacheKey(ctx.request), full), async () => handle(ctx.env, full))
}
