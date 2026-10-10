# AI 接口可替换改造方案

> 状态：**方案稿，未动代码**。
> 起因：用户提出后期可能把项目里所有 AI 接口整体换成公司提供的；DocMind 接入暂不推进；
> LMA（偏好学习）可以先做，但"接口接给谁"未定。
> 结论摘要：**协议层已经统一，真正的债在配置层和三处旁路**。建议先做一次"零行为变更"的入口收敛，
> 把"供应商是谁"压缩成一处配置，之后再写 LMA 就不会返工。
> 日期：2026-10-10

---

## 一、为什么先做这件事

用户的要求本质是：**"供应商"是变量，不是常量**。当前代码把供应商当常量写在了十几个地方，于是

- 每写一个新功能（比如 LMA 的归纳引擎），就多一个写死 DashScope 的调用点；
- 换供应商时改动面随功能数量线性增长，且**没有清单能保证不漏**。

先收敛入口，等于把"换供应商"从"改 N 个文件"降级成"改 1 个配置"。**LMA 越晚写越该先做这步**。

---

## 二、现状盘点

### 2.1 全部 AI 调用点

#### A. 对话 / 生成类（都是 OpenAI 兼容协议）

| 位置 | 密钥来源 | 接入地址来源 | 模型来源 |
|---|---|---|---|
| `service/chat_service.py:33-35` | `DASHSCOPE_API_KEY` | **`OPENAI_BASE_URL`** | `OPENAI_MODEL`（默认 `deepseek-r1`） |
| `service/dr_g.py:27-28,137` | 模块常量 `LLM_API_KEY` | 模块常量 `LLM_BASE_URL`（读 `DASHSCOPE_BASE_URL`） | 硬编码 `qwen-max` / `deepseek-r1` |
| `service/deep_research_v2/graph.py:104-120` | `llm_config.get_config()` | 同上 | `AgentsConfig`（六 agent，硬编码 `deepseek-v3.2` / `qwen-plus`） |
| `service/deep_research_v2/service.py:63` | `llm_config.get_config()` | 同上 | 同上 |
| `service/deep_research_v2/agents/base.py:34-39` | 构造参数 | 构造参数 | 默认 `qwen-max` |
| `service/memory_service.py:37-40` | `DASHSCOPE_API_KEY` | `DASHSCOPE_BASE_URL` | `DASHSCOPE_MODEL`（默认 `qwen-plus`） |
| `service/news_extraction_service.py:78-84` | `DASHSCOPE_API_KEY` | `DASHSCOPE_BASE_URL` | `NEWS_EXTRACT_MODEL`（默认 `qwen-turbo`），用 `AsyncOpenAI` |
| `service/industry_relevance_service.py:128-138` | `DASHSCOPE_API_KEY` | `DASHSCOPE_BASE_URL` | `NEWS_INDUSTRY_GATE_MODEL`（默认 `qwen-turbo`） |
| `service/text2sql_service.py:170,185` | 构造参数 | 构造参数 | 默认 `qwen-max` |
| `service/react_controller.py:347,364` | 构造参数 | 构造参数 | 默认 `qwen-max` |
| `service/tool_executor.py:92` | 构造参数 | 构造参数 | 调用方给 |
| `router/database_router.py:213,227-232` | `llm_config.get_config()` | 同上 | **硬编码 `qwen-plus`** |

#### B. 向量化（OpenAI 兼容的 `/embeddings`）

| 位置 | 说明 |
|---|---|
| `service/embedding_service.py:45-46,53` | `DASHSCOPE_API_KEY` + `DASHSCOPE_BASE_URL`；模型名 `text-embedding-v4`、`dimensions=1024` **硬编码在函数签名默认值** |
| 消费方 | `docmind_service`（知识库切片）、`news_index_service`（资讯）、`memory_service`（长期记忆） |

#### C. 重排（**不走兼容协议**）

| 位置 | 说明 |
|---|---|
| `service/news_search_service.py:447-477` | `dashscope.TextReRank.call(...)` —— 阿里云原生 SDK；候选链 `NEWS_RERANK_MODEL`（默认 `gte-rerank-v2,qwen3-rerank`） |
| `service/embedding_service.py:97-133` | `llama_index` 的 `DashScopeRerank` —— 另一套原生封装 |

#### D. 文档解析（**不走兼容协议，且已坏**）

| 位置 | 说明 |
|---|---|
| `service/docmind_service.py` | `alibabacloud_docmind_api20220711` 原生 SDK；独立凭证 `DOCMIND_ACCESS_KEY_ID/SECRET`（**已失效**） |

### 2.2 协议层：已经统一（好消息）

所有对话/生成类调用都是 `from openai import OpenAI` + `client.chat.completions.create(...)`，
接入地址指向 `https://dashscope.aliyuncs.com/compatible-mode/v1`。

**这意味着如果公司给的接口也是 OpenAI 兼容格式，90% 的改动只是换配置。** 这是当前代码最好的一个特性，
改造时必须保住它——**不要为了"抽象"引入一套私有协议**。

顺带一个容易忽略的事实：现在跑的是 **DashScope 网关 + `deepseek-v3.2` / `qwen-plus` / `qwen-turbo` 混合模型**。
也就是说"模型"和"网关"**已经是解耦的两个维度**了。用户说的"换供应商"可能是换网关、也可能是换模型，
改造方案要让这两件事都能单独换。

### 2.3 配置层：这是真正的债

现在有**两个配置入口**，且各服务大量绕过它们自己读环境变量：

- `config/llm_config.py` — `LLMConfig` / `AgentsConfig`（V2 深研、database_router、research_router 在用）
- `service/config.py` — `ServiceConfig.get_api_config()`（chat / document / research / search 路由在用）

具体症状：

| 症状 | 细节 |
|---|---|
| 接入地址 **3 套变量名** | `LLM_BASE_URL`（llm_config）、`DASHSCOPE_BASE_URL`（dr_g / embedding / memory / extraction / gate）、`OPENAI_BASE_URL`（chat_service） |
| 密钥 **2 套来源** | `DASHSCOPE_API_KEY`、`dr_g.py` 的模块常量 `LLM_API_KEY` |
| 模型名 **5 个环境变量 + 多处硬编码** | env：`DASHSCOPE_MODEL` / `NEWS_EXTRACT_MODEL` / `NEWS_INDUSTRY_GATE_MODEL` / `NEWS_RERANK_MODEL` / `OPENAI_MODEL`；硬编码：六个 agent 的 `qwen-max`、`database_router` 的 `qwen-plus`、`dr_g` 的 `qwen-max`/`deepseek-r1` |
| **无统一入口** | 8 个以上文件各自 `os.getenv(...)` 后 `new OpenAI(...)` |

后果很直接：换供应商时**没有任何机制能保证不漏**，漏掉的那处会在运行时静默退回默认地址报 401。

### 2.4 旁路层：三处不走兼容协议

| 能力 | 现状 | 换供应商的代价 |
|---|---|---|
| **Rerank** | `dashscope.TextReRank` + `llama-index DashScopeRerank` | 公司若只提供 chat/embedding，**这处必须换成等价服务或关掉** |
| **Embedding 维度** | `milvus_service.py:20` 写死 `self.vector_dim = 1024` | 换非 1024 维模型 → **Milvus 集合结构不兼容，必须重建 + 全量重嵌**（最硬的一处） |
| **DocMind** | 阿里云原生 SDK | 用户已明确暂不推进；但**知识库上传链路会一直是断的** |

Embedding 这处要特别强调：**它是唯一会"静默坏掉"的地方**。维度不匹配时不会被配置错误拦住，
而是插入失败或检索结果全乱——必须在改造时把它变成**显式校验**。

---

## 三、目标形态

新增一个薄包 `backend/app/ai/`，作为所有 AI 调用的**唯一入口**：

```
backend/app/ai/
├── __init__.py          # 对外只暴露 get_chat / get_embedder / get_reranker
├── config.py            # 统一读取 AI_* 变量，并向下兼容旧变量名
├── base.py              # 三个 Protocol：ChatProvider / EmbeddingProvider / RerankProvider
├── openai_compat.py     # 一个实现同时覆盖 chat + embedding（协议相同）
├── dashscope_native.py  # rerank 的原生实现（保持现状，作为默认）
└── factory.py           # 按 AI_PROVIDER 选实现，进程内单例
```

### 3.1 三组接口（刻意做小）

```python
class ChatProvider(Protocol):
    def chat(self, messages, *, model=None, temperature=None,
             max_tokens=None, response_format=None) -> str: ...
    async def achat(self, messages, **kw) -> str: ...

class EmbeddingProvider(Protocol):
    dimension: int                       # 供 Milvus 校验
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class RerankProvider(Protocol):
    available: bool
    def rerank(self, query: str, docs: list[str],
               top_n: int | None = None) -> list[tuple[int, float]]: ...
```

三个设计取向：

1. **只覆盖"能力"，不覆盖"厂商"**。接口里不出现 `dashscope` / `openai` 字样，换供应商写新实现即可，
   业务代码零改动。
2. **同步 + 异步都要**。`news_extraction_service` 用 `AsyncOpenAI`，抽取是并发的，不能只留同步接口。
3. **`RerankProvider.available` 显式暴露**。`news_search_service` 已有"rerank 不可用 → 退回融合分"的
   降级路径，把它提升为接口的一部分，而不是靠 try/except 猜。

### 3.2 各服务怎么改

| 现状写法 | 改后 |
|---|---|
| `os.getenv("DASHSCOPE_API_KEY")` + `OpenAI(...)` + `chat.completions.create(...)` | `get_chat().chat(messages, model=...)` |
| `dashscope.TextReRank.call(...)` | `get_reranker().rerank(query, docs)` |
| `generate_embedding(texts)` 内部自取密钥 | 内部改走 `get_embedder().embed(texts)`，函数签名不变（保住调用方） |

**关键约束：改造期间不改业务逻辑，只改"取值来源"。** 这样每一步都可以用现有回归测试验证行为未变。

---

## 四、配置规范

新增 `AI_*` 变量，**全部带旧变量回落**——这是保证"零破坏迁移"的核心：

| 新变量 | 默认 / 回落到 | 用途 |
|---|---|---|
| `AI_PROVIDER` | `openai_compat` | 选哪套实现 |
| `AI_BASE_URL` | `DASHSCOPE_BASE_URL` → `LLM_BASE_URL` → `OPENAI_BASE_URL` → dashscope 兼容地址 | 兼容协议接入点 |
| `AI_API_KEY` | `DASHSCOPE_API_KEY` → `LLM_API_KEY` | 密钥 |
| `AI_MODEL_DEEP` | `deepseek-v3.2` | 写作 / 分析 / 审核 / 规划 |
| `AI_MODEL_STANDARD` | `qwen-plus` | scout / 记忆归纳 |
| `AI_MODEL_FAST` | `qwen-turbo` | 资讯抽取 / 行业闸（高频轻量） |
| `AI_EMBED_MODEL` | `text-embedding-v4` | 向量模型 |
| `AI_EMBED_DIM` | `1024` | **必须与 Milvus 集合一致** |
| `AI_RERANK_ENABLED` | `1` | 关掉即退回融合分排序 |
| `AI_RERANK_MODEL` | `gte-rerank-v2,qwen3-rerank` | 候选链，按序尝试 |

设计说明：

- **模型按"档位"而非按"模块"命名**。现在 5 个模块级变量（`NEWS_EXTRACT_MODEL` 等）看似灵活，
  实际让"换供应商"变成要改 5 个键。档位制只需换 3 个值，且新模块能直接归类。
  保留模块级变量作为**可选覆盖**，不强制。
- **旧变量保留读取，但不再写进 `.env`**。迁移期双读，稳定后清掉兼容层。

---

## 五、分阶段实施

| 阶段 | 内容 | 改动面 | 验收 |
|---|---|---|---|
| **P0 入口收敛** | 新增 `ai/config.py` + `ai/factory.py`；把 3 套接入地址变量名收敛到一处；各服务**仍用原 SDK**，只是取值来源改成统一入口 | 新增 2 文件 + 改各服务的取值行 | `grep -r "DASHSCOPE_BASE_URL\|OPENAI_BASE_URL" app/` 只剩兼容层；现有测试全绿；**行为零变化** |
| **P1 对话类接入** | `chat_service` / `dr_g` / `deep_research_v2` 六 agent / `text2sql` / `react_controller` / `tool_executor` / `memory` / `extraction` / `industry_gate` 全部改走 `get_chat()` | 12 处 | 深研跑通、抽取跑通、行业闸跑通；模型名全部可配 |
| **P2 向量与重排接入** | `embedding_service` 改走 `get_embedder()` / `get_reranker()`；**新增启动维度自检**（读 Milvus 集合 dim 与 `AI_EMBED_DIM` 比对，不一致直接 ERROR 拒绝写入，不静默失败） | 2 处 + 自检 | 新旧集合均能检索；故意把 `AI_EMBED_DIM` 改成 768 → 启动即报错 |
| **P3 按公司接口落地** | 按确认结果写 adapter（若是 OpenAI 兼容则**只需改配置**）+ 迁移 Milvus 集合（若维度变化）+ 更新 `.env` | 视确认结果 | 端到端检索、抽取、深研全部正常 |

**P0 是纯收益、可当天完成、不影响任何现有行为**，建议先做。

### 若 embedding 维度确实要变（P3 的迁移流程）

1. 建新集合（如 `news_chemical_v2`），维度按新模型；
2. 全量重嵌回填：资讯（`industry_news`）、知识库切片（`kb_*`）、长期记忆（`long_term_memories`）；
3. 检索侧切到新集合（配置项控制），旧集合**保留一个版本周期**以便回滚；
4. 确认稳定后再下线旧集合。

因为 K2 刚把知识库集合改成 `kb_{uuid}` 命名、且当前 `kb_*` 集合为空，**知识库这部分的迁移成本目前是零**。

---

## 六、待确认（决定 P3 能不能做）

| # | 问题 | 影响 |
|---|---|---|
| 1 | 公司接口是否 **OpenAI 兼容**格式？ | 是 → P3 只是改配置；否 → 需写一个 `ChatProvider` adapter（业务代码仍不动） |
| 2 | 是否提供 **embedding**？维度多少？ | 1024 → 无缝；否则要走上面的集合迁移 |
| 3 | 是否提供 **rerank**？ | 不提供 → 需决定"关掉重排"还是"另找服务"，检索质量会受影响 |
| 4 | 文档解析（DocMind 替代）？ | 用户已说暂不表；**在此期间知识库上传链路保持断开** |
| 5 | 是否只给网关、模型仍是 qwen/deepseek？还是模型也换成公司自研？ | 决定"档位"怎么配 |

前三个是硬信息，拿到就能定 P3 的工作量。

---

## 七、明确不做

- **不做模型微调 / 自训**（已与用户确认）。
- **不做运行时热切换、多供应商负载均衡** —— 当前没有这个需求，重启改配置足够，加了是纯复杂度。
- **不引入私有协议** —— 保住"OpenAI 兼容"这个既有优势。
- **不动 DocMind**（按用户要求）。
- **不在本轮改业务逻辑** —— 只改取值来源，保证每步可回归。

---

## 附：顺手发现的两个问题

1. **`service/config.py:17` 硬编码了密钥**：
   `'api_key': os.environ.get('API_KEY', 'ragflow-FiZjAzYTVjMWM1YTExZjA4MGFmNTZlOT')`
   以及 `:18` 的 `default_dataset_id` 也是硬编码。这是 RAGFlow 的默认值，虽非当前主链路，
   但**密钥写进源码**本身是隐患，建议改造时一并清成空默认 + 显式报错。
2. **`service/embedding_service.py` 里有两套 rerank 实现**（`DashScopeRerank` 与
   `news_search_service` 的 `TextReRank`），改造时应统一到 `RerankProvider` 一个实现，避免两处漂移。
