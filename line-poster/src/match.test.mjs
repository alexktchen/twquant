import assert from "node:assert/strict";
import { test } from "node:test";

import { lineBackoffMs } from "./backoff.mjs";
import { matchPost, normalizeForMatch } from "./match.mjs";

test("normalizeForMatch：全形半形、大小寫、空白都不影響比對", () => {
  assert.equal(normalizeForMatch("１０/２１ 今日名單\n 大盤精選 ABC"), "10/21今日名單大盤精選abc");
});

test("matchPost：同內文、在 since 之後的那篇", () => {
  const posts = [
    { postId: "old", createdMs: 1_000, text: "10/21 今日名單" },
    { postId: "new", createdMs: 5_000, text: "10/21  今日名單" },
  ];
  assert.equal(matchPost(posts, "10/21 今日名單", 2_000), "new");
});

test("matchPost：內文不同或太舊都不認", () => {
  const posts = [
    { postId: "a", createdMs: 1_000, text: "10/21 今日名單" },
    { postId: "b", createdMs: 9_000, text: "10/22 今日名單" },
  ];
  assert.equal(matchPost(posts, "10/21 今日名單", 2_000), null);
  assert.equal(matchPost(posts, "", 0), null);
});

test("matchPost：沒有建立時間的照樣比內文（寧可認進來也不重複發）", () => {
  assert.equal(matchPost([{ postId: "x", createdMs: null, text: "晨報" }], "晨報", 5_000), "x");
});

test("lineBackoffMs：限制使用社群停 10 分鐘、701 停 2 分鐘、其他錯誤不停", () => {
  assert.equal(lineBackoffMs("發文失敗：code=403 因違反服務條款，您已被限制使用社群。"), 600_000);
  assert.equal(lineBackoffMs("分享貼文到聊天失敗：code=701 請稍後再重新發布貼文"), 120_000);
  assert.equal(lineBackoffMs("發文失敗：code=118 純文字功能已停止提供使用"), null);
  assert.equal(lineBackoffMs(undefined), null);
});
