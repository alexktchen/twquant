// 圖片上傳的把關（純函式：不用 linejs，測試直接跑）。

const OBS_HOST = "obs.line-apps.com";

/**
 * linejs 上傳圖片到 obs 時不看 HTTP 狀態：上傳被拒也照樣拿空的 OID 發一則訊息，群組裡就是一張破圖。
 * 上傳失敗在這裡就丟錯，那則訊息不會發出去（改走下一招）。
 */
export function guardObsUpload(req, res) {
  if (req.method === "POST" && new URL(req.url).hostname === OBS_HOST && !res.ok) {
    throw new Error(`圖片上傳失敗（obs HTTP ${res.status}）`);
  }
  return res;
}
