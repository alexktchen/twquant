// 程式交易（autotrade A-005、A-011）：交易程式寫出來的狀態（網站只讀），加上遠端緊急停止（D-225：網站只能叫停，不能解除）。
// 這裡是純函數與型別，畫面在 AutotradeView.tsx。

export type AutoMode = 'sim' | 'live'
export type AutoState = 'ok' | 'halted' | 'suspended' | 'link_down'
export type AutoFill = { ts: string; side: 'BUY' | 'SELL'; qty: number; price: number; pnl_twd: number }
export type AutoEvent = { ts: string; kind: string; text: string }

/** autotrade/src/autotrade/export.py 的 snapshot（schema 1）。金額是元。 */
export type AutoStatus = {
  schema: number
  day: string
  mode: AutoMode
  source: 'replay' | 'live'
  status: 'live' | 'closed'
  updated_at: string
  symbol: string
  strategy: string
  state: AutoState
  halt: string | null
  /** 自動交易是不是暫停中（不再進新單，A-016）；舊版的狀態沒有這一欄 */
  paused?: boolean
  position: number
  avg_price: number
  last_price: number
  last_ts: string
  open_pnl_twd: number
  realized_twd: number
  day_pnl_twd: number
  costs_twd: number
  entries: number
  limits: {
    max_position: number
    max_entries_per_day: number
    daily_loss_limit_twd: number | null
    position_stop_loss_twd: number | null
  }
  contract: { point_value: number; commission_twd: number; tax_rate: number }
  /** 模擬假設；真單是 null */
  sim: { latency_s: number; slippage_points: number } | null
  /** 遠端停止這條路：這支程式有沒有在聽、最近一次讀指令成不成功、什麼時候確認過、最後處理的指令編號 */
  control: { listening: boolean; ok: boolean | null; checked_at: string | null; poll_s: number | null; last_command_id: number }
  fills: AutoFill[]
  corrected: boolean
  events: AutoEvent[]
}

export type AutoDayRow = { day: string; status: string; pnl: number | null; fills: number | null }
/** 一支程式對一筆停止的回報：先是收到了、當下怎麼處理（code／note），之後是最後的結果（done_*；還沒有是 null）。 */
export type StopAck = { acked_at: string; ack_age_s: number; code: string; note: string; done_at: string | null; done_code: string | null; done_note: string | null }
/** 今天最新的一筆遠端停止指令與每支程式的回報。時間帶 +08:00；age_s＝伺服器算的「按下到現在幾秒」。 */
export type StopCommand = { id: string; day: string; created_at: string; age_s: number; acks: Partial<Record<string, StopAck>> }
/** 一支程式的心跳：中繼程式幾秒前回報、交易程式幾秒前讀過指令（沒讀過是 null）、多久讀一次、今天有沒有出現過。 */
export type ListenerBeat = { relay_age_s: number; engine_age_s: number | null; poll_s: number; seen_today: boolean }
/** 遙控指令（A-016、A-017／D-229）：平倉、暫停／恢復自動交易由交易程式執行，啟動／結束由本機後台執行。 */
export type RemoteAction = 'flatten' | 'pause' | 'resume' | 'start' | 'quit'
/** 今天的一筆遙控指令與接手的程式回報的結果（還沒有是 null）。age_s／ack_age_s＝伺服器算的「到現在幾秒」。 */
export type RemoteRequest = {
  id: string
  action: RemoteAction
  target: AutoMode
  created_at: string
  age_s: number
  acked_at: string | null
  ack_age_s: number | null
  code: string | null
  note: string | null
  done_at: string | null
  done_code: string | null
  done_note: string | null
}
/** 後台那支中繼程式的心跳：它幾秒前回報、後台幾秒前讀過指令（沒讀過是 null）。 */
export type RemoteBeat = { age_s: number; reader_age_s: number | null; poll_s: number; seen_today: boolean }
export type AutoEnvelope = {
  days: AutoDayRow[]
  current: AutoStatus | null
  stop: StopCommand | null
  listeners: Partial<Record<string, ListenerBeat>>
  /** 今天最近幾筆遙控指令（新的在前）；表還沒建的時候沒有這一欄 */
  requests?: RemoteRequest[]
  remote?: RemoteBeat | null
}

/** 這一版網站認得的狀態版本（autotrade export.py 的 SCHEMA）。 */
export const SCHEMA = 1

export const MODE_NAME: Record<AutoMode, string> = { sim: '模擬', live: '真單' }
export const SOURCE_NAME: Record<AutoStatus['source'], string> = { replay: '歷史重播', live: '即時行情' }
export const STRATEGY_LABEL: Record<string, string> = { momentum: '動能順勢', none: '不下單（只檢查接線）' }

/** 停手原因（autotrade risk.py 的代碼）。 */
export const HALT_TEXT: Record<string, string> = {
  daily_loss: '當日虧損到上限，今天不再進場',
  kill: '緊急停止，要人工解除',
  mismatch: '部位跟券商對不上，要人工解除',
  order_rate: '送單太頻繁，要人工解除',
  broker_reject: '連續被券商拒絕，要人工解除',
}

/** 停手原因的短名：今日大事的文字裡，括號內的代碼換成這個（交易程式的日誌是給程式看的，所以帶代碼）。 */
export const HALT_SHORT: Record<string, string> = {
  daily_loss: '當日虧損到上限',
  kill: '緊急停止',
  mismatch: '跟券商對不上',
  order_rate: '送單太頻繁',
  broker_reject: '連續被券商拒絕',
}

/** 今日大事的文字：括號裡的停手代碼換成白話；不認得的代碼照原樣留著，不吞掉。 */
export const eventText = (text: string): string =>
  text.replace(/（([a-z_]+)）/g, (all, code: string) => (HALT_SHORT[code] ? `（${HALT_SHORT[code]}）` : all))

/** 今日大事的分類名稱。 */
export const EVENT_LABEL: Record<string, string> = {
  halt: '停手',
  release: '解除',
  stop: '停損',
  correction: '部位修正',
  link: '連線',
  broker_reject: '券商拒絕',
  foreign_order: '不明委託',
  reject: '風控擋下',
  control: '遠端停止',
  control_link: '遠端連線',
}

/** 要人處理的大事（紅色）；其餘是一般提醒。 */
export const EVENT_SERIOUS: ReadonlySet<string> = new Set(['halt', 'stop', 'correction', 'broker_reject', 'foreign_order', 'control'])

/** 盤中狀態超過這麼多秒沒更新，就當成交易程式可能沒在跑。 */
export const STALE_AFTER_S = 60

export type Lamp = { tone: 'ok' | 'bad' | 'warn' | 'off'; label: string; detail: string }

/** 狀態燈。嚴重的優先：停手／暫停送單 → 連線中斷 → 盤中太久沒更新 → 已收盤 → 運作中。
 *  盤中的狀態太久沒更新時，不管是哪一種都要講：畫面上的「停手」可能是程式死掉前的最後一份。 */
export function lamp(s: AutoStatus, now: Date): Lamp {
  const why = s.halt === null ? '' : (HALT_TEXT[s.halt] ?? s.halt)
  const age = Math.floor((now.getTime() - Date.parse(s.updated_at)) / 1000)
  const stale = s.status === 'live' && age > STALE_AFTER_S ? `${ageText(age)}沒有更新，程式可能沒在跑` : ''
  const add = (detail: string) => (stale ? (detail ? `${detail}；${stale}` : stale) : detail)
  if (s.state === 'halted') return { tone: 'bad', label: '停手', detail: add(why) }
  if (s.state === 'suspended') return { tone: 'bad', label: '暫停送單', detail: add(why) }
  if (s.state === 'link_down') return { tone: 'warn', label: '券商連線中斷', detail: add('斷線期間不送單也不刪單') }
  if (s.status === 'closed') return { tone: 'off', label: '已收盤', detail: '' }
  if (stale) return { tone: 'warn', label: '可能沒在跑', detail: `${ageText(age)}沒有更新` }
  return { tone: 'ok', label: '運作中', detail: '' }
}

const ageText = (s: number): string => (s < 3600 ? `${Math.floor(s / 60)} 分鐘` : `${Math.floor(s / 3600)} 小時`)

// ── 遠端緊急停止（A-011／D-225）──
// 一筆停止對每支交易程式都有效；每支程式各自回報。真單排前面：模擬的回報不可以讓人以為真單也停了。

export type Listener = 'live' | 'sim'
export const LISTENERS: readonly Listener[] = ['live', 'sim']
export const LISTENER_NAME: Record<Listener, string> = { live: '真單', sim: '模擬' }

/** 按下停止後超過這麼多秒程式還沒收到，就標紅。 */
export const STOP_LATE_S = 15
/** 程式收到後超過這麼多秒還沒確認平掉，就標紅。 */
export const STOP_CONFIRM_LATE_S = 30
/** 心跳多新才算「在聽」：poll_s 的 LISTEN_POLLS 倍，最少 LISTEN_MIN_S 秒。 */
export const LISTEN_MIN_S = 10
export const LISTEN_POLLS = 5
/** 平常多久更新一次；有停止還在等結果時變快，最多快 STOP_FAST_FOR_S 秒。 */
export const POLL_MS = 15_000
export const STOP_FAST_MS = 3_000
export const STOP_FAST_FOR_S = 300
/** 有任何一支程式要人處理時給的下一步。先看再平：程式之後連上也會自己平，重複下會變成反向部位。 */
export const BROKER_HINT = '先看元大 App 的未平倉，有部位才平。程式之後連上也會自己平，不要重複下。'
/** 一筆停止管多久。 */
export const STOP_SCOPE = '這筆停止只算今天，不會帶到明天；解除要在跑交易程式的電腦前做。'
/** 按之前就看得出來：現在沒有任何程式在聽。 */
export const NOBODY_LISTENING = '現在沒有交易程式在聽遠端停止，按了不會平倉。有部位請直接用元大 App。'
/** 程式收到後回的第一個結果裡，這幾種是「正在處理」；之後要等它確認平掉。其他的（含不認得的）都當成要人處理。 */
export const STOP_IN_PROGRESS: ReadonlySet<string> = new Set(['flat', 'cancelling', 'flattening', 'link_down'])

const waitText = (s: number): string => (s < 60 ? `${Math.floor(s)} 秒` : ageText(s))

/** 這支程式現在是不是在聽：中繼程式與交易程式的心跳都夠新。elapsed＝拿到資料之後又過了幾秒。 */
export function listening(beat: ListenerBeat | undefined): boolean {
  if (!beat || beat.engine_age_s === null) return false
  const limit = Math.max(LISTEN_MIN_S, beat.poll_s * LISTEN_POLLS)
  return beat.relay_age_s <= limit && beat.engine_age_s <= limit
}

/** 畫面上的資料超過這麼多秒沒更新（網路斷了、分頁剛回到前景），就不拿它判斷程式在不在聽。 */
export const DATA_STALE_S = 45

export type StopLine = { who: Listener; tone: 'ok' | 'off' | 'wait' | 'bad' | 'done'; label: string; detail: string }

/**
 * 一支程式對這筆停止的現況（沒有停止、或停止已在電腦上解除時＝它在不在聽）。
 * 在不在聽看拿到資料那一刻伺服器算好的心跳秒數（頁面自己多久更新一次不算進去）；
 * 「按下多久、收到多久」才加上拿到資料之後過的 elapsed。都不拿手機的時鐘去跟伺服器的時間比。
 */
export function stopLine(who: Listener, cmd: StopCommand | null, beat: ListenerBeat | undefined, elapsed: number): StopLine {
  const hears = listening(beat)
  const seenToday = beat?.seen_today === true
  const silent = beat ? Math.max(beat.relay_age_s, beat.engine_age_s ?? beat.relay_age_s) : 0
  const idle = (extra: string): StopLine => {
    const add = (d: string) => (extra ? (d ? `${d}；${extra}` : extra) : d)
    if (hears) return { who, tone: 'ok', label: '在聽', detail: add('') }
    if (seenToday) return { who, tone: 'bad', label: '沒有回應', detail: add(`今天跑過，但已經 ${waitText(silent)}沒有回應，按了可能到不了`) }
    return { who, tone: 'off', label: '今天沒有在跑', detail: add('') }
  }
  if (cmd === null) return idle('')
  const ack = cmd.acks[who]
  if (ack === undefined) {
    const age = cmd.age_s + elapsed
    if (!hears && !seenToday) return { who, tone: 'off', label: '今天沒有在跑', detail: '今天之內一啟動就會先停下來' }
    if (age <= STOP_LATE_S) return { who, tone: 'wait', label: '等它收到…', detail: `已經 ${waitText(age)}` }
    return { who, tone: 'bad', label: '還沒收到', detail: `已經 ${waitText(age)}${hears ? '' : '，而且它現在沒有回應'}` }
  }
  const at = clock(ack.acked_at)
  if (ack.done_code === 'flat') return { who, tone: 'done', label: '已停手，沒有部位', detail: `${at} 收到；${ack.done_note ?? ''}` }
  // 在電腦上解除之後，這筆停止對它就結束了：回到「在不在聽」，程式之後沒聲音一樣要標紅
  if (ack.done_code === 'released') return idle(ack.done_note ?? '已在電腦上解除')
  if (ack.done_code !== null) return { who, tone: 'bad', label: '部位沒有平掉', detail: ack.done_note ?? ack.done_code }
  if (!STOP_IN_PROGRESS.has(ack.code)) return { who, tone: 'bad', label: ack.code === 'ignored' ? '沒有執行' : '不會自動平倉', detail: ack.note }
  const waited = ack.ack_age_s + elapsed
  if (waited > STOP_CONFIRM_LATE_S) return { who, tone: 'bad', label: '收到了，但還沒確認平掉', detail: `${at} 收到，已經 ${waitText(waited)}；${ack.note}` }
  return { who, tone: 'wait', label: '已收到，還沒確認平掉', detail: `${at} 收到；${ack.note}` }
}

/** 每支程式一行，真單在前。 */
export const stopLines = (cmd: StopCommand | null, beats: AutoEnvelope['listeners'], elapsed: number): StopLine[] =>
  LISTENERS.map((who) => stopLine(who, cmd, beats[who], elapsed))

/** 畫面上的資料不夠新（還沒讀到、或太久沒更新）時，每支程式一行都只說「不知道」，不猜。 */
export const unknownLines = (why: string): StopLine[] => LISTENERS.map((who) => ({ who, tone: 'off', label: '狀態不明', detail: why }))

/** 按之前要不要先講「沒有程式在聽，按了不會平倉」：沒有任何一支在聽，也沒有任何一支正在處理停止。 */
export const nobodyListening = (lines: readonly StopLine[]): boolean =>
  lines.every((l) => l.tone !== 'ok' && l.tone !== 'wait' && l.label !== '狀態不明')

/** 多久更新一次：有停止還在等（等收到、等確認平掉）的時候變快，其餘照平常。 */
export function pollMs(cmd: StopCommand | null, lines: readonly StopLine[], elapsed: number): number {
  if (cmd === null || cmd.age_s + elapsed > STOP_FAST_FOR_S) return POLL_MS
  return lines.some((l) => l.tone === 'wait') ? STOP_FAST_MS : POLL_MS
}

// ── 遙控按鈕（A-016、A-017）──

export const ACTION_NAME: Record<RemoteAction, string> = {
  flatten: '平倉',
  pause: '暫停自動交易',
  resume: '恢復自動交易',
  start: '啟動交易程式',
  quit: '結束交易程式',
}
/** 確認時講清楚按下去會怎樣。 */
export const ACTION_CONFIRM: Record<RemoteAction, string> = {
  flatten: '把現在的部位平掉。自動交易不會停，之後有訊號還會進場；要停到底請按緊急停止。',
  pause: '不再進新單；手上的部位照原本的規則出場。',
  resume: '可以再進場。不會解除緊急停止。',
  start: '用電腦上指定的那一份設定、今天的即時行情啟動。今天按過緊急停止的話，啟動之後一樣是停手的。',
  quit: '交易程式會結束，今天不再交易。',
}
/** 按下超過這麼多秒沒有程式接手就作廢。要跟伺服器的 REQUEST_TTL_S 一樣。 */
export const REQUEST_TTL_S = 30
/** 程式收到之後超過這麼多秒還沒做完，就標紅。 */
export const REQUEST_LATE_S = 60
/** 最後的結果裡，這幾種是「做到了」；其他的（含不認得的）都當成要人處理。 */
export const REQUEST_OK: ReadonlySet<string> = new Set(['flat', 'paused', 'resumed', 'started', 'quit'])
/** 一個分頁最多顯示最近幾筆。 */
export const REQUESTS_PER_TAB = 3

/** 後台的中繼程式是不是在聽（啟動／結束要靠它）：它自己與後台讀指令的心跳都夠新。 */
export function remoteListening(beat: RemoteBeat | null | undefined): boolean {
  if (!beat || beat.reader_age_s === null) return false
  const limit = Math.max(LISTEN_MIN_S, beat.poll_s * LISTEN_POLLS)
  return beat.age_s <= limit && beat.reader_age_s <= limit
}

export type RemoteButton = { action: RemoteAction; label: string; enabled: boolean; why: string }

/**
 * 這個分頁（模擬或真單）現在有哪幾顆按鈕、能不能按；不能按的寫原因。
 * 平倉、暫停／恢復要那一支交易程式在聽；啟動／結束要後台在聽。known＝畫面上的狀態夠新（不夠新就都不能按，不猜）。
 */
export function remoteButtons(mode: AutoMode, beats: AutoEnvelope['listeners'], remote: RemoteBeat | null | undefined, paused: boolean, known: boolean): RemoteButton[] {
  const running = known && listening(beats[mode])
  const admin = known && remoteListening(remote)
  const unsure = '狀態不明，先等畫面更新'
  const noProgram = known ? `${MODE_NAME[mode]}的交易程式沒在聽` : unsure
  const noAdmin = known ? '電腦或本機後台沒開（後台的遙控沒在聽）' : unsure
  const swing: RemoteAction = paused ? 'resume' : 'pause'
  const power: RemoteAction = running ? 'quit' : 'start'
  return [
    { action: 'flatten', label: ACTION_NAME.flatten, enabled: running, why: running ? '' : noProgram },
    { action: swing, label: ACTION_NAME[swing], enabled: running, why: running ? '' : noProgram },
    { action: power, label: ACTION_NAME[power], enabled: admin, why: admin ? '' : noAdmin },
  ]
}

export type RequestLine = { id: string; tone: 'wait' | 'done' | 'bad'; label: string; detail: string }

/** 一筆遙控指令現在到哪裡了。elapsed＝拿到資料之後又過了幾秒。 */
export function requestLine(req: RemoteRequest, elapsed: number): RequestLine {
  const name = ACTION_NAME[req.action] ?? req.action
  const at = clock(req.created_at)
  const line = (tone: RequestLine['tone'], label: string, detail: string): RequestLine => ({ id: req.id, tone, label: `${name}：${label}`, detail })
  if (req.done_code !== null) {
    if (REQUEST_OK.has(req.done_code)) return line('done', '完成', `${at} 按下；${req.done_note ?? ''}`)
    return line('bad', req.done_code === 'refused' ? '沒有執行' : '要人處理', req.done_note ?? req.done_code)
  }
  if (req.acked_at !== null) {
    if (req.code === 'ignored') return line('bad', '沒有執行', req.note ?? '')
    const waited = (req.ack_age_s ?? 0) + elapsed
    if (waited > REQUEST_LATE_S) return line('bad', '收到了，但還沒做完', `已經 ${waitText(waited)}；${req.note ?? ''}`)
    return line('wait', '已收到，處理中', req.note ?? '')
  }
  const age = req.age_s + elapsed
  if (age <= REQUEST_TTL_S) return line('wait', '等它收到…', `已經 ${waitText(age)}`)
  return line('bad', '沒有送到，已作廢', `${at} 按下，${REQUEST_TTL_S} 秒內沒有程式接手。還要做請再按一次`)
}

/** 這個分頁最近幾筆遙控指令的進度（新的在前）。 */
export const requestLines = (requests: readonly RemoteRequest[], mode: AutoMode, elapsed: number): RequestLine[] =>
  requests
    .filter((r) => r.target === mode)
    .slice(0, REQUESTS_PER_TAB)
    .map((r) => requestLine(r, elapsed))

/** 有沒有遙控指令還在等（等收到、等做完）：有的話畫面更新要變快。 */
export const requestsWaiting = (requests: readonly RemoteRequest[], elapsed: number): boolean => requests.some((r) => requestLine(r, elapsed).tone === 'wait')

/** 離每日虧損上限還有多少：used＝用掉的比例（0～1），left＝還能賠幾元。沒設上限是 null。 */
export function lossRoom(dayPnl: number, limit: number | null): { used: number; left: number } | null {
  if (limit === null || limit <= 0) return null
  const lost = Math.max(0, -dayPnl)
  return { used: Math.min(1, lost / limit), left: Math.max(0, limit - lost) }
}

/** 盤中每 15 秒看一次；畫面上的狀態不是「盤中」（已收盤、還沒有紀錄）時，每 SLOW_EVERY 次才抓一次，少打資料庫。 */
export const SLOW_EVERY = 4
export const refetchDue = (tick: number, live: boolean): boolean => live || tick % SLOW_EVERY === 0

/** 部位：空手／多 n 口／空 n 口。 */
export const positionText = (p: number): string => (p === 0 ? '空手' : `${p > 0 ? '多' : '空'} ${Math.abs(p)} 口`)

export type DayStats = { n: number; total: number; wins: number; losses: number; best: number | null; worst: number | null }

/** 每天的成績：有損益數字的日子才算；賺＝當日損益大於 0、賠＝小於 0（剛好 0 兩邊都不算）。 */
export function dayStats(days: readonly AutoDayRow[]): DayStats {
  const pnl = days.map((d) => d.pnl).filter((v): v is number => v !== null)
  return {
    n: pnl.length,
    total: pnl.reduce((a, b) => a + b, 0),
    wins: pnl.filter((v) => v > 0).length,
    losses: pnl.filter((v) => v < 0).length,
    best: pnl.length ? Math.max(...pnl) : null,
    worst: pnl.length ? Math.min(...pnl) : null,
  }
}

export type CumPoint = { day: string; pnl: number; cum: number }

/** 逐日累計（舊到新）：畫累計線用。 */
export function cumulative(days: readonly AutoDayRow[]): CumPoint[] {
  let cum = 0
  return days
    .filter((d): d is AutoDayRow & { pnl: number } => d.pnl !== null)
    .sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0))
    .map((d) => {
      cum += d.pnl
      return { day: d.day, pnl: d.pnl, cum }
    })
}

/** 累計線在寬 w、高 h 的圖裡的座標，與 0 那條線的高度。少於兩天畫不出線，回 null。 */
export function cumPath(points: readonly { cum: number }[], w: number, h: number): { line: string; zeroY: number } | null {
  if (points.length < 2) return null
  const lo = Math.min(0, ...points.map((p) => p.cum))
  const hi = Math.max(0, ...points.map((p) => p.cum))
  const span = hi - lo || 1
  const y = (v: number) => h - ((v - lo) / span) * h
  const x = (i: number) => (i / (points.length - 1)) * w
  return { line: points.map((p, i) => `${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(' '), zeroY: y(0) }
}

/** 損益是怎麼算的，一段話：模擬假設（真單沒有）＋已經扣掉的成本。 */
export function assumptionText(s: Pick<AutoStatus, 'sim' | 'contract'>): string {
  const tax = Math.round(s.contract.tax_rate * 1e7) / 100
  const cost = `損益已扣成本：手續費每口每邊 ${s.contract.commission_twd} 元、期交稅十萬分之 ${tax}（每點 ${s.contract.point_value} 元）。`
  if (s.sim === null) return cost
  return `模擬假設：下單後 ${s.sim.latency_s} 秒才成交、每邊滑價 ${s.sim.slippage_points} 點。${cost}模擬結果不代表真單會一樣。`
}

/** ISO 時間（帶 +08:00）→ HH:MM:SS。 */
export const clock = (iso: string): string => iso.slice(11, 19)
