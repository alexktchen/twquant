// 網頁推播（T-348）的背景程式：只處理「收到通知」與「點通知」，不攔任何網路請求（不做離線快取，
// 名單資料一律即時從 /api 拿，登出後不能留在快取裡）。
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { body: event.data ? event.data.text() : '' }
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'twquant 今日名單', {
      body: data.body || '今日名單已更新',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: data.tag || 'twquant-picks',
      data: { url: data.url || '/' },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const w of windows) {
        if ('focus' in w) {
          // 網站已開著：叫它切到通知要的分頁（族群資金通知帶 ?tab=group），再拉到前面
          w.postMessage({ type: 'open', url })
          return w.focus()
        }
      }
      return self.clients.openWindow(url)
    }),
  )
})
