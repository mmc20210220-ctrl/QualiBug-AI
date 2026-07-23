# 业务规则清单

本文档为QualiBug提供明确、可追溯的业务约束。规则均为项目业务要求，不是隐藏缺陷答案。

| Rule ID | 类型 | 规则 |
|---|---|---|
| BR-CON-001 | FIELD_INVARIANT | 合同总金额必须大于0 |
| BR-CON-002 | TEMPORAL | 合同开始日期早于结束日期 |
| BR-CON-003 | UNIQUENESS | 同一租户合同编号唯一 |
| BR-CON-004 | PRECONDITION | 合同提交前至少有一个里程碑 |
| BR-CON-005 | CONSERVATION | 里程碑金额之和等于合同金额 |
| BR-CON-006 | STATE_TRANSITION | 只有LEGAL_REVIEW可法务批准为APPROVED |
| BR-CON-007 | STATE_TRANSITION | 只有APPROVED可激活为ACTIVE |
| BR-CON-008 | CAUSAL_POSTCONDITION | 激活合同后预算available减少合同额，reserved增加合同额 |
| BR-CON-009 | CONSERVATION | 预算total=available+reserved+spent |
| BR-CON-010 | COMPENSATION | 取消合同释放未支付预算预留 |
| BR-CON-011 | STATE_TRANSITION | CANCELLED合同不能重新激活 |
| BR-MIL-001 | TEMPORAL | 里程碑到期日在合同周期内 |
| BR-MIL-002 | STATE_TRANSITION | 只有PENDING或REJECTED可提交为SUBMITTED |
| BR-MIL-003 | STATE_TRANSITION | 只有SUBMITTED可验收为ACCEPTED |
| BR-MIL-004 | IDEMPOTENCY | ACCEPTED里程碑重复验收不得生成第二条验收记录 |
| BR-MIL-005 | MONOTONICITY | 验收金额不得超过里程碑金额 |
| BR-INV-001 | UNIQUENESS | 同一供应商发票号唯一 |
| BR-INV-002 | FIELD_INVARIANT | 发票金额和税额不得为负 |
| BR-INV-003 | CONSERVATION | 发票含税金额=未税金额+税额 |
| BR-INV-004 | TEMPORAL | 发票日期不得晚于付款申请日期 |
| BR-PAY-001 | PRECONDITION | 付款必须关联ACTIVE合同 |
| BR-PAY-002 | PRECONDITION | 付款必须关联ACCEPTED里程碑 |
| BR-PAY-003 | LIMIT_CONSTRAINT | 付款金额不超过里程碑剩余可付金额 |
| BR-PAY-004 | LIMIT_CONSTRAINT | 合同累计付款不超过合同总金额 |
| BR-PAY-005 | LIMIT_CONSTRAINT | 发票累计付款不超过发票含税金额 |
| BR-PAY-006 | STATE_TRANSITION | 只有MANAGER_APPROVED可财务批准 |
| BR-PAY-007 | STATE_TRANSITION | 只有FINANCE_APPROVED可执行付款 |
| BR-PAY-008 | IDEMPOTENCY | 同一幂等键重复付款只产生一次资金变化 |
| BR-PAY-009 | CAUSAL_POSTCONDITION | 付款后reserved减少、spent增加、contract.paid增加，三者变化量相等 |
| BR-PAY-010 | CROSS_ENTITY_CONSISTENCY | PAID付款必须存在对应合同、里程碑、发票且租户一致 |
| BR-COM-001 | PRECONDITION | 所有里程碑验收并足额付款后才能完成合同 |
| BR-SEC-001 | TENANT_ISOLATION | 所有业务实体禁止跨租户访问 |
| BR-SEC-002 | DATA_VISIBILITY | vendor不得看到internal_notes和预算信息 |
| BR-SEC-003 | AUTHORIZATION | 只有legal可完成法务批准 |
| BR-SEC-004 | AUTHORIZATION | 只有finance可执行付款 |
| BR-CC-001 | CONCURRENCY | 合同更新version不一致时返回409 |
| BR-CC-002 | CONCURRENCY | 并发合同激活不得导致预算available为负 |
