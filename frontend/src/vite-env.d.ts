/// <reference types="vite/client" />

interface ImportMetaEnv {
  // 後端 API base；同源反代（nginx/vite proxy）時留空。
  readonly VITE_API_BASE?: string
  // 'cloud'＝雲端精簡版（每日推薦、歷史名單、K 線）；其他＝完整研究平台
  readonly VITE_APP_MODE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
