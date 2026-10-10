// 把 public/icon.svg 轉成各尺寸 PNG（手動工具，不進 npm test）：node tests/render-icon.cjs
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

;(async () => {
  const svg = fs.readFileSync(path.join(__dirname, '..', 'public', 'icon.svg'), 'utf8')
  const browser = await chromium.launch()
  for (const [name, size] of [
    ['icon-512.png', 512],
    ['icon-192.png', 192],
    ['apple-touch-icon.png', 180],
    ['favicon-32.png', 32],
  ]) {
    const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 })
    await page.setContent(
      `<html><body style="margin:0;background:transparent">${svg.replace(/width="512" height="512"/, `width="${size}" height="${size}"`)}</body></html>`,
    )
    await page.waitForTimeout(150)
    await page.screenshot({ path: path.join(__dirname, '..', 'public', name), omitBackground: true })
    await page.close()
  }
  await browser.close()
  console.log('icons rendered')
})()
