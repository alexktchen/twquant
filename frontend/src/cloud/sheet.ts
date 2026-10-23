// 手機底部抽屜（T-442；使用者 2026-10-23 看「台中恐怖島」截圖：「我的彈窗也要像這個可以上下拖動」「請用一個固定高度」）的純函式。
// 抽屜高度固定（CSS 的 --sheet-h），不會跟著拖曳變高變矮；拖上面那條把手是把整張往下推／拉回來：
// 停在「全開」（offset 0）或「只露出頭」（offset ＝ 高度 − SHEET_PEEK），再往下推就關掉。畫面在 SheetGrip.tsx。

/** 「只露出頭」時露出的高度（px）：把手＋第一排（類型、出處、關閉鍵） */
export const SHEET_PEEK = 96
/** 甩的速度（px/ms）超過這個就照甩的方向走，不看停在哪 */
export const SHEET_FLICK = 0.5
/** 拖得比這個短（px）當成點一下：露頭時點把手＝拉回全開 */
export const SHEET_TAP = 6

export type SheetPos = 'open' | 'peek' | 'closed'

/** 每個位置的 offset（整張往下推幾 px）；closed＝整張推出畫面 */
export const sheetOffset = (pos: SheetPos, height: number, peek = SHEET_PEEK): number => (pos === 'open' ? 0 : pos === 'peek' ? Math.max(0, height - peek) : height)

/** 拖曳中的 offset：往上拉不能超過全開（頂住），往下沒有上限（放手才決定） */
export const clampSheetOffset = (offset: number): number => Math.max(0, offset)

/**
 * 放手後停在哪：甩得快就照方向走一格（全開→露頭→關掉；往上甩回全開），
 * 否則停在離現在最近的位置（全開 0、露頭 height−peek、關掉 height）。
 */
export function settleSheet(offset: number, vy: number, from: SheetPos, height: number, peek = SHEET_PEEK): SheetPos {
  if (vy >= SHEET_FLICK) return from === 'open' ? 'peek' : 'closed'
  if (vy <= -SHEET_FLICK) return 'open'
  const stops: [SheetPos, number][] = [
    ['open', 0],
    ['peek', sheetOffset('peek', height, peek)],
    ['closed', height],
  ]
  let best: SheetPos = 'open', bd = Infinity
  for (const [pos, at] of stops) {
    const d = Math.abs(offset - at)
    if (d < bd) {
      bd = d
      best = pos
    }
  }
  return best
}

/** 速度：最近一段的位移／時間（px/ms）；時間太短（<1ms）當 0，避免除出無限大 */
export const sheetVelocity = (dy: number, dtMs: number): number => (dtMs < 1 ? 0 : dy / dtMs)
