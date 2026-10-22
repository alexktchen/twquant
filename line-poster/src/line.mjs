// LINE 群組聊天發圖卡（T-423／D-204）。
//
// ⚠ 登入與憑證的處理移植自 lt-foods/new_erp 的 supabase/functions/_shared/lineNote.ts（Edge 正式版）。
//   走的是 LINE **非官方**客戶端協定（linejs），違反 LINE 使用條款、帳號可能被停權：只能用備用帳號。
//
// 發到群組聊天（不是記事本，使用者 2026-10-21）：Flex 訊息走 LIFF 分享（api.line.me/message/v3/share，
// 跟手機上 LIFF 的「分享給好友」同一條），個人帳號也能發「一張圖、點了開網站」；發不出去時直接發那張圖
// （一般圖片訊息）＋一行網址，圖也不行才只發附連結的文字（talk.sendMessage，new_erp 對群組發結單提醒用的就是它）。
// 社群（OpenChat，使用者 2026-10-22）走 square 那一套：不發 Flex（LIFF 分享不支援社群），直接發圖＋一行網址。

import { loginWithAuthToken, loginWithQR } from "@evex/linejs";
import { MemoryStorage } from "@evex/linejs/storage";

import { chatKind, joinedSquareChats } from "./chat.mjs";
import { guardObsUpload } from "./obs.mjs";

export const DEFAULT_DEVICE = process.env.LINE_DEVICE || "ANDROIDSECONDARY";

export function log(...args) {
  console.error("[line-poster]", ...args);
}

/**
 * MemoryStorage + 「有人寫 refreshToken / expire 就通知我」。
 *
 * 憑證三個都要存：accessToken 只有 7 天、refreshToken 一年、expire 是到期秒數。linejs 的
 * tryRefreshToken 會把輪替後的 refreshToken 寫回 storage —— 不接這條線，新的那把跟著程序一起
 * 蒸發，下次拿舊的去換會被拒，等於又要重新掃 QR（new_erp 20260920000000 的教訓）。
 */
class PersistingStorage extends MemoryStorage {
  onCredential = null;
  seeded = {};

  async set(key, value) {
    await super.set(key, value);
    if (this.seeded[key] === value) return;
    if (key === "refreshToken" && typeof value === "string" && value) this.onCredential?.({ refreshToken: value });
    else if (key === "expire" && typeof value === "number") this.onCredential?.({ expire: value });
  }
}

/** 從跑起來的 client 身上把現在這組憑證撿出來（登入完、refresh 完都用這支對帳） */
export async function readCredential(client) {
  const st = client?.base?.storage;
  const get = async (k) => { try { return st ? await st.get(k) : undefined; } catch { return undefined; } };
  const refreshToken = await get("refreshToken");
  const expire = await get("expire");
  return {
    accessToken: String(client?.base?.authToken ?? ""),
    refreshToken: typeof refreshToken === "string" && refreshToken ? refreshToken : null,
    expire: typeof expire === "number" ? expire : null,
  };
}

/**
 * 整份 linejs storage 的快照（JSON 可序列化的值）。linejs 把登入時建立的端對端加密金鑰、reqseq 等都放在 storage，
 * 登入與送出在不同的 GitHub Actions run，要靠它把 session 接起來（等同 linejs FileStorage 的跨工作階段保存，只是改存資料庫）。
 */
export function dumpStorage(client) {
  const st = client?.base?.storage;
  if (!st || typeof st.getAll !== "function") return {};
  const out = {};
  for (const [k, v] of Object.entries(st.getAll())) {
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") out[k] = v;
  }
  return out;
}

/**
 * 網路瞬斷重試一次的 fetch（Thrift、LIFF 分享、obs 上傳圖片都走它）。只對「送不出去」的錯誤重試；
 * HTTP 回了什麼（4xx/5xx）、逾時中止都原樣丟回去——只有 obs 上傳被拒會在這裡丟錯（guardObsUpload）。
 */
const TRANSIENT_FETCH_RE = /error sending request|stream closed|broken pipe|connection (reset|closed|error)|ECONNRESET|EPIPE|fetch failed/i;
function retryingFetch() {
  return async (info, init) => {
    const req = new Request(info, init);
    const retry = req.clone(); // body 是串流，第一趟讀掉就沒了，先留一份
    let res;
    try {
      res = await globalThis.fetch(req);
    } catch (e) {
      const msg = String(e?.message ?? e);
      if (e?.name === "AbortError" || !TRANSIENT_FETCH_RE.test(msg)) throw e;
      log(`fetch 瞬斷，重試一次：${msg.slice(0, 160)}`);
      res = await globalThis.fetch(retry);
    }
    return guardObsUpload(req, res);
  };
}

/**
 * 用存好的憑證登入。⚠ 一定要把 refreshToken 一起傳：access token 過期後 linejs 只有在 storage
 * 裡有 refreshToken 時才會自動換新，沒有就直接丟 MUST_REFRESH_V3_TOKEN（看起來像掉線，其實是到期沒得換）。
 * onCredential 必須在 loginWithAuthToken 之前掛好：冷啟動那趟 getProfile 就可能觸發換新。
 */
export async function clientFromCredential(cred, { onCredential, device = DEFAULT_DEVICE } = {}) {
  if (!cred?.accessToken) throw new Error("沒有登入憑證，先跑 twquant line-group login");
  // 用上次存的整份 storage 當底（端對端加密金鑰、reqseq…都在裡面），再補上這次的 refreshToken／expire
  const seed = { ...(cred.storage ?? {}) };
  if (cred.refreshToken) seed.refreshToken = cred.refreshToken;
  if (typeof cred.expire === "number") seed.expire = cred.expire;
  const storage = new PersistingStorage(seed);
  storage.seeded = { ...seed };
  storage.onCredential = onCredential ?? null;
  const client = await loginWithAuthToken({
    accessToken: cred.accessToken,
    ...(cred.refreshToken ? { refreshToken: cred.refreshToken } : {}),
    ...(typeof cred.expire === "number" ? { expire: cred.expire } : {}),
  }, { device, storage, fetch: retryingFetch() });
  client.base.on("update:authtoken", (t) => onCredential?.({ accessToken: t }));
  // 上面那行掛得再快也趕不上 loginWithAuthToken 裡面 ready() 觸發的那次 refresh，回來再對一次
  if (client.base.authToken && client.base.authToken !== cred.accessToken) {
    onCredential?.({ accessToken: client.base.authToken });
  }
  return client;
}

/** 掃 QR 登入（備用帳號的手機掃、再輸入 PIN）。deadlineMs 內沒掃完就放棄。 */
export async function loginByQr({ onQr, onPin, deadlineMs, device = DEFAULT_DEVICE }) {
  const login = loginWithQR(
    { onReceiveQRUrl: onQr, onPincodeRequest: onPin },
    { device, storage: new MemoryStorage(), fetch: retryingFetch() },
  );
  const timeout = new Promise((_, reject) =>
    setTimeout(() => reject(new Error("等太久沒掃 QR，請再跑一次 login")), deadlineMs));
  return await Promise.race([login, timeout]);
}

export function whoami(client) {
  const p = client.base.profile ?? {};
  return { mid: p.mid, displayName: p.displayName };
}

/**
 * 加入中的群組（c…）。被別人拉進群組時只是「邀請」，要接受才算加入；只列已加入的話，
 * 小幫手被拉進去後清單永遠看不到（new_erp 2026-09-25「加了三群但是看不到」）。這裡順手接受邀請。
 */
export async function listGroups(client) {
  const all = await client.base.talk.getAllChatMids({
    request: { withMemberChats: true, withInvitedChats: true },
    syncReason: "INTERNAL",
  });
  const member = [...(all?.memberChatMids ?? [])];
  for (const mid of all?.invitedChatMids ?? []) {
    try {
      await client.base.talk.acceptChatInvitation({ request: { reqSeq: 0, chatMid: mid } });
      log("已接受群組邀請:", mid);
    } catch (e) {
      log(`acceptChatInvitation(${mid}) 失敗：${e?.message ?? e}`);
    }
    member.push(mid);
  }
  if (!member.length) return [];
  const { chats } = await client.base.talk.getChats({ chatMids: member });
  return (chats ?? [])
    .filter((c) => c.type === "GROUP" || c.type === 0 || String(c.chatMid ?? "").startsWith("c"))
    .map((c) => ({ homeId: String(c.chatMid), name: c.chatName ?? "" }));
}

/**
 * 加入中的社群聊天（m…）。社群要先用手機以這支帳號加入（每個社群各自取暱稱）；這裡不會自己加入，
 * 也不會自己接受邀請——社群通常有規矩（禁廣告、禁機器人），要不要進去是人決定的。
 */
export async function listSquareChats(client) {
  const out = [];
  const seen = new Set();
  let token;
  for (;;) {
    const res = await client.base.square.getJoinedSquareChats({
      request: { limit: 100, ...(token ? { continuationToken: token } : {}) },
    });
    out.push(...joinedSquareChats(res));
    token = res?.continuationToken;
    if (!token || seen.has(token)) return out;
    seen.add(token);
  }
}

// ── 發訊息 ─────────────────────────────────────────────────────────────────

/** linejs 的錯誤把 LINE 回的內容放在 data，併進訊息裡（判斷要不要停、寫進資料庫都看訊息）。 */
function withData(e) {
  const data = e?.data && Object.keys(e.data).length ? ` ${JSON.stringify(e.data).slice(0, 600)}` : "";
  return new Error(`${e?.message ?? e}${data}`);
}

/**
 * 用 LIFF 分享發一則 Flex 訊息到群組聊天。liffId 不給就用 linejs 內建的那個 LIFF app（第三方的）；
 * 給自己的 LIFF app（LINE Login channel、scope 要 chat_message.write）比較穩，也不會掛別人的名字。
 */
export async function sendFlex(client, chatMid, message, { liffId } = {}) {
  if (chatKind(chatMid) !== "group") throw new Error(`社群不發 Flex（LIFF 分享不支援社群）：${chatMid}`);
  if (liffId) client.base.liff.liffId = liffId;
  try {
    return await client.base.liff.sendLiff({ to: String(chatMid), messages: [message] });
  } catch (e) {
    throw withData(e);
  }
}

/**
 * 對群組或社群聊天發一張圖（看得到圖，但點了只是放大，不會開網站——網址另外用文字發）。png 是 Blob。
 * - 一般群組：linejs 的端對端加密上傳（uploadMediaByE2EE，跟發文字一樣用登入時存下的金鑰）。
 * - 社群：不加密、不給 oid 直接上傳（reqseq 模式），伺服器收到就自己發那則訊息；社群不收「先發空的圖片訊息再補上傳」
 *   （ILLEGAL_ARGUMENT，linejs SquareChat.sendImage 用的也是這招）。上傳被拒由 guardObsUpload 擋。
 */
export async function sendImage(client, chatMid, png) {
  const kind = chatKind(chatMid);
  if (!png?.size) throw new Error("圖是空的");
  try {
    if (kind === "square") return await client.base.obs.uploadObjTalk(String(chatMid), "image", png, undefined, "twquant.png");
    return await client.base.obs.uploadMediaByE2EE({ data: png, oType: "image", to: String(chatMid), filename: "twquant.png" });
  } catch (e) {
    throw withData(e);
  }
}

/** 對群組或社群聊天發一段文字（圖片後面附的網址、或圖都發不出去時的摘要；網址 LINE 會自己做成連結預覽）。 */
export async function sendText(client, chatMid, text) {
  const kind = chatKind(chatMid);
  if (!text) throw new Error("訊息不能是空的");
  try {
    if (kind === "square") return await client.base.square.sendMessage({ squareChatMid: String(chatMid), text: String(text) });
    return await client.base.talk.sendMessage({ to: String(chatMid), text: String(text) });
  } catch (e) {
    throw withData(e);
  }
}
