// 名單列的型態標記（T-440／D-215 §四）：箱型或旗型突破。純函式，供 node 單元測試；後端資料形狀見 functions/_lib/pattern.ts。
// 只供看盤：名單不因型態刪減。
import { pct } from './format.ts'

const whole = (x: number) => `${Math.round(x * 100)}%`

/** 後端判出來的型態：box＝前 60 日窄幅整理後突破；flag＝急漲（旗桿）、整理量縮（旗面）後突破；n＝急漲後拉回一次再過前高。over＝收盤高出前高多少。 */
export type Pattern =
  | { kind: 'box'; over: number; width: number; days: number }
  | { kind: 'flag'; over: number; pole: number; days: number }
  | { kind: 'n'; over: number; pole: number; days: number; retrace: number }

/** 型態的名稱與完整說明；沒有型態、舊後端沒有這欄 → null。 */
export function patternTag(p: Pattern | null | undefined): { label: string; title: string } | null {
  if (!p) return null
  const tail = '只是看圖的提示，名單不因此刪減。'
  if (p.kind === 'box')
    return {
      label: '箱型突破',
      title: `前 ${p.days} 個交易日最高與最低只差 ${whole(p.width)}（箱型整理），今天收盤高出箱頂 ${pct(p.over, 1)}（已還原除權息）。${tail}`,
    }
  if (p.kind === 'n')
    return {
      label: 'N字突破',
      title: `先急漲 ${whole(p.pole)}，拉回 ${p.days} 個交易日（回檔約旗桿的 ${whole(p.retrace)}），今天收盤再過前高 ${pct(p.over, 1)}（已還原除權息）。${tail}`,
    }
  return {
    label: '旗型突破',
    title: `先急漲 ${whole(p.pole)}（旗桿），再整理 ${p.days} 個交易日、回檔不深且量縮（旗面），今天收盤高出前高 ${pct(p.over, 1)}（已還原除權息）。${tail}`,
  }
}
