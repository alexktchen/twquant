/// <reference types="vite/client" />

interface ImportMetaEnv {
  // 後端 API base；同源反代（nginx/vite proxy）時留空。
  readonly VITE_API_BASE?: string
  // 'cloud'＝雲端精簡版（每日推薦、歷史名單、K 線）；其他＝完整研究平台
  readonly VITE_APP_MODE?: string
  // Supabase Auth（T-439）：專案網址與 anon 金鑰（公開的，設計上就是給瀏覽器用）；沒設就不能登入
  readonly VITE_SUPABASE_URL?: string
  readonly VITE_SUPABASE_ANON_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
