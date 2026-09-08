import type { StockEvent } from './stockRecommendations'

/** The trade date an event belongs to, kept as the snapshot's own Taipei calendar day. */
export function eventDay(event: StockEvent): string {
  return event.as_of.slice(0, 10)
}

/** Marker shapes follow PRD-003 §14: raw ML picks are dots, insight picks are arrows. */
export type MarkerKind = 'ml_candidate' | 'insight_pick' | 'watch' | 'excluded'

export type MarkerGroup = {
  /** Trading session the marker is drawn on. */
  session: string
  /** Publication day the events actually belong to; detail always shows this. */
  day: string
  /** True when the publication day had no bar and the marker moved to a later session. */
  mapped: boolean
  kind: MarkerKind
  events: StockEvent[]
}

export type EventMapping = {
  groups: MarkerGroup[]
  /** Events with no session at or after their publication day; listed but never plotted. */
  pending: StockEvent[]
}

/** Stable identity of one recommendation event; a repeat must never draw a second marker. */
export function eventId(event: StockEvent): string {
  return `${event.run_id}:${event.stock_id}`
}

/** Classify one event without ever promoting a raw ML candidate into a published pick. */
export function markerKind(event: StockEvent): MarkerKind {
  if (event.insight_run_id !== null) return 'insight_pick'
  if (event.bucket.includes('exclud')) return 'excluded'
  if (event.bucket === 'watch') return 'watch'
  return 'ml_candidate'
}

/** The strongest kind present decides the group's shape; insight outranks a raw candidate. */
function groupKind(events: StockEvent[]): MarkerKind {
  const order: MarkerKind[] = ['insight_pick', 'ml_candidate', 'watch', 'excluded']
  const kinds = new Set(events.map(markerKind))
  return order.find(kind => kinds.has(kind)) ?? 'ml_candidate'
}

/**
 * Map publication days onto chart sessions per PRD-003 §14.
 *
 * A day with its own bar keeps that bar. A holiday or halted day moves forward to the next
 * session and is flagged, never backwards onto the previous bar. Weekly bars absorb the day
 * into the week that contains it. Days with no later session stay pending.
 */
export function mapEventsToSessions(
  events: StockEvent[],
  sessions: string[],
  options: { weekly?: boolean } = {},
): EventMapping {
  const seen = new Set<string>()
  const unique = events.filter(event => {
    const id = eventId(event)
    if (seen.has(id)) return false
    seen.add(id)
    return true
  })
  const ordered = [...sessions].sort()
  const byDay = new Map<string, { session: string; mapped: boolean; events: StockEvent[] }>()
  const pending: StockEvent[] = []
  for (const event of unique) {
    const day = eventDay(event)
    const session = options.weekly
      ? [...ordered].reverse().find(bar => bar <= day)
      : ordered.find(bar => bar >= day)
    if (session === undefined) {
      pending.push(event)
      continue
    }
    const bucket = byDay.get(day) ?? { session, mapped: session !== day, events: [] }
    bucket.events.push(event)
    byDay.set(day, bucket)
  }
  const groups = [...byDay.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([day, bucket]) => ({
      day,
      session: bucket.session,
      mapped: bucket.mapped,
      kind: groupKind(bucket.events),
      // Same-day events stay in publication order so an expanded group keeps every event_id.
      events: bucket.events,
    }))
  return { groups, pending }
}
