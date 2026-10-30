// 台指期對照策略與檢討單（T-467，使用者 2026-10-30：「那把策略也整合進去我的系統」）。
// 對照策略只記帳、都沒通過驗證，不併進上面的主帳；檢討單記使用者自己的單，累積 100 筆才看得出哪種進場真的賺錢。
import { ClipboardList, FlaskConical } from 'lucide-react'
import { useEffect, useState } from 'react'
import { addJournal, deleteJournal, fetchJournal } from './api'
import { price } from './format'
import { tone, yuan } from './futures'
import { EXTRA_GROUPS, JOURNAL_GOAL, extrasAt, journalPoints, journalR, journalStats, journalYuan, type FuturesExtras, type JournalList, type JournalRow, type JournalStat } from './futures-extras'
import { Kpi, Section } from './Section'

const pct = (x: number) => `${(x * 100).toFixed(0)}%`
const pts = (x: number) => `${x > 0 ? '+' : ''}${x.toFixed(1)} 點`

export function ExtrasSection({ x, cut }: { x: FuturesExtras; cut: string }) {
  const v = extrasAt(x, cut)
  const total = v.groups.reduce((a, g) => a + g.realized, 0)
  const sleeveName = (k: keyof FuturesExtras['sleeves']) => x.sleeves[k]?.name ?? k
  return (
    <Section icon={FlaskConical} title="對照策略（未通過驗證）" fold summary={`${v.trades.length} 筆・${yuan(total)} 元`}>
      <div className="cq-tiles cq-fu-tiles">
        {v.groups.map((g) => (
          <Kpi key={g.name} label={g.name} value={yuan(g.realized)} tone={tone(g.realized)} sub={`${g.closed} 筆・賺 ${g.wins} 筆${g.holding ? `・抱著 ${g.holding}` : ''}`} />
        ))}
      </div>
      {v.trades.length === 0 ? (
        <p className="cq-empty">還沒有平倉的對照單。</p>
      ) : (
        <table className="cq-fu-trades cq-fu-trades-all">
          <thead>
            <tr>
              <th>策略</th>
              <th>進場</th>
              <th>出場</th>
              <th>淨損益</th>
            </tr>
          </thead>
          <tbody>
            {v.trades.map((t) => (
              <tr key={`${t.sleeve}-${t.entry_time}`}>
                <td>{sleeveName(t.sleeve)}</td>
                <td>
                  {t.entry_time}
                  <br />
                  {price(t.entry_price)}
                </td>
                <td>
                  {t.exit_time}
                  <br />
                  {price(t.exit_price)}
                </td>
                <td className={tone(t.net_pnl)}>{yuan(t.net_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {v.events.length > 0 && (
        <details className="cq-fu-foot">
          <summary>{v.events.length} 件紀錄</summary>
          <ul className="cq-fu-extra-log">
            {v.events.map((e, i) => (
              <li key={`${e.time}-${i}`}>
                {e.time} {e.text}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="cq-fu-foot">
        這三條都沒通過九年判定，只記帳不下單，不併進上面的合計。
        {EXTRA_GROUPS.map((g) => `${g.name}：${g.verdict}。`).join('')}
        區間突圍就是原本的噪音區，多空都做。回測確認是箱子突破後不追，等回測守住才進，跌回中線就走。尾盤順勢空是 13:15 低於昨收就空，沒有接三票閘門。
      </p>
    </Section>
  )
}

function StatRow({ s }: { s: JournalStat }) {
  return (
    <tr>
      <td>{s.name}</td>
      <td>{s.n}</td>
      <td>{pct(s.win)}</td>
      <td className={tone(s.avgPts)}>{pts(s.avgPts)}</td>
      <td>{s.breakeven === null ? '—' : pct(s.breakeven)}</td>
    </tr>
  )
}

const BLANK = { product: 'MTX', side: 1, time: '', entry: '', exit: '', stop: '', qty: '1', setup: '回測確認', followed: true, note: '' }

export function JournalSection({ day }: { day: string }) {
  const [list, setList] = useState<JournalList | null>(null)
  const [form, setForm] = useState({ ...BLANK, date: day })
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () =>
    fetchJournal()
      .then(setList)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : '讀不到檢討單'))
  useEffect(() => {
    void load()
  }, [])
  const set = (k: keyof typeof form, v: string | number | boolean) => setForm((f) => ({ ...f, [k]: v }))
  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      await addJournal({ ...form, entry: Number(form.entry), exit: Number(form.exit), stop: form.stop === '' ? null : Number(form.stop), qty: Number(form.qty), side: form.side as 1 | -1, product: form.product as JournalRow['product'] })
      setForm((f) => ({ ...BLANK, date: f.date, product: f.product, setup: f.setup }))
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : '存不起來')
    } finally {
      setBusy(false)
    }
  }
  const remove = async (id: string) => {
    if (!window.confirm('刪掉這一筆？')) return
    await deleteJournal(id)
    await load()
  }
  const rows = list?.items ?? []
  const s = journalStats(rows)
  return (
    <Section icon={ClipboardList} title="我的檢討單" fold summary={`${rows.length}／${JOURNAL_GOAL} 筆${rows.length ? `・勝率 ${pct(s.all.win)}・平均 ${pts(s.all.avgPts)}` : ''}`}>
      <div className="cq-fu-journal-form">
        <label>
          日期
          <input type="date" value={form.date} onChange={(e) => set('date', e.target.value)} />
        </label>
        <label>
          商品
          <select value={form.product} onChange={(e) => set('product', e.target.value)}>
            <option value="MTX">小台</option>
            <option value="TX">大台</option>
            <option value="TMF">微台</option>
          </select>
        </label>
        <label>
          方向
          <select value={form.side} onChange={(e) => set('side', Number(e.target.value))}>
            <option value={1}>做多</option>
            <option value={-1}>放空</option>
          </select>
        </label>
        <label>
          進場時間
          <input type="time" value={form.time} onChange={(e) => set('time', e.target.value)} />
        </label>
        <label>
          進場價
          <input inputMode="decimal" value={form.entry} onChange={(e) => set('entry', e.target.value)} />
        </label>
        <label>
          出場價
          <input inputMode="decimal" value={form.exit} onChange={(e) => set('exit', e.target.value)} />
        </label>
        <label>
          停損
          <input inputMode="decimal" value={form.stop} placeholder="沒設可空白" onChange={(e) => set('stop', e.target.value)} />
        </label>
        <label>
          口數
          <input inputMode="numeric" value={form.qty} onChange={(e) => set('qty', e.target.value)} />
        </label>
        <label>
          進場理由
          <select value={form.setup} onChange={(e) => set('setup', e.target.value)}>
            {['回測確認', '突破', '反彈', '順勢', '其他'].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <label className="cq-fu-journal-check">
          <input type="checkbox" checked={form.followed} onChange={(e) => set('followed', e.target.checked)} />
          有照計畫
        </label>
        <label className="cq-fu-journal-note">
          備註
          <input maxLength={200} value={form.note} onChange={(e) => set('note', e.target.value)} />
        </label>
        <button type="button" className="cq-fu-journal-save" disabled={busy || !form.time || !form.entry || !form.exit} onClick={() => void submit()}>
          記這一筆
        </button>
      </div>
      {err && <p className="cq-empty">{err}</p>}
      {rows.length > 0 && (
        <>
          <table className="cq-fu-trades cq-fu-trades-all">
            <thead>
              <tr>
                <th>分類</th>
                <th>筆數</th>
                <th>勝率</th>
                <th>平均</th>
                <th>打平勝率</th>
              </tr>
            </thead>
            <tbody>
              <StatRow s={s.all} />
              {s.setups.map((x) => (
                <StatRow key={x.name} s={x} />
              ))}
              <StatRow s={s.followed} />
              <StatRow s={s.broke} />
            </tbody>
          </table>
          <table className="cq-fu-trades cq-fu-trades-all">
            <thead>
              <tr>
                <th>日期</th>
                <th>單子</th>
                <th>結果</th>
                <th aria-label="刪除" />
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 30).map((r) => {
                const rr = journalR(r)
                return (
                  <tr key={r.id}>
                    <td>
                      {r.date.slice(5)}
                      <br />
                      <small>{r.time}</small>
                    </td>
                    <td>
                      {r.side > 0 ? '多' : '空'} {r.qty} 口・{r.setup}
                      <br />
                      <small>
                        {price(r.entry)} → {price(r.exit)}
                        {r.note ? `・${r.note}` : ''}
                      </small>
                    </td>
                    <td className={tone(journalPoints(r))}>
                      {pts(journalPoints(r))}
                      <br />
                      <small>
                        {yuan(journalYuan(r))}
                        {rr === null ? '' : `・${rr.toFixed(1)}R`}
                      </small>
                    </td>
                    <td>
                      <button type="button" aria-label="刪除這一筆" onClick={() => void remove(r.id)}>
                        ×
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}
      <p className="cq-fu-foot">
        金額和點數都還沒扣手續費與稅。打平勝率是這個賺賠比下，勝率要超過多少才不賠。累積 {JOURNAL_GOAL} 筆前的數字變動很大，先看有沒有照計畫，不要急著下結論。
      </p>
    </Section>
  )
}
