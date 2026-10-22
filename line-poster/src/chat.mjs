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
const MAIN_TYPES = new Set(["SQUARE_DEFAULT", 4]);
const ALIVE = new Set(["ALIVE", 0]);
const JOINED = new Set(["JOINED", 1]);

/**
 * fetchMyEvents 的回應裡出現過的社群聊天 mid（m…）。事件的結構很多種，整包走一遍撿 squareChatMid
 * （new_erp：getJoinedSquareChats 已被 LINE 下架、回 NOT_IMPLEMENTED；getJoinableSquareChats 只列還沒加入的）。
 */
export function squareChatMidsIn(response, into = new Set()) {
  const walk = (v, depth) => {
    if (!v || depth > 8) return;
    if (Array.isArray(v)) {
      for (const x of v) walk(x, depth + 1);
    } else if (typeof v === "object") {
      for (const [k, x] of Object.entries(v)) {
        if (k === "squareChatMid" && typeof x === "string" && /^m[0-9a-f]{32}$/.test(x)) into.add(x);
        else walk(x, depth + 1);
      }
    }
  };
  walk(response, 0);
  return into;
}

/**
 * 社群聊天 → 清單上的一列 { homeId（m…，發到這裡）, name, noteHome（記事本在哪：所屬社群 s…） }。
 * squares：加入中的社群 Map(s… → 社群名)；chats：getSquareChat 的回應 [{ squareChat, squareChatMember }]。
 * 不列：不在加入中的社群裡（退出了）、私訊、已刪除／停權、自己等核准或已退出的。主聊天用社群名，子聊天「社群名 › 聊天名」。
 */
export function squareChatRows(squares, chats) {
  const out = [];
  for (const { squareChat: c, squareChatMember: me } of chats) {
    const mid = String(c?.squareChatMid ?? "");
    if (!/^m[0-9a-f]{32}$/.test(mid) || !squares.has(c.squareMid)) continue;
    if (!POSTABLE_TYPES.has(c.type)) continue;
    if (c.state !== undefined && !ALIVE.has(c.state)) continue;
    if (me?.membershipState !== undefined && !JOINED.has(me.membershipState)) continue;
    const square = squares.get(c.squareMid);
    out.push({ homeId: mid, name: MAIN_TYPES.has(c.type) ? square : `${square} › ${c.name ?? ""}`, noteHome: String(c.squareMid) });
  }
  return out;
}
