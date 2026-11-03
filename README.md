# RefundDesk · I0 工程基础
本迭代提供 Python API、HTTP MCP、独立 Worker 和模拟支付四个入口。订单申请、审批、资金操作和登录从后续迭代实现。开发环境无需模型密钥。

## 在 macOS 连接云开发库
1. 使用 Python 3.12 和 uv（CI 使用 0.13.4）。解压后进入含 pyproject.toml 的目录。首次联网执行以下命令，保存生成的 uv.lock 并加入版本管理。已有锁的 checkout 直接使用 uv sync --locked。此交付环境无法访问 PyPI，因此发行包暂缺解析生成的锁。
2. 在云数据库创建 refunddesk_dev 和专用账号，配置公网端点、来源 IP 白名单和 CA。应用账号需要 CONNECT、schema USAGE，以及业务表的相应权限，也要能 SELECT alembic_version。DDL 可使用独立的 MIGRATION_DATABASE_URL。用另一个角色建表时，由该角色向应用账号授予表权限并设置后续表的默认权限。
3. 复制 .env.example 为 .env，替换端点、端口、密码和本地 CA 绝对路径。URL 中密码的特殊字符须按 URI 编码。下面的 uv run --env-file 会在进程启动时读取配置，无需把凭证写进 shell 历史。
4. 在专用开发库执行迁移和初始化。seed 仅允许 development 环境和名为 refunddesk_dev 的数据库。重复执行不会覆盖订单原始日期或现有数据。

```bash
uv lock
uv sync --locked
cp .env.example .env
# 编辑 .env 后继续
uv run --locked --env-file .env alembic upgrade head
uv run --locked --env-file .env python -m refunddesk.entrypoints.seed
uv run --locked --env-file .env uvicorn refunddesk.entrypoints.api:app --reload
```

另一个终端执行：

```bash
curl -i http://127.0.0.1:8000/health/live
curl -i http://127.0.0.1:8000/health/ready
```

live 检测进程，ready 查询当前数据库的迁移版本，成功返回 200；数据库故障或迁移未完成返回 503 和 DATABASE_NOT_READY。缺失或非法配置会使 API 启动失败。每次 API 响应带 X-Request-ID，可在 JSON 请求日志中查找；日志省略参数、身份凭据和响应正文。

## 其他进程
分别在不同终端执行：

```bash
uv run --locked uvicorn refunddesk.entrypoints.mcp:app --port 8001
uv run --locked uvicorn refunddesk.entrypoints.payment:app --port 8002
uv run --locked --env-file .env python -m refunddesk.entrypoints.worker
```

MCP 在 /mcp 提供 SDK 的 HTTP ASGI 应用，当前未注册业务工具或授权，只绑定本机。支付进程只暴露 /health/live。Worker 启动时验证数据库后等待退出信号，--check 表示检查后退出。进程不会发起退款。

## 可选的容器联调
需安装 Docker Compose v2，并先生成 uv.lock。与云开发库分开，Compose 使用内部 db 服务与独立卷，不会读取 DATABASE_URL 来连接云库。为本地开发库选择 URL 安全的随机密码，后续重启沿用同一密码。

```bash
# 在当前终端一次性设置并妥善保存；已有卷请沿用初始化时的值
export RD_LOCAL_PG_PASSWORD="$(openssl rand -hex 24)"
docker compose up --build -d
docker compose run --rm api python -m refunddesk.entrypoints.seed
docker compose ps
docker compose logs worker
docker compose stop
```

Compose 仅将三个 HTTP 端口发布到宿主机回环地址。数据库端口仅容器网络可达。本地开发容器的初始化用户同时用于迁移和应用，请勿使用该配置创建公网生产实例。deploy/nginx.conf.example 是同宿主机 HTTPS 入口模板，需替换证书和域名；I0 仅 /health/live 已实现，业务 API 及 MCP 公网授权在后续迭代完成。

## 数据与源码
| 路径 | 内容 |
|---|---|
| src/refunddesk/entrypoints | API、MCP、Worker、支付、seed |
| src/refunddesk/settings.py、db.py | 配置检查、异步数据库连接、迁移版本检查 |
| migrations/versions | Alembic 初始迁移，创建 tenants、users、memberships、orders |
| tests、scripts | 配置和服务测试、真实进程烟测、CI 演示数据检查 |
| .github/workflows/ci.yml | 临时 PostgreSQL 17、静态检查、迁移往返及服务测试 |

acme 和 beta 各有 support、supervisor、auditor 三个演示身份（用户名为租户-角色）。它们是数据库身份记录，I1 增加登录。两租户故意重复使用订单号，方便验证租户隔离。

| 订单号 | 状态（首次初始化时） |
|---|---|
| DEMO-1001 | 刚支付、未发货 |
| DEMO-1002 | 已支付、已发货 |
| DEMO-1003 | 未支付 |
| DEMO-1004 | 支付已超过七天、未发货 |

## 验收
配置测试可在无第三方依赖时运行：
```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_config.py -v
```

联网生成并提交锁文件后，在隔离测试库（例如 refunddesk_test）配置 .env.test，并使用 APP_ENV=test。运行：
```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked mypy src
uv run --locked --env-file .env.test alembic upgrade head
uv run --locked --env-file .env.test pytest -q
uv run --locked --env-file .env.test bash scripts/smoke.sh
```

GitHub Actions 在一次性数据库上额外验证两次 seed、租户数据量和迁移往返。首次缺锁的 CI 会生成锁并上传为构建产物；维护者须将其提交到项目。后续一律用 --locked 检查锁与项目声明一致。降级会删除 I0 表，仅在可丢弃的测试实例执行。

本包验证记录：Python 3.12 编译、4 个独立单元测试、Shell 语法、TOML/YAML 解析通过。受执行环境限制，依赖锁、Ruff/mypy、真实 PostgreSQL、HTTP 服务和 Compose 全栈仍待联网验收。本包未执行远端 CI 或云端操作。
