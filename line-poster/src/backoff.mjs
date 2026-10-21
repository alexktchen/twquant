// LINE 叫我們慢一點的錯誤（new_erp line-note-worker 實測，2026-10-08）：
//   code=403「因違反服務條款，您已被限制使用社群。」（errorExtraInfo.userRestrictionInfo）→ 約 5 分鐘自己解除，停 10 分鐘
//   code=701「請稍後再重新發布貼文」（同帳號連發十幾篇）→ 停 2 分鐘
// 撞到就整個帳號停，後面排隊的不要繼續硬打；這筆不算失敗，下一輪再送。
const RULES = [
  { re: /限制使用|userRestrictionInfo/, ms: 10 * 60_000 },
  { re: /code=701\b|請稍後再重新發布/, ms: 2 * 60_000 },
];

/** 這個錯誤要帳號停多久（毫秒）；不是「叫我們慢一點」的錯誤回 null。 */
export function lineBackoffMs(message) {
  const msg = String(message ?? "");
  for (const r of RULES) if (r.re.test(msg)) return r.ms;
  return null;
}
