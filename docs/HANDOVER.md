# 交接文档 —— 下一个会话从这里开始

> **最后更新**：2026-10-27 14:20
> **代码基线**：`ff92875`（main，与 origin 一致，工作区干净）
> **线上镜像标签**：`v0.20-certifi`（可回滚到 v0.19 / v0.18 / v0.17）
> **仓库**：Gitee `yuechuyusheng/chemical_agent`，分支 `main`，约定**改动即时 commit + push**

本文档是「人读」的交接说明。**机器读**的长期记忆在 `.workbuddy/memory/MEMORY.md`（会话启动时自动注入）
和 `.workbuddy/memory/MEMORY-details.md`（拆出去的细节：供应商替换 / 知识库 / LMA / 鉴权端点清单）。
两者内容互补，**先读本文件建立全局观，动手前查 MEMORY.md 的硬约束**。

---

## 0. 三十秒速览

| 问题 | 答案 |
|---|---|
| 这是什么 | 化工行业资讯智能体。碎片信息（公司/产品/工艺/政策）→ 相关报道，带六智能体深度研究、知识库、标注学习 |
| 主入口 | `/news` 检索页（**「拉」为主，「推」已降级**；用户是化工从业者，不是投资者） |
| 后端 | Python 3.13.17 / FastAPI，入口 `backend/app/app_main.py`，端口 **8200（仅 127.0.0.1）** |
| 前端 | React + Vite + antd，9 个页面，生产 80 → 对外 **8080（0.0.0.0）** |
| 中间件 | PostgreSQL / Redis / etcd / MinIO / Milvus / Elasticsearch，**全部只绑 127.0.0.1** |
| 部署形态 | Docker Compose 三套文件 + `compose.sh` 入口；**dev 与 prod 不能同时起** |
| 当前阻塞 | 无。功能可用；已知缺口见 §5 待办 |
| 用户当前用法 | 笔记本 + 手机热点，把 8080 分享给同事内网访问（**尚未上公司服务器**） |

---

## 1. 接手第一步（照着敲，5 分钟确认环境是好的）

```bash
cd /d/chemical-agent/industry-information-assistant-master

# 1) 启动 Docker（本机不是管理员自启，必须手动拉，就绪约 60~120s）
nohup "C:/Users/TJL20051126/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe" >/dev/null 2>&1 & disown
# 探测：不要用 ls //./pipe/...（Git Bash 会挂死）
docker info --format '{{.ServerVersion}}'

# 2) 看容器（正常应有 3 业务 + 6 中间件）
docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}"

# 3) 冒烟三连（都应为 200）
curl -s --noproxy '*' -m 8 http://127.0.0.1:8080/auth/registration-policy; echo
curl -s --noproxy '*' -m 20 -o /dev/null -w 'news/list http=%{http_code}\n' \
  "http://127.0.0.1:8080/news/list?industry_id=chemical&limit=1"
curl -s --noproxy '*' -m 8 -o /dev/null -w 'backend/docs http=%{http_code}（应为 404）\n' http://127.0.0.1:8200/docs

# 4) 代码基线
git status --short        # 应为空
git log --oneline -5
```

**如果容器不在**：`IMAGE_TAG=v0.20-certifi WEB_PORT=8080 ./compose.sh prod up`
（不加 `build` 则复用已有镜像，秒级起来；镜像若被清掉才需要 `up build`，约 3~13 分钟）。
**如果 Docker 起不来**：先看 §6 第 12 条（WSL2 / 端口排除段那一类问题）。

---

## 2. 系统全貌

### 2.1 服务清单

| 服务 | 容器名 | 端口（宿主） | 说明 |
|---|---|---|---|
| backend | `industry_backend` | `127.0.0.1:8200` | FastAPI，**不对外** |
| worker | `industry_worker` | — | 独立调度进程，**每日 12:00** 采集+抽取；非 API 内嵌 |
| frontend | `industry_frontend` | `0.0.0.0:8080→80` | nginx，**唯一对外入口** |
| postgres | `industry_postgres` | `127.0.0.1:5432` | 库名 **`industry_assistant`**（不是 chemical_agent） |
| redis | `industry_redis` | `127.0.0.1:6379` | 缓存 / 会话 |
| etcd | `industry_etcd` | 无（仅容器网） | Milvus 依赖 |
| minio | `industry_minio` | `127.0.0.1:9000/9001` | 对象存储 |
| milvus | `industry_milvus` | `127.0.0.1:19530/9091` | 向量库，embedding **1024 维 COSINE（硬编码）** |
| elasticsearch | `industry_elasticsearch` | **无**（compose 里 ports 被注释） | 1256 落在 Windows 排除段，**故意不暴露**，别去打开 |

### 2.2 三套 compose 与切换纪律

| 文件 | 用途 | 关键差异 |
|---|---|---|
| `docker-compose.yml` | 基底：6 个中间件 | 中间件端口全部 `127.0.0.1` |
| `docker-compose.dev.yml` | 开发：挂源码 + `--reload` | backend 挂 `backend/`，改代码 ~6s 生效 |
| `docker-compose.prod.yml` | 生产：代码固化 | `restart: always`，注入 `ENABLE_DOCS=false` |

⚠️ **dev / prod 的容器名相同（都叫 `industry_backend`）→ 绝对不能同时起**。
切换前必须 `./compose.sh dev down` 或 `./compose.sh prod down`。

⚠️ **prod 不挂源码**：改代码后必须 `up build` **并且换一个新 IMAGE_TAG**，
覆盖旧 tag 会毁掉唯一的回滚点。

### 2.3 目录地图（找东西先看这里）

```
backend/app/
  app_main.py                 入口；12 个 router 挂载在这里（93~104 行）
  config/llm_config.py        resolve_model()：专属env > LLM_MODEL > 代码默认；base_url 三级回落
  router/                     auth / session / knowledge / attachment / memory / database
                              / document / search / chat / research / news / feedback
  service/
    news_search_service.py    检索主链路（库内混合 + 全网 + rerank；第 7c 步补 feedback）
    news_collection_service.py 采集、落库
    news_extraction_service.py 批量抽取（worker 任务，捞 extraction_status='pending'）
    dedup_service.py          标题判重唯一口径 titles_same_article
    industry_relevance_service.py  行业相关性闸
    milvus_guard.py           ⚠️ Milvus 调用必须经此（safe_load/safe_search，默认 15s 超时）
    local_parser_service.py   v0.18 本地文档解析（txt/md/csv/html/pdf/docx/xlsx）
    kb_ingest_service.py      知识库写库唯一入口（切片→embedding→Milvus，按 doc_id 幂等）
    kb_access_service.py      知识库可见范围 + 集合命名的唯一入口（kb_{uuid hex}）
    lma_induction_service.py  LMA 归纳（唯一接触 AI 供应商处：_chat_json）
    deep_research_v2/         六智能体；实际流式走 graph.py::_run_simplified（LangGraph 版已停用）
frontend/src/
  pages/                      news / topics / chat / knowledge / memory / database / bidding / auth
  api/                        统一 request 封装，/research/* 自动加 Bearer
  store/industry.ts           INDUSTRY_CONFIGS = 行业唯一事实来源（当前只有 chemical）
  nginx.conf                  SPA 兜底 + 按裸前缀反代（/auth /news /research …，不是 /api）
docs/                         5 篇设计/部署文档 + 本文件
backend/test/                 各功能的独立验证脚本（见 §7）
```

### 2.4 本地开发模式（后端容器 + 前端宿主）

```bash
# 后端进容器（--no-deps 必加，否则会把中间件一起重建）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --no-deps backend

# 前端在本机跑（默认 5183，代理到 8200）—— 由用户自己起，不要替他起
cd frontend && npm run dev
```

**不要手动 `python app_main.py` 起后端**——会和容器抢 8200。

---

## 3. 配置与凭据

### 3.1 `backend/.env`（**不入库**，33 个键）

compose 用 `env_file:` 整体注入，**没有**在 compose 里硬编码任何 AI 变量。

| 分组 | 键 |
|---|---|
| AI | `DASHSCOPE_API_KEY` `DASHSCOPE_BASE_URL` `LLM_MODEL` `LLM_MODEL_SCOUT` `NEWS_INDUSTRY_GATE_MODEL` `NEWS_EXTRACT_MODEL` `OPENROUTER_API_KEY` |
| 文档解析 | `DOCMIND_ACCESS_KEY_ID` / `_SECRET`（**仍是占位符**） `KB_PARSER=auto` |
| 外部搜索 | `BOCHA_API_KEY`（全网检索走它） `SERPER_API_KEY`（**未配置**，代码里保留但不用） |
| 其他业务 | `JUHE_STOCK_API_KEY` `BID_APP_KEY/_SECRET/_CODE`（**CODE 是占位符**） |
| 中间件 | `POSTGRES_*` `REDIS_*` `MILVUS_*` |
| 鉴权 | `JWT_SECRET_KEY` `JWT_ALGORITHM` `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` `ALLOW_REGISTER=true` `REGISTER_INVITE_CODE` |
| 其他 | `MEM_LIMIT` `TIMEZONE` |

⚠️ 容器内访问中间件用**服务名**（postgres/redis/milvus），`.env` 里面向宿主的 `localhost` 由 compose 的
`environment:` 段覆盖。**不要在 `.env` 里改成服务名**，会让宿主侧脚本失效。

### 3.2 模型现状（**v0.16 修过的最大一次故障**）

- maas 实例 176 个模型，**不含** `qwen-turbo` / `qwen3-max` / `deepseek-v3.2` → 全 404。
- **实测可用且出内容**：`qwen-flash`（最快 1.7s）、`qwen-plus`、`qwen3.8-max`、`glm-5`、`qwen3.7-flash`。
- **推理模型陷阱**：`deepseek-v4.1-flash` / `deepseek-v4-pro` / `kimi-k3` 会 **200 OK 但 `content` 为空**
  （内容在 `reasoning_content`，本项目未适配）→ 选了等于又一次静默空结果。
- 当前配置：`LLM_MODEL=qwen-plus`（六智能体+chat 默认）、`LLM_MODEL_SCOUT=qwen-flash`、
  `NEWS_INDUSTRY_GATE_MODEL=qwen-flash`、`NEWS_EXTRACT_MODEL=qwen-flash`。

---

## 4. 近期已交付（v0.13 → v0.20，按时间倒序）

| 标签 / commit | 内容 | 影响面 |
|---|---|---|
| `ff92875` | 手册对齐 v0.20 | 仅文档 |
| **v0.20-certifi** `23631ac` | `certifi` 2026.7.22 → **2026.10.19**（移除被攻破的 eMudhra 中间 CA），并重建后端镜像（Python 3.13.17 / pip 26.2.2） | 依赖安全 |
| **v0.19-news-portal-fix** `2e7dc23` | 修「点备注框会打开原文」：卡片 `onClick` 加 `e.currentTarget.contains(e.target)`，Popover 内容 `stopPropagation` | `/news` 交互 |
| **v0.18-local-parse** `1c5fe09` | 新增本地文档解析，**不配 DocMind 也能把文档灌进知识库**；`process_document` 改同步 `def` | 知识库主干 |
| **v0.17-news-note** `dbb2755` | 资讯「备注」上线（Popover 编辑 + 卡片黄底展示）；检索结果也返回 feedback | `/news` |
| `316e7f7` | 手册更正「六智能体不可用」那条已知限制 | 仅文档 |
| **v0.16-model-fix** `820b1a2` | 修失效模型名 + `LLM_BASE_URL` 缺失 → 六智能体/行业闸/抽取此前**全部静默失败** | **最高危，已修** |
| **v0.15-auth** `2fbcd56` | 收口 `research_router` 9 个匿名端点 + prod 关闭 `/docs` | 安全 |
| **v0.14** `cf2d0ff` | 开放自助注册；登录页按 `GET /auth/registration-policy` 自动显隐邀请码框 | 准入 |
| `9cbfaef` | 同事测试部署手册（一键部署 / 防火墙 / 已知限制） | 仅文档 |
| `1f753d4` | Milvus 保护层 `milvus_guard.py`（此前裸 `load()` 真实挂死过 49h） | 稳定性 |

---

## 5. 待办清单（按优先级，均带定位）

### P0 / P1（真问题，建议优先）

| # | 问题 | 位置 | 判据 / 修法 |
|---|---|---|---|
| 1 | **删文档不删 Milvus 向量** → 已删内容仍被检索到、被引用进报告 | `knowledge_router.py` 删文档处（只写 `deleted_at`） | 只删向量会让 `restore` 失效 → 正确修法是「删时删向量 + 恢复时重建索引」 |
| 2 | **scout 的 KB 检索无 MIN_SCORE** | `deep_research_v2/agents/scout.py` 合并后 `merged[:top_k]` | 向量召回永远返回 top-K，无关切片必进报告；照 news 那边补阈值过滤 |
| 3 | **`visibility=team` 实际 = 全体登录用户可读** | `kb_access_service.py`（无条件并入 team） | 项目**没有部门/org 模型**，短期只能改文案或加白名单 |
| 4 | 行业相关性闸 **未实测是否真拦不相关内容** | `industry_relevance_service.py` | 拿几条明显无关的资讯跑一遍，看是否被拦 |
| 5 | `service/dr_g.py` 仍硬编码 `deepseek-r1` / `qwen-max`；`database_router.py:232` 硬编码模型 | 同左 | 统一走 `resolve_model()` |

### P2（体验 / 卫生）

| # | 问题 |
|---|---|
| 6 | 上传无文件大小上限；临时文件放容器 `/tmp`，重启不清理；卡在 `processing` 无看门狗 |
| 7 | 仍匿名的 router：`chat` / `document` / `search` / `attachment` / `news`（`document_router`、`search_router` 前端零引用，疑似废弃；`/news/list`、`/news/search` 的匿名是**有意**的） |
| 8 | **全项目无限流**，一旦对外必须补 |
| 9 | 六智能体整轮 >5 分钟（单次约 147s）偏慢，未优化 |
| 10 | 记忆库召回未接入主路径；向量检索缺 MIN_SCORE |
| 11 | `/bidding` 页面 `BID_APP_CODE` 是占位符 → 取不到数，等于半成品 |
| 12 | 扫描件 PDF / `.doc` `.ppt` `.xls` 仍需 DocMind（key 未配，用户明确暂不推进） |
| 13 | K3 标注（`annotations` 表未建）、K4 标注回流检索 —— 设计见 `docs/KB_COLLAB_AND_LMA_DESIGN.md` |

### 环境侧未完成

- 系统 Node `C:\node.js` 仍是 **24.19.0**（低于 10-21 补丁线 24.22.1），升级需管理员权限；
  脚本已备：`D:\node-upgrade-2026-10-22\upgrade-node-24.22.1.bat`（右键以管理员运行）。
  PATH 里实际用的是已修好的 22.23.4，日常不受影响。

---

## 6. 高危坑清单（每条都是真实踩过的，症状 → 根因 → 正确做法）

1. **「有输出但没内容」→ 先测模型名**。六智能体拿到 404 不抛错，2 秒返回空报告
   （`final_report=""`、`results_count=0`）。别去读代码找逻辑 bug，先 `curl` 一下模型是否 exist。
2. **`include_online` 不是 `include_web`**。写错被 pydantic **静默忽略**，照旧打全网博查
   → 会误判成「库内检索很慢」。测纯库内必须显式 `"include_online": false`。
3. **Milvus 调用一律经 `milvus_guard`**。pymilvus 的 `load()` 默认**无限等待**，裸调会拖死整条链路。
4. **`process_document` 必须是同步 `def`**。内部有阻塞轮询（DocMind 最长等 300s），`async def` 会把
   FastAPI 事件循环卡死，整个 API 停摆。
5. **落库 ≠ 可用**。要有 `extracted_topics` / `entities` 才算「可标注、可参与排序」；抽取是 worker 的批量
   任务，**不在检索时同步做**。失败会置 `failed`，而每日管道只捞 `pending` → **一次失败永不重试**；
   补跑前必须 `update ... set extraction_status='pending'`。
6. **antd 浮层 + 整卡可点 = 事件陷阱**。Popover/Tooltip 用 `createPortal` 挂 body，DOM 不在卡片里，
   但 **React 合成事件沿组件树冒泡** → 点浮层会触发卡片 onClick。**别再怀疑 z-index**（实测 1030 正常、
   hit-test 正确）。修法：`e.currentTarget.contains(e.target)`。
7. **判端点是否暴露必须看响应体**。nginx 有 SPA 兜底，任意路径都返回 200。
8. **鉴权口径**：`get_current_user` 是**可选认证**（无 token 返 `None`）→ 用它就等于匿名可达。
   判据：改完后「匿名请求由 **422 变 401**」，对照 `GET /sessions`（401）。
9. **删资讯是大范围破坏**。`industry_news` 无 user_id = 按行业共享语料，删一条**所有人**都看不到，
   且 `news_feedback.news_id` 是 **CASCADE**，会连带删掉**所有人**的反馈。
   → 用户点「不满意」一律走**个人屏蔽**（`news_hidden` + 弱负信号），不删除。删前必须备份
   （`backend/test/backup_low_quality.py`）。
10. **Git Bash 两个必知坑**：① `docker exec -w /app` 会被转成 Windows 路径 → 必须
    `export MSYS_NO_PATHCONV=1`；② 该变量又会让 `curl -o /tmp/x` 写到 `D:/tmp/x`。
    **长 curl（>120s）会被 SIGTERM，必须后台跑**。
11. **端口排除段会漂**。Hyper-V 从动态端口段（本机 `1024 + 13977`）里挖保留段，**每次开机变化**。
    「8000 被保留」这个旧结论 2026-10-22 实测**已不成立**；反过来 ES 的 1256 至今仍在 `1159-1258` 内。
    → 选端口前现场 `netsh int ipv4 show excludedportrange protocol=tcp` 核，别沿用旧记忆。
12. **certifi 里还有 emSign 根证书是正常的**。被攻破的是**中间 CA**，bundle 本来只放根证书
    （当前 120 张全为根）。别把这当成「没修干净」而反复折腾。
13. **Python installer 只认注册表**：`TargetDir=` **不能带引号**（带了被忽略、仍 exit 0 静默不生效）；
    已登记 `C:\Python` 时它只升那个路径 → 先升 `C:\Python`，再 `robocopy /E /XD site-packages tcl Doc`
    同步到 managed（`C:\Python` 里装了 PySide6/PyInstaller，别带过去）。
14. **Bash 里 heredoc 写文件 / 带特定动词前缀的 cmdlet 会被安全策略拦** → 多行内容用 Write 工具；
    提交信息写 `.git/COMMIT_MSG_TMP` 再 `git commit -F`。
15. **改长文档前先 `git diff --stat <doc>` 确认基线 == HEAD**。`KB_COLLAB_AND_LMA_DESIGN.md`
    已连续两次出现「磁盘被回退、HEAD 反而更新」→ 先 `git checkout HEAD -- <doc>` 再叠加。

---

## 7. 调试与验证配方（直接复用）

### 7.1 后端逻辑测试（统一在容器内跑）

```bash
export MSYS_NO_PATHCONV=1
docker cp backend/test/test_xxx.py industry_backend:/tmp/t.py
docker exec -w /app industry_backend python /tmp/t.py
```

现成脚本：`test_title_dedup.py`（判重口径）、`test_kb_collab.py`（16 断言）、`test_kb_isolation.py`（19 断言）、
`test_l2a_induction.py`、`test_l15_feedback_topics.py`、`test_news_hidden.py`、`test_hide_weak_signal.py`、
`test_interest_scoring.py`、`smoke_local_parse.py`（12 断言，文档解析+写库+幂等）、`smoke_container.py`、
`audit_imports.py`。

### 7.2 前端改动是否可编译（无 tsc，vite/esbuild 只剥类型）

```bash
cd frontend && ./node_modules/.bin/vite --port 5199 --strictPort --host 127.0.0.1 &
curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5199/src/pages/news/index.tsx
```

⚠️ ① 不加 `--noproxy '*'` 会走环境代理返回 502，误判没起来；② 临时 vite 用**托管后台任务**起。

### 7.3 UI 交互 bug（**先证伪再改**，别靠猜）

手法：Edge headless + CDP 直发鼠标事件 + 拦截 `window.open` 记调用栈。
完整配方与四个坑见 `.workbuddy/memory/2026-10-22.md`。要点：

```bash
rm -rf /d/tmp/edge-cdp-profile
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
  --remote-debugging-port=9336 "--remote-allow-origins=*" \
  --user-data-dir="D:\tmp\edge-cdp-profile" --window-size=1440,900 about:blank &
# 等 /json/version 可访问后，用 node 脚本连 CDP 发 Input.dispatchMouseEvent
```

坑：① 浏览器进程会被回收 → 起浏览器与跑脚本要在**同一次调用**内完成；
② antd 给两字按钮自动插空格（"取 消"），文本匹配要 `replace(/\s/g,'')`；
③ 对浮层内元素取坐标**不能 `scrollIntoView`**（会让 rc-trigger 重定位，拿到旧坐标）；
④ 探针账号用完要删（`delete from users where username like 'uiprobe%'`，CASCADE 会带走其反馈）。

### 7.4 数据库查询

```bash
docker exec industry_postgres psql -U postgres -d industry_assistant -c "select count(*) from industry_news;"
```

---

## 8. 文档索引

| 文件 | 内容 |
|---|---|
| **`docs/HANDOVER.md`** | **本文件**：换会话/换人接手时的总入口 |
| `docs/COLLEAGUE_TEST_GUIDE.md` | **部署手册**：一键部署、IP/防火墙、邀请码流程、按 tag 回滚、知识库说明、已知限制 |
| `docs/CHEMICAL_INTELLIGENCE_ARCHITECTURE.md` | 系统架构 |
| `docs/KB_COLLAB_AND_LMA_DESIGN.md` | 知识库协作 + LMA 设计稿（附录 B~G），改这两块前必读 |
| `docs/AI_PROVIDER_ABSTRACTION.md` | AI 供应商替换方案（**代码基本未动**，见 §9 约束） |
| `docs/UPSTREAM_DECISION_MEMO.md` | 上游数据源决策记录 |
| `docs/DOCKER_MODES.md` | dev / prod / mw 三种模式说明 |
| `.workbuddy/memory/MEMORY.md` | 长期硬约束（自动注入），**动手前查** |
| `.workbuddy/memory/MEMORY-details.md` | 拆出的细节：供应商替换 / KB / LMA / 鉴权端点清单 |
| `.workbuddy/memory/YYYY-MM-DD.md` | 每日工作日志（append-only） |

---

## 9. 约束级需求（来自用户，不能违反）

1. **产品定位**：用户是**化工从业者，不是投资者**。一等维度 = 产品品类 / 企业 / 地区 / 政策文号 / 工艺 /
   安全环保。抽取 schema 里的 `impact_direction` 是投资者视角，前端已下掉（后端仍在跑，可清理）。
2. **AI 供应商必须可替换**：用户后期可能把**所有 AI 接口换成公司提供的**。
   协议层已统一（`from openai import OpenAI` + `chat.completions.create`），隐患在配置层
   （多套变量名、8+ 文件各自 `getenv` + `new OpenAI`、模型名散落）。
   **三处不走兼容协议**：Rerank（`dashscope.TextReRank` + llama-index）、Embedding（1024 维硬编码在
   `milvus_service.vector_dim`）、DocMind 原生 SDK。
   → 等用户确认三件事：是否 OpenAI 兼容 / embedding 维度 / 是否提供 rerank。**没确认前不要动这块**。
3. **数据安全**：知识库里不要放敏感文件（`team` 可见性 = 全体登录用户可读）；备注虽然按 user_id 隔离，
   但会**截 80 字喂给大模型**，同样不宜写敏感内容。
4. **改动即时 commit + push**；prod 换 tag 不覆盖旧 tag；dev/prod 不同时起。

---

## 10. 给下一个会话的开场白（直接复制粘贴）

```
接着做化工资讯智能体这个项目（D:\chemical-agent\industry-information-assistant-master）。

先读这三份，再动手：
1. docs/HANDOVER.md        —— 交接总览（我刚写完，最新状态都在里面）
2. .workbuddy/memory/MEMORY.md —— 长期硬约束（尤其是「五、模型与 AI 端点」「十、项目约定与踩坑」）
3. docs/COLLEAGUE_TEST_GUIDE.md —— 部署与回滚

当前基线：代码 ff92875 / 线上 v0.20-certifi / 容器 prod 模式。
注意：Docker 不是自启，需要手动拉起（命令在 HANDOVER §1）。

我这次想做的事是：<在这里写具体任务>
```

---

## 11. 交接时的环境快照（2026-10-27 14:20）

| 项 | 状态 |
|---|---|
| git | `ff92875`，工作区干净，与 origin/main 一致 |
| Docker | 交接时引擎为 **29.8.1**，已就绪 |
| 容器 | **9 个全部 Up**（3 业务 + 6 中间件），镜像均为 `v0.20-certifi`；Docker 重启后**自动恢复**（业务 `restart: always`、中间件 `unless-stopped`） |
| 冒烟实测 | `/auth/registration-policy` **200**、`/news/list` **200**、`127.0.0.1:8200/docs` **404** ✅ |
| 数据 | `industry_news` **236 条，`extraction_status` 全部 `completed`**；知识库 **1 个**、文档 **0 个** |
| Node | PATH 版 **22.23.4** / npm 10.9.9（已修 10-21 安全发布）；`C:\node.js` 仍 24.19.0（待管理员升级） |
| Python | 本机 **3.13.17** / pip 26.2.2；容器内同为 3.13.17 / certifi 2026.10.19 |
| 注册策略 | **开放注册**（`ALLOW_REGISTER=true`，邀请码字段自动隐藏） |
| 记忆文件 | `MEMORY.md` 18.5KB（接近注入上限，**再加内容前先压缩或拆到 details**） |
