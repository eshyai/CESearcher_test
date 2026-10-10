# 容器化：两套模式并存

> 结论先行：容器化**不影响改功能的效率**。决定效率的是"代码挂载"还是"代码固化"，
> 所以本项目同时维护两套 compose 配置，用同一份 `docker-compose.yml`（中间件）作为公共底座。

---

## 零、前置条件：Docker 镜像源

国内网络**无法直连 Docker Hub**（`registry-1.docker.io` 连接直接 EOF），
拉取 `python:3.13-slim`、`node:22-alpine` 这类基础镜像会失败。

一次性解决——在 Docker Desktop → Settings → Docker Engine 里追加 `registry-mirrors`
（等同于编辑 `%USERPROFILE%\.docker\daemon.json`），然后 **Apply & Restart**：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run"
  ]
}
```

> 本项目已实测：`docker.m.daocloud.io` 可正常拉取 `python:3.13-slim` 与 `nginx:1.27-alpine`。
> 部署到服务器时，服务器上同样需要配这项（或直接用同镜像源的地址）。
>
> **当前状态**：`daemon.json` 已写入上面两个镜像源，但 Docker Desktop 还没重启（`docker info`
> 里看不到 `Registry Mirrors`）。本机三个基础镜像 `python:3.13-slim` / `node:22-alpine` /
> `nginx:1.27-alpine` 已通过镜像源拉好并打上标准标签，所以**现在就能直接 build**；
> 重启一次 Docker Desktop 之后镜像源才会对后续所有拉取生效。
> 原配置备份在 `~/.docker/daemon.json.bak-20261009`。

`backend/.env` 里的 `PIP_INDEX_URL` 已指向清华源，容器内装 Python 依赖不走 Docker Hub。

---

## 一、文件结构

```
docker-compose.yml            # 公共底座：PG / Redis / Milvus / etcd / MinIO / ES（两套模式共用，不用改）
docker-compose.dev.yml        # 开发模式：源码挂载 + 热重载
docker-compose.prod.yml       # 生产模式：代码固化进镜像 + restart: always

backend/app/Dockerfile        # 生产镜像（Python 3.13 + 固化代码）
backend/app/Dockerfile.dev    # 开发镜像（只装依赖，代码靠挂载）
backend/app/requirements.docker.txt   # 容器依赖锁文件（由可用环境 pip freeze 导出）

frontend/Dockerfile           # 生产镜像（Vite build → nginx 静态托管）
frontend/Dockerfile.dev       # 开发镜像（node + vite dev server）
frontend/nginx.conf           # 生产环境反向代理 + SPA 兜底
frontend/.env.docker          # 容器内开发模式的 vite 环境变量（代理指向 backend 服务名）
frontend/.npmrc               # legacy-peer-deps=true，修 ahooks vs React 19 的 peer 冲突

compose.sh                    # 两套模式的命令封装（./compose.sh dev up 等）
backend/test/audit_imports.py   # 依赖完整性审计（声明式清单 vs 代码实际 import）
backend/test/smoke_container.py # 镜像冒烟测试（容器内验证依赖/中间件/接口）
```

三套 compose 的关系：

| 命令 | 生效文件 | 结果 |
|---|---|---|
| `docker compose up -d` | 只有 `docker-compose.yml` | **只启动中间件**（与容器化之前行为完全一致） |
| `-f docker-compose.yml -f docker-compose.dev.yml up -d` | 底座 + 开发 | 中间件 + 挂载模式的前后端 |
| `-f docker-compose.yml -f docker-compose.prod.yml up -d` | 底座 + 生产 | 中间件 + 固化镜像的 前后端 + 采集 Worker |

> 关键点：一旦显式写了 `-f`，compose 就**不会**再自动加载 `docker-compose.override.yml` 之类的默认叠加文件。
> 所以这里刻意不用 `override` 命名，避免"只想起中间件却连带起了前后端"。

---

## 二、日常开发（98% 的时间用这个）

### 启动

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

访问 `http://localhost:5183`。

如果嫌命令长，用根目录的便捷脚本：

```bash
./compose.sh dev up        # 启动开发环境
./compose.sh dev logs backend
./compose.sh dev down
```

### 改功能时的行为

| 改动内容 | 需要做什么 | 生效时间 |
|---|---|---|
| 后端 `.py` 业务代码 | 保存 | 秒级（uvicorn `--reload` 自动重启） |
| 前端 `.tsx / .scss` | 保存 | 秒级（Vite HMR） |
| `backend/.env`（新加 key） | `./compose.sh dev restart backend` | 数秒 |
| `requirements.docker.txt` / `package.json` | `./compose.sh dev build` | 数分钟（重装依赖） |
| 建表 SQL（`docker/init-db/*.sql`） | 见下方「数据库迁移」 | — |

**结论：改业务代码的体验和你现在开两个终端跑 `python app/app_main.py` + `npm run dev` 完全一样，甚至更省事**（不用再记 `API_PORT=8200`）。

### 数据库迁移

`docker/init-db/*.sql` 只在 **postgres 数据卷为空时**执行一次。已有数据的情况下加列，仍然走原来的手工路径：

```bash
docker cp docker/init-db/05-xxx.sql industry_postgres:/tmp/05-xxx.sql
docker exec -it industry_postgres psql -U postgres -d industry_assistant -f /tmp/05-xxx.sql
```

---

## 三、部署到服务器（生产模式）

### 首次部署

```bash
# 1. 服务器上准备环境变量（.env 不进 git，必须手工放）
scp backend/.env  server:/opt/chemical-agent/backend/.env

# 2. 拉代码
git clone <gitee 仓库> /opt/chemical-agent && cd /opt/chemical-agent

# 3. 构建并启动（带版本号，方便回滚）
IMAGE_TAG=v0.4 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

启动后：

- 前端 `http://<服务器IP>:5183`
- 后端 `http://<服务器IP>:8200`
- 采集 Worker 常驻，每天 12:00 自动跑管道

### 更新功能

```bash
git pull
IMAGE_TAG=v0.5 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### 回滚

```bash
IMAGE_TAG=v0.4 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

因为镜像按 `IMAGE_TAG` 打了标签，回滚只是把容器重新指向旧镜像，**秒级完成**。

---

## 四、两套模式容易踩的点

1. **不要同时启动两套**：`industry_backend` / `industry_frontend` 用的是同一组容器名和端口，
   切换前先 `down` 掉当前模式。

2. **开发模式下改代码不生效**：检查 `./backend/app:/app` 挂载是否掉了（`docker compose ... config | grep -A5 volumes`）。

3. **Windows 上热重载偶发不触发**：Docker Desktop 的 WSL2 后端一般能收到文件事件；
   如果失效，把 `docker-compose.dev.yml` 里 backend 的 `--reload` 换成
   `--reload --reload-delay 2`，或者重启容器。

4. **前端 node_modules 冲突**：dev 模式下源码目录整个挂载进来，
   宿主机 Windows 版 `node_modules`（含 `esbuild.exe`）会让容器里的 vite 起不来。
   已用命名卷 `frontend_node_modules` 在 `/app/node_modules` 上做隔离，不要删掉这个挂载。

5. **容器内数据库地址**：`backend/.env` 里写的是 `localhost`（面向宿主机），
   compose 用 `environment:` 覆盖成服务名 `postgres` / `redis` / `milvus`。
   容器内代码若自己新建连接，务必用服务名。

6. **`npm ci` 必须带 `.npmrc`**：`ahooks@3.8.4` 声明的 peer 是 React `^16.8||^17||^18`，
   不接受 React 19，`npm ci` 会直接 ERESOLVE 失败。已加 `frontend/.npmrc`（`legacy-peer-deps=true`），
   并且两个前端 Dockerfile 都显式 `COPY ... .npmrc ./`——**新增前端依赖时不要删掉这个 COPY**。

7. **nginx 的 upstream 用变量 + 运行时 DNS 解析**：`frontend/nginx.conf` 里写了
   `resolver 127.0.0.11` + `set $upstream_backend` + `proxy_pass $upstream_backend$request_uri`。
   如果改回字面量 `proxy_pass http://backend:8200`，会变成**启动时解析**——
   后端容器没起来 nginx 直接启动失败，后端重建换 IP 后会一直 502。

8. **ARM 服务器**（如华为鲲鹏/部分云主机）上构建镜像会重新解析依赖，
   首次 build 时间明显更长；如遇某个包没有 aarch64 wheel，需要补 `apt-get install build-essential`。

---

## 五、实测验证记录

全部命令在 Docker Desktop（Linux 容器）+ 已运行的 6 个中间件容器下实测通过。

| 项目 | 结果 |
|---|---|
| `docker compose ... config`（两套合并） | 通过，dev = 8 个服务，prod = 9 个（多 `worker`） |
| 环境变量覆盖 | `environment` 正确压过 `env_file`：容器内 `POSTGRES_HOST=postgres` |
| backend 镜像构建 | 通过，168 个依赖全部装成 cp313 manylinux wheel，无需编译器 |
| frontend 镜像构建 | 通过（加 `.npmrc` 后），vite build 约 84s |
| 镜像冒烟（dev） | 27 个关键依赖 import 通过；`app_main` 注册 17 条路由；PG / Redis / Milvus 全部连通；`/hello` `/news/industries` `/news/search` 均 200 |
| 生产前端 nginx | `nginx -t` 通过；`/` 返回 index.html；6 条页面路由（含嵌套 `/chat/abc123`）刷新全部回 index.html；`/hello` `/news/industries` `POST /news/search` 经代理返回正确 JSON；`/assets/*` 带 `immutable` 长缓存 |
| 开发前端 vite | 启动 2.1s；`/news/industries`、`POST /news/search` 经代理返回正确 JSON；`/news` 刷新回 index.html |
| 中间件与手动进程 | 全程未重启、未占用，6 个中间件容器与用户手动起的 8200/5183 进程不受影响 |

复现冒烟测试：

```bash
# 注意：容器内启用的是「源码挂载」，所以脚本在宿主机的 backend/test/ 下，用 stdin 喂进去
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps -T backend \
  python - < backend/test/smoke_container.py
```

---

## 六、依赖清单的遗留问题（待办）

`backend/app/requirements.txt` 是项目早期的声明式清单，与当前代码实际依赖**严重不符**：

- 声明了但代码/环境里没有：`opencv-python`、`onnxruntime`、`xgboost`、`shapely`、`pyclipper`、
  `datrie`、`hanziconv`、`tika`、`beartype`、`html-text`、`pdfplumber` 等
- 代码在用但没声明：`redis`、`bcrypt`、`python-jose`、`trafilatura`、`beautifulsoup4`、
  `matplotlib`、`seaborn`、`langgraph`、`pandas` 等
- 另有一条错误条目：`dotenv==0.9.9` 对应的并不是 `python-dotenv`（本机实际装的是 `python-dotenv`）

**容器构建一律使用 `requirements.docker.txt`**。建议后续二选一清理：

- 要么删掉 `app/requirements.txt`，把 `requirements.docker.txt` 当作唯一事实来源；
- 要么用 `backend/test/audit_imports.py` 的输出重写一份干净的声明式清单，再 `pip freeze` 生成锁文件。
