import { useSyncExternalStore, type ReactNode, type JSX } from 'react'
import { parseRoute, platformPages, routeHref, type PlatformRoute } from './routes'
import './platform.css'

function subscribe(listener: () => void): () => void {
  window.addEventListener('hashchange', listener)
  return () => window.removeEventListener('hashchange', listener)
}

/** Navigation grouped the way routes.ts declares it, in declaration order. */
const navGroups = platformPages.reduce<{ group: string; pages: (typeof platformPages)[number][] }[]>((groups, page) => {
  const last = groups[groups.length - 1]
  if (last?.group === page.group) last.pages.push(page)
  else groups.push({ group: page.group, pages: [page] })
  return groups
}, [])

/** Render the research workspace while keeping page identity in the URL. */
export function PlatformShell({ live, asOf, stock, research, daily, operations }: {
  live: boolean; asOf: string | null; stock: (route: PlatformRoute) => ReactNode; research: (route: PlatformRoute) => ReactNode; daily: (route: PlatformRoute) => ReactNode; operations: (route: PlatformRoute) => ReactNode
}): JSX.Element {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash, () => '')
  const route = parseRoute(hash)
  const current = platformPages.find(p => p.id === route.page)
  return <div className="platform-shell">
    <a className="platform-skip" href="#platform-content" onClick={event => { event.preventDefault(); document.getElementById('platform-content')?.focus() }}>跳至內容</a>
    {/* 全寬儀表板：導覽在頂列而非側欄，面板格才用得到整個視窗寬度。 */}
    <header className="platform-topbar">
      <a className="platform-brand" href={routeHref('overview')} title="台股研究工作台">twquant<span> /</span></a>
      <nav aria-label="平台導覽">{navGroups.map(({ group, pages }) => <div key={group} className="platform-nav-cluster">
        <span className="platform-nav-group">{group}</span>
        {pages.map(p => <a key={p.id} href={routeHref(p.id)} aria-current={route.page === p.id ? 'page' : undefined}>{p.label}</a>)}
      </div>)}</nav>
      <div className="platform-connection"><span className={live ? 'platform-live' : 'platform-offline'}>{live ? 'API 已連線' : 'API 未連線 · 快照可用'}</span><span>資料至 {asOf ?? '未知'}</span></div>
    </header>
    <main id="platform-content" className="platform-content" tabIndex={-1}>
      <div className="platform-page-heading"><div><div className="platform-eyebrow">RESEARCH WORKSPACE · {current?.group ?? '平台'}</div><h1>{current?.label ?? '找不到這個頁面'}</h1><p>{current?.description ?? '請確認連結，或回到總覽繼續。'}</p></div><span className="platform-mode">研究模式</span></div>
      <div className="platform-context"><span>查詢日期：{route.query.get('date') ?? '最新可得'}</span>{route.query.get('model') && <span>模型：{route.query.get('model')}</span>}{route.query.get('run') && <span>推薦版本：{route.query.get('run')}</span>}</div>
      {route.page === 'stocks' ? stock(route) : route.page === 'operations' ? operations(route) : route.page === 'models' || route.page === 'backtests' || route.page === 'industries' || route.page === 'insights' || route.page === 'watchlists' ? research(route) : route.page === 'overview' || route.page === 'recommendations' ? daily(route) : route.page === 'not-found' ? <a href={routeHref('overview')}>回今日總覽 →</a> : <PlatformPending route={route} />}
    </main>
    <footer className="platform-footer"><span>本平台為個人研究整理，僅供參考，不構成投資建議；數字以各面板標示的資料日與來源為準。</span><strong>twquant 台股研究工作台</strong></footer>
  </div>
}

/** Show explicit integration state until a page has a real data source. */
function PlatformPending({ route }: { route: PlatformRoute }) {
  return <section className="platform-empty" aria-label="資料整合狀態"><div className="platform-eyebrow">{route.entityId ?? '資料狀態'}</div>
    <h2>資料來源尚未接入</h2><p>這個頁面的研究資料正在整合。完成後即可查看對應版本與紀錄。</p>
    <div className="platform-actions"><a href={routeHref('stocks')}>查看個股行情 →</a><a href={routeHref('operations')}>檢查資料與工作 →</a></div>
  </section>
}
