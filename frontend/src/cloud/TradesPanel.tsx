// 追蹤分頁的「我的下單」（T-388／D-193 §一，使用者選 C）：名單上勾的「我買了」會出現在這裡；
// 名單外的用下面的表單補（代號、日期、買或賣、價格、張數）。只記、不評。
import { useState } from 'react'
import { SIDE_TEXT, tradeFormError, tradeText, type Side, type Trade, type TradeList } from './trades'

const SHOWN = 20

export function TradesPanel({
  trades,
  err,
  lineName,
  onAdd,
  onDelete,
}: {
  trades: TradeList | null
  err: string
  lineName: (code: string | null) => string | null
  onAdd: (t: Omit<Trade, 'created_at'>) => Promise<void>
  onDelete: (t: Trade) => Promise<void>
}) {
  const today = trades?.today ?? new Date().toISOString().slice(0, 10)
  const [form, setForm] = useState({ sid: '', date: today, side: 'buy' as Side, price: '', qty: '' })
  const [formErr, setFormErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [more, setMore] = useState(false)
  const items = trades?.items ?? []
  const shown = more ? items : items.slice(0, SHOWN)

  const submit = () => {
    const e = tradeFormError(form, today)
    if (e) return setFormErr(e)
    setBusy(true)
    setFormErr('')
    onAdd({
      sid: form.sid.trim().toUpperCase(),
      date: form.date,
      side: form.side,
      price: form.price.trim() === '' ? null : Number(form.price),
      qty: form.qty.trim() === '' ? null : Number(form.qty),
      source: 'manual',
      line: null,
    })
      .then(() => setForm({ sid: '', date: today, side: 'buy', price: '', qty: '' }))
      .catch((x: Error) => setFormErr(x.message))
      .finally(() => setBusy(false))
  }

  return (
    <div className="cq-ref cq-trades">
      <h3>
        我的下單<small>名單上按「我買了」會記在這；名單外的在下面補。只記、不評，累積三個月後對照名單</small>
      </h3>
      <div className="cq-trade-form">
        <input aria-label="代號" placeholder="代號" value={form.sid} maxLength={6} onChange={(e) => setForm({ ...form, sid: e.target.value })} />
        <input aria-label="日期" type="date" value={form.date} max={today} onChange={(e) => setForm({ ...form, date: e.target.value })} />
        <select aria-label="買或賣" value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value as Side })}>
          <option value="buy">買</option>
          <option value="sell">賣</option>
        </select>
        <input aria-label="價格" placeholder="價格" inputMode="decimal" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
        <input aria-label="張數" placeholder="張" inputMode="numeric" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} />
        <button className="cq-edit-btn" disabled={busy} onClick={submit}>
          記一筆
        </button>
      </div>
      {(formErr || err) && <p className="cq-empty">{formErr || err}</p>}
      {items.length === 0 && !err && <p className="cq-empty">還沒有紀錄。在名單上按「我買了」，或用上面的表單記一筆。</p>}
      {items.length > 0 && (
        <ul>
          {shown.map((t) => (
            <li key={`${t.date}${t.sid}${t.side}`} className={t.side === 'buy' ? 'soon' : 'past'}>
              <span className="cq-stockcal-detail">{tradeText(t, lineName)}</span>
              <button className="cq-war-link" aria-label={`刪除 ${SIDE_TEXT[t.side]} ${t.sid} ${t.date}`} onClick={() => onDelete(t)}>
                刪
              </button>
            </li>
          ))}
        </ul>
      )}
      {items.length > SHOWN && (
        <button className="cq-war-link" onClick={() => setMore(!more)}>
          {more ? '收起' : `看全部 ${items.length} 筆`}
        </button>
      )}
      <p className="cq-ref-note">存在你的帳號（同追蹤股），沒有備份。這份紀錄之後只用來對照「名單說了什麼、你做了什麼」，不會改名單、不會推播。</p>
    </div>
  )
}
