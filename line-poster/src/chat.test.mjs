import assert from "node:assert/strict";
import { test } from "node:test";

import { chatKind, joinedSquareChats } from "./chat.mjs";
import { deliver } from "./deliver.mjs";

const C = `c${"a".repeat(32)}`;
const M = (n) => `m${String(n).repeat(32)}`;

test("chatKind：c… 一般群組、m… 社群聊天；個人、社群本身、格式不對都不發", () => {
  assert.equal(chatKind(C), "group");
  assert.equal(chatKind(M(1)), "square");
  for (const bad of [`u${"a".repeat(32)}`, `s${"a".repeat(32)}`, "c123", `C${"a".repeat(32)}`, "", undefined]) {
    assert.throws(() => chatKind(bad), /只發一般群組/, String(bad));
  }
});

test("joinedSquareChats：只留能發的社群聊天（主聊天、子聊天），私訊、已刪除、等核准、已退出都不算", () => {
  const chat = (n, over = {}) => ({ squareChatMid: M(n), squareMid: `s${"0".repeat(32)}`, type: "OPEN", state: "ALIVE", name: `聊天${n}`, ...over });
  const res = {
    chats: [
      chat(1, { type: "SQUARE_DEFAULT", name: "台股研究社" }),
      chat(2), // 子聊天
      chat(3, { type: 2, state: 0 }), // enum 解成數字也認
      chat(4, { type: "ONE_ON_ONE" }),
      chat(5, { state: "DELETED" }),
      chat(6),
      chat(7),
      { squareChatMid: `s${"9".repeat(32)}`, type: "OPEN", name: "社群本身" },
    ],
    chatMembers: {
      [M(6)]: { squareChatMid: M(6), membershipState: "JOIN_RESERVED" },
      [M(7)]: { squareChatMid: M(7), membershipState: "LEFT" },
      [M(2)]: { squareChatMid: M(2), membershipState: 1 },
    },
  };
  assert.deepEqual(joinedSquareChats(res), [
    { homeId: M(1), name: "台股研究社" },
    { homeId: M(2), name: "聊天2" },
    { homeId: M(3), name: "聊天3" },
  ]);
  assert.deepEqual(joinedSquareChats({}), []);
  assert.deepEqual(joinedSquareChats(undefined), []);
});

test("deliver：社群（沒有 Flex 訊息）直接發圖＋一行網址，不碰 LIFF、沒有圖卡錯誤", async () => {
  const calls = [];
  const job = { id: 1, homeId: M(1), message: null, card: "f".repeat(32), imageCaption: "看完整內容：\nhttps://x.dev/", fallbackText: "摘要 https://x.dev/" };
  const r = await deliver(job, {
    sendFlex: async () => { calls.push("flex"); },
    sendImage: async (to, card) => { calls.push(["image", to, card]); },
    sendText: async (to, t) => { calls.push(["text", to, t]); },
  });
  assert.deepEqual(r, { sent: true, via: "image" });
  assert.deepEqual(calls, [["image", M(1), job.card], ["text", M(1), job.imageCaption]]);
});

test("deliver：社群圖片也發不出去就發摘要文字；什麼都不能發就記錯誤（不是當成送出）", async () => {
  const job = { id: 1, homeId: M(1), message: null, card: "f".repeat(32), imageCaption: "x", fallbackText: "摘要" };
  const texts = [];
  const r = await deliver(job, {
    sendFlex: async () => { throw new Error("不該試"); },
    sendImage: async () => { throw new Error("圖片上傳失敗（obs HTTP 403）"); },
    sendText: async (to, t) => { texts.push(t); },
  });
  assert.deepEqual(r, { sent: true, via: "text", imageError: "圖片上傳失敗（obs HTTP 403）" });
  assert.deepEqual(texts, ["摘要"]);
  const none = await deliver({ ...job, card: "", fallbackText: "" }, { sendFlex: null, sendImage: null, sendText: null });
  assert.equal(none.sent, false);
  assert.match(none.error, /沒有任何能發的東西/);
});
