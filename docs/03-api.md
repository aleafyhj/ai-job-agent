# 接口契约 v1

第三阶段已实现：health/ready；profiles 的创建、列表、详情、更新；简历上传、列表、指定默认简历与鉴权下载；policies 的创建、列表、详情、更新。下面表格中的其他业务接口仍为后续设计，不代表已经可调用。当前以 `/docs` 中生成的 OpenAPI 为实际已实现接口清单。

2026-10-30 增量：新增 GET /jobs（当前支持 cursor/limit/platform_key/status）、GET /jobs/{id}（含最新正文快照）、GET /analyses/{id}（含 is_current）。过期分析的 eligible 返回 false。GET /artifacts/{id}/content 同时支持鉴权下载职位截图。桌面 run 负责采集和落库；POST 分析、任务调度以及候选人分数筛选接口尚未开放。

所有业务接口使用本地配置的 JOB_AGENT_API_TOKEN；缺少配置返回 503，缺少/错误令牌返回 401。就绪检查现包含数据库及本地存储。

## 1. 通用约定

基础路径 `/api/v1`，JSON UTF-8；ID 为 UUID；时间为 RFC3339 UTC；金额为十进制字符串，携带币种和计薪周期。所有接口需 `Authorization: Bearer <token>`，健康检查除外。首期单用户，不接收可伪造的 owner_id。

创建资源返回 201；后台操作返回 202 和 task_id；查询/更新返回 200；删除返回 204。列表采用 `cursor`（不透明字符串）和 `limit`（1–100，默认 20），按 `(created_at,id)` 降序，响应 `items,next_cursor`。

错误格式：`{"error":{"code":"SESSION_EXPIRED","message":"平台登录已失效","details":{}},"request_id":"uuid"}`。

400 非法组合；401 未认证；404 不存在；409 幂等冲突/状态冲突；413 文件过大；422 字段校验失败；429 额度/限流；503 依赖不可用。后台错误存到任务，不通过已返回的 HTTP 请求继续反馈。

创建 run、分析和申请接口必须带 `Idempotency-Key`（最长 128 字符）。相同接口与 key、相同请求体返回首次响应；不同请求体返回 409。记录保留 7 天；业务唯一约束永久独立生效。配置更新带 `If-Match: <version>`，过期返回 409。

## 2. 端点清单

| 方法/路径 | 请求 | 响应/说明 |
|---|---|---|
| GET `/health` | 无 | 200 `status`，仅进程存活 |
| GET `/ready` | 无 | 200/503，数据库与存储状态，不暴露凭据 |
| POST `/profiles` | ProfileWrite | 201 Profile，初始版本 1 |
| GET `/profiles` | cursor,limit | 档案分页列表 |
| GET `/profiles/{id}` | 无 | Profile，含当前版本 |
| PUT `/profiles/{id}` | ProfileWrite，If-Match | 200，新建不可变版本并切换当前版本 |
| POST `/profiles/{id}/resumes` | multipart `file` | 201 Resume；PDF/DOCX，最大 10MB，验证内容类型 |
| GET `/profiles/{id}/resumes` | 分页 | Resume 列表，不直接暴露磁盘路径 |
| PUT `/profiles/{id}/default-resume` | `resume_id` | 200，必须属于该档案 |
| GET `/platforms` | 无 | 适配器能力、是否支持搜索/投递/结果核查 |
| POST `/platform-accounts` | `platform_key,label` | 201 Account，不收明文平台密码 |
| POST `/platform-accounts/{id}/login` | `{}` | 202 task_id，本机打开交互登录窗口 |
| POST `/platform-accounts/{id}/verify-session` | `{}` | 202 task_id，检测会话 |
| GET `/platform-accounts/{id}` | 无 | 会话状态，不返回 Cookie |
| POST `/search-plans` | SearchPlanWrite | 201 Plan |
| GET `/search-plans` | 分页 | Plan 列表 |
| PUT `/search-plans/{id}` | SearchPlanWrite，If-Match | 200，新版本仅影响后续 run |
| POST `/search-plans/{id}/runs` | `mode` | 202 `run_id,task_id` |
| GET `/runs/{id}` | 无 | 状态、分阶段计数、异常、配置版本 |
| POST `/runs/{id}/pause` | `{}` | 200，进入 pausing/paused |
| POST `/runs/{id}/resume` | `{}` | 200；无效会话仍返回 409 |
| POST `/runs/{id}/cancel` | `{}` | 200，进入 cancelling/cancelled |
| GET `/jobs` | `platform_key,min_score,status,run_id`及分页 | 职位列表；分数取绑定档案的最新有效分析，不能混用候选人 |
| GET `/jobs/{id}` | 无 | 职位、最新快照、来源和有效状态 |
| POST `/jobs/{id}/analyses` | `profile_id,job_snapshot_id,policy_id` | 202 `analysis_id,task_id`，固定当前档案/规则版本 |
| GET `/analyses/{id}` | 无 | Analysis，含逐项要求与评分 |
| POST `/policies` | PolicyWrite | 201 Policy |
| GET `/policies` | cursor,limit | 策略分页列表 |
| GET `/policies/{id}` | 无 | Policy |
| PUT `/policies/{id}` | PolicyWrite，If-Match | 200，新规则版本 |
| POST `/applications` | `analysis_id,resume_id,platform_account_id` | 202 `application_id,task_id`；同样执行守卫，不能强制绕过阈值 |
| GET `/applications` | `status,job_id`及分页 | Application 列表 |
| GET `/applications/{id}` | 无 | 状态、attempts 和确认凭据引用 |
| POST `/applications/{id}/inputs` | `answers` | 200；仅接受已声明缺失字段及其真实答案，再排队校验 |
| POST `/applications/{id}/reconcile` | `{}` | 202 task_id，仅 submitting/unknown 可用 |
| POST `/applications/{id}/retry` | `{}` | 202，仅明确未提交的 failed 可重试 |
| GET `/tasks/{id}` | 无 | Task，进度/错误/重试次数/时间 |
| GET `/artifacts/{id}/content` | 无 | 鉴权下载或查看证据；禁止通过路径穿越读取文件 |

暂停和取消重复调用幂等；非法状态迁移返回 409。`inputs` 不允许改变职位要求、替换证据或自动生成虚构经历。

## 3. 主要 DTO

ProfileWrite：`name`（1–100 字）、`skills[]`（`canonical_name,level,evidence,years`，level 为 basic/proficient/expert；years ≥0）、`experience_years`、`education`、`preferred_cities[]`、`remote_preference`（any/required/onsite）、`facts`（允许填写表单的真实信息）。缺失事实保持 null。Profile 响应增加 `id,version,default_resume_id,created_at`。

已实现的字段细则：skills 最多 200 项，名称忽略大小写不可重复；evidence 为非空字符串；years/experience_years 为 0–100、最多两位小数。education/facts 为字符串键及字符串或 null 值的对象。未知请求字段拒绝，返回 422。Profile 响应额外包含 version_id；更新要求 If-Match，支持 `1` 或 `"1"`，读取和更新返回 ETag。PUT 使用完整替换语义，省略的可选字段恢复默认值。

SearchPlanWrite：`name,profile_id,platform_account_ids[],keywords[],cities[],remote,min_salary,max_salary,currency,salary_period,employment_types[],max_pages,max_jobs,policy_id,schedule,enabled`。keywords 至少一项；max_pages 1–20，max_jobs 1–200；salary_period 为 month/year/hour；schedule 为 null 或 `{"cron":"0 9 * * *","timezone":"Asia/Shanghai"}`。cron 使用五段格式，平台数量首期限制 1。

PolicyWrite：`name,threshold`（0–100，默认 70）、`comparison`（gte/gt）、`daily_limit`（1–100，默认 20）、`timezone`（默认 Asia/Shanghai）、`auto_apply`（默认 false，启用后符合条件即投递）、`blocked_companies[]`、`allowed_platforms[]`。评分权重和技能等价规则作为受控版本管理，首期不开放任意脚本规则接口。

当前 allowed_platforms 只接受 boss（BOSS直聘），默认 ["boss"]；timezone 必须是有效 IANA 时区。threshold 支持最多六位小数，响应为十进制字符串；Policy 响应含 id/version/version_id/created_at。本阶段仅保存策略，不执行投递。策略更新同样使用 If-Match/ETag。

Run mode：`collect_only` / `analyze_only` / `auto_apply`。analyze_only 包含搜索采集；auto_apply 只有策略 auto_apply=true 才接受，否则 409。任务创建时冻结配置版本。

Resume：`id,profile_id,version,filename,mime_type,size,sha256,created_at`。

当前 Resume 还返回 artifact_id，用于 `/artifacts/{id}/content` 下载。上传接受 application/pdf 或 application/vnd.openxmlformats-officedocument.wordprocessingml.document，扩展名须对应；最大 10 MiB。拒绝损坏/加密 PDF、空文件、超过 100 页的 PDF、无效 DOCX 包和宏内容；不提取简历文本、不执行文档中的内容。整个写请求限制 12 MiB；文件不合格返回 422，超限返回 413。指定其他档案的默认简历返回 409。上传和选择默认简历不修改能力档案版本号。

Task：`id,type,status,run_id,progress:{stage,completed,total},attempt_count,error:{code,message,retryable},created_at,started_at,finished_at`。未知 total 为 null，不伪造进度百分比。

Analysis 示例：

```json
{
  "id": "uuid",
  "job_snapshot_id": "uuid",
  "profile_version_id": "uuid",
  "policy_version_id": "uuid",
  "status": "ready",
  "score": 71.428571,
  "threshold": 70,
  "comparison": "gte",
  "hard_constraints": "pass",
  "eligible": true,
  "requirements": [
    {
      "text": "熟悉 Python",
      "source_quote": "熟悉 Python，具备接口开发经验",
      "importance": "required",
      "weight": 3,
      "coverage": 1,
      "evidence_refs": ["skills.python"],
      "reason": "档案包含 Python 项目证据"
    }
  ],
  "missing_skills": ["Kubernetes"],
  "uncertainties": [],
  "scoring_rule_version": "v1"
}
```

上例 requirements 为节选，总分由完整要求集计算。eligible 仅表示分析结论，不替代投递时的额度、会话、去重等实时检查。

Application：`id,job_id,profile_id,analysis_id,resume_id,platform_account_id,status,blocked_reason,required_inputs[],submitted_at,confirmation_ref,artifact_ids[],created_at`。

## 4. 内部接口（Python Protocol 设计，无实现）

| 接口 | 输入 → 输出 | 约束 |
|---|---|---|
| PlatformAdapter.search | SearchQuery, Session → AsyncIterator[JobRef] | 分页、限流、去重标识 |
| PlatformAdapter.fetch_detail | JobRef, Session → RawJob | 返回原文、来源、内容散列 |
| PlatformAdapter.prepare_application | JobSnapshot, ProfileVersion, Resume → FormPlan | 提取必填项，不提交 |
| PlatformAdapter.submit | FormPlan, AttemptContext → SubmitResult | 必须已持久化 attempt；结果 confirmed/not_submitted/unknown |
| PlatformAdapter.reconcile | Application → ReconcileResult | confirmed/not_submitted/unknown，附证据 |
| BrowserDriver | navigate/click/fill/press/scroll/upload/snapshot | 定位器优先；超时分类，不自动重放 submit |
| DesktopInputDriver | click/type/press/screenshot | 独占桌面锁，分辨率/焦点检查 |
| LLMProvider.extract_requirements | RawJob → RequirementSet | JSON schema 校验、引用必须能定位原文 |
| LLMProvider.match | RequirementSet, ProfileVersion → MatchEvidence | 不返回有执行权的动作 |
| ScoringService.evaluate | MatchEvidence, RuleVersion → ScoreResult | 纯计算、可重放 |
| Storage | put/get/delete → ArtifactRef | 文件引用不能暴露明文密钥或绝对路径 |

AI 调用记录输入散列、prompt/model/schema 版本和输出；格式错误最多修复 1 次，仍失败则分析 failed，不进入自动投递。
