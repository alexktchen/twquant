import { useEffect, useMemo, useState } from 'react'
import {
  fetchHealth,
  fetchHeartbeat,
  fetchHolidayCalendar,
  fetchIngestGaps,
  fetchIngestJobs,
  fetchIngestMatrix,
  fetchIngestTracks,
  fetchMeta,
  startHolidayFetch,
  startIngestFetch,
  startIngestJob,
  startPipelineJob,
  type CellState,
  type Heartbeat,
  type HolidayCalendar,
  type IngestGap,
  type IngestGaps,
  type IngestJob,
  type IngestJobs,
  type IngestMatrix,
  type IngestTrack,
} from './api'
import { FALLBACK } from './fallback'
import { MetaCtx, useMeta, type MetaCtxT } from './hooks'
import { StockFactorsPage } from './stock'
import { Kpi, Panel, Skeleton } from './ui'

/* ---------- 擷取後台（熱力圖 + 漏抓清單 + 一鍵補抓 / 引導指令）---------- */
// 運維色盤：綠=有／橙=擷取失敗／紅=漏抓／暗=尚未涵蓋。刻意不套台股紅漲綠跌（紅在此＝缺）。
const CELL: Record<CellState, string> = {
  present: 'var(--good)',
  missing: 'var(--crit)',
  failed: 'var(--warn)',
  none: 'var(--panel-2)',
}
const STATE_ZH: Record<CellState, string> = { present: '有資料', missing: '漏抓', failed: '擷取失敗', none: '未涵蓋' }

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setOk(true)
          setTimeout(() => setOk(false), 1200)
        })
      }}
      className="shrink-0 px-2 py-1 text-[11px] rounded border border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)] hover:border-[var(--accent)] transition-colors"
    >
      {ok ? '已複製 ✓' : '複製指令'}
    </button>
  )
}

function HeatLegend() {
  const items: [CellState, string][] = [
    ['present', '有資料'],
    ['missing', '漏抓'],
    ['failed', '擷取失敗'],
    ['none', '未涵蓋'],
  ]
  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px] text-[var(--muted)]">
      {items.map(([s, l]) => (
        <span key={s} className="flex items-center gap-1">
          <span className="inline-block rounded-[2px]" style={{ width: 11, height: 11, background: CELL[s] }} />
          {l}
        </span>
      ))}
      <span className="text-[10px]">（運維色，與台股紅漲綠跌無關）</span>
    </div>
  )
}

function Heatmap({ mx }: { mx: IngestMatrix }) {
  const { datasets, dates, cells } = mx
  if (dates.length === 0) return <div className="text-[13px] text-[var(--muted)]">資料湖尚無交易日</div>
  return (
    <div className="flex flex-col gap-2">
      <HeatLegend />
      <div className="overflow-x-auto">
        <div className="inline-flex flex-col gap-1">
          {datasets.map((ds) => (
            <div key={ds.key} className="flex items-center gap-2">
              <div className="w-20 shrink-0 text-[12px] text-[var(--fg)] whitespace-nowrap">{ds.label}</div>
              <div className="flex gap-[2px]">
                {dates.map((d) => {
                  const s = cells[ds.key]?.[d] ?? 'none'
                  return (
                    <div
                      key={d}
                      title={`${d}　${ds.label}　${STATE_ZH[s]}`}
                      className="rounded-[2px]"
                      style={{ width: 13, height: 13, background: CELL[s] }}
                    />
                  )
                })}
              </div>
              <div
                className="ml-2 shrink-0 text-[11px] mono tnum"
                style={{ color: ds.window_missing ? 'var(--crit)' : 'var(--muted)' }}
              >
                {ds.window_missing ? `缺 ${ds.window_missing}` : '齊'}
              </div>
            </div>
          ))}
          <div className="flex items-center gap-2 mt-0.5">
            <div className="w-20 shrink-0" />
            <div className="flex justify-between text-[10px] mono text-[var(--muted)]" style={{ width: dates.length * 15 }}>
              <span>{dates[0]}</span>
              <span>{dates[dates.length - 1]}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function GapList({ gaps, busy, onRun }: { gaps: IngestGap[]; busy: boolean; onRun: (g: IngestGap) => void }) {
  if (gaps.length === 0) return <div className="text-[13px] text-[var(--good)]">✓ 涵蓋區間內無漏抓</div>
  return (
    <div className="flex flex-col">
      {gaps.map((g, i) => (
        <div key={`${g.table}-${g.source}-${g.from}-${i}`} className="py-2.5 border-b border-[var(--line)] last:border-0">
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-[var(--fg)]">{g.label}</span>
              <span className="mono text-[var(--muted)]">
                {g.from}
                {g.to !== g.from ? ` ~ ${g.to}` : ''}
              </span>
              <span
                className="text-[11px] px-1.5 py-0.5 rounded bg-[var(--panel-2)]"
                style={{ color: g.kind === 'failed' ? 'var(--warn)' : 'var(--crit)' }}
              >
                {g.kind === 'failed' ? '失敗' : '漏抓'} {g.days} 日
              </span>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                onClick={() => onRun(g)}
                disabled={busy}
                className="px-2 py-1 text-[11px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] transition-colors disabled:opacity-40 disabled:pointer-events-none"
              >
                {busy ? '補抓中…' : '一鍵補抓'}
              </button>
              <CopyBtn text={g.command} />
            </div>
          </div>
          <code className="block mt-1.5 px-2 py-1.5 rounded bg-[var(--panel-2)] text-[11px] mono text-[var(--fg)] overflow-x-auto whitespace-nowrap">
            {g.command}
          </code>
          {g.warn_ban && (
            <div className="mt-1 text-[10px] text-[var(--warn)] leading-relaxed">
              ⚠ 跨度大：TWSE 免費端點連續大量回補易被暫時封鎖（D-015）→ 建議改走 FinMind 或分塊、加長間隔
            </div>
          )}
          {g.detail && <div className="mt-1 text-[10px] text-[var(--muted)]">失敗原因：{g.detail}</div>}
        </div>
      ))}
    </div>
  )
}

// 補抓工作狀態色：執行中=accent、成功=綠、失敗=紅（運維色）。
const JOB_TONE: Record<IngestJob['status'], string> = {
  running: 'var(--accent)',
  success: 'var(--good)',
  failed: 'var(--crit)',
}
const JOB_ZH: Record<IngestJob['status'], string> = { running: '執行中', success: '成功', failed: '失敗' }

function JobList({ jobs }: { jobs: IngestJob[] }) {
  return (
    <div className="flex flex-col">
      {jobs.map((j) => (
        <details key={j.id} open={j.status === 'running'} className="py-2 border-b border-[var(--line)] last:border-0">
          <summary className="flex flex-wrap items-center gap-2 cursor-pointer text-[13px]">
            <span
              className="text-[11px] px-1.5 py-0.5 rounded bg-[var(--panel-2)]"
              style={{ color: JOB_TONE[j.status] }}
            >
              {JOB_ZH[j.status]}
              {j.status === 'failed' && j.exit_code !== null ? ` (exit ${j.exit_code})` : ''}
            </span>
            <span className="text-[var(--fg)]">{j.label}</span>
            <code className="mono text-[11px] text-[var(--muted)]">{j.command}</code>
            <span className="ml-auto text-[10px] mono text-[var(--muted)]">
              {j.started.slice(11, 19)}
              {j.ended ? ` → ${j.ended.slice(11, 19)}` : ''}
            </span>
          </summary>
          <pre className="mt-1.5 px-2 py-1.5 rounded bg-[var(--panel-2)] text-[10px] mono text-[var(--muted)] leading-relaxed overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
            {j.tail.length ? j.tail.join('\n') : '（尚無輸出）'}
          </pre>
        </details>
      ))}
    </div>
  )
}

/* ---------- 手動抓取歷史資料（指定資料集 + 期間，伺服器端跑 twquant CLI）---------- */
const p2 = (n: number) => String(n).padStart(2, '0')
function todayISO(): string {
  const d = new Date() // 表單預設值用瀏覽器本地日期即可（非時間點語意）
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
}
function monthsBackISO(baseISO: string, n: number): string {
  const [y, m, d] = baseISO.split('-').map(Number)
  const back = new Date(y, m - 1 - n, d || 1)
  return `${back.getFullYear()}-${p2(back.getMonth() + 1)}-${p2(back.getDate())}`
}

/* ---------- 一鍵跑當日全部資料（排程錯過時的介面補跑，＝排程 16:40 同一 pipeline）---------- */
function PipelinePanel({
  hb,
  busy,
  onSubmitted,
}: {
  hb: Heartbeat | null
  busy: boolean
  onSubmitted: () => void
}) {
  const [date, setDate] = useState('')
  const [sending, setSending] = useState<'today' | 'date' | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)

  // 「指定交易日」欄預設今日；台灣使用者瀏覽器本地日即台北日（一鍵今日不看此欄）。
  useEffect(() => {
    setDate(todayISO())
  }, [])

  // watchdog：heartbeat 日 ≠ 最新行情日 → 排程可能錯過（未跑到最新交易日）。
  const never = hb != null && hb.heartbeat == null
  const missed = hb != null && hb.heartbeat != null && !hb.watchdog_ok
  const stale = never || missed

  // day 省略 → 後端以今日台北補跑（一鍵今日）；給定 → 補跑指定交易日。
  const run = (which: 'today' | 'date', day?: string) => {
    if (which === 'date' && !day) return
    setSending(which)
    setMsg(null)
    startPipelineJob(day)
      .then((j) => {
        setSending(null)
        setMsg({ kind: 'ok', text: `已排入：${j.label}（於下方「背景工作」看即時進度）` })
        onSubmitted()
      })
      .catch((e: unknown) => {
        setSending(null)
        setMsg({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
      })
  }

  const anyBusy = busy || sending != null
  return (
    <Panel
      title="一鍵跑當日全部資料"
      sub="擷取行情＋法人＋融資券 → 品質閘門 → 券商買賣超（富邦DJ 全市場分點）→ 訊號 → 供應層（因子物化）→ 選股池快照（＝排程 16:40 同一流程）"
    >
      {hb && (
        <div
          className="mb-3 px-3 py-2 rounded text-[12px] leading-relaxed"
          style={{ background: 'var(--panel-2)', color: stale ? 'var(--warn)' : 'var(--good)' }}
        >
          {never
            ? '⚠ 尚無 heartbeat：pipeline 從未執行，按下方按鈕跑一次建立每日狀態。'
            : missed
              ? `⚠ 排程可能已錯過：heartbeat 停在 ${hb.heartbeat?.day}，最新行情日 ${hb.latest ?? '—'}（${hb.watchdog_reason}）。按下方一鍵補上今日全部資料。`
              : `✓ pipeline 已跑到 ${hb.heartbeat?.day}（與最新行情日一致）。`}
        </div>
      )}
      {/* 主動作：一鍵跑今日（不需選日期，後端以台北今日補跑）。*/}
      <button
        onClick={() => run('today')}
        disabled={anyBusy}
        className="px-4 py-2 text-[13px] font-medium rounded border border-[var(--accent)] bg-[var(--accent)] text-[var(--bg)] hover:opacity-90 disabled:opacity-40 disabled:pointer-events-none transition-opacity"
      >
        {sending === 'today' ? '排入中…' : busy ? '有工作進行中…' : '▶ 一鍵跑今日全部資料'}
      </button>
      {/* 次動作：補跑過去某個交易日（如連假後補多天）。*/}
      <div className="flex flex-wrap items-end gap-2 mt-3 pt-3 border-t border-[var(--line)]">
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          指定交易日
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          />
        </label>
        <button
          onClick={() => run('date', date)}
          disabled={anyBusy || !date}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] disabled:opacity-40 disabled:pointer-events-none transition-colors"
        >
          {sending === 'date' ? '排入中…' : '補跑此日'}
        </button>
      </div>
      <p className="mt-2.5 text-[11px] text-[var(--muted)] leading-relaxed">
        含富邦DJ 全市場券商分點爬取，整趟約 10 分鐘（執行中於下方「背景工作」看即時進度）。
        非交易日或當日行情尚未公布時，品質閘門會擋下訊號（降級、沿用前日）、券商分點由探測快速跳過——與排程行為一致。
        重跑同一日冪等（券商續傳、供應層/選股池 upsert）。
      </p>
      {msg && (
        <div
          className="mt-2 text-[12px]"
          style={{ color: msg.kind === 'ok' ? 'var(--good)' : 'var(--crit)' }}
        >
          {msg.kind === 'ok' ? '✓ ' : '補跑失敗：'}
          {msg.text}
        </div>
      )}
    </Panel>
  )
}

/* ---------- 手動抓取歷史資料（指定資料集 + 期間，伺服器端跑 twquant CLI）---------- */
function FetchForm({
  tracks,
  latest,
  busy,
  onSubmitted,
}: {
  tracks: IngestTrack[]
  latest: string | null
  busy: boolean
  onSubmitted: () => void
}) {
  const [key, setKey] = useState('prices_daily:twse')
  const track = tracks.find((t) => `${t.table}:${t.source}` === key) ?? tracks[0]
  const unit = track?.unit ?? 'date'
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [sending, setSending] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)

  // 切資料集或首次拿到最新行情日 → 重設期間預設值（日軌預設近一個月、月軌預設當月）。
  useEffect(() => {
    const base = latest ?? todayISO()
    if (unit === 'month') {
      setFrom(base.slice(0, 7))
      setTo(base.slice(0, 7))
    } else {
      setFrom(monthsBackISO(base, 1))
      setTo(base)
    }
    setMsg(null)
  }, [key, unit, latest])

  const run = (force: boolean) => {
    if (!track || !from || !to) return
    setSending(true)
    setMsg(null)
    startIngestFetch({ table: track.table, source: track.source, from, to, force })
      .then((j) => {
        setSending(false)
        setMsg({ kind: 'ok', text: `已排入：${j.label}（於下方「背景工作」看進度）` })
        onSubmitted()
      })
      .catch((e: unknown) => {
        const text = e instanceof Error ? e.message : String(e)
        // D-015：TWSE 行情大區間需明示 force → 後端回 400，這裡確認後帶 force 重送。
        if (!force && /D-015|force/i.test(text) && window.confirm(`${text}\n\n仍要強制執行？`)) {
          run(true)
          return
        }
        setSending(false)
        setMsg({ kind: 'err', text })
      })
  }

  const disabled = busy || sending || !track || !from || !to
  return (
    <Panel
      title="手動抓取歷史資料"
      sub="指定資料集 + 期間 · 伺服器端跑 twquant CLI · 單一併發"
    >
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          資料集
          <select
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="px-2 py-1.5 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          >
            {tracks.map((t) => (
              <option key={`${t.table}:${t.source}`} value={`${t.table}:${t.source}`}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          起
          <input
            type={unit === 'month' ? 'month' : 'date'}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          迄
          <input
            type={unit === 'month' ? 'month' : 'date'}
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="px-2 py-1 text-[12px] mono rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
          />
        </label>
        <button
          onClick={() => run(false)}
          disabled={disabled}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] disabled:opacity-40 disabled:pointer-events-none transition-colors"
        >
          {sending ? '排入中…' : busy ? '有工作進行中…' : '抓取'}
        </button>
      </div>
      <p className="mt-2.5 text-[11px] text-[var(--muted)] leading-relaxed">
        台股行情為全市場擷取（單日含所有個股）——抓完某段期間後，即可到「個股因子 / 市場 K 線」頁查任一檔的歷史 K 線。
        此表單不受漏抓清單限制，可往前延伸歷史或初次建置資料湖。
      </p>
      {msg && (
        <div
          className="mt-2 text-[12px]"
          style={{ color: msg.kind === 'ok' ? 'var(--good)' : 'var(--crit)' }}
        >
          {msg.kind === 'ok' ? '✓ ' : '抓取失敗：'}
          {msg.text}
        </div>
      )}
    </Panel>
  )
}

/* ---------- 官方開休市日曆（TWSE market_holidays）---------- */
function HolidayPanel({
  hc,
  busy,
  onFetch,
}: {
  hc: HolidayCalendar | null
  busy: boolean
  onFetch: () => void
}) {
  const today = todayISO()
  const upcoming = (hc?.closed ?? []).filter((r) => r.date >= today).slice(0, 8)
  const cc = hc?.cross_check
  const nMiss = cc?.missing.length ?? 0
  const nUnexp = cc?.unexpected.length ?? 0
  return (
    <Panel
      title="官方開休市日曆"
      sub={hc?.available ? `${hc.sessions} 開市日 · ${hc.first}~${hc.last}` : 'TWSE 官方公布'}
    >
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          onClick={onFetch}
          disabled={busy}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--bg)] disabled:opacity-40 disabled:pointer-events-none transition-colors"
        >
          {busy ? '有工作進行中…' : '抓官方開休市日曆'}
        </button>
        <span className="text-[11px] text-[var(--muted)]">
          TWSE OpenAPI · 當前公布年度 · 平日−官方休市＝前瞻交易日曆
        </span>
      </div>
      {!hc?.available ? (
        <div className="text-[13px] text-[var(--muted)]">
          尚未擷取——按上方「抓官方開休市日曆」，或 CLI{' '}
          <code className="mono text-[var(--fg)]">twquant ingest-holidays</code>。
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="text-[12px]">
            官方 vs 觀測（{cc?.range ? `${cc.range[0]}~${cc.range[1]}` : '尚無行情'}）：
            {cc?.range == null ? (
              <span className="text-[var(--muted)]"> 尚無行情可核對</span>
            ) : nMiss === 0 && nUnexp === 0 ? (
              <span style={{ color: 'var(--good)' }}> ✓ 涵蓋年度內完全一致</span>
            ) : (
              <span style={{ color: 'var(--warn)' }}>
                {nMiss ? ` 官方開市卻無行情 ${nMiss} 日` : ''}
                {nUnexp ? ` 有行情卻官方判休市 ${nUnexp} 日` : ''}
              </span>
            )}
          </div>
          <div>
            <div className="eyebrow mb-1">近期休市（{upcoming.length}）</div>
            {upcoming.length === 0 ? (
              <div className="text-[12px] text-[var(--muted)]">今年後續無休市註記</div>
            ) : (
              <table className="w-full text-[12px] mono">
                <tbody>
                  {upcoming.map((r) => (
                    <tr key={r.date} className="border-b border-[var(--line)] last:border-0">
                      <td className="py-1 tnum text-[var(--fg)]">{r.date}</td>
                      <td className="py-1 pl-3 text-[var(--muted)]">{r.weekday ? `（${r.weekday}）` : ''}</td>
                      <td className="py-1 pl-3 text-[var(--fg)]">{r.name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}

function Ingest() {
  const { meta, live } = useMeta()
  const [mx, setMx] = useState<IngestMatrix | null>(null)
  const [gp, setGp] = useState<IngestGaps | null>(null)
  const [hb, setHb] = useState<Heartbeat | null>(null)
  const [jb, setJb] = useState<IngestJobs | null>(null)
  const [hc, setHc] = useState<HolidayCalendar | null>(null)
  const [tracks, setTracks] = useState<IngestTrack[]>([])
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [at, setAt] = useState('')

  useEffect(() => {
    if (!live) return
    let alive = true
    const load = () => {
      fetchIngestMatrix().then((d) => alive && setMx(d)).catch(() => {})
      fetchIngestGaps().then((d) => alive && setGp(d)).catch(() => {})
      fetchHeartbeat().then((d) => alive && setHb(d)).catch(() => {})
      fetchHolidayCalendar().then((d) => alive && setHc(d)).catch(() => {})
      if (alive) setAt(new Date().toLocaleTimeString('zh-TW', { hour12: false }))
    }
    load()
    const id = setInterval(load, 30000) // 每 30 秒即時更新（ops 看板）
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [live, tick])

  // 補抓工作獨立輪詢（3 秒：追執行中的輸出尾行）；工作一結束立即刷新矩陣/缺口。
  useEffect(() => {
    if (!live) return
    let alive = true
    let busyPrev = false
    const poll = () =>
      fetchIngestJobs()
        .then((d) => {
          if (!alive) return
          setJb(d)
          if (busyPrev && !d.busy) setTick((t) => t + 1)
          busyPrev = d.busy
        })
        .catch(() => {})
    poll()
    const id = setInterval(poll, 3000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [live])

  // 資料軌白名單（手動抓取表單的資料集選單）：載一次即可。
  useEffect(() => {
    if (!live) return
    let alive = true
    fetchIngestTracks()
      .then((t) => alive && setTracks(t))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [live])

  const busy = jb?.busy ?? false
  const refreshJobs = () => {
    fetchIngestJobs()
      .then((d) => setJb(d))
      .catch(() => {})
  }
  const runGap = (g: IngestGap) => {
    if (
      g.warn_ban &&
      !window.confirm(
        `跨度 ${g.days} 交易日：TWSE 免費端點連續大量回補易被暫時封鎖（D-015），建議分塊或改走 FinMind。仍要強制執行？`,
      )
    )
      return
    setErr('')
    startIngestJob(g, g.warn_ban)
      .then(refreshJobs)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
  }
  const runHolidayFetch = () => {
    setErr('')
    startHolidayFetch()
      .then(refreshJobs)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
  }

  if (!live) {
    return (
      <Panel title="資料擷取後台" sub="離線快照">
        <p className="text-[13px] text-[var(--muted)] leading-relaxed">
          後台熱力圖與漏抓清單需即時監控 API（<code className="mono">twquant serve</code>）；目前為內嵌快照、無法即時計算缺口。以下為快照涵蓋概況：
        </p>
        <table className="w-full text-[13px] mono mt-3">
          <tbody>
            {meta.coverage.map((c) => (
              <tr key={c.label} className="border-b border-[var(--line)] last:border-0">
                <td className="py-2 text-[var(--fg)]">{c.label}</td>
                <td className="py-2 tnum text-right text-[var(--muted)]">{c.days} 交易日</td>
                <td className="py-2 pl-3 text-right text-[11px] text-[var(--muted)]">{c.span}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="最新行情日" value={hb?.latest ?? mx?.latest ?? '—'} />
        <Kpi label="最後擷取" value={mx?.last_run ? mx.last_run.slice(0, 16).replace('T', ' ') : '—'} />
        <Kpi
          label="Heartbeat"
          value={hb?.heartbeat?.day ?? '—'}
          tone={hb ? (hb.watchdog_ok ? 'var(--good)' : 'var(--warn)') : undefined}
        />
        <Kpi
          label="漏抓合計"
          value={gp ? `${gp.total_missing} 日` : '…'}
          tone={gp ? (gp.total_missing ? 'var(--crit)' : 'var(--good)') : undefined}
        />
      </div>
      <div className="flex items-center justify-between">
        <div className="text-[12px] text-[var(--muted)]">一鍵補抓在伺服器端執行 twquant CLI（單一併發）· 每 30 秒自動更新</div>
        <button
          onClick={() => setTick((t) => t + 1)}
          className="px-3 py-1.5 text-[12px] rounded border border-[var(--line)] text-[var(--muted)] hover:text-[var(--fg)] hover:border-[var(--accent)] transition-colors"
        >
          重新整理{at ? ` · ${at}` : ''}
        </button>
      </div>
      {err && <div className="text-[12px] text-[var(--crit)]">補抓失敗：{err}</div>}
      <PipelinePanel hb={hb} busy={busy} onSubmitted={refreshJobs} />
      {tracks.length > 0 && (
        <FetchForm
          tracks={tracks}
          latest={hb?.latest ?? mx?.latest ?? meta.as_of ?? null}
          busy={busy}
          onSubmitted={refreshJobs}
        />
      )}
      {jb && jb.jobs.length > 0 && (
        <Panel title="背景工作" sub="伺服器端執行 twquant CLI（補抓／每日 pipeline）· 單一併發 · 重啟 API 後清空">
          <JobList jobs={jb.jobs} />
        </Panel>
      )}
      <HolidayPanel hc={hc} busy={busy} onFetch={runHolidayFetch} />
      <Panel title="擷取涵蓋熱力圖" sub={mx ? `近 ${mx.dates.length} 交易日 · dataset × 日` : '載入中…'}>
        {mx ? <Heatmap mx={mx} /> : <Skeleton height={160} />}
      </Panel>
      <Panel
        title="漏抓清單 · 一鍵補抓"
        sub={gp ? `${gp.runs} 段 · 共 ${gp.total_missing} 交易日` : '載入中…'}
      >
        {gp ? <GapList gaps={gp.gaps} busy={busy} onRun={runGap} /> : <Skeleton height={120} />}
      </Panel>
    </div>
  )
}

/* ---------- shell ---------- */
const PAGES = [
  { id: 'stock', label: '個股因子', el: <StockFactorsPage /> },
  { id: 'ingest', label: '資料後台', el: <Ingest /> },
]

function Shell() {
  const { meta, live } = useMeta()
  // 預設落在個股因子頁（PG 直查、毫秒級）；總覽等讀湖頁面切到才算（分鐘級）
  const [page, setPage] = useState('stock')
  const cur = useMemo(() => PAGES.find((p) => p.id === page)!, [page])
  return (
    <div className="min-h-screen md:flex text-[var(--fg)]">
      {/* 桌面側欄（md 起）；手機改用下方頂欄 */}
      <aside className="hidden md:flex w-52 shrink-0 border-r border-[var(--line)] bg-[var(--panel)] flex-col sticky top-0 h-screen">
        <div className="px-4 py-4 border-b border-[var(--line)]">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: 'var(--accent)' }} />
            <span className="font-semibold tracking-wide">twquant</span>
          </div>
          <div className="text-[11px] text-[var(--muted)] mt-0.5">台股量化平台 · 監控</div>
        </div>
        <nav className="flex flex-col p-2 gap-0.5">
          {PAGES.map((p) => (
            <button
              key={p.id}
              onClick={() => setPage(p.id)}
              className={`text-left px-3 py-2 rounded text-[13px] transition-colors ${
                page === p.id ? 'bg-[var(--panel-2)] text-[var(--fg)] font-medium' : 'text-[var(--muted)] hover:text-[var(--fg)] hover:bg-[var(--panel-2)]'
              }`}
            >
              {p.id === page && <span className="inline-block w-1 h-1 rounded-full mr-2 align-middle" style={{ background: 'var(--accent)' }} />}
              {p.label}
            </button>
          ))}
        </nav>
        <div className="mt-auto p-4 text-[10px] text-[var(--muted)] border-t border-[var(--line)] leading-relaxed">
          <div>資料 {live ? '即時' : '快照'} {meta.as_of ?? '—'}</div>
          <div>TWSE · TPEX · MOPS</div>
          <div className="mt-1">
            <span style={{ color: 'var(--up)' }}>紅漲</span> · <span style={{ color: 'var(--down)' }}>綠跌</span>（台股慣例）
          </div>
        </div>
      </aside>

      {/* 手機頂欄（mobile-first）：品牌＋狀態列與橫向捲動頁籤，sticky 常駐 */}
      <div className="md:hidden sticky top-0 z-30 border-b border-[var(--line)] bg-[var(--bg)]/95 backdrop-blur">
        <div className="flex items-center gap-2 px-3 pt-2">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: 'var(--accent)' }} />
          <span className="font-semibold tracking-wide text-[14px]">twquant</span>
          <span className="ml-auto flex items-center gap-1.5 text-[10px] mono text-[var(--muted)] whitespace-nowrap">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{ background: live ? 'var(--good)' : 'var(--muted)' }}
              title={live ? '即時連線 API' : '離線快照 fallback'}
            />
            {live ? '即時' : '快照'} {meta.as_of ?? '—'}
          </span>
        </div>
        <nav className="flex gap-1 overflow-x-auto hscroll px-2 py-2">
          {PAGES.map((p) => (
            <button
              key={p.id}
              onClick={() => setPage(p.id)}
              className={`shrink-0 whitespace-nowrap px-2.5 py-1 text-[12px] rounded-[2px] border transition-colors ${
                page === p.id
                  ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--panel-2)]'
                  : 'border-transparent text-[var(--muted)]'
              }`}
            >
              {p.label}
            </button>
          ))}
        </nav>
      </div>

      <main className="flex-1 min-w-0">
        <header className="hidden md:flex px-6 py-4 border-b border-[var(--line)] items-baseline justify-between">
          <h1 className="text-[17px] font-semibold tracking-wide">{cur.label}</h1>
          <span className="text-[12px] mono text-[var(--muted)] flex items-center gap-2">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{ background: live ? 'var(--good)' : 'var(--muted)' }}
              title={live ? '即時連線 API' : '離線快照 fallback'}
            />
            {live ? '即時 · 讀資料湖' : '快照 · 內嵌 data.json'} · lightweight-charts
          </span>
        </header>
        <div className="p-3 md:p-6 max-w-[1180px]">{cur.el}</div>
      </main>
    </div>
  )
}

export default function App() {
  // 開頁零等待（使用者裁決，2026-07-10）：立即以內嵌 meta 渲染殼，/api/health（毫秒級）
  // 判定 live；meta 讀資料湖可能要分鐘級，改背景載入、到了再補 as_of/涵蓋等欄位。不快取。
  const [ctx, setCtx] = useState<MetaCtxT>({ meta: FALLBACK.meta, live: false })
  useEffect(() => {
    let alive = true
    fetchHealth()
      .then(() => alive && setCtx((c) => (c.live ? c : { ...c, live: true })))
      .catch(() => {}) // 探針失敗 → 維持快照模式；meta 若成功仍會翻 live
    fetchMeta()
      .then((m) => alive && setCtx({ meta: m, live: true }))
      .catch(() => {}) // API 不可達 → 維持內嵌 meta
    return () => {
      alive = false
    }
  }, [])
  return (
    <MetaCtx.Provider value={ctx}>
      <Shell />
    </MetaCtx.Provider>
  )
}
