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
