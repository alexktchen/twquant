import assert from "node:assert/strict";
import { test } from "node:test";

import { lineBackoffMs } from "./backoff.mjs";
import { deliver } from "./deliver.mjs";
import { guardObsUpload } from "./obs.mjs";

const JOB = { id: 1, homeId: "cabc", message: { type: "flex" }, fallbackText: "10/21 今日名單 https://x.dev/" };
const fail = (msg) => async () => { throw new Error(msg); };
const ok = async () => ({});

test("deliver：圖卡發得出去就不發文字", async () => {
  let texts = 0;
  const r = await deliver(JOB, { sendFlex: ok, sendText: async () => { texts++; } });
  assert.deepEqual(r, { sent: true, via: "flex" });
  assert.equal(texts, 0);
});

test("deliver：圖卡失敗改發附連結的文字，圖卡的錯誤留著", async () => {
  const sent = [];
  const r = await deliver(JOB, { sendFlex: fail("Failed to get LiffToken"), sendText: async (to, t) => { sent.push([to, t]); } });
  assert.equal(r.sent, true);
  assert.equal(r.via, "text");
  assert.match(r.flexError, /LiffToken/);
  assert.deepEqual(sent, [["cabc", JOB.fallbackText]]);
});

test("deliver：LINE 叫停就不試備案", async () => {
  let texts = 0;
  const r = await deliver(JOB, { sendFlex: fail("code=403 您已被限制使用"), sendText: async () => { texts++; } });
  assert.equal(r.sent, false);
  assert.equal(r.backoffMs, 600_000);
  assert.equal(texts, 0);
});

test("deliver：兩個都失敗，錯誤兩段都寫", async () => {
  const r = await deliver(JOB, { sendFlex: fail("flex 壞了"), sendText: fail("code=701 請稍後再重新發布") });
  assert.equal(r.sent, false);
  assert.match(r.error, /圖卡：flex 壞了；文字：code=701/);
  assert.equal(r.backoffMs, 120_000);
});

test("deliver：沒有備案文字就直接算失敗", async () => {
  const r = await deliver({ ...JOB, fallbackText: "" }, { sendFlex: fail("x"), sendText: ok });
  assert.deepEqual(r, { sent: false, error: "x", backoffMs: null });
});

const IMG = { ...JOB, card: "f".repeat(32), imageCaption: "看完整內容：\nhttps://x.dev/" };
const recorder = () => {
  const calls = [];
  return {
    calls,
    sendImage: async (to, card) => { calls.push(["image", to, card]); },
    sendText: async (to, t) => { calls.push(["text", to, t]); },
  };
};

test("deliver：圖卡失敗就直接發那張圖，再發一行網址；圖卡的錯誤留著", async () => {
  const r0 = recorder();
  const r = await deliver(IMG, { sendFlex: fail("Failed to get LiffToken"), ...r0 });
  assert.deepEqual(r, { sent: true, via: "image", flexError: "Failed to get LiffToken" });
  assert.deepEqual(r0.calls, [["image", "cabc", IMG.card], ["text", "cabc", IMG.imageCaption]]);
});

test("deliver：圖片也發不出去才改發摘要文字，兩段錯誤都留著", async () => {
  const sent = [];
  const r = await deliver(IMG, {
    sendFlex: fail("Failed to get LiffToken"),
    sendImage: fail("圖片上傳失敗（obs HTTP 400）"),
    sendText: async (to, t) => { sent.push(t); },
  });
  assert.equal(r.sent, true);
  assert.equal(r.via, "text");
  assert.match(r.imageError, /obs HTTP 400/);
  assert.deepEqual(sent, [JOB.fallbackText]); // 不是圖片後面那行網址
});

test("deliver：圖發出去了、後面那行網址失敗也算送出（不重送，圖才不會出現兩次）；被叫停照樣停", async () => {
  const r = await deliver(IMG, { sendFlex: fail("x"), sendImage: ok, sendText: fail("code=701 請稍後再重新發布") });
  assert.equal(r.sent, true);
  assert.equal(r.via, "image");
  assert.match(r.captionError, /code=701/);
  assert.equal(r.backoffMs, 120_000);
});

test("deliver：發圖時被叫停就不再試文字", async () => {
  let texts = 0;
  const r = await deliver(IMG, { sendFlex: fail("x"), sendImage: fail("您已被限制使用"), sendText: async () => { texts++; } });
  assert.deepEqual(r, { sent: false, error: "圖卡：x；圖片：您已被限制使用", backoffMs: 600_000 });
  assert.equal(texts, 0);
});

test("deliver：三招都失敗，錯誤三段都寫", async () => {
  const r = await deliver(IMG, { sendFlex: fail("a"), sendImage: fail("b"), sendText: fail("c") });
  assert.deepEqual(r, { sent: false, error: "圖卡：a；圖片：b；文字：c", backoffMs: null });
});

test("guardObsUpload：obs 上傳被拒就丟錯（不讓 linejs 拿空 OID 發破圖）；其他請求原樣", () => {
  const post = (url) => new Request(url, { method: "POST", body: "x" });
  const res = (status) => new Response(null, { status });
  assert.throws(() => guardObsUpload(post("https://obs.line-apps.com/r/talk/emi/reqid-1"), res(400)), /obs HTTP 400/);
  const okRes = res(201);
  assert.equal(guardObsUpload(post("https://obs.line-apps.com/r/talk/emi/reqid-1"), okRes), okRes);
  const notObs = res(400); // Thrift／LIFF 的錯誤由 linejs 自己讀
  assert.equal(guardObsUpload(post("https://gw.line.naver.jp/S4"), notObs), notObs);
  const get = res(404); // 下載 linejs 自己會檢查
  assert.equal(guardObsUpload(new Request("https://obs.line-apps.com/r/talk/m/1"), get), get);
});

test("lineBackoffMs：限制使用停 10 分鐘、701 停 2 分鐘、其他錯誤不停", () => {
  assert.equal(lineBackoffMs("code=403 因違反服務條款，您已被限制使用社群。"), 600_000);
  assert.equal(lineBackoffMs("code=701 請稍後再重新發布貼文"), 120_000);
  assert.equal(lineBackoffMs("Failed to send Liff message: Bad Request"), null);
  assert.equal(lineBackoffMs(undefined), null);
});
