// 純函式（不 import linejs，測試不用 npm install）。

/** 比對用：全形半形統一、轉小寫、去空白（同 new_erp normalizeForMatch）。 */
export function normalizeForMatch(s) {
  return String(s ?? "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");
}

/**
 * 記事本上有沒有 sinceMs 之後發的、內文一樣的貼文 → 回它的 id，沒有回 null。
 * 用在兩個地方：發文前（上一輪發了但沒記到，不要再發第二篇）與發文後（create 回應撿不到 id）。
 * 貼文沒有建立時間的照樣比內文（寧可認錯一篇同內文的，也不要重複發）。
 */
export function matchPost(posts, text, sinceMs) {
  const want = normalizeForMatch(text);
  if (!want) return null;
  const hit = posts.find((p) => p.postId
    && (!sinceMs || !p.createdMs || p.createdMs >= sinceMs)
    && normalizeForMatch(p.text) === want);
  return hit ? String(hit.postId) : null;
}
