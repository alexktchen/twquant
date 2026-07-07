/// <reference types="vite/client" />

interface ImportMetaEnv {
  // 後端 API base；同源反代（nginx/vite proxy）時留空。
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
