import assert from "node:assert/strict";
import { test } from "node:test";

import { pickE2EEKeys, withE2EEHint } from "./e2ee-keys.mjs";

test("pickE2EEKeys：只挑 e2eeKeys: 開頭、值是字串的，其他（token、群組金鑰快取）不帶", () => {
  const all = {
    refreshToken: "r",
    expire: 9,
    "e2eeKeys:3": '{"keyId":3}',
    "e2eeKeys:u1": '{"keyId":3}',
    "e2eeKeys:bad": 5,
    "e2eeGroupKeys:c1": "x",
    "e2eePublicKeys:u2:1": "y",
  };
  assert.deepEqual(pickE2EEKeys(all), { "e2eeKeys:3": '{"keyId":3}', "e2eeKeys:u1": '{"keyId":3}' });
});

test("pickE2EEKeys：沒有就回 null（舊的登入、還沒存過）", () => {
  assert.equal(pickE2EEKeys({ refreshToken: "r" }), null);
  assert.equal(pickE2EEKeys(undefined), null);
  assert.equal(pickE2EEKeys(null), null);
});

test("withE2EEHint：沒有加密金鑰時加上怎麼修，其他錯誤原樣", () => {
  const msg = "E2EE Key has not been saved, try register `saveE2EESelfKeyDataByKeyId` or use E2EE Login";
  assert.match(withE2EEHint(msg), /重新登入一次/);
  assert.ok(withE2EEHint(msg).startsWith(msg));
  assert.equal(withE2EEHint("code=701 請稍後再重新發布"), "code=701 請稍後再重新發布");
});
