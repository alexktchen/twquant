#!/usr/bin/env node
// twquant（Python，`twquant line-group …`）呼叫的轉接器（T-423／D-204）。資料庫、排隊、組文字都在 Python；
// 這裡只負責跟 LINE 講話。
//
// 用法：node src/cli.mjs <login|groups|send>，stdin 一份 JSON 請求；stdout 一行一個事件（NDJSON），
// 人看的訊息（QR、PIN、錯誤細節）走 stderr。事件：
//   credential  憑證有換就馬上吐（程序中途死掉也不會丟掉輪替後的 refresh token）
//   profile     登入的是哪個帳號
//   group       加入中的群組（groups）
//   result      一筆貼文的結果（send）
//   fatal       整個指令失敗（登不進去之類），之後 exit 2

import { lineBackoffMs } from "./backoff.mjs";
import {
  clientFromCredential, createPost, findPost, listGroups, loginByQr, readCredential, sharePost, whoami,
} from "./line.mjs";

const LOGIN_DEADLINE_MS = 180_000; // 掃 QR＋輸入 PIN 的時間

const emit = (ev) => process.stdout.write(JSON.stringify(ev) + "\n");

async function readRequest() {
  let s = "";
  for await (const chunk of process.stdin) s += chunk;
  return s.trim() ? JSON.parse(s) : {};
}

async function connect(req) {
  const client = await clientFromCredential(req.credential, {
    onCredential: (patch) => emit({ type: "credential", ...patch }),
  });
  emit({ type: "profile", ...whoami(client) });
  return client;
}

async function cmdLogin() {
  const client = await loginByQr({
    deadlineMs: LOGIN_DEADLINE_MS,
    async onQr(url) {
      console.error("\n用要發文的那支 LINE 帳號掃這個 QR（備用帳號！不要用本人主帳號）：\n");
      try {
        const qr = await import("qrcode-terminal");
        (qr.default ?? qr).generate(url, { small: true }, (s) => console.error(s));
      } catch {
        // 沒裝 qrcode-terminal 也沒關係，下面有網址
      }
      console.error("\n或把這個網址貼到手機 LINE 開啟：\n" + url + "\n");
    },
    onPin(pin) {
      console.error(`\n手機 LINE 會要你輸入 PIN：  ${pin}\n`);
    },
  });
  emit({ type: "credential", ...(await readCredential(client)) });
  emit({ type: "profile", ...whoami(client) });
}

async function cmdGroups(req) {
  const client = await connect(req);
  for (const g of await listGroups(client)) emit({ type: "group", ...g });
  emit({ type: "credential", ...(await readCredential(client)) });
}

/**
 * jobs: [{ id, homeId, text, sinceMs, linePostId?, share }]
 * 沒有 linePostId → 先找記事本上有沒有同內文的（上一輪發了沒記到）、沒有才發；有 → 只補分享。
 * LINE 叫我們慢一點（backoffMs）就停，後面的這一輪不送。
 */
async function cmdSend(req) {
  const client = await connect(req);
  for (const job of req.jobs ?? []) {
    const out = { type: "result", id: job.id, linePostId: job.linePostId ?? null, posted: Boolean(job.linePostId), shared: false };
    let stage = "post";
    try {
      if (!out.linePostId) {
        let existing = null;
        try {
          existing = await findPost(client, job.homeId, job.text, job.sinceMs);
        } catch (e) {
          // 列貼文失敗（新群組記事本還是空的之類）照樣發文，同 new_erp；LINE 叫停的話就不要再打
          if (lineBackoffMs(e?.message ?? e) !== null) throw e;
          console.error(`[line-poster] 第 ${job.id} 筆找既有貼文失敗（照樣發文）：${e?.message ?? e}`);
        }
        if (existing) {
          out.linePostId = existing;
          out.adopted = true;
        } else {
          out.linePostId = await createPost(client, job.homeId, job.text);
        }
        out.posted = true;
        // create 的回應撿不到 id 時，馬上用 list 把剛發的那篇對回來（沒有 id 就分享不了）
        if (!out.linePostId) out.linePostId = await findPost(client, job.homeId, job.text, job.sinceMs);
      }
      if (job.share && out.linePostId) {
        stage = "share";
        await sharePost(client, job.homeId, out.linePostId);
        out.shared = true;
      }
    } catch (e) {
      const msg = String(e?.message ?? e);
      console.error(`[line-poster] 第 ${job.id} 筆（${stage}）失敗：${msg}`);
      out.stage = stage;
      out.error = msg.slice(0, 1000);
      out.backoffMs = lineBackoffMs(msg);
    }
    emit(out);
    if (out.backoffMs) break;
  }
  emit({ type: "credential", ...(await readCredential(client)) });
}

const COMMANDS = { login: cmdLogin, groups: cmdGroups, send: cmdSend };

const name = process.argv[2];
const cmd = COMMANDS[name];
if (!cmd) {
  console.error(`用法：node src/cli.mjs <${Object.keys(COMMANDS).join("|")}>（stdin 給 JSON 請求）`);
  process.exit(64);
}
// linejs 會留著長連線，不主動結束的話程序不會自己退出；但 Windows 的 pipe 是非同步寫入，
// 直接 exit 會截掉最後幾行（登入憑證就在最後），所以先等 stdout 寫完。
const exit = (code) => process.stdout.write("", () => process.exit(code));

try {
  await cmd(name === "login" ? {} : await readRequest());
  exit(0);
} catch (e) {
  const msg = String(e?.message ?? e);
  console.error(`[line-poster] ${name} 失敗：${msg}`);
  emit({ type: "fatal", error: msg.slice(0, 1000) });
  exit(2);
}
