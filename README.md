# AI 职位搜索与自动投递系统

当前已完成架构设计、第一步工程骨架及第二步 PostgreSQL 数据库层：22 张表、Alembic 迁移、事务与任务租约基础操作。尚未实现档案等业务 API、网页采集、AI 分析和真实投递。

目标：自动搜索 AI 相关职位，获取职位详情，分析与候选人技术栈的匹配度，并按配置自动投递真实简历。

## 设计文档

1. [需求与验收标准](docs/01-requirements.md)
2. [技术栈与系统架构](docs/02-architecture.md)
3. [HTTP 与内部接口文档](docs/03-api.md)
4. [数据库设计](docs/04-database.md)
5. [实施顺序与待确认事项](docs/05-roadmap.md)

默认规则：匹配度达到 70%（含 70）且满足硬性条件才进入投递；支持切换为严格大于 70。技术栈指候选人真实掌握的技术及证据，不等同于本项目使用的开发技术栈。

首期面向单个候选人、本机部署、一个招聘平台，结构上支持后续增加平台。平台、真实技能、简历、AI 服务商需要在对应实现阶段确定。

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
- [GET /api/v1/ready](http://127.0.0.1:8000/api/v1/ready) 检查数据库连接和迁移版本；未配置或未就绪返回 503。存储将在接入后追加检查。

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
