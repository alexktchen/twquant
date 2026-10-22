#!/usr/bin/env node
// twquant（Python，`twquant line-group …`）呼叫的轉接器（T-423／D-204）。資料庫、排隊、組文字都在 Python；
// 這裡只負責跟 LINE 講話。
//
// 用法：node src/cli.mjs <login|groups|send>，stdin 一份 JSON 請求；stdout 一行一個事件（NDJSON），
// 人看的訊息（QR、PIN、錯誤細節）走 stderr。事件：
//   credential  憑證有換就馬上吐（程序中途死掉也不會丟掉輪替後的 refresh token）
//   storage     整份 linejs storage 快照（端對端加密金鑰等）；跨 run 接續 session 用，收尾時吐一次
//   qr          登入用的 QR 網址（login；網站「LINE 群組」分頁把它畫成 QR 碼）
//   pin         掃完 QR 後手機要輸入的 PIN（login）
//   profile     登入的是哪個帳號
//   group       加入中的群組（c…）與社群聊天（m…）（groups）
//   sending     要開始送某一筆了（之後沒有 result＝送到一半中斷，不確定有沒有發出去）
//   result      一筆貼文的結果（send）
//   fatal       整個指令失敗（登不進去之類），之後 exit 2

import { Buffer } from "node:buffer";

import { setTimeout as sleep } from "node:timers/promises";

import { deliver } from "./deliver.mjs";
import {
  clientFromCredential, dumpStorage, listGroups, listSquareChats, loginByQr, readCredential, sendFlex, sendImage, sendText,
  whoami,
} from "./line.mjs";
import { createNotePost, sharePostToChat } from "./note.mjs";

const LOGIN_DEADLINE_MS = 180_000; // 掃 QR＋輸入 PIN 的時間

const emit = (ev) => process.stdout.write(JSON.stringify(ev) + "\n");
// 整份 storage（端對端加密金鑰等）收尾時吐一次，Python 存起來給下一個 run 接續 session
const emitStorage = (client) => emit({ type: "storage", data: JSON.stringify(dumpStorage(client)) });

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
      emit({ type: "qr", url });
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
      emit({ type: "pin", pin: String(pin) });
      console.error(`\n手機 LINE 會要你輸入 PIN：  ${pin}\n`);
    },
  });
  emit({ type: "credential", ...(await readCredential(client)) });
  emit({ type: "profile", ...whoami(client) });
  emitStorage(client);
}

async function cmdGroups(req) {
  const client = await connect(req);
  for (const g of await listGroups(client)) emit({ type: "group", ...g });
  for (const g of await listSquareChats(client)) emit({ type: "group", ...g });
  emit({ type: "credential", ...(await readCredential(client)) });
  emitStorage(client);
}

/** 請求裡的圖（base64，token → 圖）→ Blob；同一張圖發很多群組，每張只解一次。 */
function cardImages(cards, type, what) {
  const cache = new Map();
  return (token) => {
    if (!cache.has(token)) {
      const b64 = cards?.[token];
      if (!b64) throw new Error(`請求裡沒有圖卡 ${token} 的${what}`);
      cache.set(token, new Blob([Buffer.from(b64, "base64")], { type }));
    }
    return cache.get(token);
  };
}

/**
 * 請求：{ credential, liffId?, gapMs, cards: { token: PNG base64 }, noteImages: { token: JPEG base64 },
 *         jobs: [{ id, homeId（c… 群組／m… 社群聊天）, noteHome（記事本在哪：c… 或 s…；空＝不走記事本）, noteText, noteCard, noteId（上次發好的那篇）,
 *                message（Flex 訊息物件；社群是 null）, card（圖卡 token）, imageCaption, fallbackText }] }
 * 每筆先發記事本再分享到聊天、不行發 Flex 圖卡、再不行直接發圖＋一行網址、最後才是文字（deliver.mjs）。
 * LINE 叫我們慢一點（backoffMs）就停，後面的這一輪不送。每筆之間等 gapMs（同帳號連發十幾篇記事本會被 LINE 擋，code=701）。
 */
async function cmdSend(req) {
  const client = await connect(req);
  const image = cardImages(req.cards, "image/png", "圖");
  const noteImage = cardImages(req.noteImages, "image/jpeg", "記事本用的 JPEG");
  // 同一個社群的主聊天、子聊天共用一個記事本：同一輪同一張圖在同一個記事本只發一篇，分享到各個聊天
  const notes = new Map();
  // LIFF 拿不到 token 是那個 LIFF app／這支帳號的事，換群組也一樣：同一輪不要每個群組都再去要一次
  let liffDown = null;
  const send = {
    async sendFlex(to, message) {
      if (liffDown) throw new Error(`${liffDown}（這一輪前面已失敗，沒再試）`);
      try {
        return await sendFlex(client, to, message, { liffId: req.liffId || undefined });
      } catch (e) {
        if (/Failed to get LiffToken/.test(String(e?.message))) liffDown = String(e.message).replace(/@\S+/, "");
        throw e;
      }
    },
    async postNote(home, text, token) {
      const key = `${home}|${token}`;
      if (!notes.has(key)) notes.set(key, await createNotePost(client, home, { text, jpeg: noteImage(token) }));
      return notes.get(key);
    },
    shareNote: (home, postId, to) => sharePostToChat(client, home, postId, to),
    sendImage: (to, token) => sendImage(client, to, image(token)),
    sendText: (to, text) => sendText(client, to, text),
  };
  const jobs = req.jobs ?? [];
  for (const [i, job] of jobs.entries()) {
    if (i > 0 && req.gapMs > 0) await sleep(req.gapMs);
    emit({ type: "sending", id: job.id });
    const out = { type: "result", id: job.id, ...(await deliver(job, send)) };
    if (!out.sent) console.error(`[line-poster] 第 ${job.id} 筆沒送出：${out.error}`);
    else if (out.noteError || out.flexError || out.imageError) {
      const errs = [["記事本", out.noteError], ["圖卡", out.flexError], ["圖片", out.imageError]].filter(([, e]) => e);
      console.error(`[line-poster] 第 ${job.id} 筆改發${out.via}：${errs.map(([k, e]) => `${k}：${e}`).join("；")}`);
    }
    if (out.captionError) console.error(`[line-poster] 第 ${job.id} 筆圖片後面那行網址沒發出去：${out.captionError}`);
    emit(out);
    if (out.backoffMs) break;
  }
  emit({ type: "credential", ...(await readCredential(client)) });
  emitStorage(client);
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
