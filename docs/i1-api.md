# I1 HTTP API 契约

基于用户确认的 I1 范围。完整字段和响应模型见 `i1-openapi.json`，运行时 `/openapi.json`、`/docs`。
所有调用使用项目当前 schema，不接受业务请求体中的身份、角色、退款金额等权威字段。

## 身份与安全

- `POST /api/auth/login`：JSON `{user_id, tenant_id, password}`；Header `X-CSRF-Protection: 1`。
  user_id 是已有 users.id。tenant_id 只是选择候选成员关系，必须经服务端校验。
  成功 200 设置 `rd_session` HttpOnly/SameSite=Strict Cookie，返回 `{csrf_token, expires_at}`。
  有 Origin 的登录请求必须匹配 PUBLIC_ORIGIN 或直接请求 origin。不启用跨域凭据 CORS。
- `GET /api/auth/me`：返回 user_id、tenant_id、roles；每次认证重查用户 enabled 和成员关系。
- `POST /api/auth/logout`：需 Cookie 和 X-CSRF-Token；204，删除持久会话及 Cookie。
- 会话固定有效 8 小时，无滑动续期或自动刷新；CSRF token 由客户端保存，丢失时重新登录。
  生产 Cookie 使用 Secure；本机开发只有 APP_ENV=development/test 时允许 HTTP。
- 所有其他 POST 都需会话及 `X-CSRF-Token`，包括只读资格评估 POST。
- 每个失败业务请求独立写 security_events，不保存密码、Cookie、连接串或原始请求正文。
  数据库不可用返回 503，无法在同一不可用数据库保证安全事件持久化。

## 接口

| 方法/路径 | 参数 | 响应 |
|---|---|---|
| GET /api/orders/{order_no} | 订单号 | 订单字段、可申请退款金额 refundable_amount_minor；无资格或已有有效申请为 0 |
| GET /api/refund-policies/current | 无 | 当前已生效的最新政策 |
| GET /api/refund-policies/{version} | 版本 | 本租户历史政策，允许读取未生效版本但不能用其申请 |
| POST /api/orders/{order_no}/refund-evaluation | 无正文 | eligible、reason_codes、全额金额、币种、policy_version、evaluated_at |
| POST /api/action-confirmations | action=create/cancel、target=订单号/申请ID、reason | 201，confirmation_id、expires_at、服务端计算的 confirmed_action |
| POST /api/refunds | order_no、reason、confirmation_id；Header Idempotency-Key | 200，创建或复用的申请 |
| POST /api/refunds/{id}/cancel | reason、confirmation_id；Header Idempotency-Key | 200，关闭后的申请或幂等重试结果 |
| GET /api/refunds/{id} | limit、offset 控制时间线 | request、timeline、limit、offset |
| GET /api/approvals | limit、offset | 本租户未过期 pending 列表，仅主管 |
| POST /api/approvals/{id}/decision | decision=approve/reject、note | 更新后的申请，仅主管且不能自审 |
| GET /api/audit-events | user_id、order_no、request_id、since、until、limit、offset | items、limit、offset |

创建与取消必须先由可信调用者明确调用确认接口。确认有效 5 分钟，绑定当前用户、租户、动作、
对象、原因、金额/币种，创建还绑定政策版本。请求再次执行时重新计算摘要；参数、金额或政策
变化都会使旧确认无效。确认不是审批，也不保证动作届时仍符合资格；实际写入再次校验。
金额不接受客户端提交。当前订单数据没有客户姓名、电话等隐私字段，不输出凭据或会话字段。

名字/ID 最多 128 字符，原因和审批意见去除首尾空白后 1–2000 字符，幂等键 1–128 字符。
未知请求字段拒绝。分页默认 20、最大 100，offset 最大 100000；详情时间线默认 100。
时间过滤必须带时区。业务时间 UTC，JSON 使用 ISO 8601。列表稳定排序；相同时间戳用 ID 排序。
查询和审计对 support/supervisor/auditor 开放；写权限按角色及资源关系校验。

## 生命周期与事务

- 满 168 小时仍可申请，超过则拒绝；未来支付时间、未支付、已发货、已退款/预留、非正金额均不合格。
- I1 只接受已确认的 168h/24h/30min、CNY、全额、未发货、主管审批政策；不同规则配置返回 POLICY_UNSUPPORTED。
- 同一幂等键同参数返回原申请当前状态；不同参数冲突。不同键同订单返回已有有效申请，不修改原申请。
  已成功退款不创建新申请。取消后新建必须换新幂等键并重新确认、评估。
- 取消仅允许申请人本人操作未过期 pending；批准后不能取消。拒绝关闭原申请。
- 审批在 `now >= approve_by` 时过期；批准设置 start_by=批准时刻+30分钟，并原子创建 execute/ready 任务。
- `now >= start_by` 且仍 approved 的申请由扫描关闭。executing/reconciling/manual/succeeded 不被审批期限撤销。
- 扫描与写入按订单→申请锁定。后台扫描是过期持久化的可靠路径；审批也会持久化观察到的过期。
  资格查询不写库；失败的创建/取消事务不会顺带提交状态变更。
- 在启动过期扫描前，详情可能显示尚未扫描的 pending/approved；截止时间同时返回，待办排除已过期申请。
- 已批准仅表示等待 I2 执行；I1 不创建 refund_operations，也不调用支付。

## 统一错误

结构：`{"error":{"code":"...","request_id":"..."}}`，同时返回 X-Request-ID。

| HTTP | 代码 |
|---|---|
| 401 | INVALID_CREDENTIALS、UNAUTHENTICATED |
| 403 | FORBIDDEN、CSRF_INVALID、SELF_APPROVAL_FORBIDDEN |
| 404 | NOT_FOUND（也用于跨租户资源，避免泄露存在性） |
| 409 | ORDER_NOT_ELIGIBLE、POLICY_UNSUPPORTED、CONFIRMATION_INVALID、IDEMPOTENCY_CONFLICT、STATE_CONFLICT、REQUEST_EXPIRED |
| 422 | INVALID_INPUT、IDEMPOTENCY_KEY_REQUIRED、INVALID_TIME_RANGE、INVALID_ACTION、INVALID_DECISION |
| 503 | DATABASE_NOT_READY |

请求校验错误不回显原始输入，避免泄露密码。资格评估 reason_codes 为业务原因，不等同于 HTTP 错误。

## 无模型演示顺序

1. 迁移至 0003，为现有 support、supervisor、auditor 用户分别设置密码。
2. 客服登录并保存 Cookie/CSRF，查询订单与当前政策，调用资格评估。
3. 明确确认：POST action-confirmations，使用返回凭据和新的 Idempotency-Key 创建申请。
4. 主管登录，查看 approvals，批准或拒绝。检查申请状态与审批审计。
5. 审计人员登录，读取退款详情和 audit-events，验证写入被拒绝。
6. 另一租户登录，访问第一租户申请 ID，应得到与不存在相同的 404。
7. 过期演示使用可控时钟自动测试，不直接修改真实演示库时间或通过等待一天测试。
