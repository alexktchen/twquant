# 在乾淨的 Linux node 環境打包 React 前端（繞開 Windows toolchain 問題）。
FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY . .
# 只跑 vite build（跳過 tsc 嚴格檢查）；vite-plugin-singlefile 產出自包含單檔
RUN npx vite build

# 產物放到極小的最終層，方便 docker cp 取出
FROM scratch AS artifact
COPY --from=build /app/dist/index.html /twquant_dashboard.html

# 可跑的服務：nginx 靜態伺服 SPA + 反代 /api 到後端（預設 target）
FROM nginx:alpine AS serve
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/index.html /usr/share/nginx/html/index.html
EXPOSE 80
# 用 127.0.0.1 而非 localhost：容器內 localhost 會先解到 IPv6 ::1，但 nginx 只聽 IPv4，
# 會 connection refused 導致誤判 unhealthy。
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
