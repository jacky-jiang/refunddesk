# I1 验收追踪

范围由用户于 2026-11-11 确认。只交付 HTTP 业务闭环；I2 支付执行、I3 MCP 业务工具、I4 UI 不在本轮。

| 需求/任务 | 实现 | 主要测试 |
|---|---|---|
| F01 / I1-01 | IdentityService、auth_sessions、scrypt 凭据、HTTP CSRF | test_login_csrf_logout_and_disabled_user、test_session_expiry_and_input_redaction、test_tenant_isolation_and_identity_forgery |
| F02/F03 / I1-02 | 租户订单与政策仓储、只读 HTTP | test_full_workflow_and_audit、test_confirmation_binds_policy_version |
| F04 / I1-03 | domain eligibility、服务端金额和政策校验 | test_eligibility（9 个场景）、HTTP 资格评估 |
| F05 / I1-04 | 确认摘要、幂等事务锁、订单锁、申请及审计原子写入 | test_confirmation_replay_tampering_and_idempotency、test_concurrent_submissions、test_submission_failure_rolls_back_confirmation_and_idempotency |
| F05 / I1-04 | 取消本人 pending、新确认与重新评估 | test_cancel_reapply_and_self_approval、test_confirmation_expiry_other_user_and_cancel_restrictions |
| F06 / I1-05 | 审批角色/自审禁止、决定和任务同事务 | test_concurrent_final_decisions、test_approval_job_failure_rolls_back_decision |
| R09 / I1-06 | 可控时钟、独立周期扫描、幂等关闭任务及审计 | test_expiry_boundary、test_approval_expiry_boundary、test_expiry_restart_and_started_request_not_revoked、test_expiry_and_approval_compete |
| F08/F10 / I1-07 | 详情时间线、审计筛选、独立安全事件 | test_full_workflow_and_audit、test_rejection_can_reapply_and_pagination |
| 迁移保留数据 | 新增 0003，不改历史迁移 | 原 tests/test_migrations.py；0002 核验接管后升级 0003 保留全部演示行 |

运行完整验收使用独立 PostgreSQL 数据库、.env.test，命令见 README。并发测试真实竞争数据库事务，
审计/任务失败测试注入仓储写入异常后核对所有相关记录回滚；不跳过断言或替换成 SQLite。
测试中完整数据使用随机 schema；测试账号密码只作为临时测试数据，不初始化运行环境的默认密码。

## 数据基线与运行限制

- 当前源码基线：0002、refunddesk 默认 schema、14 张业务表，已有接管机制。
- 本轮使用本机临时 PostgreSQL 17，先迁移到 0002，确认 revision 后升级到 0003；没有连接 Lightsail。
- 0003 仅新增 user_credentials、auth_sessions、security_events 和索引；不修改历史业务数据。
- 历史 schema.sql 仍固定为 0002 参考，已经升级的库不重新执行 adopt_schema。
- 无 HTTP 用户注册/密码重置；通过受控 CLI 对已有账号设置密码。I1 不包含第三方身份平台、登录刷新或 UI。
- 原演示时间不会刷新。过期扫描运行后会关闭已超时的旧样本，这是业务规则效果。
- 0003 无破坏性 downgrade。回退前备份，应用回退和数据库恢复分别评估；优先前向修复。
- 未执行真实支付、真实远端数据库修改、远端 CI 或性能 SLA 验收。

最终运行证据另见 docs/i1-validation.md。
