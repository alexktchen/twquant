// 選擇個股：搜尋全部上市櫃普通股 + 追蹤股 + 宇宙綜合排名熱門列。
import { useMemo, useState } from 'react'
import type { StockMetaMap } from '../api'
import { labelOf } from '../theme'
import { ChipRow, TPanel, chip } from './ui'

export type HotRow = { sid: string; name: string; rank: number; pct: number | null }

export function StockPicker({
  sid,
  setSid,
  mm,
  watch,
  toggleWatch,
  hot,
  hotIsRanked,
  list,
  live,
}: {
  sid: string
  setSid: (s: string) => void
  mm: StockMetaMap
  watch: string[]
  toggleWatch: (s: string) => void
  hot: HotRow[]
  hotIsRanked: boolean
  list: string[]
  live: boolean
}) {
  const [q, setQ] = useState('')
  const allStocks = useMemo(
    () =>
      Object.entries(mm)
        .filter(([c]) => /^[1-9]\d{3}$/.test(c))
        .map(([c, e]) => [c, e.name ?? ''] as [string, string])
        .sort((a, b) => a[0].localeCompare(b[0])),
    [mm],
  )
  const pick = (raw: string) => {
    const t = raw.trim()
    const code = t.match(/^([1-9]\d{3})/)
    if (code) {
      setSid(code[1])
      setQ('')
      return
    }
    const byName = allStocks.find(([, name]) => name === t)
    if (byName) {
      setSid(byName[0])
      setQ('')
    }
  }
  return (
    <TPanel title="選擇個股" sub={`全部上市櫃普通股 ${allStocks.length} 檔 · Enter 查詢`}>
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <input
            list="sf-stocklist"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') pick(q)
            }}
            onBlur={() => pick(q)}
            placeholder="❯ 代號或中文名，如 6488 或 環球晶…"
            className="w-full sm:w-64 px-2.5 py-1.5 text-[12px] mono rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)] focus:border-[var(--accent)] outline-none"
          />
          <datalist id="sf-stocklist">
            {allStocks.map(([c, name]) => (
              <option key={c} value={`${c} ${name}`} />
            ))}
          </datalist>
          {sid && (
            <button
              onClick={() => toggleWatch(sid)}
              className={`sm:ml-auto ${chip(watch.includes(sid))}`}
              title={watch.includes(sid) ? '點擊取消追蹤' : '加入追蹤（存於本機瀏覽器）'}
            >
              {watch.includes(sid) ? '★ 已追蹤' : '☆ 追蹤'} {labelOf(mm, sid)}
            </button>
          )}
        </div>
        {watch.length > 0 && (
          <ChipRow label="追蹤">
            {watch.map((s) => (
              <button key={s} onClick={() => setSid(s)} className={chip(sid === s)}>
                <span style={{ color: 'var(--accent)' }}>★</span> {s}
                {mm[s]?.name && <span className="ml-1 text-[11px] opacity-75">{mm[s]?.name}</span>}
              </button>
            ))}
          </ChipRow>
        )}
        <ChipRow
          label={hotIsRanked ? `宇宙前 ${hot.length}` : '熱門'}
          title="宇宙綜合排名＝描述性相對定位，非預測訊號（D-020；因子預測力 RankIC 見健檢頁）"
        >
          {hot.map((h) => (
            <button
              key={h.sid}
              onClick={() => setSid(h.sid)}
              className={chip(sid === h.sid)}
              title={h.pct != null ? `綜合分位 ${Math.round(h.pct * 100)}%` : undefined}
            >
              {h.rank > 0 && <span className="mr-1 opacity-60">{h.rank}</span>}
              {h.sid}
              {h.name && <span className="ml-1 text-[11px] opacity-75">{h.name}</span>}
            </button>
          ))}
          {live && list.length > hot.length && (
            <select
              value={hot.some((h) => h.sid === sid) ? '' : sid}
              onChange={(e) => e.target.value && setSid(e.target.value)}
              className="shrink-0 px-2 py-1 text-[12px] mono rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
            >
              <option value="">更多個股（前 {list.length}）…</option>
              {list.map((s) => (
                <option key={s} value={s}>{labelOf(mm, s)}</option>
              ))}
            </select>
          )}
        </ChipRow>
      </div>
    </TPanel>
  )
}
