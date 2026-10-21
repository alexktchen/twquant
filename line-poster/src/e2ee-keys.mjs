// 加密聊天（Letter Sealing）的金鑰（純函式：測試不用 linejs）。
//
// QR 登入時手機把帳號的金鑰鏈交給這台「第二台裝置」，linejs 存在 storage 的 `e2eeKeys:<keyId>`（有時還有
// `e2eeKeys:<mid>`）。群組開了加密就不收明文訊息，linejs 改發加密的那條要用這把私鑰；只存 access／refresh token
// 的話金鑰跟著登入那趟的程序一起蒸發，之後每次都是「E2EE Key has not been saved」，只能重新掃 QR。

export const E2EE_KEY_PREFIX = "e2eeKeys:";

/** 從 storage 全部內容（或存回來的那份）挑出自己的加密金鑰；只收 `e2eeKeys:` 開頭、值是字串的。沒有 → null。 */
export function pickE2EEKeys(all) {
  const out = {};
  for (const [k, v] of Object.entries(all ?? {})) {
    if (k.startsWith(E2EE_KEY_PREFIX) && typeof v === "string" && v) out[k] = v;
  }
  return Object.keys(out).length ? out : null;
}

const NO_E2EE_KEY_RE = /E2EE Key has not been saved/;

/** 沒有加密金鑰時，告訴看錯誤的人怎麼修（網站「LINE 群組」分頁會顯示這段）。 */
export function withE2EEHint(msg) {
  const s = String(msg ?? "");
  if (!NO_E2EE_KEY_RE.test(s)) return s;
  return `${s}（群組開了加密，這次登入沒存到加密金鑰：到網站「LINE 群組」分頁重新登入一次）`;
}
