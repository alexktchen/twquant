// 一筆貼文怎麼送（純函式：真正送訊息的函式由呼叫端注入，測試不用 linejs）。
import { lineBackoffMs } from "./backoff.mjs";

/**
 * 先發 Flex 圖卡；不是「LINE 叫我們慢一點」的失敗就改發附連結的文字（fallbackText），兩個都失敗才算沒送出。
 * 回 { sent, via, error?, flexError?, backoffMs? }。LINE 叫停時不試備案：帳號正被限制，再打只會更久。
 */
export async function deliver(job, { sendFlex, sendText }) {
  let flexError;
  try {
    await sendFlex(job.homeId, job.message);
    return { sent: true, via: "flex" };
  } catch (e) {
    flexError = String(e?.message ?? e);
  }
  const backoff = lineBackoffMs(flexError);
  if (backoff !== null || !job.fallbackText) {
    return { sent: false, error: flexError.slice(0, 1000), backoffMs: backoff };
  }
  try {
    await sendText(job.homeId, job.fallbackText);
    return { sent: true, via: "text", flexError: flexError.slice(0, 1000) };
  } catch (e) {
    const textError = String(e?.message ?? e);
    return {
      sent: false,
      error: `圖卡：${flexError}；文字：${textError}`.slice(0, 1000),
      backoffMs: lineBackoffMs(textError),
    };
  }
}
