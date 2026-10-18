// GET /api/picks?date=YYYY-MM-DD（空＝最新）：各條線的名單，附收盤、當日漲跌、連續上榜、新進、族群，
// 與「為什麼選它」的理由（D-180，serving.pick_reasons；那天沒算就是 null）；
// 破底標記（weak）與加權大跌那天的歷史對照（drop，沒大跌是 null）——D-183，只供看盤，不改名單；
// 每條線另附歷史同區間參考（D-179，serving.score_reference），網站每檔依分數對到區間；
// 潛伏兩條線每檔另附歷史相似情境（D-182，serving.pick_similar；那天沒算就是 null）；
// 每檔另附近 30 天的內部人持股轉讓申報摘要（T-384，serving.insider_transfer_notices；沒有就是 null）。
import { fail, guarded, json, type Env } from '../../_lib/db'
import { groupsFor } from '../../_lib/groups'
import { readHolders } from '../../_lib/holders'
import { readInsider } from '../../_lib/insider'
import { readStopHit } from '../../_lib/stop-hit'
import { readRisks } from '../../_lib/risks'
import { readDownRisk } from '../../_lib/down-risk'
import { readReasons } from '../../_lib/reasons'
import { readReference } from '../../_lib/reference'
import { readSimilar } from '../../_lib/similar'
import { readDrop, readWeak } from '../../_lib/weak'
import { DATE_RE, LINES, RECENT_WINDOW, STREAK_LOOKBACK, annotate, change, linesForDay, recentFlags, type PickRow } from '../../_lib/picks'
import { cached, cacheKey } from '../../_lib/cache'

const handle: PagesFunction<Env> = async ({ request, env }) => {
  const want = new URL(request.url).searchParams.get('date')
  if (want !== null && !DATE_RE.test(want)) return fail(400, `日期格式錯誤：${want}`)
  return guarded(env, async (db) => {
    const dates = (
      await db<{ d: string }[]>`
        SELECT DISTINCT date::text AS d FROM serving.daily_picks
        WHERE date <= ${want ?? '9999-12-31'}::date ORDER BY d DESC LIMIT ${STREAK_LOOKBACK + 1}`
    ).map((r) => r.d)
    if (dates.length === 0 || (want !== null && dates[0] !== want)) return fail(404, '這天沒有名單')
    const target = dates[0]
    const rows = await db<PickRow[]>`
      SELECT date::text AS date, line, rank, stock_id, score FROM serving.daily_picks
      WHERE date::text = ANY(string_to_array(${dates.join(',')}, ',')) ORDER BY date DESC, line, rank`
    const todayRows = rows.filter((r) => r.date === target)
    const sids = [...new Set(todayRows.map((r) => r.stock_id))]
    const px = await db<{ stock_id: string; close: number | null; prev: number | null }[]>`
      SELECT p.stock_id, p.close, (SELECT q.close FROM serving.prices_daily q
        WHERE q.stock_id = p.stock_id AND q.date < p.date ORDER BY q.date DESC LIMIT 1) AS prev
      FROM serving.prices_daily p WHERE p.date = ${target}::date AND p.stock_id = ANY(string_to_array(${sids.join(',')}, ','))`
    const meta = await db<{ stock_id: string; name: string | null; industry: string | null }[]>`
      SELECT stock_id, name, industry FROM serving.stock_meta WHERE stock_id = ANY(string_to_array(${sids.join(',')}, ','))`
    const groups = await groupsFor(db, target, meta)
    const reasons = await readReasons(db, target)
    const weak = await readWeak(db, target, sids)
    const insider = await readInsider(db, target, sids)
    const holders = await readHolders(db, target, sids)
    const stopHit = await readStopHit(db, target, sids)
    const risks = await readRisks(db, target, sids)
    const downRisk = await readDownRisk(db, target)
    const drop = await readDrop(db, target)
    const reference = await readReference(db)
    const similar = await readSimilar(db, target)
    const close = new Map(px.map((r) => [r.stock_id, r]))
    const window = dates.slice(0, RECENT_WINDOW)
    const startClose = new Map<string, number | null>()
    if (window.length > 1) {
      const start = await db<{ stock_id: string; close: number | null }[]>`
        SELECT stock_id, close FROM serving.prices_daily
        WHERE date = ${window[window.length - 1]}::date AND stock_id = ANY(string_to_array(${sids.join(',')}, ','))`
      for (const r of start) startClose.set(r.stock_id, r.close)
    }
    const names = new Map(meta.map((r) => [r.stock_id, r.name]))
    const byDay = (d: string, line: string) => rows.filter((r) => r.date === d && r.line === line)
    const lines = linesForDay(todayRows.map((r) => r.line)).map((code) => {
      const today = byDay(target, code)
      const recentLists = window.map((d) => {
        const day = byDay(d, code)
        return day.length ? day.map((r) => r.stock_id) : null
      })
      const marks = annotate(
        today.map((r) => r.stock_id),
        dates.slice(1).map((d) => byDay(d, code).map((r) => r.stock_id)),
      )
      return {
        code,
        name: LINES.find((l) => l.code === code)?.name ?? code,
        note: LINES.find((l) => l.code === code)?.note ?? null,
        ref: reference.get(code) ?? [],
        picks: today.map((r) => {
          const p = close.get(r.stock_id)
          const c = p?.close ?? null
          const [streak, isNew] = marks[r.stock_id]
          return {
            rank: r.rank,
            sid: r.stock_id,
            name: names.get(r.stock_id) ?? null,
            group: groups.get(r.stock_id) ?? null,
            reasons: reasons.get(`${code}|${r.stock_id}`) ?? null,
            similar: similar.get(`${code}|${r.stock_id}`) ?? null,
            score: r.score,
            close: c,
            prev: p?.prev ?? null,
            chg: change(c, p?.prev ?? null),
            streak,
            new: isNew,
            recent: recentFlags(r.stock_id, recentLists),
            chg6: change(c, startClose.get(r.stock_id) ?? null),
            weak: weak.get(r.stock_id) ?? null,
            insider: insider.get(r.stock_id) ?? null,
            holders: holders.get(r.stock_id) ?? null,
            stop_hit: stopHit.get(r.stock_id) ?? null,
            risks: risks.get(r.stock_id) ?? null,
            down_risk: downRisk.get(`${code}|${r.stock_id}`) ?? null,
          }
        }),
      }
    })
    return json({ date: target, window: [...window].reverse(), drop, lines })
  })
}

// 每個人看到都一樣、一天只更新兩次：同一執行個體 60 秒暫存，省下連資料庫的 CPU（_lib/cache.ts）
export const onRequestGet: PagesFunction<Env> = (ctx) => cached(cacheKey(ctx.request), async () => handle(ctx))
