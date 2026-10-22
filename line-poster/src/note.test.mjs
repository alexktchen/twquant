import assert from "node:assert/strict";
import { test } from "node:test";

import { deliver } from "./deliver.mjs";
import { channelCandidates, extractPosts, findOwnPost, postIdFromCreate, prefixCandidates } from "./note.mjs";

const C = `c${"a".repeat(32)}`;
const M = `m${"1".repeat(32)}`;
const S = `s${"1".repeat(32)}`;

test("記事本路由：社群（s…）先試 /sn 與社群記事本的 channel，群組先試 /mh", () => {
  assert.deepEqual(prefixCandidates(S), ["/sn", "/mh", "/ext/note/nt"]);
  assert.deepEqual(prefixCandidates(C), ["/mh", "/ext/note/nt"]);
  assert.equal(channelCandidates(S)[0], "1657618623");
  assert.equal(channelCandidates(C)[0], "1341209850");
});

test("extractPosts／postIdFromCreate：回應包法不一定，幾種都認", () => {
  const posts = extractPosts({ feeds: [{ post: { id: "p1", userInfo: { mid: "u1" }, contents: { text: "hi" }, postInfo: { createdTime: 1_700_000_000 } } }] });
  assert.deepEqual(posts, [{ postId: "p1", authorMid: "u1", text: "hi", createdMs: 1_700_000_000_000 }]); // 秒也轉成毫秒
  assert.equal(extractPosts({ posts: [{ postInfo: { postId: "p2" } }] })[0].postId, "p2");
  assert.deepEqual(extractPosts(null), []);
  assert.equal(postIdFromCreate({ post: { id: "a" } }), "a");
  assert.equal(postIdFromCreate({ feed: { post: { postInfo: { postId: "b" } } } }), "b");
  assert.equal(postIdFromCreate({ postId: "c" }), "c");
  assert.equal(postIdFromCreate({}), null);
});

test("findOwnPost：拿不到 id 時只認自己 10 分鐘內發的、內文一樣的那篇；認不出來回 null", () => {
  const now = 1_800_000_000_000;
  const p = (id, over = {}) => ({ postId: id, authorMid: "me", text: "今日名單\n\nhttps://x.dev/", createdMs: now - 60_000, ...over });
  const opt = { text: "今日名單 https://x.dev/", mid: "me", nowMs: now, withinMs: 600_000 };
  assert.equal(findOwnPost([p("old", { createdMs: now - 3_600_000 }), p("other", { authorMid: "you" }), p("hit")], opt), "hit");
  assert.equal(findOwnPost([p("x", { text: "別篇" })], opt), null);
  assert.equal(findOwnPost([], opt), null);
});

const JOB = {
  id: 1, homeId: M, noteHome: S, noteText: "摘要\n\nhttps://x.dev/", noteId: "", noteCard: "f".repeat(32),
  message: null, card: "f".repeat(32), imageCaption: "看完整內容：\nhttps://x.dev/", fallbackText: "摘要 https://x.dev/",
};
const recorder = (over = {}) => {
  const calls = [];
  const rec = (name, ret) => async (...a) => { calls.push([name, ...a]); return ret; };
  return { calls, send: { postNote: rec("note", "P1"), shareNote: rec("share"), sendFlex: rec("flex"), sendImage: rec("image"), sendText: rec("text"), ...over } };
};
const fail = (msg) => async () => { throw new Error(msg); };

test("deliver：先發記事本、再分享到聊天（社群：記事本在 s…、分享到 m…）", async () => {
  const { calls, send } = recorder();
  assert.deepEqual(await deliver(JOB, send), { sent: true, via: "note", notePostId: "P1" });
  assert.deepEqual(calls, [["note", S, JOB.noteText, JOB.noteCard], ["share", S, "P1", M]]);
  // 聊天發圖的備案關掉（card 空）也照樣發記事本：記事本用自己的 noteCard
  const { calls: c2, send: s2 } = recorder();
  assert.equal((await deliver({ ...JOB, card: "" }, s2)).via, "note");
  assert.equal(c2[0][3], JOB.noteCard);
});

test("deliver：上次已經發過那篇（noteId）就只分享，不再發一篇", async () => {
  const { calls, send } = recorder();
  assert.deepEqual(await deliver({ ...JOB, noteId: "P0" }, send), { sent: true, via: "note", notePostId: "P0" });
  assert.deepEqual(calls, [["share", S, "P0", M]]);
});

test("deliver：分享時被 LINE 叫停 → 這筆先不算送出，但回報記事本那篇的 id（下次只分享）", async () => {
  const { calls, send } = recorder({ shareNote: fail("code=701 請稍後再重新發布") });
  const r = await deliver(JOB, send);
  assert.deepEqual(r, { sent: false, error: "code=701 請稍後再重新發布", backoffMs: 120_000, notePostId: "P1" });
  assert.deepEqual(calls.map((c) => c[0]), ["note"]); // 沒有改發圖片
});

test("deliver：記事本不通就照舊改發圖片（群組還會先試 Flex），錯誤留著", async () => {
  const { calls, send } = recorder({ postNote: fail("記事本 API 全部打不通") });
  const r = await deliver({ ...JOB, homeId: C, noteHome: C, message: { type: "flex" } }, send);
  assert.deepEqual(r, { sent: true, via: "flex", noteError: "記事本 API 全部打不通" });
  const { calls: c2, send: s2 } = recorder({ shareNote: fail("分享貼文到聊天失敗：code=400") });
  const r2 = await deliver(JOB, s2);
  assert.equal(r2.via, "image");
  assert.equal(r2.notePostId, "P1"); // 記事本上有那篇，記下來
  assert.match(r2.noteError, /分享貼文到聊天失敗/);
  assert.deepEqual(c2.map((c) => c[0]), ["note", "image", "text"]); // 失敗的那招（分享）不記
  assert.deepEqual(calls.map((c) => c[0]), ["flex"]);
});

test("deliver：關掉記事本（noteHome 空）就跟以前一樣", async () => {
  const { calls, send } = recorder();
  assert.deepEqual(await deliver({ ...JOB, noteHome: "" }, send), { sent: true, via: "image" });
  assert.deepEqual(calls.map((c) => c[0]), ["image", "text"]);
});
