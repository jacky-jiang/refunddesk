# RefundDesk

当前 I1 提供登录会话、租户角色、订单与政策查询、资格评估、操作确认、创建/取消申请、主管审批、审计和过期扫描。HTTP API 不依赖模型。数据库最新版本为 Alembic `0003`，在原 `0002` 的 14 张业务表基础上新增凭据、会话和安全事件表。批准会原子创建执行任务，但 I1 不领取任务或调用支付；MCP 业务工具和前端仍属于后续迭代。

## 1. 安装与环境配置（macOS / Linux）

安装 Python 3.12 和 uv，然后进入项目根目录。后续命令均从此目录执行。

```bash
cd /你的项目路径/refunddesk
uv sync --locked
# 仅在 .env 不存在时创建，保留已有配置
if [ ! -e .env ]; then cp .env.example .env; fi
```

项目已有 `uv.lock`，正常安装不需要重新生成锁文件。`--locked` 要求依赖声明与锁文件一致。

在本地 `.env` 或运行环境中设置以下变量，填写实际 IP、端口和密码：

```dotenv
APP_ENV=development
DB_HOST=你的数据库IP
DB_PORT=5432
DB_NAME=helloagents
DB_USER=yijiang
DB_PASSWORD='你的数据库密码'
DB_SCHEMA=refunddesk
DB_SSLMODE=disable
```

`.env` 已被 Git 忽略，不要提交真实凭据。API、Worker、seed 和 Alembic 导入配置模块时，`python-dotenv` 自动读取**当前工作目录**的 `.env`，启动命令无需再加 `--env-file .env`。修改配置后重启进程。

配置规则：

- 系统环境变量优先于 `.env`；终端里以前 `export` 的值也会生效。`.env` 不存在时直接使用系统环境变量。
- `DATABASE_URL` 优先于 `DB_HOST` 等分项变量。迁移和接管优先使用 `MIGRATION_DATABASE_URL`，其次为应用连接配置。旧 URL 可能覆盖新填的 IP、数据库名或密码。
- 分项密码无需 URI 编码；完整 URL 中的密码需要 URI 编码。dotenv 禁用 `${...}` 插值，含空格或 `#` 的值应使用引号。
- `DB_SCHEMA` 默认 `refunddesk`，应用、迁移和版本表使用同一 schema，不回退到 `public` 查找业务表。
- `DB_SSLMODE=disable` 不使用 TLS 或 CA。服务支持 TLS 时可使用 `require`；完整证书验证使用 `verify-full` 并设置 `DB_SSL_ROOT_CERT`。未指定模式时默认 `verify-full`。

Lightsail 防火墙及 PostgreSQL 访问规则需允许当前客户端 IP 和端口。数据库 `helloagents`、账号 `yijiang` 需事先存在；迁移不会创建数据库或账号。应用需要 schema USAGE、业务表权限及版本表 SELECT 权限；迁移和接管还需要相应 DDL 权限，可配置独立迁移账号。

## 2. 根据数据库现状选择初始化方式

### A. 已执行附件建表 SQL，并已有演示数据

这是已有 `helloagents.refunddesk` 数据的场景。先备份数据库，确认 `.env` 中 `DB_SCHEMA=refunddesk`，然后执行：

```bash
uv run --locked python -m refunddesk.entrypoints.adopt_schema
uv run --locked alembic current
uv run --locked alembic upgrade head
```

接管命令会创建随机命名的核验 schema，读取 `src/refunddesk/schema.sql` 建立参考结构，比较字段类型、非空、默认值、主键、外键、唯一约束、CHECK 和索引。结构一致才登记 `0002`，不会修改已有业务数据；不一致则报告表名并回滚，不应绕过检查直接 `stamp`。

该命令需要创建核验 schema、表以及写入版本表的权限。核验 schema 在成功时删除、失败时随事务回滚。锁等待超时为 5 秒，单语句超时为 60 秒，同 schema 的接管操作串行执行。它接受无版本、`0001` 或 `0002`，可重复执行，但不会核验演示数据的业务内容。

`adopt_schema` 仅登记 `0002` 基线，之后 `alembic upgrade head` 追加身份表到 `0003 (head)`。已经升级到 `0003` 的库不再执行接管命令。**不要重新执行已有批次的演示数据脚本。**

### B. 新库，尚未建立业务表

配置好已存在的目标数据库后执行：

```bash
uv run --locked alembic upgrade head
uv run --locked alembic current
```

迁移创建目标 schema、版本表和业务表：`0001` 创建四张基础表，`0002` 增加字段、约束及其余十张表。`0003` 再新增登录凭据、会话与安全事件表，不会自动插入演示数据或默认密码。

### C. 已由 Alembic 管理，当前版本为 0001

先确认原表及 `alembic_version` 所在的 schema。若在 `public`，设置 `DB_SCHEMA=public`；不要直接切换到 `refunddesk`，否则可能创建另一套表。

```bash
uv run --locked alembic current
uv run --locked alembic upgrade head
```

升级保留已有行；如果原数据不满足新增约束，升级失败并回滚，需要先核对数据。`0002` 不支持 downgrade，以避免删除接管的业务数据；回退需使用审核过的备份恢复方案。

## 3. 启动服务

完成迁移或接管后，在不同终端分别进入项目根目录并启动所需服务：

| 服务 | 启动命令 | 地址或行为 |
|---|---|---|
| API | `uv run --locked uvicorn refunddesk.entrypoints.api:app --reload` | 本机端口 8000 |
| MCP | `uv run --locked uvicorn refunddesk.entrypoints.mcp:app --port 8001` | 本机端口 8001，路径 `/mcp` |
| 模拟支付 | `uv run --locked uvicorn refunddesk.entrypoints.payment:app --port 8002` | 本机端口 8002 |
| Worker | `uv run --locked python -m refunddesk.entrypoints.worker` | 保留的 I0 入口，仅数据库检查，不执行支付 |
| 过期扫描 | `uv run --locked python -m refunddesk.entrypoints.expiry` | 每轮最多处理 100 条过期申请，间隔 30 秒；不调用支付 |

在各终端按 `Ctrl+C` 停止对应进程。仅检查 Worker 的数据库就绪状态可执行：

```bash
uv run --locked python -m refunddesk.entrypoints.worker --check
```

在另一终端验证：

```bash
curl -i http://127.0.0.1:8000/health/live
curl -i http://127.0.0.1:8000/health/ready
curl -i http://127.0.0.1:8002/health/live
curl -i -X OPTIONS http://127.0.0.1:8001/mcp
```

API `live` 只检查进程；`ready` 查询目标 schema 的 `alembic_version`，要求版本为 `0003`。成功返回 200 和 `{"status":"ready"}`；数据库不可达、权限不足、缺失版本表或版本不匹配时返回 503 / `DATABASE_NOT_READY`。这不是对全部表结构的再次核验。API 响应包含 `X-Request-ID`，可关联 JSON 请求日志。

模拟支付应返回包含 `mock-payment` 的健康响应。MCP OPTIONS 返回 200、204 或 405 均符合当前路由烟测预期，仅表示端点路由存在，不证明业务工具或授权已实现。

## 4. 演示数据的两种来源

| 来源 | 数据规模及限制 |
|---|---|
| 附件 `helloagents_seed.sql`（副本位于 `tests/fixtures/`） | 要求 helloagents/refunddesk；默认创建 `rd-demo-01-a`、`rd-demo-01-b` 两个租户、200 笔订单、10 笔退款申请和 26 条审计记录，同批次重复执行会报错并回滚 |
| `python -m refunddesk.entrypoints.seed` | 仅允许 `APP_ENV=development` 且数据库名为 `refunddesk_dev`；创建 acme/beta 两个租户、6 个用户、8 笔订单及 v1 政策，可重复执行；不设置默认密码 |

已有附件演示数据时，只需接管数据库。Python seed 不适用于 `helloagents`，也不用于补充完整的退款演示场景。

附件中的 DEMO-1001 至 1004 分别表示可退款、已发货、未支付及超期；1005 至 1009 分别表示待审批、已拒绝、已退款、待核实及已批准。相对时间取决于首次执行时刻，之后不会自动刷新。

## 5. 开发检查与测试

不需要数据库的检查：

```bash
uv run --locked ruff check .
uv run --locked mypy src
uv run --locked pytest tests/test_config.py tests/test_dotenv.py -q
```

完整测试使用**独立、可丢弃的 PostgreSQL 测试库**，不要指向已有演示库或生产库。创建 `.env.test`，明确填写测试连接、`DB_SCHEMA`、`APP_ENV=test` 和 SSL 模式。如果开发 `.env` 中存在完整 URL，测试文件应显式提供测试用 `DATABASE_URL` 和 `MIGRATION_DATABASE_URL`，避免残留配置回退到开发数据库；运行前也应检查终端已导出的同名变量。

```bash
uv run --locked --env-file .env.test alembic upgrade head
uv run --locked --env-file .env.test pytest -q
uv run --locked --env-file .env.test bash scripts/smoke.sh
```

这里由 uv 显式读取 `.env.test`，项目的 dotenv 不会自动寻找该文件。迁移测试需要创建、删除临时 schema 的权限；会验证旧版本升级、结构不匹配拒绝接管、重复接管及完整演示数据保留。烟测临时使用 18000–18002 端口，结束时清理自己启动的服务。

I0 基线阶段曾通过 19 个测试并验证两份附件原样执行后接管成功；本轮 I1 全量 69 个测试、Ruff、mypy、架构检查及服务烟测通过，详见 docs/i1-validation.md。未连接 Lightsail 验证实际远程数据，完整 Compose 构建也未在此次验证中执行。远端 CI 状态以 GitHub Actions 为准。

## 6. 可选：Docker Compose 本地联调

需要 Docker 和 Compose v2。Compose 使用内部 PostgreSQL 17、`refunddesk_dev` 数据库及独立卷，应用连接由 Compose 中的 `DATABASE_URL` 指向 `db`，不是 Lightsail。新建本地卷时生成密码；后续启动必须沿用该卷初始化时的密码。

```bash
# 仅首次初始化时生成，并妥善保存；已有卷请设置原密码
export RD_LOCAL_PG_PASSWORD="$(openssl rand -hex 24)"
docker compose up --build -d
docker compose run --rm api python -m refunddesk.entrypoints.seed
docker compose ps
docker compose logs worker
docker compose stop
```

Compose 的 migrate 服务在 API 和 Worker 启动前升级数据库，默认 schema 为 `refunddesk`。历史卷若将表建在 `public`，需先调整容器连接的 `DB_SCHEMA` 配置后再升级。三个 HTTP 端口仅发布到宿主机回环地址，数据库端口仅容器网络可达。

## 7. 主要代码与迁移文件

| 路径 | 用途 |
|---|---|
| `src/refunddesk/settings.py` | dotenv 加载、连接配置及 schema 校验 |
| `src/refunddesk/db.py` | 异步数据库引擎、版本就绪检查 |
| `src/refunddesk/entrypoints/` | API、MCP、支付、Worker、seed、adopt_schema |
| `migrations/env.py` | 同步迁移连接、目标 schema 和版本表配置 |
| `migrations/versions/0001_foundation.py` | 历史四表基础迁移 |
| `migrations/versions/0002_refund_schema.py` | 对齐附件完整结构的增量迁移 |
| `src/refunddesk/schema.sql` | 固定的 0002 结构定义，供迁移及接管核验读取，不含演示数据 |
| `tests/fixtures/helloagents_seed.sql` | 附件 seed 副本，用于回归测试 |
| `tests/`、`scripts/` | 配置、服务、迁移、数据保留测试及烟测 |
| `.github/workflows/ci.yml` | 一次性 PostgreSQL 17、静态检查、测试和 seed 验证 |

后续数据库结构变化应新增迁移，不要直接修改已发布的 `0001`、`0002`、`0003` 或作为 0002 基准的 `schema.sql`。


## 8. 多 AI 编码工具规则

统一规则源为 `.ai/rules.json`。Codex 使用 `AGENTS.md`，Claude Code 使用 `CLAUDE.md`，
Copilot 使用 `.github/copilot-instructions.md`，Cursor 使用 `.cursor/rules/refunddesk.mdc`；
其他工具可显式读取 `docs/ai-coding-guidelines.md`。实际加载行为取决于工具及版本，应在新会话确认。

规则只在源文件编辑，再同步生成，不能单独修改生成文件：

```bash
python scripts/render_ai_rules.py
python scripts/render_ai_rules.py --check
python -m unittest discover -s tests/rules -v
python scripts/check_architecture.py
```

独立工作流 `architecture-rules` 检查规则漂移、工具回归及静态导入边界，原业务 CI 保留。
仓库管理员可将该检查设为分支保护的必需检查；添加工作流不会自动启用分支保护。
检查器不验证动态导入、事务、授权或前端依赖，仍需业务测试与代码审阅。
详细接入说明见 [规则接入文档](docs/ai-rules-setup.md)，目录约定见
[架构契约](docs/architecture-contract.md)，本次验证见 [验证记录](docs/ai-rules-validation.md)。


## 9. I1 登录与 HTTP 演示

先 `alembic upgrade head` 到 0003，再为已有用户单独设置密码（交互输入，至少 12 字符，
不通过命令行参数传入密码）。密码更新会注销该用户全部会话：

```bash
# Python seed 的本地演示账号
uv run --locked python -m refunddesk.entrypoints.set_password acme-support
uv run --locked python -m refunddesk.entrypoints.set_password acme-supervisor
uv run --locked python -m refunddesk.entrypoints.set_password acme-auditor
# 附件 seed 的用户 ID 则是 rd-demo-01-a-support 等，不要重新执行 seed
```

登录使用 `users.id` 和经过成员关系验证的 `tenant_id`。`POST /api/auth/login` 必须带
`X-CSRF-Protection: 1`，JSON 请求包含 user_id、tenant_id、password。成功设置 HttpOnly、
SameSite=Strict 会话 Cookie，并返回 csrf_token；后续写请求使用该 Cookie 和
`X-CSRF-Token`，退出后会话失效。生产（APP_ENV 非 development/test）Cookie 必须走 HTTPS；
反向代理后配置 PUBLIC_ORIGIN 为浏览器访问的准确 origin（不含路径）。

API 启动后可在 `/docs` 查看 OpenAPI 并调用接口。完整接口、字段、错误码、期限和演示步骤见
[API 契约](docs/i1-api.md)，已确认决策见 [ADR](docs/adr/0001-i1-business-boundary.md)，
验收追踪见 [I1 验收](docs/i1-acceptance.md)。没有为账号配置密码前，登录会统一拒绝。

过期扫描仅关闭 pending/approved 的超期申请并记录审计，不会把 executing 或后续状态撤销：

```bash
uv run --locked python -m refunddesk.entrypoints.expiry --once
uv run --locked python -m refunddesk.entrypoints.expiry
```

旧附件中的演示申请时间不会刷新；启动扫描会按这些真实截止时间关闭已过期记录。
Compose 已包含 expiry 服务。I1 不会把 approved 展示成退款成功，也不会消费已创建的 execute 任务。
