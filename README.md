# 行业信息助手 (Industry Information Assistant)

基于 AI 的行业深度研究助手，提供智能搜索、深度研究报告、知识图谱、数据可视化、行业资讯、招投标信息、上市公司股票查询等能力，帮助用户快速完成行业研究与信息分析。

## 核心特性

- **深度研究（Deep Research）**：基于 LangGraph 的多智能体工作流，自动完成「问题拆解 → 信息检索 → 数据分析 → 报告撰写 → 交叉验证」的全流程研究。
- **智能搜索与 RAG**：融合网络搜索与本地知识库检索，支持向量召回（Milvus）与重排序（DashScope Rerank）。
- **知识图谱**：对研究内容自动抽取实体与关系，生成可视化知识图谱。
- **数据可视化**：内置 ECharts 图表生成能力，自动产出市场规模、增长趋势等分析图表。
- **行业资讯与招投标**：定时采集行业新闻与招投标信息，支持政策检索。
- **股票查询**：自动识别查询中的上市公司并获取实时行情。
- **会话与记忆**：支持多会话管理、长期记忆与研究断点续跑。

## 技术栈

| 端 | 技术 |
|----|------|
| 前端 | React 19 · Vite · TypeScript · Ant Design 5 · ECharts · Valtio |
| 后端 | FastAPI · SQLAlchemy · LangGraph · LlamaIndex · OpenAI/DashScope SDK |
| 存储 | PostgreSQL 15 · Redis 7 · Milvus 2.3 · Elasticsearch 8 · MinIO |
| AI | 阿里云百炼（DashScope LLM & Embedding & Rerank） · OpenRouter |

## 快速启动

> 详细部署文档见 [READMED.md](./READMED.md)。

### 环境要求

- Docker 20.0+（运行 PostgreSQL、Redis、Milvus、Elasticsearch、MinIO）
- Python 3.10+（后端）
- Node.js 18+（前端）

### 1. 启动基础服务

```bash
# 方式 A：使用启动脚本（推荐）
./start-services.sh start

# 方式 B：Docker Compose
docker compose up -d
```

### 2. 配置环境变量

```bash
cd backend
cp .env.example .env
# 编辑 .env，填入必填的 API Key
```

必填项：`DASHSCOPE_API_KEY`（阿里云百炼）、`BOCHA_API_KEY`（博查搜索）。

### 3. 启动后端

```bash
cd backend
pip install -r requirements.txt
python app/app_main.py
# 后端运行在 http://localhost:8000
```

### 4. 启动前端

```bash
cd frontend
npm install --legacy-peer-deps
npm run dev
# 前端运行在 http://localhost:5173/login
```

## 项目结构

```
industry_information_assistant/
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── api/              # 业务路由
│   │   ├── core/             # 核心配置（安全/数据库/Redis）
│   │   ├── models/           # SQLAlchemy 数据模型
│   │   ├── schemas/          # Pydantic Schema
│   │   ├── service/          # 业务逻辑
│   │   │   └── deep_research_v2/  # 深度研究多智能体工作流
│   │   ├── scripts/          # 初始化脚本
│   │   └── app_main.py       # 入口文件
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/                 # React 前端
│   ├── src/
│   │   ├── api/              # API 调用
│   │   ├── components/       # 通用组件
│   │   ├── pages/            # 页面（chat/database/knowledge/news/bidding...）
│   │   ├── store/            # 状态管理（Valtio）
│   │   └── router/           # 路由
│   └── package.json
├── docker/                   # 数据库初始化脚本
├── docker-compose.yml        # 基础服务编排
├── start-services.sh         # 服务管理脚本
└── READMED.md                # 详细部署文档
```

## 服务访问

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:5173/login |
| 后端 API | http://localhost:8000 |
| API 文档 (Swagger) | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 (postgres/postgres123) |
| Redis | localhost:6379 |
| Milvus | localhost:19530 |
| Elasticsearch | localhost:1200 |
| MinIO Console | localhost:9001 (admin/minioadmin) |

## License

本项目仅用于学习与研究用途。

## 化工资讯智能体扩展（开发中）

第一阶段已加入：

- 行业、客户、工艺、技术、产品五类关注主题；
- “感兴趣/不感兴趣、相关/不相关”反馈与透明权重更新；
- 登录用户的个性化资讯排序及命中主题展示；
- 共享知识库成员角色、修订号、软删除、恢复与审计日志。

详细设计见 [docs/CHEMICAL_INTELLIGENCE_ARCHITECTURE.md](./docs/CHEMICAL_INTELLIGENCE_ARCHITECTURE.md)。已有 PostgreSQL 数据卷需要手动执行 `docker/init-db/02-intelligence-learning.sql`；全新数据卷会在首次初始化时自动执行。
