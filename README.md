# AI 职位搜索与自动投递系统

当前已完成架构设计和第一步工程骨架：配置、FastAPI、JSON 日志、请求追踪与健康检查。尚未实现数据库、网页采集、AI 分析和真实投递。

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
- `/ready` 将随数据库与存储接入实现；当前健康检查只表示 API 进程存活。

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
