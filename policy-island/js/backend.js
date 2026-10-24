/* 靜態站（Cloudflare Pages）的共享後端設定。
   填入 Supabase 專案的 URL 與 anon public key 就會啟用：匿名登入、即時線上人數、共享戰績、政策與議題。
   anon key 是設計給瀏覽器用的公開金鑰（權限由 supabase/schema.sql 的 RLS 控制），可以放這裡；service_role key 絕對不要放。
   兩個都空著 = 本機模式（戰績只存在瀏覽器）。在 claude.ai artifact 裡會自動改用 artifact 的共享資料，不會用到這裡。 */
window.PI_BACKEND = {
  supabaseUrl: 'https://sagvwzinlltlypxgxkre.supabase.co',
  supabaseAnonKey: '',  // Project Settings → API → anon public
  supabaseJs: 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/+esm',
  heroesLimit: 1000,
};
