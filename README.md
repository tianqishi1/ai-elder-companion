# 小暖 · AI 外呼陪伴老年人系统（M2 试点工作台）

[![ci](https://github.com/tianqishi1/ai-elder-companion/actions/workflows/ci.yml/badge.svg)](https://github.com/tianqishi1/ai-elder-companion/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

面向独居/空巢老人的 AI 电话陪伴与风险预警系统：**Agent 编排 + 风险双通道 + 记忆个性化 + 方言话术 + 评测门禁 + 语音模式**。
医学/健康相关内容仅为提醒与转介，不做诊断；所有外呼以老人本人同意为前提，录音需显式授权。

> **生态**：Agent 底座 [agent-base](https://github.com/tianqishi1/agent-base) · 评测流水线 [eval-pipeline](https://github.com/tianqishi1/eval-pipeline) · 同类应用 [pregnancy-butler](https://github.com/tianqishi1/pregnancy-butler)

## 快速开始

```bash
git clone https://github.com/tianqishi1/ai-elder-companion.git
cd ai-elder-companion/app
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m xiaonuan.demo        # 离线演示：川话陪聊+跌倒橙级、次周记忆回访（MockLLM，无需 key）
python eval/run_eval.py        # 20 张场景卡回归 + 发布门禁（exit code 即门禁结果）
python -m pytest tests/test_m2.py -q   # M2 边界自测（进程内服务层；live API 用例需先启动服务）
```

Web 工作台（ FastAPI ）：`python server.py`，详见 [app/README.md](app/README.md)。

## 架构

- `app/xiaonuan/`：schemas 数据契约 / memory 记忆 / strategy 策略包+实时控制器 / fragments 话术片段库（方言×适老变体）/ prompts 组装器 / llm 接入层 / risk 双通道 / orchestrator 编排 / scheduler 计划与升级链
- `app/xnplatform/`：会话/鉴权/限流/韧性/审计
- `app/config/`：6 类老人分型策略包、方言话术、风险规则（含方言风险词）
- `app/eval/`：场景卡 + 门禁

## 安全边界（硬约束）

- 风险双通道：实时规则词表（含方言）+ 呼后 LLM 结构化抽取；橙/红级走人工升级链，不依赖模型单点判断。
- 无同意不外呼、无授权不录音；记忆内容脱敏存储。
- 默认 MockLLM/MockGateway 离线可跑；接真实语音引擎需满足通话中可调 TTS 语速/音量/VAD。

## 许可

Apache-2.0。产品/技术方案文档见仓库根目录（中文）。
