// 自選股與研究筆記的本機持久化（SPEC-014 §6）。
// 鍵名沿用 stock 頁的 twquant.watchlist，舊格式（純字串陣列）必須能讀。
// 損毀資料一律保留原值並回報，絕不默默覆寫或刪除。

export const WATCH_KEY = 'twquant.watchlist'
export const WATCH_VERSION = 2

/** One watched stock plus the research note and recommendation run that justified it. */
export type WatchEntry = {
  stock_id: string
  note: string
  run_id: string | null
  /** 買進價（D-116）：只存在這台瀏覽器，永不送到伺服器。null＝未填。 */
  entry_price: number | null
}

/** ok = usable; legacy = old array upgraded in memory; corrupt = present but unreadable. */
export type WatchStatus = 'ok' | 'legacy' | 'corrupt'

export type WatchState = {
  entries: WatchEntry[]
  status: WatchStatus
  /** Human-readable reason, present whenever the status is not ok. */
  detail: string | null
}

function entryFrom(value: unknown): WatchEntry | null {
  const row = value as Record<string, unknown> | null
  if (!row || typeof row.stock_id !== 'string' || row.stock_id === '') return null
  // 欄位缺席（D-116 之前存的資料）＝未填；出現卻不是正數＝損毀，不得默默當成未填。
  const price = row.entry_price ?? null
  if (price !== null && !(typeof price === 'number' && Number.isFinite(price) && price > 0)) return null
  return {
    stock_id: row.stock_id,
    note: typeof row.note === 'string' ? row.note : '',
    run_id: typeof row.run_id === 'string' ? row.run_id : null,
    entry_price: price,
  }
}

/**
 * Decode a stored watchlist without ever discarding data it cannot understand.
 *
 * A legacy `["0050"]` array upgrades in memory with empty notes; anything unrecognisable is
 * reported as corrupt so the caller can warn instead of silently starting from empty.
 */
export function parseWatchlist(raw: string | null): WatchState {
  if (raw === null || raw === '') return { entries: [], status: 'ok', detail: null }
  let decoded: unknown
  try {
    decoded = JSON.parse(raw)
  } catch {
    return { entries: [], status: 'corrupt', detail: '自選資料不是有效的 JSON，已保留原值未覆寫' }
  }
  if (Array.isArray(decoded)) {
    // 舊格式：純字串陣列。代號保持字串，"0050" 不得被轉成數字。
    const ids = decoded.filter((item): item is string => typeof item === 'string' && item !== '')
    if (ids.length !== decoded.length) {
      return { entries: [], status: 'corrupt', detail: '舊版自選含非字串項目，已保留原值未覆寫' }
    }
    return {
      entries: ids.map(stock_id => ({ stock_id, note: '', run_id: null, entry_price: null })),
      status: 'legacy',
      detail: '讀到舊版自選格式；下次修改時才會升級寫入，現在不動原值',
    }
  }
  const object = decoded as Record<string, unknown> | null
  if (!object || !Array.isArray(object.entries)) {
    return { entries: [], status: 'corrupt', detail: '自選資料格式無法辨識，已保留原值未覆寫' }
  }
  const entries = object.entries.map(entryFrom)
  if (entries.some(entry => entry === null)) {
    return { entries: [], status: 'corrupt', detail: '自選資料有無法辨識的項目，已保留原值未覆寫' }
  }
  return { entries: entries as WatchEntry[], status: 'ok', detail: null }
}

/** Encode the current watchlist in the versioned format. */
export function serializeWatchlist(entries: WatchEntry[]): string {
  return JSON.stringify({ version: WATCH_VERSION, entries })
}

/** Add or remove one stock, keeping any note already written for it. */
export function toggleWatch(entries: WatchEntry[], stock_id: string, run_id: string | null = null): WatchEntry[] {
  return entries.some(entry => entry.stock_id === stock_id)
    ? entries.filter(entry => entry.stock_id !== stock_id)
    : [...entries, { stock_id, note: '', run_id, entry_price: null }]
}

/** Set the research note for a watched stock; unknown stocks are added with that note. */
export function setNote(entries: WatchEntry[], stock_id: string, note: string): WatchEntry[] {
  return entries.some(entry => entry.stock_id === stock_id)
    ? entries.map(entry => (entry.stock_id === stock_id ? { ...entry, note } : entry))
    : [...entries, { stock_id, note, run_id: null, entry_price: null }]
}

/** Set or clear (null) the local entry price of a watched stock. */
export function setEntryPrice(entries: WatchEntry[], stock_id: string, entry_price: number | null): WatchEntry[] {
  return entries.map(entry => (entry.stock_id === stock_id ? { ...entry, entry_price } : entry))
}

/** Replace the watched set from a plain id list while preserving existing notes and runs. */
export function fromIds(entries: WatchEntry[], ids: string[]): WatchEntry[] {
  return ids.map(stock_id => entries.find(entry => entry.stock_id === stock_id) ?? { stock_id, note: '', run_id: null, entry_price: null })
}

/** Read the watchlist, turning a blocked storage read into an explicit corrupt state. */
export function readWatchlist(): WatchState {
  try {
    return parseWatchlist(localStorage.getItem(WATCH_KEY))
  } catch {
    return { entries: [], status: 'corrupt', detail: '瀏覽器封鎖了本機儲存，無法讀取自選' }
  }
}

/** Persist the watchlist; a failed write is reported rather than silently dropped. */
export function writeWatchlist(entries: WatchEntry[]): string | null {
  try {
    localStorage.setItem(WATCH_KEY, serializeWatchlist(entries))
    return null
  } catch {
    return '自選無法寫入本機儲存（可能是隱私模式或容量已滿），這次的變更只在本頁有效'
  }
}
