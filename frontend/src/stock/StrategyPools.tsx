// 策略選股池（SPEC-007 §6：研究高勝率條件每日成員；點擊即選股）。
// 回溯：date 給定時後端先讀 screener_daily 快照（snapshot=true）、無快照以 ≤date 資料回算。
import { useState } from 'react'
import type { Screener, ScreenerPool, StockMetaMap } from '../api'
import { fetchScreener } from '../api'
import { useResource } from '../hooks'
import { pct } from '../theme'
import { ChipRow, TPanel, chip } from './ui'

export function StrategyPools({ sid, setSid, mm }: { sid: string; setSid: (s: string) => void; mm: StockMetaMap }) {
  const [date, setDate] = useState('') // ''＝最新即時
  const scr = useResource(`screener:${date}`, () => fetchScreener(30, date || undefined), () => null as Screener | null)
  const d = scr.data
  const [key, setKey] = useState('main_accum')
  const pool: ScreenerPool | undefined = d?.pools.find((p) => p.key === key) ?? d?.pools[0]
  return (
    <TPanel
      title="策略選股池"
      sub={
        d?.as_of
          ? `${d.as_of}${date ? (d.snapshot ? ' · 已存快照' : ' · 即時回算') : ''} · 宇宙 ${d.universe_n} 檔 · 描述性、非交易訊號`
          : '需 PG（broker_trades / factor_values）'
      }
    >
      <div className="flex flex-col gap-2.5">
        <ChipRow>
          {(d?.pools ?? []).map((p) => (
            <button key={p.key} onClick={() => setKey(p.key)} className={chip(pool?.key === p.key)}>
              {p.key === 'main_distrib' && <span className="mr-1" style={{ color: 'var(--up)' }}>⚠</span>}
              {p.name}
              <span className="ml-1 text-[11px] opacity-75">{p.total}</span>
            </button>
          ))}
        </ChipRow>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          {pool && (
            <span className="text-[11px] text-[var(--muted)] min-w-0 flex-1 basis-52">
              {pool.rule} · {pool.note}
            </span>
          )}
          <span className="inline-flex items-center gap-1.5 ml-auto">
            <input
              type="date"
              value={date}
              max={new Date().toISOString().slice(0, 10)}
              onChange={(e) => setDate(e.target.value)}
              className="px-2 py-1 text-[12px] mono rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] text-[var(--fg)]"
              title="回溯歷史選股池（每日快照由 screener-snapshot 產生；無快照即時回算）"
            />
            {date && (
              <button onClick={() => setDate('')} className={chip(false)} title="回到最新">
                最新
              </button>
            )}
          </span>
        </div>
        {pool && pool.members.length > 0 ? (
          <ChipRow>
            {pool.members.map((m) => (
              <button
                key={m.stock_id}
                onClick={() => setSid(m.stock_id)}
                className={chip(sid === m.stock_id)}
                title={[
                  m.close != null ? `收盤 ${m.close}` : null,
                  m.chg != null ? `漲跌 ${pct(m.chg, 2)}` : null,
                  m.yoy != null ? `營收YoY ${pct(m.yoy)}` : null,
                  m.conc != null ? `主力10日佔量 ${pct(m.conc, 2)}` : null,
                  m.accum_ratio != null ? `主力60日佔量 ${pct(m.accum_ratio, 2)}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              >
                {m.stock_id}
                {(m.name ?? mm[m.stock_id]?.name) && (
                  <span className="ml-1 text-[11px] opacity-75">{m.name ?? mm[m.stock_id]?.name}</span>
                )}
                <span className="ml-1 text-[11px] mono opacity-75">連{m.streak}日</span>
              </button>
            ))}
            {pool.total > pool.members.length && (
              <span className="shrink-0 text-[11px] text-[var(--muted)]">…共 {pool.total} 檔（顯示前 {pool.members.length}）</span>
            )}
          </ChipRow>
        ) : (
          <div className="text-[12px] text-[var(--muted)]">
            {scr.loading ? '載入中…' : '今日無成員（或 PG 資料未更新）'}
          </div>
        )}
      </div>
    </TPanel>
  )
}
