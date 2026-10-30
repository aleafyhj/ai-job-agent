# AI 职位搜索与自动投递系统

BOSS直聘采用截图/真实鼠标模式（千问视觉），默认只读。见 [桌面执行器使用说明](docs/06-boss-desktop.md)。独立鼠标服务已在本机通过项目命令实测移动和卡片点击，右侧职位切换校验成功；完整自动匹配与沟通流程仍待验证。

新增 inspect / click-card 命令，自动识别卡片序号并生成坐标预览图；run 可把职位正文、截图证据和评分保存到 PostgreSQL，通过 /jobs 和 /analyses 接口查询。当前未完成真实自动沟通/简历上传的端到端验证。

当前已完成架构设计、服务骨架、PostgreSQL 数据库层及第三步档案/简历/投递策略 API。可以录入真实技能、上传和下载简历、设置默认简历及维护匹配策略。BOSS直聘网页采集、AI 分析和真实投递尚未实现。

目标：自动搜索 AI 相关职位，获取职位详情，分析与候选人技术栈的匹配度，并按配置自动投递真实简历。

## 设计文档

1. [需求与验收标准](docs/01-requirements.md)
2. [技术栈与系统架构](docs/02-architecture.md)
3. [HTTP 与内部接口文档](docs/03-api.md)
4. [数据库设计](docs/04-database.md)
5. [实施顺序与待确认事项](docs/05-roadmap.md)

默认规则：匹配度达到 70%（含 70）且满足硬性条件才进入投递；支持切换为严格大于 70。技术栈指候选人真实掌握的技术及证据，不等同于本项目使用的开发技术栈。

首期面向单个候选人、本机部署，招聘平台已确定为 **BOSS直聘**，结构上支持后续增加平台。真实技能、简历、AI 服务商需要在对应实现阶段确定；BOSS直聘的采集与投递尚未实现。

## 本地运行（PowerShell）

需要 Python 3.12 或 3.13 和 uv。`uv.lock` 锁定完整依赖版本，应与 pyproject.toml 一起提交。不要使用本机默认的 Python 3.9。

```powershell
uv sync --locked --python 3.12
Copy-Item .env.example .env
uv run --locked python -m app
```

已有 `.env` 时不要覆盖，按需修改配置。进程默认监听 `127.0.0.1:8000`，使用 Ctrl+C 停止。

- 健康检查：[GET /api/v1/health](http://127.0.0.1:8000/api/v1/health)，返回 `{"status":"ok"}`。
- 接口文档：[Swagger UI](http://127.0.0.1:8000/docs)。
- 每个请求返回 `X-Request-ID`，错误响应包含同一 request_id。
- [GET /api/v1/ready](http://127.0.0.1:8000/api/v1/ready) 检查数据库连接、迁移版本及简历存储目录可写性；未配置或未就绪返回 503。

配置读取优先级：显式初始化参数 > 系统环境变量 > 项目根目录 `.env` > 默认值。变量均带 `JOB_AGENT_` 前缀，见 [.env.example](.env.example)。端口、日志级别和环境名启动时校验，非法配置拒绝启动。第一步仅允许本机监听。

日志以 JSON 输出至标准输出，包含 UTC 时间、日志级别、请求 ID、路由模板、状态码与耗时，不记录请求体、查询参数或认证头。通过 `python -m app` 启动可统一应用和 Uvicorn 日志配置。

```powershell
uv run --locked pytest -q
uv run --locked ruff check app tests
uv run --locked ruff format --check app tests
```

本次工作环境已创建 `.venv`，无需激活即可使用：

```powershell
.\.venv\Scripts\python.exe -m app
.\.venv\Scripts\python.exe -m pytest -q
```

依赖管理依据：[uv 锁文件](https://docs.astral.sh/uv/concepts/projects/layout/)、[FastAPI 配置管理](https://fastapi.tiangolo.com/advanced/settings/)。

## PostgreSQL 数据库（第二步）

本机开发库已于 2026-10-29 接通：`127.0.0.1:5432/ai_job_agent`，应用使用同名专用非管理员账号，迁移版本为 `0001`，API 就绪检查通过。真实连接凭据仅保存在被 Git 忽略的 `.env`；以下初始化说明用于新环境，不需在本机重复创建账号和数据库。

需要运行中的 PostgreSQL（本次在 PostgreSQL 18 上验证）。先由数据库管理员创建专用数据库和用户，例如在 psql 中执行：

```sql
CREATE ROLE ai_job_agent LOGIN PASSWORD '替换为你的强密码';
CREATE DATABASE ai_job_agent OWNER ai_job_agent;
```

在本地 `.env` 增加以下配置，替换用户名、密码、地址、端口；密码含 `@`、`:` 等字符时需 URL 编码。真实凭据不要提交到 Git。

```dotenv
JOB_AGENT_DATABASE_URL=postgresql+psycopg://ai_job_agent:YOUR_PASSWORD@127.0.0.1:5432/ai_job_agent
```

```powershell
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked alembic current
uv run --locked alembic check
uv run --locked python -m app
```

已有 `.venv` 时，可用 `.\.venv\Scripts\python.exe -m alembic upgrade head` 执行迁移。启动服务不会自动建表或迁移；变更通过 Alembic 显式执行。

数据库相关代码集中在：

- `app/models.py`：SQLAlchemy Core 表模型、外键、唯一约束、检查约束和索引。
- `app/database.py`：连接池和 Session 工厂，连接延迟建立，参数隐藏。
- `app/transactions.py`：申请去重、版本校验、跨账户额度预留和任务创建的原子操作；任务领取使用 SKIP LOCKED，续租校验 owner、次数和有效期。
- `migrations/`：冻结的初始迁移；包含版本不可修改、审计追加写入、申请关联一致性触发器。后续改表需新增迁移，不修改已执行的初始迁移。

事务函数仅供后续业务服务调用，必须置于 `with session.begin():` 中；没有启动 worker，也不会执行投递。过期 running 任务不直接重新领取，专用恢复流程留到第六步实现。额度消费/释放与提交状态流转也随执行流程后续接入。

PostgreSQL 集成测试：

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/ai_job_agent_test'
uv run --locked pytest -q
```

请使用专用测试库，用户需要创建 schema 的权限。每项测试创建随机 `test_<uuid>` schema，结束时仅清理该 schema；未设置变量时跳过数据库集成测试。测试验证迁移往返、模型一致性、版本归属/不可变性、申请去重、事务回滚、跨账户并发额度、任务租约和数据库就绪检查。`TEST_DATABASE_URL` 需通过系统环境变量传入，不从 `.env` 自动加载。

初始迁移不依赖当前模型执行 `create_all()`，后续修改模型不会改变历史迁移。实现参考：[SQLAlchemy 行锁](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update)、[Alembic 离线 SQL](https://alembic.sqlalchemy.org/en/latest/offline.html)。

## 录入档案、简历和策略（第三步）

本机 `.env` 已生成 `JOB_AGENT_API_TOKEN`，请保留该值。新环境需自行生成至少 32 字符的随机令牌，填写方法见 `.env.example`；没有令牌时业务接口返回 503。令牌、简历文件及数据库密码不提交到 Git。

启动服务后，打开 `/docs`，点击 **Authorize**，填写 `.env` 中令牌的值（不需要额外输入 Bearer），再按以下顺序操作：

1. `POST /api/v1/profiles` 创建候选人档案。只填写真实技能与证据，缺失信息保持空值。
2. `POST /api/v1/profiles/{profile_id}/resumes` 选择 PDF/DOCX 上传。响应中返回 resume id 与 artifact id。
3. `PUT /api/v1/profiles/{profile_id}/default-resume` 用 resume id 指定默认简历。上传不会自动更换默认简历。
4. `POST /api/v1/policies` 创建策略。默认 BOSS直聘（平台标识 `boss`）、匹配度 ≥70、每日 20 份、自动投递关闭；本阶段没有投递执行器，开启该字段也不会触发真实投递。

档案请求示例（示意内容需替换为真实情况）：

```json
{
  "name": "你的姓名",
  "skills": [
    {
      "canonical_name": "Python",
      "level": "proficient",
      "evidence": "填写你实际完成的项目与承担的工作",
      "years": "2.00"
    }
  ],
  "experience_years": "2.00",
  "education": {"degree": "本科"},
  "preferred_cities": ["上海"],
  "remote_preference": "any",
  "facts": {}
}
```

`GET /profiles`、`GET /policies`、`GET /profiles/{id}/resumes` 提供 cursor 分页。GET 档案/策略会返回 `ETag`；更新用完整请求体并携带 `If-Match`（如 `"1"`），冲突返回 409。每次修改追加不可变版本，已有分析使用的旧版本保持不变。分数与年限等 Decimal 字段以十进制字符串返回。

简历最多 10 MiB，检查扩展名、MIME 和内部结构；PDF 需非加密且为 1–100 页，DOCX 检查包结构并限制解压总大小。整个写请求不能超过 12 MiB。文件按随机名称保存到 `artifacts/resumes/`，不直接使用上传路径；原文件名只作为展示名称。数据库写入失败时回滚记录并清理文件；进程突然退出仍可能遗留未被引用的文件，后续清理流程需按数据库引用核对。

`GET /api/v1/artifacts/{artifact_id}/content` 可鉴权下载简历；不提供直接公开文件路径。文件存储目录可通过 `JOB_AGENT_ARTIFACTS_DIR` 设置，备份数据库时一并备份该目录。

新增代码主要是 `app/schemas.py`（数据契约）、`app/api/resources.py`（业务接口）、`app/api/dependencies.py`（鉴权和会话）、`app/storage.py`（文件校验与存储）。本阶段无数据库结构变更，仍使用迁移 0001。

实现参考：[FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)、[pypdf PDF 读取](https://pypdf.readthedocs.io/en/3.15.3/modules/PdfReader.html)。

## Git 版本控制

使用 `main` 保存稳定版本，每个后续阶段在独立分支开发，例如：

```powershell
git switch -c feat/database
git status
git diff
git add app tests docs pyproject.toml uv.lock
git diff --cached
git commit -m "feat: add database models and migrations"
```

提交前检查暂存区，确保没有凭据或个人简历。`.gitignore` 已排除环境文件、虚拟环境、工具、缓存、测试临时目录、日志和运行产物，保留 `.env.example` 和依赖锁文件。`.gitattributes` 统一文本换行规则。

查看历史用 `git log --oneline`。目前仅使用本地仓库，远程仓库在确定托管地址后再配置。
