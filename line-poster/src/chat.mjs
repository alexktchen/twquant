// 聊天的種類與社群清單（純函式：不用 linejs，測試直接跑）。
//
// 一般群組 c…：訊息走 talk、端對端加密。社群（OpenChat）的聊天 m…：走 square 那一套、不加密；
// 社群本身（s…）不能直接發訊息，要發到它底下的聊天（主聊天 SQUARE_DEFAULT 或子聊天）。

const MID_RE = /^[cm][0-9a-f]{32}$/;

/** "group"（一般群組 c…）或 "square"（社群的聊天 m…）；其他（個人 u…、社群本身 s…）不發。 */
export function chatKind(mid) {
  const s = String(mid ?? "");
  if (!MID_RE.test(s)) throw new Error(`只發一般群組（c…）或社群的聊天（m…），拿到 ${s.slice(0, 40)}`);
  return s[0] === "c" ? "group" : "square";
}

// linejs 解出來的 enum 有時是名字、有時是數字，兩種都認
const POSTABLE_TYPES = new Set(["OPEN", "SECRET", "SQUARE_DEFAULT", 1, 2, 4]); // 不含 ONE_ON_ONE（社群裡的私訊）
const ALIVE = new Set(["ALIVE", 0]);
const JOINED = new Set(["JOINED", 1]);

/**
 * getJoinedSquareChats 的一頁 → 能發的社群聊天 [{ homeId, name }]。
 * 已刪除／停權的聊天、等管理員核准（JOIN_RESERVED）或已退出的都不算：發了也會被拒。
 */
export function joinedSquareChats(res) {
  const out = [];
  for (const c of res?.chats ?? []) {
    const mid = String(c?.squareChatMid ?? "");
    if (!MID_RE.test(mid) || mid[0] !== "m") continue;
    if (!POSTABLE_TYPES.has(c.type)) continue;
    if (c.state !== undefined && !ALIVE.has(c.state)) continue;
    const member = res.chatMembers?.[mid];
    if (member?.membershipState !== undefined && !JOINED.has(member.membershipState)) continue;
    out.push({ homeId: mid, name: c.name ?? "" });
  }
  return out;
}
