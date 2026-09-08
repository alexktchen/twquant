import { useEffect, useState, type JSX } from 'react'
import { StockFactorsPage } from '../stock'
import { routeHref, type PlatformRoute } from './routes'
import type { StockTabId } from './stockTabs'
import { fetchStockEvents, type StockEvent } from './stockRecommendations'

type State = { events: StockEvent[]; loading: boolean; error: string | null }

/** Own the recommendation events for one stock so both the chart and the list use one read. */
export function StockResearch({ route }: { route: PlatformRoute }): JSX.Element {
  const sid = route.entityId
  const [state, setState] = useState<State>({ events: [], loading: Boolean(sid), error: null })
  useEffect(() => {
    if (!sid) { setState({ events: [], loading: false, error: null }); return }
    let alive = true
    setState({ events: [], loading: true, error: null })
    fetchStockEvents(sid).then(
      events => alive && setState({ events, loading: false, error: null }),
      error => alive && setState({ events: [], loading: false, error: error instanceof Error ? error.message : String(error) }),
    )
    return () => { alive = false }
  }, [sid])
  const navigate = (entityId: string | undefined, patch: Record<string, string>) => {
    const query = new URLSearchParams(route.query)
    for (const [key, value] of Object.entries(patch)) query.set(key, value)
    window.location.hash = routeHref('stocks', entityId, query)
  }
  return <StockFactorsPage
    selectedSid={sid}
    onSelectSid={sid => { window.location.hash = routeHref('stocks', sid, route.query) }}
    tab={route.query.get('tab')}
    onSelectTab={(tab: StockTabId) => navigate(sid, { tab })}
    dateContext={route.query.get('date')}
    events={state.events}
    eventsLoading={state.loading}
    eventsError={state.error}
  />
}
