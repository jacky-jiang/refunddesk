# RefundDesk 架构契约

业务架构依据已评审方案；以下子目录是对应职责的落位约定。尚未存在的目录按功能需要新增，不要求 I1 开始前创建空层或重写所有 I0 代码。若用户本地的已批准目录不同，先对照映射再更新规则和检查器。

| 目录 | 职责 | 可依赖的项目层 |
|---|---|---|
| `src/refunddesk/domain/` | 实体、值对象、资格和状态规则 | domain |
| `src/refunddesk/application/` | 用例、应用 DTO、ports、UoW 协议 | application、domain |
| `src/refunddesk/infrastructure/` | repository、UoW、ORM、外部客户端实现 | infrastructure、application、domain |
| `src/refunddesk/adapters/http/` | HTTP DTO、身份入口、错误映射、调用用例 | application、本适配器 |
| `src/refunddesk/adapters/mcp/` | MCP 工具协议、身份入口、错误映射、调用用例 | application、本适配器 |
| `src/refunddesk/workers/` | 任务调度循环，通过用例领取和执行任务 | workers、application |
| `src/refunddesk/agents/` | 模型编排、MCP 客户端、调用预算 | agents |
| `src/refunddesk/entrypoints/` | 配置加载、依赖注入、进程启动 | 各层，仅用于装配 |
| `migrations/` | 版本化数据结构升级 | 迁移运行上下文 |
| `web/` | React/TS/Vite，服务端状态的展示和交互 | 通过已定义的 API 通信 |

### 用例落位示例

`adapters/http` 的申请退款接口和 `adapters/mcp` 的申请退款工具都调用 `application` 的同一申请用例。用例从可信调用上下文取租户/操作者，通过仓储端口读取订单并调用领域资格规则；在 UoW 中记录申请与审计，返回应用结果。基础设施实现仓储/UoW，entrypoints 装配依赖。应用用例不能导入实际数据库实现。

仓储、UoW、时钟、支付网关等端口定义放 application；框架类型和 ORM 仅保留在边界。事务边界属于应用，数据库提交细节属于基础设施 UoW。Worker 通过应用端口执行任务而不是在循环中拼接 SQL。

### I1 数据基线检查

当前工程已经实现 Alembic 0001→0002 迁移，默认通过 DB_SCHEMA=refunddesk
统一应用和迁移的 schema；版本检查要求 0002。src/refunddesk/schema.sql 固定定义
0002 的 14 张业务表，adopt_schema 会核对结构后登记版本，不修改业务数据。
已有 public/0001 数据库需显式设置 DB_SCHEMA=public 后按现有升级流程处理。

首次业务数据库变更前仍应核对实际连接目标、schema、revision、表/约束/索引和数据保留要求。
源码现状不能证明远程数据库现状。复用 README 中的新库、旧版本升级或已有库接管流程，
不要重新设计接管、盲目 stamp 或重跑初始化。附件 seed 同批次重复执行会失败；保留已有演示数据。


### PR 必查事项

- HTTP/MCP 是否复用应用用例，是否存在跨层导入和隐藏 SQL？
- 租户、操作者、确认和审批是否来自可信上下文，是否测试了绕过路径？
- 事务边界是否短小，任务与审批是否原子落库，外部支付是否在事务外？
- 唯一约束、锁、稳定幂等键、未知支付结果、重试和崩溃恢复是否匹配设计？
- 数据迁移是否保留现有数据，历史迁移是否保持不变？
- 状态机/契约变更是否有 ADR，是否报告真实测试证据？

### 规则变更流程

编辑 `.ai/rules.json` → 运行生成器 → 检查五个生成文件的差异 → 运行同步检查及回归测试 → 随 PR 审查。同步失败修复源和输出，不能删掉工作流。规则版本改变不自动授权架构变更。保留已有团队/用户指令，不为接入此包清空其他约束。
