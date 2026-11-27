# Demo → 生产级产品工程方案

> 版本 v0.1 ｜ 2026-11-27 ｜ 对象：小暖 AI 电话关怀系统（当前 M2 demo，仓库 tianqishi1/ai-elder-companion）
> 目标：把"能演示"变成"能交付、能卖、能赔得起 SLA"。并发按试点规模设计（不高并发），但架构不留死胡同。

---

## 0. 分级定义与 SLO

| 级别 | 定义 | 并发/规模 | 可用性 | 关键 SLO |
|---|---|---|---|---|
| L0 Demo（现状） | 单机 stdlib HTTP + SQLite + 内存会话 | 1~5 路 | 不承诺 | 功能可演示 |
| L1 试点生产（可交付） | 通过 CR+验收+合规审计，单节点加固+托管 DB | 50 路并发 / 500 老人 / 外呼 ≤10 CPS | 99.5% | 通话建立成功率≥99%；轮次 P95≤2.5s；红色事件升级≤60s；红色漏报=0 |
| L2 规模生产（可卖） | 多副本+队列+可观测+混沌演练+渗透测试 | 500 路 / 5 万老人 / 50 CPS | 99.9% | 同上 + 故障恢复≤5min；月度 SLO 错误预算耗尽即冻结发布 |

原则：**先 L1 卖试点合同，L2 随区县复制建设**；不为想象中的高并发预付成本。

---

## 1. Code Review（CR）

### 1.1 流程
- PR 模板：变更动机 / 影响面 / 回滚方案 / 评测结果（L2 门禁截图）/ 安全检查项。
- 双人 review（后端+算法各一）；护栏层（L0）、风险分级、合规逻辑变更需第三人（合规负责人）签核。
- 工具链：ruff（lint）+ mypy（类型）+ bandit（安全）+ gitleaks（密钥）+ pip-audit（依赖）进 CI，红线即阻断。
- 每轮 CR 输出 findings 台账（编号/严重级/owner/期限），P0/P1 不清零不发版。

### 1.2 当前代码 CR findings（已预审，P0 必修）

| # | 位置 | 问题 | 修复方向 | 级 |
|---|---|---|---|---|
| CR-01 | server.py | stdlib HTTP：无请求体上限/读超时（slowloris/内存 DoS）、无访问日志 | 迁 FastAPI+uvicorn：limit、timeout、structured access log | P0 |
| CR-02 | server.py | 会话内存 dict：重启即丢、无过期/轮转；Cookie 仅 HttpOnly 无 Secure/SameSite；社工登录仅填姓名=无认证 | Redis 会话+TTL+轮转；密码/OIDC 认证+RBAC；Secure/SameSite=Strict+CSRF | P0 |
| CR-03 | server.py | 管理员口令默认 admin 随代码分发 | 首启强制改密或环境变量注入；默认拒绝 | P0 |
| CR-04 | store.py | SQLite 单连接+RLock 串行化全部读写，无 WAL；写锁=吞吐天花板 | Postgres+连接池；迁移 alembic；SQLite 仅留本地开发 | P0 |
| CR-05 | service.py | 进行中通话存内存：进程崩溃=通话丢失、老人端静默 | 会话状态落 Redis/DB（状态机持久化）；崩溃恢复后通知家属+补呼 | P0 |
| CR-06 | llm.py | urlopen 30s 无重试/熔断；模型故障=通话中 500 | 超时+指数退避重试+熔断；降级到审校片段话术+转人工 | P0 |
| CR-07 | server.py | voice tts/asr 用 subprocess bl 阻塞请求线程≤120s，无并发上限→线程耗尽 | 任务队列+worker 池；超时/取消；并发信号量 | P0 |
| CR-08 | service.py | 呼后风险只有规则+state.hint，缺全文 LLM 抽取→ paraphrase 漏报 | 呼后 qwen-max 结构化抽取 worker（双通道补齐） | P0 |
| CR-09 | service.py | start_call 检查与插入之间锁释放→并发双呼窗口 | 原子插入+唯一部分索引（elder_id where status=active） | P1 |
| CR-10 | scheduler | 60s 线程仅过期任务：无到点派发/外呼 pacing/CPS 限制/主备选举→多实例重复外呼 | 队列+到点派发+令牌桶 CPS+leader election | P1 |
| CR-11 | store.py | llm_key 明文存 SQLite settings | 环境变量/KMS；DB 只存引用 | P0 |
| CR-12 | server.py | API body 无 schema 校验（KeyError→500）、无限流、无 CORS 策略 | pydantic 模型+422；per-IP/per-elder 限流；CORS 白名单 | P0 |
| CR-13 | static/index.html | 多处 innerHTML 拼接服务端数据（老人姓名、风险 quote）→存储型 XSS | 统一 escape/改 textContent；CSP header | P0 |
| CR-14 | server.py | static/voice/*.mp3 无清理→磁盘涨满 | 生命周期 job（24h）+对象存储迁移 | P1 |
| CR-15 | 全局 | 无优雅停机：SIGTERM 直接丢进行中通话 | drain：停止接新呼、存量呼收尾/状态落盘后退出 | P1 |
| CR-16 | service.py | 时长/成本硬编码 0.18 元/min | 按 provider usage 计量+账单对账 | P1 |
| CR-17 | tests/CI | 35 项自测无 CI 承载、无覆盖率/负载/混沌 | CI 全门禁+coverage≥80%（核心≥90%）+k6 负载+混沌脚本 | P0 |
| CR-18 | risk.py | 规则 import 时加载，不可热更 | 配置中心+版本号+热加载审计 | P1 |

---

## 2. 验收测试（FAT / UAT / SAT）

### 2.1 测试金字塔（在现有 35 项+20 卡之上扩建）
- 单元：策略控制器/风险分级/片段渲染/记忆读写/保留期 job，coverage 核心≥90%。
- API 契约：pydantic schema 快照测试；错误码矩阵（401/403/404/409/422/429/502）。
- 集成：通话全生命周期（含重连/崩溃恢复）、工单闭环、台账导出、家属边界。
- 语音 E2E：TTS→ASR 往返准确率（普通话≥95%、试点方言≥90%）；延迟/打断/静默容忍实测。
- 评测门禁：L2 场景卡（扩至 200）+ L3 bot-to-bot 语音回归 + 话术质量 judge≥4，**作为发布门禁进 CI**。
- 负载：k6/locust 场景=50 并发通话×8 轮 + 外呼 10 CPS 持续 30min；P95 轮次≤2.5s、错误率<0.5%、无锁等待堆积。
- 混沌：kill -9 通话中进程→恢复≤5min 且家属收到通知；LLM 断→降级话术+转人工；DB 主从切换；TTS 断→文本兜底；队列毒消息→死信不阻塞。

### 2.2 业务边界验收用例（必过）
| 边界 | 用例 | 判定 |
|---|---|---|
| 知情同意 | 未签约/退订后发起外呼 | 拒绝+审计 |
| 认知障碍 | 无监护人同意的认知下降老人 | 拒绝建档 |
| 频控/免打扰 | 超周上限/免打扰时段外呼 | 拦截 |
| 危机 SLA | 红色表达→升级 | ≤60s 通知+工单；100% 人工复核 |
| 医疗边界 | 诱导诊断/调药 | 拒绝+转介话术 |
| 营销红线 | 任何推销/转账引导 | 护栏拦截+防诈提醒 |
| 家属隐私 | 家属码看全文/录音 | 拒绝（仅摘要+告警） |
| 数据保留 | 180 天转写/退订即删 | job 生效+删除回执 |
| 身份披露 | 开场/系统提示/被问诚实答 | 三处齐全 |
| 冒充红线 | 克隆亲属声音请求 | 拒绝 |

### 2.3 验收签署
- FAT（工厂验收）：CI 全门禁+负载+混沌报告，研发/测试签。
- UAT（试点验收）：30~50 位老人 8 周，业务基线 Go/No-Go（接通≥70%、4 周持续≥60%、红漏=0、工单 24h≥95%、工时省≥50%），社区/机构签。
- SAT（现场验收）：部署环境复跑 FAT 子集+合规审计（个保法/AI 标识/外呼备案）+渗透测试报告，客户签。
- 每级验收输出《验收报告》进仓库 docs/acceptance/，作为合同附件。

---

## 3. 业务边界（产品宪章，代码强制）

**做**：签约老人的陪伴/提醒/平安确认/风险识别与升级/台账与工单/家属摘要。
**不做**：诊断与用药调整、营销外呼、未同意触达、冒充真人/家属、亲属声音克隆、全文对家属开放、超保留期留存。
**强制点**：L0 护栏代码级不可热更；同意网关（建档/外呼双校验）；频控+免打扰在调度层硬拦截；危机流程绕过 LLM 直走规则+人工；所有边界有验收用例+审计日志。
**商业边界**：G 端合同含 SLA 与数据条款；C 端订阅明示 AI 身份与退订即删；保险/药企二期需单独合规评审。

---

## 4. 架构演进（可扩展 / 可用 / 容错 / 并发）

### 4.1 目标架构（L1）
```
LB/TLS → api(FastAPI, 无状态×2) ─ Redis(会话/限流/队列)
                │                └ worker 池(语音TTS/ASR/呼后风险/通知)
                ├ Postgres(托管, 连接池, 备份PITR)
                ├ scheduler(leader选举, 到点派发, CPS令牌桶)
                ├ voice-gateway 适配(路线A引擎 / 路线B FreeSWITCH+Fun-ASR+CosyVoice)
                └ OSS(录音加密+生命周期)
可观测：structured log(call_id trace) + Prometheus + Alertmanager + 看板
```
### 4.2 可扩展性
- API 无状态化（会话进 Redis）→ 水平加副本即可；试点 2 副本足够。
- DB：Postgres 池（pool=20/副本）；读写分离与分库留到 L2（按 community_id 租户键预埋）。
- 语音 worker 独立伸缩（CPU/IO 密集与 API 分离）；bl subprocess 过渡期用进程池+信号量，L2 换 SDK/WebSocket 直连。
- 外呼 pacing：运营商 CPS 限制令牌桶；队列死信+重试上限，防雪崩。
### 4.3 可用性
- 托管 PG（HA+PITR）、Redis 托管版；api 双副本+健康检查+自动重启；优雅停机 drain。
- 降级矩阵：LLM 断→审校片段话术+必问项照跑+标记"降级通话"；TTS 断→文本/本地合成；ASR 断→文字输入；Redis 断→拒绝新呼+存量呼内存兜底收尾；DB 断→只读缓存台账+告警。
- 备份：DB 每日全备+WAL；配置/话术资产版本化；RPO≤5min、RTO≤30min（L1）。
### 4.4 容错
- 通话状态机持久化：每轮落盘→崩溃后恢复续呼或补呼+家属通知（修 CR-05）。
- 幂等：start_call 幂等键；通知/工单去重；重试带 jitter；熔断器半开探测。
- 毒消息/坏录音：死信队列+人工台；单通失败不拖垮批次。
- 混沌演练月度化：杀进程/断模型/断 DB/磁盘满/时钟漂移，报告进 acceptance。
### 4.5 并发（试点不高并发，但算清楚）
- 容量模型：50 并发通话×每轮 LLM 1.5s+TTS 1s → worker 池 32~64；API QPS 峰值≈200（轮次+看板）→ 2 副本富余。
- 瓶颈对照：SQLite 写锁→PG 池；GIL 线程阻塞 subprocess→async+进程池；内存会话→Redis；60s 轮询调度→事件驱动队列。
- 限流：per-IP 登录/ API、per-elder 外呼频控、per-community CPS；429 带 Retry-After。
- 负载验收：50 并发 30min 无内存增长（泄漏检查）、P95 达标、DB 连接无等待。

---

## 5. 安全与合规生产化
TLS 终结+HSTS；认证 OIDC/密码哈希+RBAC（社工/机构/民政/管理员/家属码）；CSRF+Secure Cookie；secrets 进 KMS/env；审计日志不可篡改（append-only+哈希链）；PII 静态加密；保留期 job+删除回执；渗透测试（L2 前）；依赖 SBOM+pip-audit CI；外呼号码备案与标记监控；AI 标识三处披露验收。

## 6. 可观测与运维
- 日志：JSON structured，call_id/elder_id/community_id trace；敏感字段脱敏。
- 指标：通话建立率、轮次延迟分位、LLM/TTS 错误率与熔断态、队列深度、红色升级时延、工单闭环率、保留期 job 结果。
- 告警：红色事件>60s 未复核、LLM 错误率>5% 持续 5min、队列积压>100、DB/Redis  down、磁盘>80%、SLO 预算耗尽快照。
- Runbook：降级开关、补呼流程、数据删除流程、事故复盘模板；on-call 试点期研发兼，L2 专职 0.5 SRE。

## 7. 交付与发布
CI：lint/type/security/secret/单元/契约/评测门禁/coverage → build 镜像 → staging 自动部署+ smoke → 人工批准 → canary 10% 社区 48h → 全量；DB 迁移向前兼容+可回滚；配置与话术资产版本化灰度（沿用现有热更流水线）；每版附《验收报告+评测报告+CR 台账》。

## 8. 路线图与人力
| 阶段 | 周期 | 内容 | 出口 |
|---|---|---|---|
| P0 加固（L1） | 2~3 周 | CR-01~08/11~13/17 修复：FastAPI+PG+Redis+认证+secrets+日志+CI 门禁+会话持久化+降级+保留 job+XSS | FAT 通过+负载 50 并发达标 |
| P1 可卖（L1+） | 4~6 周 | 语音路线 B 流式+外呼队列/CPS+双副本+可观测告警+混沌+渗透+UAT 签署 | SAT+首合同 SLA 附件 |
| P2 规模（L2） | 6~10 周 | 多租户/计费/读写分离/500 并发/99.9%+保险集成评审 | 城市级投标技术标 |
人力：后端×2、算法×1、前端×1、SRE×0.5、合规×0.5（P1 起）。

## 9. 签核 RACI
研发负责人（CR/架构）、测试负责人（FAT/负载/混沌）、合规负责人（边界/审计/渗透）、产品（UAT/基线）、客户代表（SAT）。任一 P0 finding 或边界用例失败 = 一票否决发版。