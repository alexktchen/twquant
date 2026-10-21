import assert from "node:assert/strict";
import { test } from "node:test";

import { lineBackoffMs } from "./backoff.mjs";
import { deliver } from "./deliver.mjs";

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

test("lineBackoffMs：限制使用停 10 分鐘、701 停 2 分鐘、其他錯誤不停", () => {
  assert.equal(lineBackoffMs("code=403 因違反服務條款，您已被限制使用社群。"), 600_000);
  assert.equal(lineBackoffMs("code=701 請稍後再重新發布貼文"), 120_000);
  assert.equal(lineBackoffMs("Failed to send Liff message: Bad Request"), null);
  assert.equal(lineBackoffMs(undefined), null);
});
