// 遙控按鈕（autotrade A-016、A-017／D-229）：平倉、暫停或恢復自動交易、啟動或結束交易程式。
// 使用者 2026-11-14 要網站不只能叫停。這裡沒有、也不要加：手動買賣、改設定、解除緊急停止。
import { Minimize2, Pause, Play, Power } from 'lucide-react'
import { useState } from 'react'
import { sendAutotradeCommand } from './api'
import { ACTION_CONFIRM, MODE_NAME, type AutoEnvelope, type AutoMode, type RemoteAction, type RemoteButton, type RequestLine } from './autotrade'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** 送出遙控指令時伺服器回來的東西：今天最近幾筆與後台的心跳。 */
export type CommandSent = Pick<AutoEnvelope, 'requests' | 'remote'>
const ACTION_ICON: Record<RemoteAction, typeof Pause> = { flatten: Minimize2, pause: Pause, resume: Play, start: Power, quit: Power }

/**
 * 遙控按鈕（A-016、A-017）：平倉、暫停或恢復、啟動或結束。只對現在看的這個分頁（模擬或真單）有效。
 * 按一下只是打開確認，再按一次才送出。該接手的程式沒在聽就不能按，下面寫原因；送出之後顯示這一筆的進度。
 */
export function RemoteBar({ mode, buttons, lines, onSent }: { mode: AutoMode; buttons: RemoteButton[]; lines: RequestLine[]; onSent: (r: CommandSent) => void }) {
  const [asking, setAsking] = useState<RemoteButton | null>(null)
  const [sending, setSending] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const send = (b: RemoteButton) => {
    setSending(true)
    setErr(null)
    sendAutotradeCommand(b.action, mode)
      .then(onSent)
      .catch((e) => setErr(errText(e)))
      .finally(() => {
        setSending(false)
        setAsking(null)
      })
  }
  const blocked = [...new Set(buttons.filter((b) => !b.enabled).map((b) => b.why))]
  return (
    <div className="cq-at-remote">
      {asking === null ? (
        <div className="cq-at-remote-btns" role="group" aria-label={`${MODE_NAME[mode]}的遙控`}>
          {buttons.map((b) => {
            const Icon = ACTION_ICON[b.action]
            return (
              <button key={b.action} className={`cq-at-remote-btn ${b.action}`} disabled={!b.enabled} title={b.why} onClick={() => setAsking(b)}>
                <Icon size={20} aria-hidden="true" />
                {b.label}
              </button>
            )
          })}
        </div>
      ) : (
        <div className="cq-at-remote-confirm" role="alertdialog" aria-label={`確認${asking.label}`}>
          <b>
            確定要{asking.label}？（{MODE_NAME[mode]}）
          </b>
          <span>{ACTION_CONFIRM[asking.action]}</span>
          <div>
            <button className="cq-at-remote-yes" disabled={sending} onClick={() => send(asking)}>
              {sending ? '送出中…' : `確定${asking.label}`}
            </button>
            <button className="cq-at-remote-no" disabled={sending} onClick={() => setAsking(null)}>
              取消
            </button>
          </div>
        </div>
      )}
      {err && <p className="cq-err">沒有送出去：{err}</p>}
      {blocked.map((why) => (
        <p key={why} className="cq-at-remote-why">
          {why}
        </p>
      ))}
      {lines.length > 0 && (
        <ul className="cq-at-stop-lines cq-at-remote-lines" aria-label="遙控指令的進度">
          {lines.map((l) => (
            <li key={l.id} className={l.tone}>
              <i aria-hidden="true" />
              <strong>{l.label}</strong>
              {l.detail && <span>{l.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
