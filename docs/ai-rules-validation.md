# 规则接入验证记录

日期：2026-11-10。基于 RefundDesk-AI-Coding-Rules-v1.0.0.zip，接入当前项目工作区。

## 接入调整

- 保留项目运行 README，包内 README 保存为 docs/ai-rules-setup.md。
- .ai/rules.json 补充当前 0002、DB_SCHEMA 和 adopt_schema 基线，以及已有 seed 批次不可重复的事实。
- 明确用户已批准的 DB_SSLMODE=disable 例外，不以规则接入更改连接策略。
- 五个工具输出均通过生成器同步，未单独编辑生成文件。
- 修正新增脚本和测试的导入排序，subprocess.run 显式指定 check=False 以便断言失败返回码。
- 保留原业务 CI，新增 architecture-rules 独立工作流。

## 本次实际执行

| 命令 | 结果 |
|---|---|
| python scripts/render_ai_rules.py --check | 五个输出同步 |
| python -m unittest discover -s tests/rules -v | 14 项通过 |
| python scripts/check_architecture.py | 扫描 11 个 Python 文件，0 个问题 |
| uv 环境中的 ruff check . | 通过 |
| uv 环境中的 mypy src | 11 个源码文件通过 |
| uv 环境中的 pytest tests/rules tests/test_config.py tests/test_dotenv.py -q | 27 项测试及 5 个子测试通过（包含上述 14 项规则测试） |

本次改动限于规则、规则工具、文档及新增 CI，没有修改应用代码、历史迁移或依赖。
本次未重跑 PostgreSQL 集成测试和服务烟测，也未连接 Lightsail。
未验证各 AI 客户端实际自动加载行为，未执行远端 GitHub Actions，未配置分支保护。
工作流加入仓库不等于成为必需检查；需仓库管理员另行配置。
