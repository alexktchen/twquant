// LINE 群組聊天發圖卡（T-423／D-204）。
//
// ⚠ 登入與憑證的處理移植自 lt-foods/new_erp 的 supabase/functions/_shared/lineNote.ts（Edge 正式版）。
//   走的是 LINE **非官方**客戶端協定（linejs），違反 LINE 使用條款、帳號可能被停權：只能用備用帳號。
//
// 發到群組聊天（不是記事本，使用者 2026-10-21）：Flex 訊息走 LIFF 分享（api.line.me/message/v3/share，
// 跟手機上 LIFF 的「分享給好友」同一條），個人帳號也能發「一張圖、點了開網站」；發不出去時改發一則附連結的文字
// （talk.sendMessage，new_erp 對群組發結單提醒用的就是它）。

import { loginWithAuthToken, loginWithQR } from "@evex/linejs";
import { MemoryStorage } from "@evex/linejs/storage";

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
 * 網路瞬斷重試一次的 fetch（Thrift 與 LIFF 分享都走它）。只對「送不出去」的錯誤重試；
 * HTTP 回了什麼（4xx/5xx）、逾時中止都原樣丟回去。
 */
const TRANSIENT_FETCH_RE = /error sending request|stream closed|broken pipe|connection (reset|closed|error)|ECONNRESET|EPIPE|fetch failed/i;
function retryingFetch() {
  return async (info, init) => {
    const req = new Request(info, init);
    const retry = req.clone(); // body 是串流，第一趟讀掉就沒了，先留一份
    try {
      return await globalThis.fetch(req);
    } catch (e) {
      const msg = String(e?.message ?? e);
      if (e?.name === "AbortError" || !TRANSIENT_FETCH_RE.test(msg)) throw e;
      log(`fetch 瞬斷，重試一次：${msg.slice(0, 160)}`);
      return await globalThis.fetch(retry);
    }
  };
}

/**
 * 用存好的憑證登入。⚠ 一定要把 refreshToken 一起傳：access token 過期後 linejs 只有在 storage
 * 裡有 refreshToken 時才會自動換新，沒有就直接丟 MUST_REFRESH_V3_TOKEN（看起來像掉線，其實是到期沒得換）。
 * onCredential 必須在 loginWithAuthToken 之前掛好：冷啟動那趟 getProfile 就可能觸發換新。
 */
export async function clientFromCredential(cred, { onCredential, device = DEFAULT_DEVICE } = {}) {
  if (!cred?.accessToken) throw new Error("沒有登入憑證，先跑 twquant line-group login");
  const storage = new PersistingStorage();
  storage.seeded = { refreshToken: cred.refreshToken ?? undefined, expire: cred.expire ?? undefined };
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

// ── 發訊息 ─────────────────────────────────────────────────────────────────

function assertGroup(chatMid) {
  // 社群（s…／m…）走另一套 API；D-204 只發一般群組
  if (!String(chatMid).startsWith("c")) throw new Error(`只支援一般群組（c…），拿到 ${chatMid}`);
}

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
  assertGroup(chatMid);
  if (liffId) client.base.liff.liffId = liffId;
  try {
    return await client.base.liff.sendLiff({ to: String(chatMid), messages: [message] });
  } catch (e) {
    throw withData(e);
  }
}

/** 對群組聊天發一段文字（Flex 發不出去時的備案，網址 LINE 會自己做成連結預覽）。 */
export async function sendText(client, chatMid, text) {
  assertGroup(chatMid);
  if (!text) throw new Error("訊息不能是空的");
  try {
    return await client.base.talk.sendMessage({ to: String(chatMid), text: String(text) });
  } catch (e) {
    throw withData(e);
  }
}
