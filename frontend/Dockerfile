# ==================== 构建阶段 ====================
FROM node:22-alpine AS builder

WORKDIR /app

# 依赖单独占一层，改业务代码不会重装 npm 依赖
# .npmrc 必须一起 COPY，npm ci 会读其中的 legacy-peer-deps
COPY package.json package-lock.json .npmrc ./
# HUSKY=0：容器内没有 .git，禁用 husky 的 prepare 钩子避免安装失败
ENV HUSKY=0
RUN npm ci --registry=https://registry.npmmirror.com

COPY . .
RUN npm run build


# ==================== 运行阶段 ====================
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
