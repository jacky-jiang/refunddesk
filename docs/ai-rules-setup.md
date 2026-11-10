# RefundDesk 多工具编码规则包 v1.0.0

适用阶段：I0 已由用户确认测试通过，I1 开发前接入。此包不修改应用代码、不执行数据库操作，不替代完整技术方案。来源为此前「前端全栈脚手架设计」的单一规则源/多工具输出约定、RefundDesk 已评审方案、可读取的早期 I0 工程和 helloagents 建表脚本。未取得用户测试后的最新代码快照，因此不能声称已验证当前完整项目。

## 当前工程接入说明

本文件由规则包 README 独立保存，项目运行方式见根目录 README.md。
接入版本基于 v1.0.0；已核对当前 dev 工程，补充 0002 接管及用户已批准的 SSL 配置说明。
项目原有业务代码、迁移、依赖锁文件和业务 CI 保持不变。原始包自测记录已替换为本次
工程验证记录，见 docs/ai-rules-validation.md。当前任务在已有分支引入文件，未创建或发布 PR。

## 使用方式

1. 在已测试通过的项目上新建规则接入分支。将本包文件按相同相对路径合入项目根目录；不要用整个目录替换工程。
2. 若已有 AGENTS.md、CLAUDE.md、Copilot/Cursor 规则或同名脚本，先审查差异。将需要保留的项目规则合入 `.ai/rules.json`，保留备份再生成，禁止直接覆盖已有规则。生成器也会拒绝覆盖非本工具生成的目标文件。
3. 使用 Python 3.12 执行下列命令。纯规则工具只依赖标准库；无需连接数据库。
4. 在 AI 工具中打开项目根目录，新会话要求其列出实际读取的规则文件和本次修改涉及的层。对不自动加载这些文件的工具，显式附加 `docs/ai-coding-guidelines.md`。
5. 合并前由仓库管理员将工作流 `architecture-rules` 加入分支必需检查；仅增加 YAML 不会自动启用分支保护。保留并运行现有 I0 测试流程。

```bash
python scripts/render_ai_rules.py
python scripts/render_ai_rules.py --check
python -m unittest discover -s tests/rules -v
python scripts/check_architecture.py
```

随后按当前工程配置运行 `uv run ruff check .`、`uv run mypy src`、`uv run pytest`。上述标准库检查不能替代业务检查；若工程已有不同命令，以其实际配置为准并记录差异，不静默降低质量门槛。

## 文件分工

| 文件 | 用途 |
|---|---|
| `.ai/rules.json` | 唯一维护源，15 组规则 |
| `AGENTS.md` | Codex 及兼容 AGENTS.md 的工具 |
| `CLAUDE.md` | Claude Code 项目指令 |
| `.github/copilot-instructions.md` | 支持仓库指令的 Copilot 场景 |
| `.cursor/rules/refunddesk.mdc` | Cursor alwaysApply 项目规则 |
| `docs/ai-coding-guidelines.md` | 通用完整规则，便于手工附加 |
| `docs/architecture-contract.md` | 目录职责、约束与迁移基线说明 |
| `scripts/render_ai_rules.py` | 确定性生成、漂移检测、已有文件保护 |
| `scripts/check_architecture.py` | Python AST 导入方向检查 |
| `tests/rules/` | 架构检查器与生成器回归测试 |
| `.github/workflows/architecture-rules.yml` | 独立 CI 门禁，不覆盖原 CI |

## 执行边界

规则文本不能保证 AI 永远遵守。采用三重约束：工具加载规则、CI 拦截部分结构违规、业务测试与人工审查验证语义。不得把规则文件当成网络、凭据、数据库或命令权限配置。

架构检查器支持绝对/相对导入、别名导入、`from refunddesk import ...` 形式，禁止 domain/application 使用第三方库、适配器直接使用数据库驱动，并防止 HTTP/MCP 交叉调用。它不分析动态导入、反射、运行时调用图、事务、授权实现或前端 import。不得故意使用这些机制绕过规则；仍需代码审查和针对性测试。

早期 I0 的 `db.py`、`settings.py`、`events.py` 是精确文件白名单；新业务层不能引用它们。只有 entrypoints 可引用这些基础模块。白名单文件不检查内部依赖，修改时必须人工审查；不要向其中追加业务代码。entrypoints 能导入所有层是为了装配，不代表允许将业务逻辑放在入口。

## 工具接入事实与来源

核对日期：2026-11-10。不同客户端/版本加载范围和指令优先级有差异，不保证所有“AI 编程工具”都自动识别同一文件。提供完整生成文件可避免依赖某一种工具特有的文件导入语法。配置不会安装插件或修改全局权限。

- Codex：[AGENTS.md 官方说明](https://developers.openai.com/codex/guides/agents-md)
- Claude Code：[项目记忆与 CLAUDE.md](https://code.claude.com/docs/en/memory)
- Copilot：[自定义指令及客户端差异](https://docs.github.com/en/copilot/concepts/prompting-and-context/custom-instructions)
- Cursor：[项目规则与 MDC](https://cursor.com/docs/rules)

## I1 任务提示模板

> 阅读本仓库编码规则、已批准方案及相关 ADR，先说明本任务涉及的层、文件、事务边界和验证方式，然后实现当前 I1 任务。复用现有用例与端口，不绕过租户授权、不把 ORM 暴露给适配器。发现迁移基线不一致时报告实际差异，不重建数据库。完成后提供变更说明、运行的命令及真实结果，并列出尚未验证的风险。
