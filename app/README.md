# 小暖 · M0 代码骨架

对应《技术实现方案》M0/M1：全链路编排 + 双字段实时策略 + 风险双通道 + 记忆 + L2 评测门禁。
默认离线可跑（MockLLM/MockGateway）；接真实 agent 底座与语音引擎时替换两个适配位。

## 运行（任意 Python 3.10+，无第三方依赖）
```
cd app
python -m xiaonuan.demo        # 两通模拟通话：川话陪聊+跌倒橙级、次周记忆回访
python eval/run_eval.py        # 20 张场景卡回归 + 发布门禁（exit code 即门禁结果）
```

## 接真实底座
- LLM：设环境变量 XIAONUAN_LLM_BASE_URL / XIAONUAN_LLM_API_KEY / XIAONUAN_LLM_MODEL
  （OpenAI 兼容接口，输出契约 {"reply","state"}，见 xiaonuan/prompts.py）。
- 语音/电话：实现 xiaonuan/gateway.py 的 VoiceGateway（路线A 声网引擎 webhook /
  路线B FreeSWITCH+Fun-ASR+CosyVoice2）。硬性验收：通话中可调 TTS 语速/音量/VAD。

## 目录
- xiaonuan/：schemas 数据契约 / memory 记忆 / strategy 策略包+实时控制器 /
  fragments 话术片段库 / prompts 组装器 / llm 接入层 / risk 双通道 / orchestrator 编排 / scheduler 计划与升级链
- config/：strategy_packs.json（6类分型）fragments.json（方言x适老变体）risk_rules.json（含方言风险词）
- eval/：cards.json 场景卡 + run_eval.py 门禁

## 已知边界（M0）
- MockLLM 为规则版，验证链路与门禁；真实话术质量需接底座后跑 L2/L3。
- 风险抽取的 LLM 通道目前取 state.risk_hint；产品化加呼后 qwen-max 全文结构化抽取。
- 台账/工单/通知为结构化输出，未接真实通道。
---

## M2 试点工作台（可给人使用）

```
cd app
python server.py            # 默认 http://127.0.0.1:8765；端口被占用时设 XIAONUAN_PORT=8090
```
浏览器打开后：
- 社工/运营登录即用；管理员口令默认 admin（可用 XIAONUAN_ADMIN_CODE 修改）
- 老人档案：新建需勾选知情同意；自动生成家属访问码；退订即删（管理员）
- 关怀通话：选老人发起 → 文字或🎙语音模式（浏览器 Web Speech，普通话）→ 实时策略/风险升级 → 结束写台账
- 工单与风险：橙级24h闭环、红色100%复核；探访台账 CSV 导出；数据看板；家属视图（仅摘要+告警）
- 系统设置：填 OpenAI 兼容 Base URL/Key/Model（如百炼 compatible-mode）即切真实大模型；一键跑 L2 评测门禁；180天转写清理

数据落 SQLite（app/xiaonuan.db）。当前为浏览器模拟通话；真实电话外呼需运营商线路，
接入位为 xiaonuan/gateway.py 的 VoiceGateway（路线A 声网引擎 / 路线B FreeSWITCH+Fun-ASR+CosyVoice2）。