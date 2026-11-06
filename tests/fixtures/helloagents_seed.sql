-- RefundDesk 演示数据。先执行 helloagents_schema.sql，再整份执行本文件。
-- 每批新建两个独立演示租户。相同 batch_id 再次执行会报错并回滚。
-- 1001 可退款，1002 已发货，1003 未支付，1004 超期。
-- 1005 待审批，1006 已拒绝，1007 已退款，1008 待核实，1009 已批准。
-- 额外生成的订单只扩充查询数据；申请和支付样本数固定。时间以首次执行时刻为准。
-- jobs 包含可领取的模拟任务，仅在开发/测试数据库中使用。
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL search_path=pg_catalog, refunddesk, pg_temp;
DO $seed$
DECLARE
    batch_id CONSTANT text := 'rd-demo-01';
    orders_per_tenant CONSTANT integer := 100;
    demo_tenant text;
    demo_name text;
BEGIN
    IF current_database() <> 'helloagents' OR to_regnamespace('refunddesk') IS NULL THEN
        RAISE EXCEPTION '需要 helloagents 数据库和 refunddesk schema';
    END IF;
    IF orders_per_tenant NOT BETWEEN 9 AND 1000000 OR batch_id !~ '^[a-z0-9-]{1,40}$' THEN
        RAISE EXCEPTION '每租户 9 至 1000000 笔订单；批次名限 1–40 位小写字母、数字、连字符';
    END IF;
    PERFORM pg_advisory_xact_lock(84719,hashtext(batch_id));
    FOR demo_tenant,demo_name IN
        SELECT batch_id||'-'||v.suffix,'演示企业 '||upper(v.suffix)
        FROM (VALUES ('a'),('b')) AS v(suffix)
    LOOP
        IF EXISTS (SELECT 1 FROM refunddesk.tenants WHERE id=demo_tenant) THEN
            RAISE EXCEPTION '租户 % 已存在，请核对批次；本事务回滚',demo_tenant;
        END IF;
        INSERT INTO refunddesk.tenants(id,name) VALUES(demo_tenant,demo_name);
        INSERT INTO refunddesk.users(id,display_name)
            SELECT t.id||'-'||r.role,t.name||' '||r.role FROM (VALUES (demo_tenant, demo_name)) AS t(id,name)
            CROSS JOIN (VALUES ('support'),('supervisor'),('auditor')) r(role);
        INSERT INTO refunddesk.memberships(tenant_id,user_id,role) SELECT t.id,t.id||'-'||r.role,r.role FROM (VALUES (demo_tenant, demo_name)) AS t(id,name)
            CROSS JOIN (VALUES ('support'),('supervisor'),('auditor')) r(role);
        INSERT INTO refunddesk.refund_policies(tenant_id,version,effective_at,rules) SELECT id,'v1',now()-interval '30 days',
            '{"apply_hours":168,"approval_hours":24,"execution_start_minutes":30,"full_refund_only":true,"unshipped_only":true,"supervisor_required":true}'::jsonb FROM (VALUES (demo_tenant)) AS t(id);

        INSERT INTO refunddesk.orders(tenant_id,order_no,amount_minor,currency,paid_at,shipped_at,refund_state) SELECT t.id,'DEMO-'||n,10000 + (n*97)%90000,'CNY',
            CASE WHEN (n=1003 OR (n>1009 AND n%5=0)) THEN NULL WHEN (n=1004 OR (n>1009 AND n%5=1)) THEN now()-interval '8 days' ELSE now()-interval '1 day' END,
            CASE WHEN (n=1002 OR (n>1009 AND n%5=2)) THEN now()-interval '2 hours' END,
            CASE n WHEN 1007 THEN 'refunded' WHEN 1008 THEN 'reserved' ELSE 'none' END
            FROM (VALUES (demo_tenant, demo_name)) AS t(id,name) CROSS JOIN generate_series(1001,1000+orders_per_tenant) n;
        INSERT INTO refunddesk.refund_requests(tenant_id,id,order_no,applicant_id,reason,amount_minor,currency,policy_version,
                                    state,close_reason,created_at,approve_by,start_by)
            SELECT o.tenant_id,'R-'||v.n,o.order_no,o.tenant_id||'-support','重复购买',o.amount_minor,o.currency,'v1',
                   v.state,CASE WHEN v.state='closed' THEN 'REJECTED' END,
                   now()-interval '20 minutes',now()+interval '23 hours 40 minutes',
                   CASE WHEN v.state IN ('succeeded','reconciling','approved') THEN now()+interval '20 minutes' END
            FROM refunddesk.orders o JOIN (VALUES(1005,'pending'),(1006,'closed'),(1007,'succeeded'),(1008,'reconciling'),(1009,'approved'))
                v(n,state) ON o.order_no='DEMO-'||v.n WHERE o.tenant_id=demo_tenant;
        INSERT INTO refunddesk.refund_decisions(tenant_id,request_id,applicant_id,reviewer_id,decision,note,decided_at)
            SELECT tenant_id,id,applicant_id,tenant_id||'-supervisor',CASE WHEN state='closed' THEN 'reject' ELSE 'approve' END,
                   CASE WHEN state='closed' THEN '申请说明不足' ELSE '已核对订单' END,now()-interval '10 minutes'
            FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant AND state<>'pending';
        INSERT INTO refunddesk.refund_operations(tenant_id,id,request_id,payment_key,payload_digest,state,channel_ref,created_at) SELECT tenant_id,'PAY-'||id,id,tenant_id||'-payment-'||id,
            encode(sha256(convert_to(concat_ws('|',tenant_id,id,amount_minor,currency),'UTF8')),'hex'),
            CASE state WHEN 'succeeded' THEN 'succeeded' ELSE 'unknown' END,
            CASE WHEN state='succeeded' THEN tenant_id||'-mock-'||id END,now()-interval '9 minutes'
            FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant AND state IN ('succeeded','reconciling');
        INSERT INTO refunddesk.jobs(tenant_id,id,request_id,kind,state,run_at,attempts,last_error)
            SELECT tenant_id,'EXEC-'||id,id,'execute',CASE state WHEN 'approved' THEN 'ready' ELSE 'done' END,
                   now()-interval '9 minutes',CASE state WHEN 'approved' THEN 0 ELSE 1 END,NULL
            FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant AND state IN ('approved','succeeded','reconciling');
        INSERT INTO refunddesk.jobs(tenant_id,id,request_id,kind,state,run_at,attempts,last_error)
            SELECT tenant_id,'CHECK-'||id,id,'reconcile','ready',now(),0,'PAYMENT_RESPONSE_TIMEOUT'
            FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant AND state='reconciling';
        INSERT INTO refunddesk.idempotency_records(tenant_id,user_id,action,key,payload_digest,request_id) SELECT tenant_id,applicant_id,'create_refund_request','submit-'||id,
            encode(sha256(convert_to(concat_ws('|',tenant_id,order_no,reason,amount_minor,currency),'UTF8')),'hex'),id
            FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant;
        INSERT INTO refunddesk.action_confirmations(tenant_id,id,user_id,action,payload_digest,expires_at,consumed_at) SELECT tenant_id,'C-'||request_id,user_id,action,payload_digest,
            now()-interval '15 minutes',now()-interval '20 minutes' FROM refunddesk.idempotency_records WHERE tenant_id=demo_tenant;
        INSERT INTO refunddesk.agent_runs(tenant_id,id,user_id,trace_id,started_at,state) SELECT tenant_id,'RUN-'||id,applicant_id,tenant_id||'-trace-'||id,
            created_at-interval '1 minute','completed' FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant;
        INSERT INTO refunddesk.agent_steps(tenant_id,run_id,seq,tool,result_summary) SELECT tenant_id,'RUN-'||id,1,'create_refund_request',
            jsonb_build_object('request_id',id,'state_at_submission','pending') FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant;
        INSERT INTO refunddesk.audit_events(tenant_id,id,occurred_at,actor,request_id,action,details,trace_id) SELECT tenant_id,'SUB-'||id,created_at,applicant_id,id,'submitted',
            jsonb_build_object('state','pending'),tenant_id||'-trace-'||id FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant;
        INSERT INTO refunddesk.audit_events(tenant_id,id,occurred_at,actor,request_id,action,details,trace_id) SELECT tenant_id,'DEC-'||request_id,decided_at,reviewer_id,request_id,decision,
            jsonb_build_object('note',note),tenant_id||'-trace-'||request_id FROM refunddesk.refund_decisions WHERE tenant_id=demo_tenant;
        INSERT INTO refunddesk.audit_events(tenant_id,id,occurred_at,actor,request_id,action,details,trace_id) SELECT tenant_id,'EXEC-'||id,created_at,'system:worker',request_id,'payment_started',
            jsonb_build_object('operation_id',id),tenant_id||'-trace-'||request_id FROM refunddesk.refund_operations WHERE tenant_id=demo_tenant;
        INSERT INTO refunddesk.audit_events(tenant_id,id,occurred_at,actor,request_id,action,details,trace_id) SELECT tenant_id,'RES-'||id,created_at+interval '1 minute','system:worker',request_id,'payment_result',
            jsonb_build_object('payment_state',state),tenant_id||'-trace-'||request_id FROM refunddesk.refund_operations WHERE tenant_id=demo_tenant;


        IF (SELECT count(*) FROM refunddesk.orders WHERE tenant_id=demo_tenant) <> orders_per_tenant
        OR (SELECT count(*) FROM refunddesk.refund_requests WHERE tenant_id=demo_tenant) <> 5
        OR (SELECT count(*) FROM refunddesk.audit_events WHERE tenant_id=demo_tenant) <> 13 THEN
            RAISE EXCEPTION '批次校验失败 %',demo_tenant;
        END IF;
        RAISE NOTICE '租户 %：% 笔订单，5 笔退款申请，13 条审计记录', demo_tenant, orders_per_tenant;
    END LOOP;
END;
$seed$;
COMMIT;
