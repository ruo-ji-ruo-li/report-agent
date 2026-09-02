# 体检报告问答助手(report-agent)系统设计

- 日期:2026-09-02
- 状态:设计已经用户逐节确认,待实现规划
- 需求文档:`docs/requirements.md`(需求与约束以该文档为准,本文不重复)
- 参考架构:`D:\DeskTop\agent\all-in-rag\code\C9`(菜谱 GraphRAG 项目,继承与规避清单见第 13 节)

---

## 1. 设计原则

1. **控制流分层混合**(需求决策 1):报告解读与复查计划走确定性管线(显式阶段函数);追问对话走有界 Agent(固定工具集 + 轮数上限)
2. **LLM 只做解释与成文**:区间判定、单位换算、危急值触发、组合模式识别全部为代码逻辑;LLM 失败时降级到规则/模板,不阻塞产出
3. **KG 是事实源,向量库是派生检索层**(需求决策 4):知识事实只存 Neo4j;Milvus 语料由 KG + 种子释义派生,可随时重建
4. **降级显式化**:每个外部依赖失败都有一条写明的降级路径(第 11 节),系统整体永不因单点失败而不可用
5. **可审计**:任务表 checkpoints 持久化每阶段中间产物,护栏/降级/拒答事件全部入审计表

## 2. 本次设计过程中确认的决策

| 决策点 | 结论 |
|---|---|
| 部署形态 | 云服务器自建 docker-compose(Neo4j + Milvus + PostgreSQL),LLM/Embedding 用云端 API |
| 种子数据来源 | LLM 起草 → YAML 草稿 → 人工校对 → 脚本增量入库 |
| 用户模型 | 无鉴权 + ID 隔离(report_id / session_id);表结构预留 user_id 可空列 |
| BM25 实现 | **Milvus 2.5 内建全文检索**(text 字段 BM25 Function + jieba analyzer + 稀疏倒排索引),不用进程内 rank-bm25,零重建、真增量 |
| meta 缺失处理 | 解析后性别/年龄缺失 → 任务暂停 `awaiting_meta`,API 补录后继续,不静默降级 |
| 参考区间优先级 | **报告自带区间优先**(反映检测方法特异性),缺失时用 KG 按(指标×性别×年龄段)补全;**危急值阈值永远来自 KG**;两者差异显著时记 audit 事件 |
| 编排形态 | 解读管线为纯 async 显式阶段代码;**LangGraph 仅用于追问 Agent**(Postgres checkpointer)——"必要处使用" |
| 异步任务机制 | 进程内 asyncio + Postgres 状态表(checkpoints)+ 启动恢复;不引入 Celery/Redis |
| 仓库与依赖 | 独立 git 仓库于 `D:\DeskTop\agent\report-agent`;目录分 `backend/` + `frontend/`(预留)+ `docs/`;依赖管理 uv + pyproject.toml,Python 3.13 |

## 3. 系统架构与代码结构

### 3.1 分层架构

```
┌─ FastAPI 层 ──────────────────────────────────────────────┐
│  /reports(上传/手动录入)  /tasks(轮询)  /chat(SSE)      │
├─ 服务层 ─────────────────────────────────────────────────┤
│  报告解读管线(确定性,显式阶段函数)                      │
│  追问 Agent(LangGraph 有界,4 工具 + 轮数上限)           │
│  复查计划(模板渲染 + 槽位锁定润色)                      │
├─ 护栏层(横切,被管线与 Agent 共用)─────────────────────┤
│  规则护栏(纯代码) / 二次 LLM 审核(风险信号触发) / 审计  │
├─ 知识与检索层 ───────────────────────────────────────────┤
│  三路检索: KG(Neo4j) + 向量(Milvus dense) + BM25(Milvus 稀疏) → RRF 融合 │
│  指标词典 / 参考区间查询 / 组合模式判定(纯代码)          │
├─ 基础设施层 ─────────────────────────────────────────────┤
│  LLM 客户端(OpenAI SDK→DeepSeek, 指数退避) / 结构化日志 │
│  Postgres(SQLAlchemy async) / Milvus / Neo4j            │
└───────────────────────────────────────────────────────────┘
```

依赖方向:上层依赖下层;检索层三个后端互相独立,任一失败返回空结果由 RRF 自然吸收。

### 3.2 代码结构

```
report-agent/
├── docs/
│   ├── requirements.md
│   └── superpowers/specs/           # 设计文档(本文件)
├── backend/
│   ├── src/report_agent/
│   │   ├── config.py                # pydantic-settings,全部读环境变量(修 C9 env 未接线缺陷)
│   │   ├── api/                     # 路由: reports, tasks, chat, health
│   │   ├── parsing/                 # pdf 解析(文字层)/ 多模态解析 / 手动录入 / 归一化 / 数据模型
│   │   ├── knowledge/               # 指标词典、参考区间查询、KG 客户端、Milvus 客户端
│   │   ├── retrieval/               # 三路检索 + RRF(自 C9 hybrid_retrieval 移植改造)
│   │   ├── pipeline/                # 任务表、runner、规则比对、组合模式、逐项解读、复查计划
│   │   ├── chat/                    # LangGraph Agent、受控工具、SSE 转换
│   │   ├── guardrails/              # 规则护栏、LLM 审核、审计
│   │   ├── llm/                     # DeepSeek 客户端(重试) + prompt 模板集中管理
│   │   ├── db/                      # SQLAlchemy async 模型与会话
│   │   └── observability/           # structlog JSON 日志、LangSmith 开关
│   ├── knowledge_seeds/             # 可审查 YAML 种子文件(每指标一个)
│   ├── scripts/                     # seed_draft / seed_import / gen_eval_reports / run_eval / smoke
│   ├── eval/                        # 20 份标注报告 + 30 条 QA 对 + baseline.json
│   ├── tests/                       # 规则层单测(100%)+ 各层测试
│   ├── docker-compose.yml           # postgres + neo4j + milvus(etcd+minio)
│   ├── pyproject.toml
│   └── .env.example
└── frontend/                        # 预留占位,本期不实现
```

### 3.3 技术落位

| 组件 | 实现 |
|---|---|
| DeepSeek 调用 | OpenAI SDK 指向 DeepSeek base_url(兼容接口),全项目共享单例 client |
| 多模态解析 | 同一 client,`deepseek-v4-flash-vision-exp`,页图 base64 + JSON 结构化输出 |
| Embedding | DashScope `text-embedding-v3`(1024 维) |
| 向量/全文检索 | Milvus 2.5.x:HNSW(dense)+ BM25 Function + jieba analyzer(sparse) |
| 会话持久化 | `langgraph-checkpoint-postgres`,thread_id = session_id |
| Postgres 访问 | SQLAlchemy 2.0 async + asyncpg |
| PDF 文字层 | pdfplumber(表格提取);页渲染 pymupdf |
| 日志 | structlog JSON 输出 |

## 4. 数据模型

### 4.1 Postgres(9 张表)

| 表 | 职责 | 关键字段 |
|---|---|---|
| `reports` | 报告元数据 | source(pdf/photo/manual)、file_path、institution、report_date、**sex、age**、user_id(可空预留) |
| `report_items_raw` | 解析原始输出(不可变事实) | report_id、section、item_name、value_text/value_num、unit、ref_range_text、报告自带异常标记 |
| `report_items_normalized` | 归一化+判定结果(可重跑派生层) | raw_item_id、indicator_code(空=unmapped)、标准单位换算后数值、status、判读用区间、is_abnormal |
| `interpretation_tasks` | 异步任务与断点 | status(pending/awaiting_meta/running/completed/failed/degraded)、**checkpoints JSONB**(各阶段产物)、error、timings JSONB |
| `interpretations` | 四段式解读产物 | task_id、summary、items JSONB(判定/解读/建议级别/证据ID)、advice_summary、disclaimer、degraded |
| `followup_plans` | 复查计划 | items JSONB(项目/时间窗/科室/依据引用) |
| `chat_sessions` | 追问会话 | report_id(会话锚定报告)、user_id 可空 |
| `chat_messages` | 对话记录(审计与查询用,独立于 LangGraph checkpoint;**异步写,失败告警不阻塞**) | role、content、tool_calls、guardrail_flags、evidence_ids |
| `audit_events` | 审计事件 | event_type(guardrail_suspect/guardrail_block/review_failed/degraded/refusal/retrieval_fallback/parse_failed 等)、payload JSONB、关联 report/task/session |

设计要点:raw 与 normalized 分表——解析是事实、归一化是派生,词典升级后可只重跑归一化及以下阶段;`interpretation_tasks.checkpoints` 即断点恢复载体,不引入队列中间件。

`report_items_normalized.status` 枚举:`normal / high / low / critical_high / critical_low / unknown / unmapped`。

### 4.2 Neo4j 知识图谱(事实源)

节点:

```
(:Indicator   {code, name, aliases[], unit, category, description})
(:RangeSpec   {sex: male|female|any, age_min, age_max, low, high, unit, critical_low, critical_high, source_note})
(:Condition   {name, description})              # 疾病/健康风险方向提示(非诊断)
(:IndicatorCluster {name, description})         # 指标联合解读簇(如"血脂四项")
(:Pattern     {name, description, criteria_json})   # 组合模式,criteria_json 结构化可执行
(:Intervention {level: lifestyle|recheck|specialist|urgent, text, timeframe})
(:Department  {name})
```

关系(方向语义单独建边):

```
(:Indicator)-[:HAS_RANGE]->(:RangeSpec)
(:Indicator)-[:HIGH_SUGGESTS {strength, note}]->(:Condition)   # 升高提示
(:Indicator)-[:LOW_SUGGESTS  {strength, note}]->(:Condition)   # 降低提示
(:Indicator)-[:PART_OF]->(:IndicatorCluster)
(:Pattern)-[:REQUIRES {direction}]->(:Indicator)               # 组合模式成员(带方向)
(:Condition)-[:HAS_INTERVENTION]->(:Intervention)
(:Indicator)-[:DEFAULT_INTERVENTION]->(:Intervention)          # 指标级通用建议
(:Intervention)-[:REFER_TO]->(:Department)
```

关键点:`Pattern.criteria_json` 存结构化条件(如 `[{"indicator_code":"TG","direction":"high"},...]`),组合模式判定由代码执行,不依赖 LLM 理解规则文本。

### 4.3 Milvus(派生检索层)

单 collection,dense + sparse 双索引:

| 字段 | 类型 | 说明 |
|---|---|---|
| chunk_id | VARCHAR PK | |
| text | VARCHAR | 原文(analyzer: jieba) |
| sparse_vec | SPARSE_FLOAT_VECTOR | 由 text 上的 **BM25 Function** 自动生成,SPARSE_INVERTED_INDEX |
| dense_vec | FLOAT_VECTOR(1024) | DashScope text-embedding-v3,HNSW M=16 efC=200 COSINE |
| entity_type | VARCHAR | indicator/condition/cluster/pattern |
| entity_id | VARCHAR | 指向 KG 节点(code/name),**增量更新删除键** |
| title / chunk_index / total_chunks / parent_id | | 父文档回填用(自 C9 继承) |

- 文档来源:KG 节点结构化释义文档(机器拼装)+ 种子 YAML 的 narrative 自由文本 → 按语义边界切块(C9 三级分块策略:语义边界优先、长度滑窗兜底)
- **增量更新 = 按 entity_id delete + insert**(dense 由 DashScope 生成、sparse 由 text 自动生成)
- 检索时对同一 collection 发两次独立 search(dense / fulltext),各自带 `search_method` 标记进入应用层 RRF

## 5. 报告解读管线

阶段序列:`parse → (awaiting_meta) → normalize → compare → retrieve → generate → guardrail → plan → done`

### 5.1 报告解析

三种输入共用同一输出模型(`RawReportItem[] + ReportMeta`):

```
电子 PDF ──pdfplumber 表格提取──→ 规则解析成功(有效项≥阈值)
              │ 提取不足            ↓
              └──────────→ 页渲染为图(pymupdf)──┐
拍照/扫描件 ─────────────────────────────────────┤
                                                ↓
                              多模态解析(deepseek-v4-flash-vision-exp)
                              逐页 → JSON 结构化输出 → 合并去重
手动录入 ──POST JSON,跳过解析──────────────→ 同一数据模型
```

- 多模态解析:指数退避重试 3 次(1s/2s/4s)→ schema 校验失败带错误反馈再试 1 次 → 仍失败则任务置 `failed` 并附引导信息("请改用拍照上传或手动录入"),**不产半成品**
- meta(性别/年龄)缺失 → 任务暂停 `awaiting_meta`,`PATCH /api/reports/{id}/meta` 补录后从 normalize 阶段继续

### 5.2 归一化(代码为主,LLM 增强一处)

1. **项目名对齐**:指标词典(启动时从 KG Indicator 加载)精确匹配 + 规范化匹配(全半角/空白/括号变体)→ 未命中项**批量一次 LLM 调用**尝试映射(输入候选列表,JSON 返回)→ 仍不中 → `unmapped` 保留继续
2. **单位换算**:词典内置指标单位换算表;无法换算则保留原值、判定改用报告自带区间
3. **区间来源**:报告自带区间优先;缺失时从 KG 按(指标×性别×年龄段)补全;危急值阈值永远来自 KG;报告区间与 KG 区间差异显著时记 audit 事件
4. **RangeSpec 选择**:sex 精确匹配优先于 any;age 落入 [min, max) 区间;多条命中取最窄;无命中 → status=unknown

### 5.3 规则比对 + 组合模式(纯代码,单测 100% 覆盖层)

- 逐项:value_num vs [low, high] → normal/high/low;vs critical_low/high → critical(触发强提醒);定性结果(阴性/阳性)走词典正常值映射
- 组合模式:遍历 Pattern.criteria_json,全部条件满足则命中;命中的模式作为虚拟异常项进入后续检索与生成流程

### 5.4 逐异常项并发检索 + 解读生成

- **KG 路 = 直接 Cypher 子图查询**(indicator_code 已知,无需 LLM 查询理解):该指标的疾病提示(带方向)、建议、科室、所属簇/模式
- Milvus dense + BM25(sparse)两路 → 与 KG 路三路 **RRF 融合**(自 C9 移植,含 rrf_score/rrf_sources 等 metadata 溯源)
- `asyncio.Semaphore`(默认 5)并发全部异常项;**单项检索全失败 → 该项用占位证据(建议线下咨询),不阻塞其他项**
- 逐项解读(LLM,并发):输入 = 指标数据 + KG 结构化事实 + 证据文本;输出 JSON 含 meaning / risks / advice_level / advice / evidence_ids(强制引用输入证据)
- 总体结论(LLM):汇总全部判定与逐项解读 → 总评段;LLM 失败重试耗尽 → **规则模板总评**("共 X 项异常,其中危急 Y 项…")
- 四段式组装:总评 + 逐项解读 + **分级建议汇总(代码排序:urgent > specialist > recheck > lifestyle,危急置顶)** + 免责声明(固定模板)

### 5.5 护栏流程

```
每个 LLM 输出 → 规则护栏(纯代码)
   ├─ pass   → 放行
   ├─ suspect(词表弱命中等风险信号)→ 二次 LLM 审核(独立审核 prompt)
   │      ├─ 通过 → 放行
   │      └─ 不通过 ──┐
   └─ block(明确违规)┤
                      ↓
        带问题反馈重生成 1 次 → 再过规则+审核
              ├─ 通过 → 放行
              └─ 仍不通过 → 安全降级版(仅数值对照表 + 强建议线下就医),标记 degraded
```

规则护栏四项(全部可单测):

1. **诊断用语拦截**(词表:确诊/诊断为/你患有…)
2. **处方/剂量检测**(药品词表 + 剂量正则)
3. **必含元素校验**(免责声明、危急值强提醒、四段式完整)
4. **数值一致性校验**:输出中数值必须 ∈ 解析结果白名单(含单位换算等价值)

### 5.6 复查计划(plan 阶段)

- **规则收集**(代码):异常项 → KG 的 Intervention(level=recheck)取时间窗/科室;危急项 → "立即就医"条目;模式命中 → 簇级复查项目(如血脂全套)
- **模板渲染**:复查单 = 项目 / 时间窗 / 挂号科室 / 依据(指标+判定+证据引用)四槽位
- **LLM 仅润色"依据"槽位措辞**,输出过槽位 schema 校验,非法槽位回退模板原文;LLM 失败 → 纯模板,不阻塞

### 5.7 任务执行与断点恢复

- `POST /api/reports` 创建 report + task(pending)→ 返回 `{report_id, task_id}` → `asyncio.create_task(runner)`
- runner:`UPDATE ... WHERE status='pending'`(SKIP LOCKED 语义)认领 → running → 逐阶段执行,每阶段完成即写 checkpoints + timings
- 服务重启:startup 扫描 running 任务 → 从最后完成 checkpoint 的下一阶段续跑
- uvicorn workers=1(POC);SKIP LOCKED 保证将来多 worker 安全
- `GET /api/tasks/{id}` 轮询返回 status / 当前阶段 / 各阶段耗时 / 错误

## 6. 追问对话 Agent(LangGraph 有界)

### 6.1 四个受控工具(全部只读)

| 工具 | 实现 | 说明 |
|---|---|---|
| `get_my_report(section?)` | Postgres 查询 | 本人报告归一化项 + 解读结论摘要;数值类问题必须先查 |
| `query_indicator_knowledge(indicator)` | KG Cypher(词典匹配 code) | 指标/疾病提示/分级建议/科室;匹配不上返回候选列表 |
| `compute_reference_range(indicator, value?)` | KG RangeSpec + 代码判定 | 性别/年龄自动取自会话绑定的报告 |
| `search_knowledge(query)` | 三路检索 + RRF | query 先经词典代码匹配(确定性),命中实体走 KG 子图查询,与 Milvus dense+BM25 融合;未命中则仅 Milvus 两路 |

### 6.2 边界与拒答

- **轮数上限**:工具调用轮计数(默认 8)+ LangGraph recursion_limit 双保险;超限强制切"仅生成"节点——注入收敛指令(基于已获取信息作答,禁止再调用工具)
- **拒答机制**:system prompt 规定——search_knowledge 证据为空或不足时必须明确拒答并建议咨询医生,禁止编造;"不足"的判定 = 检索结果为空,或 RRF 融合后 top 证据得分低于可配置阈值(env:REFUSAL_RRF_THRESHOLD);输出护栏校验兜底
- 每轮 assistant 输出过与管线共用的护栏模块(5.5 同一套流程)

### 6.3 持久化与流式

- `langgraph-checkpoint-postgres`,thread_id = session_id,服务重启可续
- `chat_messages` 表异步双写(审计/历史查询;写失败告警不阻塞用户响应)
- SSE 事件类型:`token`(增量文本)/ `tool_call`(工具开始/结束)/ `evidence`(证据引用)/ `done`(message_id、护栏状态、degraded 标记)/ `error`(含可否重试);LangGraph `astream_events` 转换

## 7. API 设计

```
# 报告与解读
POST   /api/reports                       # multipart(pdf/jpg/png)或 JSON 手动录入 → {report_id, task_id}
GET    /api/reports/{id}                  # 元数据 + 原始项 + 归一化项
PATCH  /api/reports/{id}/meta             # 补录性别/年龄 → awaiting_meta 任务恢复
GET    /api/tasks/{task_id}               # 状态/当前阶段/各阶段耗时/错误
GET    /api/reports/{id}/interpretation   # 四段式解读(含 degraded 标记、证据引用)
GET    /api/reports/{id}/followup-plan    # 结构化复查单

# 追问对话
POST   /api/reports/{id}/chat/sessions    # 创建会话(锚定报告)→ {session_id}
POST   /api/chat/sessions/{sid}/messages  # 追问,SSE 流式
GET    /api/chat/sessions/{sid}/history   # 历史(读 chat_messages)

# 运维
GET    /api/health                        # PG/Milvus/Neo4j 连通性
```

知识库增量更新走 CLI(`scripts/seed_import.py --entity <code>`),不加管理 API。

## 8. 知识库建设

### 8.1 种子数据流

```
指标清单(50~100 高频项:血常规/肝功/肾功/血脂/糖代谢/甲状腺/尿常规等)
   ↓ scripts/seed_draft.py
LLM 逐指标起草 YAML 草稿(断点续传+分批落盘;prompt 附已校对样例保证风格)
   ↓ 人工校对(直接改 YAML,git diff 可审查;重点:区间数值、疾病提示方向、危急值阈值)
   ↓ scripts/seed_import.py
pydantic 校验 → Neo4j MERGE(实体级) → 派生释义文档+切块 → DashScope embedding
→ Milvus 按 entity_id delete+insert(增量)
```

### 8.2 种子 YAML(每指标一个文件,`knowledge_seeds/indicators/GLU.yaml` 示意)

```yaml
code: GLU
name: 空腹血糖
aliases: [血糖, FBG, 空腹葡萄糖]
unit: mmol/L
category: 糖代谢
ranges:
  - {sex: any, age_min: 18, age_max: 100, low: 3.9, high: 6.1, critical_low: 2.8, critical_high: 22.0}
high_suggests: [{condition: 糖尿病风险, strength: strong, note: ...}]
low_suggests:  [{condition: 低血糖, strength: strong, note: ...}]
clusters: [糖代谢]
interventions:
  - {level: lifestyle, text: 控制精制碳水摄入..., timeframe: null}
  - {level: recheck, text: 复查空腹血糖+糖化血红蛋白, timeframe: 2-4周}
departments: [内分泌科]
narrative: |        # 自由文本释义 → 向量库
  空腹血糖反映...
```

Pattern(组合模式)与 Condition 的种子同样以 YAML 管理,入库为对应节点与关系。

## 9. 评测与回归

### 9.1 评测集(git 管理)

- `eval/reports/*.json`:20 份标注报告(LLM 合成多样化样本:性别/年龄/异常组合/危急值/未知项,人工校对);ground truth = 归一化 code + 判定 status
- `eval/qa_pairs.jsonl`:30 条追问 QA,四类:报告数值类(必须答对数值)、知识类(须引用证据)、**知识库外(必须拒答)**、诊断请求边界类(必须拒绝)

### 9.2 `scripts/run_eval.py`

| 维度 | 方法 |
|---|---|
| 解析准确率 | 跑真解析,归一化名称对齐 F1(对比基线阈值) |
| 异常判定准确率 | **直接喂归一化 ground truth 给规则层(绕过 LLM 波动),要求 100%** |
| 证据引用覆盖率 | 跑完整管线统计 |
| 数值一致性通过率 | 跑完整管线 + 护栏统计 |
| 安全指标 | 违规为 0、拒答题答对 |
| 回归门禁 | 与 `eval/baseline.json` 比较,任一指标回退 → 非零退出 |

评测脚本进 CI/回归流程,管线改动后必跑。

## 10. 可观测与审计

- structlog JSON 日志:上下文绑定 request_id / task_id / session_id;阶段耗时、LLM 调用(model/purpose/tokens/duration/status)全入日志
- LangSmith:环境变量开关(LANGSMITH_TRACING),LangGraph 原生回调接入
- `audit_events` 覆盖:护栏触发(suspect/block)、审核不通过、各类降级(检索回退/模板总评/degraded 产出)、拒答、解析失败
- `GET /api/health`:PG/Milvus/Neo4j 连通性(降级演示与运维用)
- 私有化预留:LLM/Embedding 客户端走 OpenAI 兼容工厂,base_url/model 全 env 化,未来切 vLLM/Ollama 只改配置

## 11. 错误处理与降级总览

| 故障点 | 降级路径 |
|---|---|
| 解析失败(重试后) | 任务 failed + 引导改用拍照上传/手动录入,不产半成品 |
| meta 缺失 | awaiting_meta 暂停,补录后继续 |
| 词典未命中 | unmapped/unknown 标记,继续流程 |
| 单项检索失败 | 该项占位证据(建议线下咨询),不阻塞其他项 |
| Milvus 不可用 | KG 路 + RRF 降级(dense 与 BM25 共享 Milvus 实例,一并失效) |
| Neo4j 不可用 | Milvus dense+BM25 两路继续 |
| 全部检索不可用 | 占位证据,解读仍产出 |
| LLM 调用超时/失败 | 指数退避重试 3 次 → 阶段级降级(模板总评/纯模板复查单/拒答话术) |
| 规则护栏 block / 审核不通过 | 重生成 1 次 → 仍不通过 → 安全降级版(数值对照表 + 线下就医),标记 degraded |
| chat_messages 写失败 | 告警不阻塞用户响应 |

## 12. 测试策略

- **单测(100% 目标)**:规则比对、单位换算、危急值、组合模式、规则护栏四项、词典匹配、模板渲染
- 阶段测试:管线各阶段独立测,LLM mock
- 集成 smoke:`scripts/smoke.py` 端到端(上传→轮询→解读→追问→拒答),跑在 docker-compose 真依赖上;含降级场景演示(如停 Neo4j 后仍产出解读)
- 评测回归:第 9 节

## 13. 对 C9 的继承与规避

**直接继承**:RRF 融合实现(`hybrid_retrieval.py:629-714`,含同源去重/canonical 选择/metadata 溯源)、三路召回 candidate_k 策略、模块分解+构造注入+LLM client 单例、流式生成三层降级链、Document metadata 溯源透传、离线批处理断点续传+分批落盘、三级分块策略、"LLM→JSON→规则兜底"模板。

**规避(已知缺陷)**:ENTITY_RELATION/PATH_FINDING 空桩、constraints 解析后丢弃、env→config 未接线、增量接口未接线、检索非确定性(LLM 关键词抽取温度抖动——本设计检索入口全部确定性词典匹配,消除此问题)、prompt 复制粘贴两份(集中到 llm/prompts/)。

**明确不做**:LLM 意图路由器(需求决策 5,三入口在 API 层显式区分);本设计检索层无需 LLM 查询理解(管线侧 code 已知,Agent 侧词典匹配)。

## 14. 技术选型落位与依赖

| 层 | 选型 | 备注 |
|---|---|---|
| 后端框架 | FastAPI + Pydantic v2 | |
| 编排 | LangGraph + langgraph-checkpoint-postgres | 仅追问 Agent |
| LLM | DeepSeek API(OpenAI SDK 兼容) | deepseek 主模型 + deepseek-v4-flash-vision-exp 多模态 |
| Embedding | DashScope text-embedding-v3(1024 维) | |
| 图数据库 | Neo4j 5.x community(neo4j Python driver 5.x) | docker-compose |
| 向量+全文 | Milvus 2.5.x standalone + pymilvus 2.5.x | etcd + minio,HNSW + BM25/jieba |
| 结构化存储 | PostgreSQL 16 + SQLAlchemy 2.0 async + asyncpg | docker-compose |
| PDF | pdfplumber(文字层表格)+ pymupdf(页渲染) | |
| 中文分词 | Milvus 内建 jieba analyzer(BM25 路) | |
| 日志 | structlog | JSON 输出 |
| 依赖管理 | uv + pyproject.toml,Python 3.13 | |

## 15. 实现期验证项(设计风险)

1. **Milvus jieba analyzer + BM25 Function 组合验证**:在所 pin 的 Milvus 2.5.x 版本上验证 analyzer 配置与稀疏索引创建、中文查询分词效果。失败兜底:Postgres FTS(zhparser + GIN,chunk 文本表放 PG)
2. **deepseek-v4-flash-vision-exp 结构化输出能力**:验证 JSON 输出稳定性与页图分辨率要求;必要时用 prompt 约束 + 宽松解析重试
3. **LangSmith 开关与 DeepSeek OpenAI 兼容接口的 tracing 兼容性**:可选功能,失败仅降级为纯日志
4. **pdfplumber 对常见体检报告版式的表格提取率**:决定"提取不足"阈值的经验值

## 16. 建设顺序建议(供实现规划参考)

1. **基础设施**:docker-compose、config(pydantic-settings 全 env)、DB 模型与迁移、LLM/Milvus/Neo4j 客户端、structlog
2. **知识与规则底座**:指标词典、参考区间查询、规则比对、组合模式、单位换算(全纯代码 + 单测 100%)
3. **知识库建设链**:seed_draft → 人工校对 → seed_import(Neo4j + Milvus 含增量)
4. **检索层**:三路检索 + RRF(移植 C9)与降级
5. **解读管线**:runner + 任务表 + 各阶段 + 护栏 + 复查计划
6. **追问 Agent**:LangGraph + 工具 + SSE
7. **评测与回归**:评测集生成、run_eval、smoke
8. **收尾**:.env.example、README、验收演示脚本

## 17. 验收标准映射(需求 §7 → 设计落点)

| 验收项 | 设计落点 |
|---|---|
| 1. PDF 与拍照端到端 | §5.1 解析双路径 + §5 管线 + §7 API |
| 2. 规则层单测 100%、安全零违规 | §5.3/§12 单测 + §9.2 评测门禁 |
| 3. 降级路径可真实触发演示 | §11 总览 + §12 smoke 停服务场景 + audit_events 记录 |
| 4. 知识库外正确拒答 | §6.2 拒答机制 + §9.1 拒答类 QA |
| 5. 增量更新可演示 | §8.1 seed_import --entity(单实体 delete+insert / MERGE) |
