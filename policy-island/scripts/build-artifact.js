#!/usr/bin/env node
/* 把獨立站的 index.html 轉成 claude.ai artifact 用的頁面（去掉 doctype/head/body 外殼，保留 title/link 與 body 內容）。
   用法：node scripts/build-artifact.js <輸出路徑> */
const fs = require('fs'), path = require('path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const title = (html.match(/<title>[\s\S]*?<\/title>/) || [''])[0];
const head = html.match(/<head>([\s\S]*?)<\/head>/)[1];
const links = [...head.matchAll(/<link[^>]+>/g)].map(m => m[0]).join('\n');
const metaFit = (head.match(/<meta name="claude:viewport-fit-sides"[^>]*>/) || [''])[0];
const body = html.match(/<!-- artifact:body:start -->([\s\S]*?)<!-- artifact:body:end -->/)[1];
const out = process.argv[2]; if (!out) { console.error('need output path'); process.exit(1); }
fs.writeFileSync(out, [title, metaFit, links, body].join('\n'));
console.log('wrote', out, fs.statSync(out).size, 'bytes');
