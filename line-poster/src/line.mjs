// LINE 群組記事本發文（T-423／D-204）。
//
// ⚠ 移植自 lt-foods/new_erp 的 supabase/functions/_shared/lineNote.ts（Edge 正式版）與
//   tools/line-note-scraper/src/line.mjs，只留「登入、列群組、記事本發文、分享卡片到群組聊天」。
//   走的是 LINE **非官方**客戶端協定（linejs），違反 LINE 使用條款、帳號可能被停權：只能用備用帳號。
//
// 記事本不是 Thrift，是 LINE 內部的 JSON REST（myhome）：
//   群組  GET https://<host>/mh/api/v57/post/list.json?homeId=<c…>&sourceType=TALKROOM
// header 要 X-Line-Access（登入 token）+ X-Line-ChannelToken（channel token）+ X-Line-Mid。
// 沒有 wire trace，所以 host / 路徑前綴 / channel 都做了候選清單，第一個回 code 0 的就記住。
// ⚠ 不要改用 linejs 內建的 timeline.createPost/listPost —— 它把網址寫死成 legy.line-apps.com
//   （LEGY 加密閘道，不吃純 HTTPS JSON），直接 fetch failed。

import { loginWithAuthToken, loginWithQR } from "@evex/linejs";
import { MemoryStorage } from "@evex/linejs/storage";
import { matchPost } from "./match.mjs";

export const DEFAULT_DEVICE = process.env.LINE_DEVICE || "ANDROIDSECONDARY";

const CHANNEL_IDS = {
  HOME: "1341209850",
  TIMELINE: "1341209950",
  NOTE: "1655599932",
  SQUARE_NOTE: "1657618623",
};

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
 * 網路瞬斷重試一次的 fetch（Thrift 與記事本 REST 都走它）。只對「送不出去」的錯誤重試；
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

// ── 記事本 REST ────────────────────────────────────────────────────────────

function assertGroup(homeId) {
  // 社群（s…／m…）要另外找主聊天室、路徑也不同；D-204 只發一般群組
  if (!String(homeId).startsWith("c")) throw new Error(`只支援一般群組（c…），拿到 ${homeId}`);
}

function hostCandidates(client) {
  const forced = process.env.LINE_NOTE_HOST;
  if (forced) return [forced];
  const ep = client.base.request?.endpoint;
  return [...new Set([ep, "gw.line.naver.jp", "ga2.line.naver.jp"].filter(Boolean))];
}

const PREFIXES = process.env.LINE_NOTE_PREFIX ? [process.env.LINE_NOTE_PREFIX] : ["/mh", "/ext/note/nt"];
const CHANNELS = process.env.LINE_NOTE_CHANNEL
  ? [process.env.LINE_NOTE_CHANNEL]
  : [CHANNEL_IDS.HOME, CHANNEL_IDS.NOTE, CHANNEL_IDS.TIMELINE, CHANNEL_IDS.SQUARE_NOTE];

// channel token 是「這個帳號 × 這個 channel」的，依 client 分開存
const tokenCache = new WeakMap();
function tokensFor(client) {
  let m = tokenCache.get(client);
  if (!m) { m = new Map(); tokenCache.set(client, m); }
  return m;
}
async function channelToken(client, channelId) {
  const cache = tokensFor(client);
  if (cache.has(channelId)) return cache.get(channelId);
  let token;
  try {
    const r = await client.base.channel.approveChannelAndIssueChannelToken({ channelId });
    token = r?.channelAccessToken ?? r?.token;
  } catch (e) {
    log(`approveChannelAndIssueChannelToken(${channelId}) 失敗：${e?.message ?? e}`);
  }
  if (!token) {
    const r = await client.base.channel.issueChannelToken({ channelId });
    token = r?.token ?? r?.channelAccessToken;
  }
  if (!token) throw new Error(`no channel token for ${channelId}`);
  cache.set(channelId, token);
  return token;
}

function baseHeaders(client, token) {
  return {
    accept: "application/json",
    "content-type": "application/json",
    "user-agent": client.base.request.userAgent,
    "x-line-application": client.base.request.systemType,
    "x-line-mid": client.base.profile?.mid ?? "",
    "x-line-access": client.base.authToken,
    "x-line-channeltoken": token,
    "x-lal": process.env.LINE_LANG || "zh-Hant_TW",
    "x-lsr": process.env.LINE_REGION || "TW",
    "x-lpv": "1",
    "x-lhm": "GET",
    "x-line-bdbtemplateversion": "v1",
    "x-line-global-config": "discover.enable=true; follow.enable=true",
  };
}

// 記住第一個打通的組合，之後同一個 homeId 直接用。
const routeCache = new Map();

/** 對記事本 REST 打一次請求，回 { code, message, result }；依序試 host × prefix × channel 直到 code 0。 */
async function noteRequest(client, homeId, path, params, { method = "GET", body } = {}) {
  const qs = new URLSearchParams(Object.fromEntries(Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== null && v !== "")));
  const tryOne = async (host, prefix, channelId) => {
    const token = await channelToken(client, channelId);
    const url = `https://${host}${prefix}${path}?${qs}`;
    const res = await client.base.fetch(url, {
      method,
      headers: { ...baseHeaders(client, token), "x-lhm": method },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await res.text();
    let json;
    try { json = JSON.parse(text); } catch { json = { code: res.status, message: text.slice(0, 200), result: null }; }
    return { httpStatus: res.status, json };
  };

  const cached = routeCache.get(homeId);
  if (cached) {
    let { json } = await tryOne(cached.host, cached.prefix, cached.channelId);
    // channel token 會過期：401/403 就丟掉重 issue 再試一次
    if (json && (json.code === 401 || json.code === 403)) {
      tokensFor(client).delete(cached.channelId);
      ({ json } = await tryOne(cached.host, cached.prefix, cached.channelId));
    }
    return json;
  }
  const attempts = [];
  for (const host of hostCandidates(client)) {
    for (const prefix of PREFIXES) {
      for (const channelId of CHANNELS) {
        try {
          const { httpStatus, json } = await tryOne(host, prefix, channelId);
          if (json && json.code === 0) {
            routeCache.set(homeId, { host, prefix, channelId });
            return json;
          }
          attempts.push(`${httpStatus} ${host}${prefix}${path} ch=${channelId} code=${json?.code} ${json?.message ?? ""}`);
        } catch (e) {
          attempts.push(`ERR ${host}${prefix}${path} ch=${channelId}: ${e?.message ?? e}`);
        }
      }
    }
  }
  throw new Error(`記事本 API 全部打不通（homeId=${homeId}）：\n` + attempts.join("\n"));
}

function pick(obj, ...paths) {
  for (const p of paths) {
    const v = p.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
    if (v !== undefined && v !== null && v !== "") return v;
  }
  return undefined;
}

function toMs(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n < 1e12 ? n * 1000 : n;
}

function normalizePost(p) {
  const post = p?.post ?? p; // feeds[] 會包一層 { post: {...} }
  return {
    postId: pick(post, "id", "postInfo.postId", "postId"),
    createdMs: toMs(pick(post, "postInfo.createdTime", "createdTime")),
    text: pick(post, "contents.text", "contents.textMeta.text", "text") ?? "",
  };
}

/** 最近的記事本貼文（只看第一頁，夠用來對回剛發的那篇）。 */
async function recentPosts(client, homeId) {
  const json = await noteRequest(client, homeId, "/api/v57/post/list.json", {
    homeId, sourceType: "TALKROOM", likeLimit: "0", commentLimit: "0",
  });
  const r = json?.result;
  const rows = Array.isArray(r?.posts) ? r.posts : Array.isArray(r?.feeds) ? r.feeds : Array.isArray(r) ? r : [];
  return rows.map(normalizePost).filter((p) => p.postId);
}

export async function findPost(client, homeId, text, sinceMs) {
  assertGroup(homeId);
  return matchPost(await recentPosts(client, homeId), text, sinceMs);
}

/**
 * 在群組記事本發一篇純文字貼文，回 LINE 的貼文 id（撿不到回 null）。
 * 呼叫前先 findPost：它用唯讀的 list 把路由探出來並記住，create 才不會在探路時對不同前綴各發一篇
 * （list 失敗時 create 會自己探路，同 new_erp）。
 * ⚠ 不要帶 textStyle／backgroundColor：2026-09-18 起 LINE 回 code=118「『純文字』功能已停止提供使用」。
 */
export async function createPost(client, homeId, text) {
  assertGroup(homeId);
  if (!text) throw new Error("貼文不能是空的");
  const body = {
    postInfo: { readPermission: { type: "ALL", gids: [] } },
    contents: { contentsStyle: { mediaStyle: { displayType: "GRID_1_A" } }, stickers: [], locations: [], media: [], text },
  };
  const json = await noteRequest(client, homeId, "/api/v57/post/create.json",
    { homeId, sourceType: "TALKROOM" }, { method: "POST", body });
  if (!json || json.code !== 0) {
    throw new Error(`發文失敗：code=${json?.code} ${json?.message ?? ""}`);
  }
  // 回應裡貼文長在哪一層沒有 wire trace（new_erp 線上 38 篇都撿不到），多試幾種包法
  const r = json.result;
  const post = r?.post ?? r?.feed?.post ?? r?.feeds?.[0]?.post ?? r?.posts?.[0] ?? r?.feeds?.[0] ?? r;
  const id = normalizePost(post).postId ?? pick(r, "postId", "id", "post.id", "feed.post.id");
  return id ? String(id) : null;
}

/** 把記事本貼文用 LINE 原生的「分享貼文」卡片發到群組聊天（同手機上按「分享到聊天」）。 */
export async function sharePost(client, homeId, postId) {
  assertGroup(homeId);
  if (!postId) throw new Error("沒有貼文 id，無法分享");
  const json = await noteRequest(client, homeId, "/api/v57/post/sendPostToTalk.json",
    {}, { method: "POST", body: { postId: String(postId), receiveMids: [String(homeId)] } });
  if (!json || json.code !== 0) {
    throw new Error(`分享貼文到聊天失敗：code=${json?.code} ${json?.message ?? ""}`);
  }
}
