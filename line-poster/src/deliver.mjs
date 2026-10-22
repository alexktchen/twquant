// 一筆貼文怎麼送（純函式：真正送訊息的函式由呼叫端注入，測試不用 linejs）。
import { lineBackoffMs } from "./backoff.mjs";

const LABEL = { noteError: "記事本", flexError: "圖卡", imageError: "圖片", textError: "文字" };
const errText = (e) => String(e?.message ?? e).slice(0, 1000);

/** 失敗的每一招都寫進錯誤；只有一招就原樣。 */
function joined(errors) {
  const parts = Object.entries(errors);
  if (parts.length === 1) return parts[0][1];
  return parts.map(([k, v]) => `${LABEL[k]}：${v}`).join("；").slice(0, 1000);
}

/**
 * 依序試四招，前一招不是「LINE 叫我們慢一點」的失敗才試下一招：
 *   0. 記事本發文（圖 job.noteCard＋摘要＋網址）再分享到聊天（job.noteHome：記事本在哪；空的就跳過）。
 *      之前已經發過那篇（job.noteId，例如上次分享時被叫停）就只分享，不再發一篇。
 *   1. Flex 圖卡（LIFF 分享）：整則是圖、點圖打開網站。job.message 空的就跳過（社群：LIFF 分享不支援）。
 *   2. 直接發那張圖（job.card，一般圖片訊息：看得到、點了只是放大），再發一行附網址的文字（job.imageCaption）。
 *   3. 摘要＋網址的文字（job.fallbackText）。
 * 回 { sent, via: note|flex|image|text, notePostId?, error?, noteError?, flexError?, imageError?, captionError?, backoffMs? }。
 * notePostId：記事本上已經有這篇（不管最後有沒有送出都回，下次重試才不會再發一篇）。
 * LINE 叫停時不試下一招：帳號正被限制，再打只會更久。
 */
export async function deliver(job, { postNote, shareNote, sendFlex, sendImage, sendText }) {
  const errors = {};
  const extra = {};
  let backoff = null;
  if (job.noteHome) {
    try {
      if (!job.noteId) extra.notePostId = await postNote(job.noteHome, job.noteText, job.noteCard);
      else extra.notePostId = String(job.noteId);
      await shareNote(job.noteHome, extra.notePostId, job.homeId);
      return { sent: true, via: "note", ...extra };
    } catch (e) {
      errors.noteError = errText(e);
    }
    backoff = lineBackoffMs(errors.noteError);
    if (backoff !== null) return { sent: false, error: joined(errors), backoffMs: backoff, ...extra };
  }
  if (job.message) {
    try {
      await sendFlex(job.homeId, job.message);
      return { sent: true, via: "flex", ...errors, ...extra };
    } catch (e) {
      errors.flexError = errText(e);
    }
    backoff = lineBackoffMs(errors.flexError);
    if (backoff !== null) return { sent: false, error: joined(errors), backoffMs: backoff, ...extra };
  }

  if (job.card) {
    try {
      await sendImage(job.homeId, job.card);
    } catch (e) {
      errors.imageError = errText(e);
    }
    if (!errors.imageError) {
      // 圖已經在群組裡了：後面那行網址發不出去也算送出（重送的話圖會出現兩次）
      const out = { sent: true, via: "image", ...errors, ...extra };
      if (job.imageCaption) {
        try {
          await sendText(job.homeId, job.imageCaption);
        } catch (e) {
          out.captionError = errText(e);
          const b = lineBackoffMs(out.captionError);
          if (b !== null) out.backoffMs = b;
        }
      }
      return out;
    }
    backoff = lineBackoffMs(errors.imageError);
    if (backoff !== null) return { sent: false, error: joined(errors), backoffMs: backoff, ...extra };
  }

  if (!job.fallbackText) {
    if (!Object.keys(errors).length) errors.textError = "沒有任何能發的東西（這種聊天不發 Flex，圖片與文字備案又都關著）";
    return { sent: false, error: joined(errors), backoffMs: null, ...extra };
  }
  try {
    await sendText(job.homeId, job.fallbackText);
    return { sent: true, via: "text", ...errors, ...extra };
  } catch (e) {
    const textError = errText(e);
    return { sent: false, error: joined({ ...errors, textError }), backoffMs: lineBackoffMs(textError), ...extra };
  }
}
