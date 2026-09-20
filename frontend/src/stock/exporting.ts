// 儀表板的匯出：CSV 內容為純函數（可測）；觸發瀏覽器下載的 DOM 操作另外放，兩者不混。

type Row = { d: string; o: number; h: number; l: number; c: number; v?: number }

/**
 * 日 K 轉 CSV。成交量單位是「股」並寫在欄名上（SPEC-000：不在輸出端偷換成張）；
 * 缺成交量的列留空，不補 0。開頭加 BOM，Excel 才會以 UTF-8 開中文檔名以外的內容。
 */
export function ohlcCsv(rows: Row[]): string {
  const lines = ['date,open,high,low,close,volume_shares']
  for (const r of rows) lines.push([r.d, r.o, r.h, r.l, r.c, r.v ?? ''].join(','))
  return `﻿${lines.join('\n')}\n`
}

/** Hand a blob to the browser as a file download. */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
