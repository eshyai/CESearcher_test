# 同事测试部署手册

面向「把这套东西以网页版给同事试用」的场景。本机既是服务器也是开发机。

---

## 一、一键部署（生产模式）

```bash
cd /d/chemical-agent/industry-information-assistant-master
IMAGE_TAG=v0.14 WEB_PORT=8080 ./compose.sh prod up build
```

做的事：构建后端 / 前端镜像（代码固化进镜像，不挂源码）→ 起 3 个业务容器
（`industry_backend` / `industry_worker` / `industry_frontend`）→ 复用已在跑的中间件。

**`WEB_PORT` 默认 5183，这里显式用 8080**，避免和开发时宿主上的 vite（5183）撞车。

> ⚠️ **dev / prod 两套不能同时起**：两边的容器名都是 `industry_backend`，会直接冲突。
> 从 dev 切到 prod 前，先 `./compose.sh dev down`。

---

## 二、同事的访问地址

前端容器是 nginx，同时负责：静态页 + `/auth` `/news` `/research` 等 API 反代（同源，无 CORS 暴露面）。

本机有两个可用网段，**取决于同事走哪条网络**：

| 同事所处网络 | 访问地址 |
|---|---|
| 与你在同一个 Wi-Fi / 局域网 | `http://192.168.43.138:8080` |
| 走公司 aTrust VPN | `http://10.184.0.140:8080` |

用 `ipconfig` 可在网络变化后重新确认。`172.28.80.1` / `172.22.160.1` 是 WSL 和 Hyper-V 的虚拟网卡，**不要给同事**。

---

## 三、防火墙放行（必须，需管理员）

当前 WLAN 是 **Public** 网络配置文件，Windows 默认**禁止一切入站**。不放行的话同事会一直连不上。

以**管理员身份**打开 PowerShell，执行：

```powershell
New-NetFirewallRule -DisplayName "chemical-agent-web-8080" `
  -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Any
```

撤销（测试结束后）：

```powershell
Remove-NetFirewallRule -DisplayName "chemical-agent-web-8080"
```

> 中间件（PG / Redis / Milvus / MinIO）在安全收尾时已全部收敛到 `127.0.0.1`，
> 同事只能从 8080 这一个口进前端，碰不到数据库。

---

## 四、同事怎么拿到账号

**当前为开放自助注册**（`ALLOW_REGISTER=true`）：同事在登录页点「立即注册」，
填用户名 / 邮箱 / 密码即可，**不需要邀请码**，注册完自动登录。
登录页会向 `GET /auth/registration-policy` 查询准入策略，**开放注册时自动隐藏邀请码输入框**。

想切回「凭邀请码注册」：

```bash
# 编辑 backend/.env：ALLOW_REGISTER=false（REGISTER_INVITE_CODE 保持非空）
IMAGE_TAG=v0.14 WEB_PORT=8080 ./compose.sh prod up -d --force-recreate backend
```

此时登录页会自动重新出现邀请码输入框并变为必填。邀请码 = `backend/.env` 的 `REGISTER_INVITE_CODE`。

想**完全关闭**自助注册：`ALLOW_REGISTER=false` + `REGISTER_INVITE_CODE` 留空 → 只能管理员建号。

> 注意：安全收尾时换过 `JWT_SECRET_KEY`，**此前签发的登录态全部失效**，
> 你自己也需要重新登录一次。

---

## 五、日常起停与回滚

```bash
./compose.sh prod ps                 # 看状态
./compose.sh prod logs frontend      # 看 nginx 日志
./compose.sh prod logs backend       # 看接口日志
./compose.sh prod down               # 停业务容器（中间件保持运行）
IMAGE_TAG=v0.14 ./compose.sh prod up # 用同一版本重新拉起
```

**回滚**：镜像打了标签，换回上一个可用标签即可：

```bash
IMAGE_TAG=v0.13 ./compose.sh prod up
```

改代码后要生效**必须重新 build**（prod 不挂源码），**并且换一个新 tag**，
这样旧 tag 仍是真实可回滚的点：

```bash
IMAGE_TAG=v0.15 WEB_PORT=8080 ./compose.sh prod up build backend
```

---

## 六、切回开发模式

```bash
./compose.sh prod down               # 先停 prod
./compose.sh dev up                  # 起 dev（源码挂载 + 热重载）
```

dev 模式下后端仍是 `127.0.0.1:8200`，宿主机上 `npm run dev`（5183）继续用。
想两边都要：**保留 prod 后端 + 只在宿主跑 vite**，`localhost:5183` 走 vite 热更新，
同事那边 `:8080` 走固化版本，互不干扰。只有**改后端代码**时才需要切回 dev。

---

## 七、本轮已知限制（请提前告知同事，避免误报 bug）

| 项 | 现状 |
|---|---|
| 行业 | 只保留**化工**一个行业 |
| 主入口 | `/news` 只留「检索」一个入口；关注主题管理在 `/topics` |
| 检索 | 库内检索秒级；打开「同时搜全网」会打博查，约 5~15s |
| 六智能体 `/chat` | **可用但慢**：一次研究 >5 分钟（单次 LLM 生成实测 147s）。v0.16 前因模型名 404 **只返回空报告**，已修 |
| 投标页 `/bidding` | **取不到数据**（`BID_APP_CODE` 仍是占位符） |
| 知识库传文档 | **传不进去**（DocMind AccessKey 已失效，暂不推进） |
| 记忆库 | 只写不读，检索暂未接入主路径 |
| 协议 | **HTTP，无 HTTPS**。仅限内网试用，不要放公网 |
| 部署形态 | 跑在你的办公本上；关机 / 休眠 / 断网 = 同事访问不了 |

> 模型相关的坑（v0.16 已修，留档备查）：本 maas 实例不含 `qwen-turbo`、`qwen3-max`、
> `deepseek-v3.2`，而**失败是静默的**——六智能体不报错、2 秒返回空报告。换模型名时务必实测，
> 并避开「推理模型」（`deepseek-v4-*`、`kimi-k3` 返回 200 但正文为空）。详见 `backend/.env.example`。

---

## 八、冒烟验证（部署后自测）

```bash
# 1. 前端能打开
curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/

# 2. 首页 HTML 有内容
curl -s --noproxy '*' http://127.0.0.1:8080/ | head -c 200

# 3. 接口反代通（应返回 422/401 而不是 404/502）
curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/auth/login

# 4. 检索链路（vector_indexed 应为 true）
#    include_online 是真实字段名，默认 true（会打博查）；测库内检索记得显式设 false
curl -s --noproxy '*' -X POST http://127.0.0.1:8080/news/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"己二腈","industry_id":"chemical","top_k":3,"include_online":false}'

# 5. 注册准入策略（开放注册应为 allow_register=true）
curl -s --noproxy '*' http://127.0.0.1:8080/auth/registration-policy
```

> 本机 curl 一定要带 `--noproxy '*'`，否则会走环境代理返回 502，误判成服务没起来。
> 另外 `include_web` **不是**有效字段名，写了会被静默忽略、照旧打全网——容易误判成「库内检索很慢」。
