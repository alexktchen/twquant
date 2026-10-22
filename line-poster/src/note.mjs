// 記事本發文＋分享到聊天（使用者 2026-10-22：「先貼文到記事本，之後再分享到聊天」）。
//
// ⚠ 移植自 lt-foods/new_erp 的 supabase/functions/_shared/lineNote.ts（Edge 正式版，同一版 linejs 3.4.2），
//   那邊踩過的雷都留著註解。不要改用 linejs 內建的 timeline.createPost/listPost/sharePost：它把網址寫死成
//   `https://${client.request.endpoint}/…`，endpoint 預設是 legy.line-apps.com（LEGY 加密閘道，不吃純 HTTPS JSON），直接 `fetch failed`。
//
// 記事本在哪：一般群組（c…）的記事本就長在群組上；社群的記事本掛在整個社群（s…）上，主聊天、子聊天（m…）共用。
// 分享：sendPostToTalk 把貼文用 LINE 原生的「分享貼文」卡片發進聊天（同手機上按「分享到聊天」）。
// 這個檔案不 import linejs（client 由呼叫端給），純函式可以直接測。

const CHANNEL_IDS = {
  HOME: "1341209850",
  TIMELINE: "1341209950",
  NOTE: "1655599932",
  SQUARE_NOTE: "1657618623",
};
const LANG = "zh-Hant_TW";
const REGION = "TW";
const LIST_PATH = "/api/v57/post/list.json";

const isSquareHome = (homeId) => String(homeId)[0] === "s";

/** 記事本 REST 的路徑前綴，依序試（社群 /sn、群組 /mh）。 */
export function prefixCandidates(homeId) {
  return isSquareHome(homeId) ? ["/sn", "/mh", "/ext/note/nt"] : ["/mh", "/ext/note/nt"];
}

/** 要哪一個 channel 的 token，依序試。 */
export function channelCandidates(homeId) {
  return isSquareHome(homeId)
    ? [CHANNEL_IDS.SQUARE_NOTE, CHANNEL_IDS.HOME, CHANNEL_IDS.TIMELINE, CHANNEL_IDS.NOTE]
    : [CHANNEL_IDS.HOME, CHANNEL_IDS.NOTE, CHANNEL_IDS.TIMELINE, CHANNEL_IDS.SQUARE_NOTE];
}

function hostCandidates(client) {
  const ep = client.base.request?.endpoint;
  return [...new Set([ep, "gw.line.naver.jp", "ga2.line.naver.jp"].filter(Boolean))];
}

// channel token 是「這個帳號 × 這個 channel」的，依 client 分開存（new_erp 2026-10-07：同一個程序跑好幾個帳號時
// 拿別人的 channel token 配自己的 x-line-access，記事本 API 整排 401）。WeakMap 跟著 client 走。
const tokenCache = new WeakMap();
function tokensFor(client) {
  let m = tokenCache.get(client);
  if (!m) tokenCache.set(client, (m = new Map()));
  return m;
}

async function channelToken(client, channelId) {
  const cache = tokensFor(client);
  if (cache.has(channelId)) return cache.get(channelId);
  let token;
  let approveError;
  try {
    const r = await client.base.channel.approveChannelAndIssueChannelToken({ channelId });
    token = r?.channelAccessToken ?? r?.token;
  } catch (e) {
    approveError = e; // 有些 channel 不給 approve，改 issue；issue 也失敗才算失敗（錯誤兩個都帶）
  }
  if (!token) {
    const r = await client.base.channel.issueChannelToken({ channelId });
    token = r?.token ?? r?.channelAccessToken;
  }
  if (!token) throw new Error(`拿不到 channel ${channelId} 的 token${approveError ? `（approve：${approveError?.message ?? approveError}）` : ""}`);
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
    "x-lal": LANG,
    "x-lsr": REGION,
    "x-lpv": "1",
    "x-lhm": "GET",
    "x-line-bdbtemplateversion": "v1",
    "x-line-global-config": "discover.enable=true; follow.enable=true",
  };
}

// 記住每個 homeId 第一個打通的 host × prefix × channel（同 client 才共用）
const routeCache = new WeakMap();
function routesFor(client) {
  let m = routeCache.get(client);
  if (!m) routeCache.set(client, (m = new Map()));
  return m;
}

/**
 * 對記事本 REST 打一次請求，回 { code, message, result }。第一次對某個 homeId 會依序試 host × prefix × channel，
 * 直到有一組回 code 0，然後記住那組。⚠ 會寫東西的請求（發文、分享）不要拿來探路——先用 probeRoute（唯讀的 list）探好，
 * 不然探路的過程中每一組都可能發出一篇（new_erp：重複貼文、好幾張分享卡片）。
 */
async function noteRequest(client, homeId, path, params, { method = "GET", lhm, body } = {}) {
  const qs = new URLSearchParams(Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const tryOne = async ({ host, prefix, channelId }) => {
    const token = await channelToken(client, channelId);
    const res = await client.base.fetch(`https://${host}${prefix}${path}?${qs}`, {
      method,
      // x-lhm 預設跟著 HTTP method，但不是每支都一樣（post/delete.json 是 POST 卻要送 GET）
      headers: { ...baseHeaders(client, token), "x-lhm": lhm ?? method },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await res.text();
    try {
      return { httpStatus: res.status, json: JSON.parse(text) };
    } catch {
      return { httpStatus: res.status, json: { code: res.status, message: text.slice(0, 200), result: null } };
    }
  };

  const routes = routesFor(client);
  const cached = routes.get(homeId);
  if (cached) {
    let { json } = await tryOne(cached);
    // channel token 會過期：401／403 就丟掉重拿再試一次
    if (json && (json.code === 401 || json.code === 403)) {
      tokensFor(client).delete(cached.channelId);
      ({ json } = await tryOne(cached));
    }
    return json;
  }
  const attempts = [];
  for (const host of hostCandidates(client)) {
    for (const prefix of prefixCandidates(homeId)) {
      for (const channelId of channelCandidates(homeId)) {
        const route = { host, prefix, channelId };
        try {
          const { httpStatus, json } = await tryOne(route);
          if (json?.code === 0) {
            routes.set(homeId, route);
            return json;
          }
          attempts.push(`${httpStatus} ${host}${prefix}${path} ch=${channelId} code=${json?.code} ${json?.message ?? ""}`);
        } catch (e) {
          attempts.push(`ERR ${host}${prefix}${path} ch=${channelId}: ${e?.message ?? e}`);
        }
      }
    }
  }
  throw new Error(`記事本 API 全部打不通（homeId=${homeId}）：\n${attempts.join("\n")}`.slice(0, 1500));
}

/** 用唯讀的 list 把這個 homeId 的路由探出來並記住（會寫東西的請求之前一定先叫它）。 */
async function probeRoute(client, homeId) {
  return await noteRequest(client, homeId, LIST_PATH, { homeId, sourceType: "TALKROOM", likeLimit: "0", commentLimit: "0" });
}

// ── 回應解析（結構沒有 wire trace，寫成寬鬆版） ───────────────────────────

function pick(obj, ...paths) {
  for (const p of paths) {
    const v = p.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
    if (v !== undefined && v !== null && v !== "") return v;
  }
  return undefined;
}

/** list.json 的 result → 貼文 [{ postId, authorMid, text, createdMs }]。 */
export function extractPosts(result) {
  const raw = Array.isArray(result?.posts) ? result.posts : Array.isArray(result?.feeds) ? result.feeds : Array.isArray(result) ? result : [];
  return raw.map((p) => {
    const post = p?.post ?? p; // feeds[] 會包一層 { post: {...} }
    const created = Number(pick(post, "postInfo.createdTime", "createdTime"));
    return {
      postId: pick(post, "id", "postInfo.postId", "postId"),
      authorMid: pick(post, "userInfo.mid", "userInfo.writerMid", "postInfo.userInfo.mid", "actorId"),
      text: String(pick(post, "contents.text", "contents.textMeta.text", "text") ?? ""),
      createdMs: Number.isFinite(created) && created > 0 ? (created < 1e12 ? created * 1000 : created) : null,
    };
  });
}

/** create.json 的回應裡撿貼文 id（new_erp：貼文長在哪一層不一定，2026-09 發的 38 篇全撿不到，所以多試幾層）。 */
export function postIdFromCreate(result) {
  const post = result?.post ?? result?.feed?.post ?? result?.feeds?.[0]?.post ?? result?.posts?.[0] ?? result?.feeds?.[0] ?? result;
  return pick(post, "id", "postInfo.postId", "postId") ?? pick(result, "postId", "id", "post.id", "feed.post.id") ?? null;
}

const normalizeForMatch = (s) => String(s ?? "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");

/**
 * create 撿不到 id 時，從記事本最新的貼文裡認出剛發的那篇：自己發的、內文一樣、withinMs 內建立的。
 * 認不出來回 null（寧可回報失敗，不要認錯篇）。
 */
export function findOwnPost(posts, { text, mid, nowMs, withinMs }) {
  const want = normalizeForMatch(text);
  const hit = posts.find((p) => p.postId
    && (!p.authorMid || !mid || p.authorMid === mid)
    && (p.createdMs === null || p.createdMs >= nowMs - withinMs)
    && normalizeForMatch(p.text) === want);
  return hit ? String(hit.postId) : null;
}

// ── 發文、分享 ─────────────────────────────────────────────────────────────

const FIND_WINDOW_MS = 10 * 60_000; // 補認剛發的貼文：只看 10 分鐘內建立的（new_erp 同一個值）

/**
 * 在記事本發一篇貼文（文字＋一張圖），回貼文 id。jpeg 是 Blob：linejs 上傳記事本圖片時一律宣告 image/jpeg，
 * 宣告錯的 MIME 會被 obs 退掉，所以給 JPEG。圖上傳失敗就整篇不發（這裡的重點就是那張圖）。
 */
export async function createNotePost(client, homeId, { text, jpeg }) {
  if (!text || !jpeg?.size) throw new Error("記事本貼文要有文字和圖");
  const { objId } = await client.base.timeline.uploadNoteMedia("image", jpeg);
  if (!objId) throw new Error("記事本圖片上傳後沒拿到 objId");
  await probeRoute(client, homeId);
  // ⚠ 不要帶 textStyle（linejs 的 createPost 會塞 backgroundColor）：2026-09-18 起 LINE 回 code=118
  //   「『純文字』功能已停止提供使用」，連帶有圖的貼文也被退。只留 mediaStyle。
  const body = {
    postInfo: { readPermission: { type: "ALL", gids: [] } },
    contents: {
      contentsStyle: { mediaStyle: { displayType: "GRID_1_A" } },
      stickers: [],
      locations: [],
      media: [{ objectId: objId, type: "PHOTO", obsFace: "[]" }],
      text,
    },
  };
  const res = await noteRequest(client, homeId, "/api/v57/post/create.json", { homeId, sourceType: "TALKROOM" }, { method: "POST", body });
  if (res?.code !== 0) throw new Error(`記事本發文失敗：code=${res?.code} ${res?.message ?? ""}`.slice(0, 1000));
  const id = postIdFromCreate(res.result);
  if (id) return String(id);
  const list = await probeRoute(client, homeId);
  const found = findOwnPost(extractPosts(list?.result), {
    text, mid: client.base.profile?.mid, nowMs: Date.now(), withinMs: FIND_WINDOW_MS,
  });
  if (!found) throw new Error(`記事本發文成功但拿不到貼文 id（回應：${JSON.stringify(res.result).slice(0, 300)}）`);
  return found;
}

/** 把記事本貼文分享到聊天（chatMid：群組 c… 或社群的聊天 m…）。路由先用唯讀的 list 探好，免得一組一組試、發出好幾張卡片。 */
export async function sharePostToChat(client, homeId, postId, chatMid) {
  if (!postId) throw new Error("沒有貼文 id，無法分享");
  await probeRoute(client, homeId);
  const res = await noteRequest(client, homeId, "/api/v57/post/sendPostToTalk.json", {}, {
    method: "POST", body: { postId: String(postId), receiveMids: [String(chatMid)] },
  });
  if (res?.code !== 0) throw new Error(`分享貼文到聊天失敗：code=${res?.code} ${res?.message ?? ""}`.slice(0, 1000));
  return res;
}
