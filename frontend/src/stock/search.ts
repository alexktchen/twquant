// 個股搜尋字串 → 股票代號。代號一律是字串，前導零原樣保留（"0050" 不是 50）。

type NameMap = Record<string, { name: string | null }>

/** 上市櫃代號形狀：數字開頭、4~6 碼、可帶英文尾碼（如 00632R）；與後端白名單同向但更嚴。 */
const CODE = /^[0-9][0-9A-Z]{3,5}$/

/**
 * Resolve what the user typed into a stock id, or null when it names nothing.
 *
 * A leading well-formed code wins ("0050 元大台灣50" → "0050"), so the datalist's
 * "code name" suggestions work. Otherwise the text must equal one stock's name, or be
 * contained in exactly one name — an ambiguous fragment resolves to nothing rather than a guess.
 */
export function resolveStockQuery(raw: string, meta: NameMap): string | null {
  const text = raw.trim()
  if (!text) return null
  const token = text.split(/\s+/)[0].toUpperCase()
  if (CODE.test(token)) return token
  const exact = Object.entries(meta).find(([, entry]) => entry.name === text)
  if (exact) return exact[0]
  const partial = Object.entries(meta).filter(([, entry]) => entry.name?.includes(text))
  return partial.length === 1 ? partial[0][0] : null
}
