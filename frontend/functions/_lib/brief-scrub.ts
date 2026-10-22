// 晨報／盤後快訊交給沒有名單權限的人之前，拿掉名單的痕跡（T-435／D-212，使用者 2026-10-22：「首頁那邊的名單跟該擋的都要擋」）。
// 2026-10-22 以前產生、已經存起來的晨報：事實有「今天名單「大盤精選」15 檔，其中新增 3 檔」，名單股的公告標「名單」
// （有的只因為在名單上才被挑進來），規則句有「今日名單 …」，模型寫的句子可能提到。之後產生的晨報不讀名單
// （reporting/morning_brief.py），這裡對它們不會改到任何東西。純函數，供 node 單元測試。
type Row = Record<string, unknown>

const LIST_WORD = '名單'
/** 公告事實句：「重大訊息 8084 巨虹：主旨（名單、變更交易／下市）」——最後一組全形括號是標記。 */
const ANNOUNCEMENT = /^重大訊息 (\S+) ([^：]*)：.*（([^（）]*)）$/
/** 規則句全部被拿掉時（只剩名單那段）用的句子，同 morning_brief.rule_text 沒資料時。 */
const NO_DATA = '今天沒有可用的資料。'

const strs = (x: unknown): string[] | null => (Array.isArray(x) ? x.filter((s): s is string => typeof s === 'string') : null)

/** 一份晨報（資料庫那一列）→ 沒有名單痕跡的版本；null 照回。 */
export function scrubBrief(b: Row | null): Row | null {
  if (b === null) return b
  // 只因為在名單上才被挑進晨報的股票（代號與股名）：模型寫的句子提到它們也要拿掉
  const listOnly: string[] = []
  const facts = strs(b.facts)?.flatMap((f) => {
    if (f.startsWith('今天名單「')) return []
    const m = ANNOUNCEMENT.exec(f)
    if (m && m[3].split('、').includes(LIST_WORD)) {
      const rest = m[3].split('、').filter((t) => t !== LIST_WORD)
      if (rest.length === 0) {
        listOnly.push(m[1], ...(m[2].trim() ? [m[2].trim()] : []))
        return []
      }
      return [`${f.slice(0, f.length - m[3].length - 2)}（${rest.join('、')}）`]
    }
    return f.includes(LIST_WORD) ? [] : [f]
  })
  const leaks = (s: unknown) => typeof s === 'string' && (s.includes(LIST_WORD) || listOnly.some((k) => s.includes(k)))
  const rule = typeof b.rule_text === 'string' ? b.rule_text.replace(/。$/, '').split('；').filter((p) => !leaks(p)) : null
  return {
    ...b,
    facts: facts ?? b.facts ?? null,
    rule_text: rule === null ? b.rule_text : rule.length > 0 ? `${rule.join('；')}。` : NO_DATA,
    headline: leaks(b.headline) ? null : (b.headline ?? null),
    bullets: strs(b.bullets)?.filter((x) => !leaks(x)) ?? b.bullets ?? null,
    risk_note: leaks(b.risk_note) ? null : (b.risk_note ?? null),
  }
}
