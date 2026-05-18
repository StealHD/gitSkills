#!/usr/bin/env python3
"""Create reusable AI project-control files for a repository."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


CONTROL_FILES = [
    "AGENTS.md",
    "PLAN.md",
    "API_CONTRACT.md",
    "ARCHITECTURE_CONTRACT.md",
    "DECISION_LOG.md",
    "CONTEXT_READ_RULES.md",
    "WORKLOG.md",
]


def now_text() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def write_file(path: Path, content: str, force: bool) -> str:
    if path.exists() and not force:
        return "skipped"
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return "created" if not path.exists() else "written"


def agents_md(project_name: str, domain: str, stack: str, config_file: str) -> str:
    return f"""# {project_name} AI 控制总文件

## 1. 文档目的
本文件用于约束后续 AI 协作开发的方向、边界和交付顺序。

本项目不是通用脚手架，而是面向以下业务语境的系统：{domain}。所有后续文档、接口设计、规则定义和实现方案都必须以本文件为最高约束之一。

## 2. 当前项目目标
当前阶段目标如下：

1. 明确系统边界与职责分层
2. 建立统一运行时接入或适配边界
3. 明确标准数据模型或接口模型
4. 明确规则、状态、风险或输出口径
5. 输出可验证的结果或报告
6. 为后续扩展能力预留统一接口

## 3. 当前交付范围
当前阶段必须明确“做什么”和“不做什么”。

当前阶段默认先做：

1. 后端、CLI、服务或核心模块
2. 最小可验证输出
3. 面向后续消费方稳定的接口合同

当前阶段默认不做：

1. 与当前验证目标无关的正式前端
2. 复杂可视化或交互编排
3. 未经确认的生产自动化动作

## 4. 运行时来源与语义参考
运行时来源必须通过统一 adapter、gateway、repository、client 或 integration 层进入系统。

说明：
- 上层业务只依赖标准模型、capability 和稳定标识
- 不允许把上游原始路径、字段名、返回结构扩散到路由、规则或报告层
- 官方文档、协议文档或产品文档只作为语义参考源，不作为业务运行时数据源
- `archive/**` 只保存历史记录，不作为默认上下文读取来源

## 5. 硬约束
后续 AI 协作必须同时满足以下约束：

1. 技术栈优先遵循：{stack}
2. 不允许把采集/接入逻辑、业务分析逻辑、输出逻辑堆在入口层
3. 必须通过统一抽象层隔离外部系统差异
4. 规则、阈值、报告或输出格式必须可配置、可扩展
5. 任何结论输出都必须能追溯到规则和证据
6. 能力不足必须通过 capability / degrade / unsupported / unknown 表达，不能硬编码跳过

## 6. 系统核心控制面
当前优先维护以下控制文件：

1. `AGENTS.md`
2. `PLAN.md`
3. `API_CONTRACT.md`
4. `ARCHITECTURE_CONTRACT.md`
5. `DECISION_LOG.md`
6. `CONTEXT_READ_RULES.md`
7. `WORKLOG.md`

当前同步维护以下可编辑配置文件：

1. `{config_file}`

## 7. AI 协作顺序
后续 AI 协作默认按以下顺序推进：

1. 明确系统边界与责任分层
2. 明确运行时来源抽象
3. 明确标准模型
4. 明确规则与状态模型
5. 明确输出合同
6. 固化初始 YAML 配置
7. 之后才进入 API、服务或模块实现

## 8. AI 输出要求
所有 AI 产出必须满足以下要求：

1. 先讲项目业务语境，再讲技术细节
2. 任何接口或规则说明都必须说明适用范围
3. 涉及外部系统的说明必须区分“运行时接入”和“语义参考”
4. 若当前能力不足，必须通过 capability / degrade 机制表达
5. 当前输出优先服务可验证的后端、服务、CLI 或模块，不倒逼系统绑定某种前端实现

## 8.1 默认回复格式
除非用户明确要求展开分析，否则任何 agent 完成任务后默认使用精简输出。

默认回复格式仅包含：

1. `状态：成功 / 部分完成 / 阻塞`
2. `结果：一句话说明做成了什么`
3. `验证：测试是否通过，接口是否验证`
4. `阻塞：如果有，列 1~3 条；如果没有可省略`
5. `文件：只列修改过的关键文件路径，最多 8 个`

## 8.2 Agent 执行记录要求
任何 agent 在执行任务时都必须记录操作，但记录必须简洁，统一维护在 `WORKLOG.md`。

执行规则：

1. 每次任务开始前，先读取最小必要控制文件
2. 每次任务结束后，向 `WORKLOG.md` 追加一条任务摘要
3. 不允许为每次执行新建新的 Markdown 文档
4. 日志只记录高价值信息，不贴大段命令输出

## 8.3 控制文件维护规则
任何 agent 执行代码任务时，不应默认修改 Markdown 控制文件。

只有在“控制面发生变化”时，才允许维护必要的控制文件。控制面变化包括：

1. API 或外部接口合同变化
2. 运行时来源能力确认或被推翻
3. 规则口径变化
4. 输出结构变化
5. 上下文读取策略变化
6. 当前阶段实施目标变化

若只是普通代码实现、修 bug、补测试，但不改变控制面：

1. 不要改 Markdown 控制文件
2. 只更新 `WORKLOG.md`

## 8.4 控制文件唯一真源
如果控制面发生变化，必须按以下唯一真源更新：

1. 总体目标、硬约束变化：`AGENTS.md`
2. 当前阶段实施目标变化：`PLAN.md`
3. API 或接口变化：`API_CONTRACT.md`
4. 架构边界变化：`ARCHITECTURE_CONTRACT.md`
5. 决策原因变化：`DECISION_LOG.md`
6. 上下文读取策略变化：`CONTEXT_READ_RULES.md`
7. 默认配置变化：`{config_file}`

要求：

1. 不允许同一条规则分散复制到多份文档中反复维护
2. 主文档只保留当前有效结论
3. 变更原因统一沉淀到 `DECISION_LOG.md`

## 8.5 Agent 默认读取范围
任何 agent 默认只应先读取：

1. `PLAN.md`
2. `API_CONTRACT.md`
3. `{config_file}`
4. 当前任务直接相关的代码文件
5. 当前任务直接相关的测试文件

除非任务明确要求，否则不要读取：

1. 虚拟环境目录
2. 缓存目录
3. `.env` / `.env.*`
4. 本地 agent 设置文件
5. `archive/**`
6. 不相关的 Markdown 控制文件
7. 大多数空壳包声明文件
"""


def plan_md(project_name: str, config_file: str) -> str:
    return f"""# {project_name} 实施计划

## 1. 计划目的
本文件用于指导当前阶段和下一阶段代码实现。后续 agent 应以本文件作为开发入口之一，但不得用本文件覆盖更细的 API、架构、规则和输出合同。

## 2. 当前阶段状态
结论：待项目负责人确认。

已完成：

1. 控制面初始化

当前仍需推进：

1. 明确首个可验证业务闭环
2. 实现最小接口或模块
3. 增加面向当前闭环的测试

## 3. Agent 开工前默认读取
默认先读：

1. `PLAN.md`
2. `API_CONTRACT.md`
3. `{config_file}`
4. 当前任务直接相关代码
5. 当前任务直接相关测试

按需再读：

1. 架构任务：`ARCHITECTURE_CONTRACT.md`
2. 决策核对：`DECISION_LOG.md`
3. 上下文策略任务：`CONTEXT_READ_RULES.md`

默认不读：

1. `archive/**`
2. 虚拟环境目录
3. 缓存目录
4. `.env*`
5. 不相关 Markdown

## 4. 当前实施范围
本阶段继续做：

1. 核心接口、服务或模块
2. 运行时来源抽象
3. 标准模型
4. capability / degrade 表达
5. 最小可验证输出

本阶段不做：

1. 与当前闭环无关的大型重构
2. 未确认的生产自动化动作
3. 正式前端或复杂可视化，除非项目目标明确要求

## 5. API / 模块实现优先级
当前优先级：

1. 定义最小输入输出合同
2. 实现入口层薄编排
3. 实现服务层业务流程
4. 实现适配层或存储层
5. 增加回归测试

## 6. 当前实现强约束
1. 不得把外部系统原始字段扩散到业务层
2. 不得把规则写死在入口层
3. 不得让输出层直接访问运行时来源
4. 不得静默跳过能力缺口，必须显式表达 capability / degrade
5. 不得读取 `archive/**`，除非用户明确要求查历史

## 7. 建议测试顺序
1. 运行当前任务相关单测
2. 运行接口或模块 smoke 验证
3. 运行受影响范围的回归测试
4. 如涉及配置，验证 YAML 解析

## 8. 执行后可视化校验
完成计划中的阶段任务后，建议生成一次控制面校验报告：

```bash
python3 "${{CODEX_HOME:-$HOME/.codex}}/skills/init-pro/scripts/validate_project_controls.py" \\
  --project-root . \\
  --primary-config {config_file} \\
  --output INIT_PRO_VALIDATION.md
```

报告会输出：

1. 控制文件覆盖检查
2. 默认读取范围检查
3. API 错误 / 兼容 / 幂等 / 后台任务合同检查
4. `WORKLOG.md` 和主 YAML 记录检查
5. Mermaid 约束图与业务变更影响图
"""


def api_contract_md(project_name: str) -> str:
    return f"""# {project_name} API / 接口合同

## 1. 文档目的
本文件定义当前系统的公共接口合同。若项目不是 HTTP API，也应在此描述 CLI、事件、模块函数或集成边界。

## 2. 设计原则
当前接口必须遵守：

1. 入口层只做请求接收、参数校验和应用编排
2. 业务层不直接依赖外部系统原始字段
3. 返回结果必须显式表达 capability / degrade 状态
4. 输出结构必须对后续消费方稳定

## 3. 标识合同
定义外部请求主键、内部稳定标识和可选补充键。

最小要求：

1. 外部调用方可理解
2. 内部缓存、存储和日志可稳定追踪
3. 多来源或多租户场景可消歧

## 4. 第一版最小接口集合
待补充。

每个接口至少说明：

1. 用途
2. 适用范围
3. 请求参数
4. 返回字段
5. capability / degrade 表达
6. 错误语义

## 5. 错误响应合同
所有公共接口必须定义稳定错误语义。

错误响应至少说明：

1. 错误码或异常类型
2. 人类可读错误说明
3. 调用方可执行的下一步动作
4. 是否可重试
5. 相关请求标识或资源标识

要求：

1. 不允许只返回裸字符串错误
2. 参数校验错误必须区分字段缺失、格式非法和业务约束冲突
3. 外部系统失败必须表达为标准错误，不得泄露上游原始错误结构或敏感信息

## 6. 兼容性合同
当接口、命令、事件或模块函数发生变化时，必须说明兼容策略。

至少覆盖：

1. 新增字段是否向后兼容
2. 字段删除或语义变化的迁移方式
3. 旧版本调用方的保留周期
4. 默认值和缺省行为
5. capability / degrade 状态变化

要求：

1. 不允许无记录地改变已有字段语义
2. breaking change 必须进入 `DECISION_LOG.md`
3. 若当前阶段不做多版本 API，也必须说明“单版本但保持字段语义稳定”

## 7. 幂等性合同
会创建、导入、触发任务、写入状态或调用外部副作用的接口必须说明幂等策略。

至少说明：

1. 幂等键来源
2. 重复请求返回既有结果还是创建新任务
3. 超时后客户端如何安全重试
4. 服务端如何记录重复请求

若接口不是幂等的，必须明确写出原因和调用方限制。

## 8. 后台任务合同
异步、批量、定时、长耗时任务必须定义任务状态合同。

至少说明：

1. 任务 ID
2. 状态枚举
3. 进度字段
4. 成功 / 失败 / 部分成功结果结构
5. 超时策略
6. 重试策略
7. 并发或速率限制
8. 结果保留周期

要求：

1. 提交任务的接口不得长时间阻塞等待完整结果
2. 查询任务状态的接口必须可追踪失败原因
3. 批量任务必须说明部分失败的表达方式
"""


def architecture_contract_md(project_name: str) -> str:
    return f"""# {project_name} 架构合同

## 1. 文档目的
本文件定义系统职责分层和不可跨越的边界。

## 2. 默认分层
推荐分层：

1. API / CLI / event 入口层：接收请求和薄编排
2. Service 层：业务流程编排
3. Domain 层：标准模型、规则输入输出、核心状态
4. Adapter / Integration 层：隔离外部系统差异
5. Storage 层：持久化抽象和实现
6. Output / Reporting 层：输出渲染，不直接采集数据

## 3. 禁止事项
1. 禁止入口层直接访问外部系统细节
2. 禁止输出层反向驱动领域模型
3. 禁止规则散落在路由、命令入口或模板中
4. 禁止把某个运行时来源的字段命名作为全系统标准命名

## 4. 扩展原则
新增来源、规则、输出或存储时，应先扩展抽象合同，再实现具体适配。
"""


def decision_log_md(project_name: str) -> str:
    date = dt.date.today().isoformat()
    return f"""# {project_name} 决策记录

## 1. 文档目的
本文件记录重要架构决策、范围裁剪和兼容性约束，用于避免后续 AI 协作过程中反复偏航。

## 2. 决策记录格式
每条记录建议包含：

1. 决策编号
2. 决策主题
3. 决策日期
4. 当前状态
5. 决策内容
6. 原因
7. 影响范围
8. 后续待验证事项

## 3. 已确认决策
### D001 初始化控制面
- 决策日期：{date}
- 当前状态：已确认
- 决策内容：项目采用 `AGENTS.md`、`PLAN.md`、`API_CONTRACT.md`、`ARCHITECTURE_CONTRACT.md`、`DECISION_LOG.md`、`CONTEXT_READ_RULES.md`、`WORKLOG.md` 和主配置文件维护 AI 协作控制面。
- 原因：减少上下文漂移，明确唯一真源，并保留每次任务的简洁执行记录。
- 影响范围：全部控制文件
- 待验证事项：后续是否需要新增领域专用合同文件。
"""


def context_read_rules_md(project_name: str, config_file: str) -> str:
    return f"""# {project_name} 上下文读取规则

## 1. 文档目的
本文件用于控制 agent 在本项目中的上下文读取范围，减少无效 token 消耗。

目标不是极限省 token，而是：

1. 避免重复读取低价值文件
2. 优先读取高价值控制文件和关键代码入口
3. 让每轮只读与当前任务相关的最小集合

## 2. 默认必读文件
对于绝大多数编码任务，默认只需要先读这 3 个文件：

1. `PLAN.md`
2. `API_CONTRACT.md`
3. `{config_file}`

## 3. 第二层按需读取文件
只有在任务确实涉及对应边界时，再读以下文件：

1. `AGENTS.md`
2. `ARCHITECTURE_CONTRACT.md`
3. `DECISION_LOG.md`
4. 当前任务相关代码
5. 当前任务相关测试

## 4. 默认不需要读取的文件
以下内容默认不应读入上下文：

1. 虚拟环境目录
2. 包管理器缓存
3. 测试缓存
4. 构建产物
5. `.env` / `.env.*`
6. 本地 agent 设置文件
7. `archive/**`
8. 不相关 Markdown
9. 多数空壳包声明文件

## 5. 推荐读取策略
### 5.1 普通编码任务
默认读取：

1. `PLAN.md`
2. `API_CONTRACT.md`
3. `{config_file}`
4. 当前要改的代码文件
5. 当前要改的测试文件

### 5.2 架构或接口任务
再追加读取：

1. `ARCHITECTURE_CONTRACT.md`
2. `DECISION_LOG.md`

### 5.3 API / 接口任务
默认读取：

1. `PLAN.md`
2. `API_CONTRACT.md`
3. `{config_file}`
4. API / CLI / event 入口文件
5. 对应 service 文件
6. 对应接口测试

如涉及 breaking change、幂等、后台任务或错误格式，再追加 `DECISION_LOG.md` 和 `ARCHITECTURE_CONTRACT.md`。

### 5.4 Adapter / 外部集成任务
默认读取：

1. `PLAN.md`
2. `ARCHITECTURE_CONTRACT.md`
3. `{config_file}`
4. 对应 adapter / integration / client 文件
5. 对应标准模型文件
6. 对应 adapter 测试

如确认或推翻外部能力，必须追加 `DECISION_LOG.md`。

### 5.5 规则 / 阈值 / 状态口径任务
默认读取：

1. `PLAN.md`
2. `{config_file}`
3. 规则实现文件
4. 规则测试文件

如规则语义、阈值含义或风险等级变化，必须追加 `DECISION_LOG.md`，并更新唯一真源中的规则说明。

### 5.6 输出 / 报告 / 返回结构任务
默认读取：

1. `API_CONTRACT.md`
2. `{config_file}`
3. 输出渲染或响应组装代码
4. 输出相关测试

如输出结构变化影响调用方，必须追加 `DECISION_LOG.md`。

### 5.7 存储 / 后台任务任务
默认读取：

1. `PLAN.md`
2. `API_CONTRACT.md`
3. `ARCHITECTURE_CONTRACT.md`
4. `{config_file}`
5. 存储或任务调度代码
6. 对应测试

如新增持久化边界、任务状态、重试、超时或并发策略，必须追加 `DECISION_LOG.md`。

### 5.8 前端 / 页面任务
默认读取：

1. `PLAN.md`
2. `API_CONTRACT.md`
3. `{config_file}`
4. 当前页面或组件文件
5. 当前页面或组件测试

如当前阶段从“不做正式前端”变为“做正式前端”，必须追加 `AGENTS.md`、`ARCHITECTURE_CONTRACT.md` 和 `DECISION_LOG.md`。

## 6. 每轮不应做的事
除非当前任务明确要求，否则不要：

1. 每轮重新读取全部 Markdown 控制文件
2. 每轮重新输出完整项目树
3. 读取虚拟环境、缓存或归档
4. 重复总结整个项目背景
5. 新增额外 Markdown 设计文档
"""


def worklog_md(project_name: str, config_file: str) -> str:
    return f"""# 工作记录

## 说明
本文件只保留当前开发必要信息。完整历史如需归档，应移动到 `archive/**`。

后续 agent 仍需在每次任务结束后向本文件追加简洁记录。普通代码实现只更新 `WORKLOG.md`；控制面变化才更新对应控制文件。

## 当前状态摘要

### 系统主线

1. {project_name} 已初始化 AI 协作控制面。
2. 当前正式实施范围以 `PLAN.md` 为准。
3. 当前接口或模块合同以 `API_CONTRACT.md` 为准。

## 最近关键记录

### {now_text()} Codex
- 任务：初始化项目控制面约束文件
- 读取文件：用户需求、skill `init-pro`
- 修改文件：`AGENTS.md`、`PLAN.md`、`API_CONTRACT.md`、`ARCHITECTURE_CONTRACT.md`、`DECISION_LOG.md`、`CONTEXT_READ_RULES.md`、`WORKLOG.md`、`{config_file}`
- 执行验证：生成文件并保留既有文件不覆盖，除非使用 `--force`
- 结果：生成可复用 AI 协作约束、上下文读取规则和工作记录模板
- 未解决问题：需要按目标项目实际领域补充具体接口、规则和实现优先级
- 控制面变更：初始化控制面

## 追加记录模板

```md
### YYYY-MM-DD HH:MM AgentName
- 任务：一句话说明当前任务
- 读取文件：列出关键控制文件、代码文件、测试文件
- 修改文件：列出本次实际修改的文件
- 执行验证：列出关键命令、测试、接口验证
- 结果：说明完成了什么
- 未解决问题：如无则写“无”
- 控制面变更：如无则写“无”；如有，写明更新了哪些控制文件以及原因
```
"""


def defaults_yaml(project_name: str) -> str:
    slug = project_name.lower().replace(" ", "-")
    return f"""version: "0.1"
project: "{slug}"

system:
  current_phase: "initialization"
  capability_degrade_enabled: true
  evidence_required: true

features:
  api_enabled: true
  formal_frontend_enabled: false
  html_validation_enabled: false

runtime_sources: {{}}

capabilities:
  default_status: "unknown"
  allowed_statuses:
    - "confirmed"
    - "partial"
    - "unsupported"
    - "unknown"

output:
  concise_agent_response: true
  retain_source_evidence: true
"""


def build_templates(project_name: str, domain: str, stack: str, config_file: str) -> dict[str, str]:
    return {
        "AGENTS.md": agents_md(project_name, domain, stack, config_file),
        "PLAN.md": plan_md(project_name, config_file),
        "API_CONTRACT.md": api_contract_md(project_name),
        "ARCHITECTURE_CONTRACT.md": architecture_contract_md(project_name),
        "DECISION_LOG.md": decision_log_md(project_name),
        "CONTEXT_READ_RULES.md": context_read_rules_md(project_name, config_file),
        "WORKLOG.md": worklog_md(project_name, config_file),
        config_file: defaults_yaml(project_name),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="Target repository root")
    parser.add_argument("--project-name", required=True, help="Human-readable project name")
    parser.add_argument("--domain", required=True, help="Short business/domain context")
    parser.add_argument("--stack", default="TBD", help="Primary technology stack")
    parser.add_argument("--primary-config", default="project-defaults.yaml", help="Primary YAML config filename")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    templates = build_templates(args.project_name, args.domain, args.stack, args.primary_config)
    result = {}
    for filename, content in templates.items():
        path = root / filename
        existed = path.exists()
        if existed and not args.force:
            result[filename] = "skipped"
            continue
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        result[filename] = "overwritten" if existed else "created"

    print(json.dumps({"project_root": str(root), "files": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
