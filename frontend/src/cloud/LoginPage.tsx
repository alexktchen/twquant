// 登入／註冊頁 /login（T-432 深色頁）。
// Google（T-457）：整頁到 /api/login/google，Cloudflare Access 直接跳 Google（不經白卡），回來就是登入狀態。
// Email（T-439）：Supabase Auth 寄一組驗證碼（信裡也有連結），填進去就登入。
// 第一次登入就自動完成註冊，不用另外填資料。
import { useEffect, useState, type FormEvent } from 'react'
import { GOOGLE_LOGIN } from './auth'
import { authEnabled, hasSession, sendEmailCode, verifyEmailCode } from './session-client'
import { emailLooksValid } from './session'
import './login.css'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  /** pick＝還沒寄驗證碼；code＝寄了，等填驗證碼 */
  const [step, setStep] = useState<'pick' | 'code'>('pick')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [sentTo, setSentTo] = useState('')
  const ready = authEnabled()

  // 已經登入的人不用再登入
  useEffect(() => {
    if (hasSession()) window.location.replace('/')
  }, [])

  const send = (e: FormEvent) => {
    e.preventDefault()
    const to = email.trim().toLowerCase()
    if (!emailLooksValid(to)) return setErr('請輸入正確的 Email')
    setErr('')
    setBusy(true)
    sendEmailCode(to)
      .then(() => {
        setSentTo(to)
        setStep('code')
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false))
  }

  const verify = (e: FormEvent) => {
    e.preventDefault()
    if (code.trim().length < 6) return setErr('驗證碼是 6 位數字')
    setErr('')
    setBusy(true)
    verifyEmailCode(sentTo, code)
      .then(() => window.location.assign('/'))
      .catch((e: Error) => {
        setErr(e.message)
        setBusy(false)
      })
  }

  return (
    <div className="lg-page">
      <div className="lg-bg" />
      <div className="lg-wrap">
        <section className="lg-intro">
          <a className="lg-brand" href="/">
            <img src="/icon-192.png" alt="" />
            twquant AI 選股
          </a>
          <h2>
            國內外財經新聞，
            <br />
            <em>一頁聚合</em>連到台股
          </h2>
          <p>登入後可以看：</p>
          <ul className="lg-perks">
            <li>
              <i />
              <div>
                <b>新聞搜尋</b>
                <span>新聞不用登入就能看；登入後可以照意思搜尋，例如「輝達財測」「美債殖利率」。</span>
              </div>
            </li>
            <li>
              <i />
              <div>
                <b>今日戰情</b>
                <span>開盤前一頁看完：美股與台指夜盤、大盤體檢、法人進出、重大訊息。</span>
              </div>
            </li>
            <li>
              <i />
              <div>
                <b>盤勢</b>
                <span>每個族群今天拿到多少資金、幾檔漲幾檔跌，看資金往哪跑。</span>
              </div>
            </li>
            <li>
              <i />
              <div>
                <b>追蹤股</b>
                <span>輸入任何股號看模型評分，存在帳號裡，手機和電腦同一份。</span>
              </div>
            </li>
          </ul>
        </section>

        <main className="lg-card">
          <a className="lg-brand lg-top-brand" href="/">
            <img src="/icon-192.png" alt="" />
            twquant AI 選股
          </a>
          <h1>登入／註冊</h1>
          <p className="lg-sub">第一次登入就自動完成註冊，不用另外填資料。</p>

          {step === 'pick' && (
            <a className="lg-btn lg-google" href={GOOGLE_LOGIN}>
              <span className="g">
                <GoogleMark />
              </span>
              使用 Google 帳號繼續
            </a>
          )}
          {!ready ? (
            <p className="lg-hint">Email 驗證碼登入還沒設定（網站建置時沒有給 VITE_SUPABASE_URL／VITE_SUPABASE_ANON_KEY），請先用 Google 登入。</p>
          ) : step === 'pick' ? (
            <>
              <div className="lg-or">或</div>
              <form className="lg-form" onSubmit={send}>
                <input
                  className="lg-input"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  placeholder="你的 Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  maxLength={120}
                  aria-label="Email"
                  required
                />
                <button type="submit" className="lg-btn lg-mail" disabled={busy || !email.trim()}>
                  <MailMark />
                  {busy ? '寄送中…' : '用 Email 收驗證碼'}
                </button>
              </form>
              <p className="lg-hint">信箱會收到一組 6 位數驗證碼（信裡也有可以直接登入的連結），幾分鐘內有效。</p>
            </>
          ) : (
            <form className="lg-form" onSubmit={verify}>
              <p className="lg-ok">驗證碼已寄到 {sentTo}，請到信箱查看（找不到時看一下垃圾郵件）。</p>
              <input
                className="lg-input lg-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                placeholder="6 位數驗證碼"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                aria-label="驗證碼"
                autoFocus
                required
              />
              <button type="submit" className="lg-btn lg-google" disabled={busy || code.trim().length < 6}>
                {busy ? '登入中…' : '登入'}
              </button>
              <p className="lg-hint">
                沒收到？{' '}
                <button type="button" className="lg-link" onClick={() => setStep('pick')} disabled={busy}>
                  換一個 Email 或重寄
                </button>
              </p>
            </form>
          )}
          {err && <p className="lg-err">{err}</p>}

          <p className="lg-safe">
            <ShieldMark />
            <span>
              Google 登入由 Cloudflare Access 處理、Email 驗證碼由 Supabase Auth 處理，網站拿不到你的密碼，只用 Email 確認身分。詳見<a href="/privacy">隱私權政策</a>。
            </span>
          </p>

          <a className="lg-back" href="/">
            ← 先回網站看看
          </a>
        </main>

        <footer className="lg-foot">
          內容由程式自動整理，僅供參考，不是投資建議。　<a href="/about">網站介紹</a>　·　<a href="/privacy">隱私權政策</a>
        </footer>
      </div>
    </div>
  )
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.6 13.2l7.8 6.1C12.3 13.6 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.5 5.8c4.4-4 6.8-10 6.8-17.2z" />
      <path fill="#FBBC05" d="M10.4 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C1 16.5 0 20.1 0 24s1 7.5 2.6 10.8l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.8 2.3-8.4 2.3-6.3 0-11.7-4.1-13.6-9.8l-7.8 6.1C6.6 42.6 14.6 48 24 48z" />
    </svg>
  )
}

function MailMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </svg>
  )
}

function ShieldMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3 4 6v6c0 5 3.4 8.3 8 9 4.6-.7 8-4 8-9V6l-8-3z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}
