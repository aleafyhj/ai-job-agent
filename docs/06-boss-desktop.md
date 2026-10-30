# BOSS直聘：截图与真实鼠标模式

## 独立鼠标服务（本机已验证）

2026-10-30：项目虚拟环境通过本机独立 Python 3.9 鼠标服务执行输入，截图、千问分析和数据库操作继续使用原项目虚拟环境。已通过项目 `move --x 1100 --y 700` 和 `click-card --card-index 1` 实测：鼠标坐标核对成功，第一张卡片被点击，右侧切换为“AI智能体开发人员 / 4–7K”，程序确认职位身份一致。没有执行沟通或上传简历。

重启服务后再次验证移动成功，并通过项目 `scroll --x 800 --y 900 --ticks 1` 确认页面实际向下滚动，保存 `scroll-before.png` / `scroll-after.png`。62 项回归测试通过（包含隔离 PostgreSQL 测试），Ruff 检查通过。

当前机器的 `.env` 已设置 `JOB_AGENT_MOUSE_PYTHON=D:/app/Anaconda3/python.exe`。每次电脑重启后，先在项目目录启动服务一次（重复运行会复用存活服务）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-mouse.ps1
# 原项目虚拟环境照常使用，不需要切换到 Python 3.9
python -m app.boss_desktop move --x 1100 --y 700
python -m app.boss_desktop click-card --card-index 1
```

启动脚本默认使用本机 `D:\app\Anaconda3\python.exe`，可用 `-MousePython` 指定另一条已验证路径，并保持 `.env` 配置一致。服务通过本机回环端口接收带本地随机密钥的 JSON 请求；端口、PID、密钥和启动错误日志位于被 Git 忽略的 `artifacts/boss-desktop`。服务不需要千问密钥或数据库凭据。`scripts/windows_mouse_bridge.py --status` 只检查服务，不操作鼠标。关闭服务可按 `mouse-service.json` 的 PID 核对命令行后停止该工作进程。

服务拒绝前台窗口不符、位置变化、屏幕角落和无效滚轮参数；通信中断或结果未知时不自动重试。主程序仍保留截图变化与职位身份校验。启动脚本须独立运行，不能用每次请求创建短命子进程替代本机已验证的服务方式。此前失败的确切系统原因尚未确定，不能据此断言所有 Python 3.12 都无法控制鼠标。

## 命令与流程

已按新的需求增加独立命令行执行器，绕开 DOM 抓取这一实现路径。鼠标操作不能保证不触发平台风控；验证码、登录异常和弹窗不自动处理。用户先手动登录并打开职位搜索结果，将浏览器放到 Windows 主屏幕前台。

代码：app/desktop.py（截图、鼠标、滚轮、DPI/窗口校验），app/vision.py（千问视觉、结构化结果和评分），app/boss_desktop.py（循环与本地记录）。没有通过截图自动上传简历，也没有自动填写聊天内容。

## 模型配置

默认 qwen3-vl-plus，国内地址 https://dashscope.aliyuncs.com/compatible-mode/v1，自动读取已有系统环境变量 DASHSCOPE_API_KEY；也可使用 JOB_AGENT_VISION_API_KEY 覆盖。区域或模型不同可在 .env 设置 JOB_AGENT_VISION_BASE_URL / JOB_AGENT_VISION_MODEL。本机已用用户提供的截图真实调用成功，识别出 5 张卡片，正确标记详情未完整显示。未向模型上传简历、联系方式或数据库密码；截图本身仍可能包含网页个人信息。

## 使用

以下命令启动后有 5 秒切换窗口时间。窗口标题默认包含 BOSS直聘，可用 --title 指定更准确的标题片段。不要在执行期间移动/缩放窗口或操作其他应用。

```powershell
# 截取当前主屏幕，输出 artifacts/boss-desktop/screen.png
.\.venv\Scripts\python.exe -m app.boss_desktop screenshot

# 不调用模型、不点击：单独核对鼠标能否移动到原始屏幕像素坐标
python -m app.boss_desktop move --x 553 --y 716

# 只识别当前截图并标注卡片序号/像素坐标，不点击，不需要档案ID
python -m app.boss_desktop inspect

# 离线识别已有截图：不会创建桌面控制器、不会等待切换窗口
python -m app.boss_desktop inspect --image artifacts/boss-desktop/click-before.png

# 自动识别当前卡片，点击本次截图中的第2张，并检查右侧详情身份
# 这不是运行之前 inspect 的旧坐标；会重新截图、重新识别
python -m app.boss_desktop click-card --card-index 2

# 原始屏幕像素坐标点击，或在该位置滚轮向下滚动3刻度
.\.venv\Scripts\python.exe -m app.boss_desktop click --x 200 --y 300
.\.venv\Scripts\python.exe -m app.boss_desktop scroll --x 200 --y 500 --ticks 3

# 先通过 API 创建真实能力档案和策略，将下面 ID 替换为实际 UUID
# 默认只读：点击卡片、采集截图、分析，不点击沟通
.\.venv\Scripts\python.exe -m app.boss_desktop run --profile-id PROFILE_UUID --policy-id POLICY_UUID --pages 3 --jobs 20
```

原始 click 命令直接点击给定坐标，执行前自行确认目标；run 则使用模型识别坐标。模型坐标在原图上归一化为 0..1000，程序按实际截图宽高换算；不会把附件裁剪图坐标直接用到屏幕上。点击前重新截屏，图像变化则旧坐标作废并停止。动态广告/动画可能导致此项检查停止，需要保持页面稳定。

卡片导航现改为：核对同一窗口句柄、窗口边界和屏幕尺寸，再严格比较目标标题附近 440×64 像素区域，忽略区域外时钟/广告等变化。目标区域变化或窗口移动仍停止；鼠标移动后再次核对窗口。原始坐标 click 和“立即沟通”仍使用整屏严格检查，本次仅调整可逆的职位切换操作。校验失败时保存 guard-current.png，便于与 screen.png 对比；此前旧版本没有保存失败时的新截图，无法据旧报告精确判断是哪部分发生了变化。

手动测试结果：screenshot 保存 screen.png；click 保存 click-before.png / click-after.png；scroll 保存 scroll-before.png / scroll-after.png，终端打印实际绝对路径（同名文件覆盖）。操作后窗口改变时可能只保留 before 图，并明确提示人工核查。reference-analysis.json 是开发时对用户附件调用千问的识别结果，不是本机实时截图或鼠标操作记录。

左右滚动点默认由千问从当前全屏截图识别；无法识别时停止。可用 --left-scroll 200 500 / --right-scroll 750 800 显式覆盖，这些只是归一化坐标示例，不是不同窗口布局通用值。每次只点一张卡，重新截图确认右侧身份，向上滚动并核对正文顶部，向下滚动读取详情，读完整后合并文本再评估。每页详情都检查职位身份；超过 --detail-scrolls（默认5）仍未读完整则不判定可沟通。遇到识别不确定会停止，不跳过关键核验。

## 识别与点击报告

每次列表/详情/全文评估调用千问时，独立保存到 observations/<时间戳-随机ID>/：screen.png（实际输入原图）、targets.png（卡片序号与像素坐标标记）、report.json（图像尺寸、散列、模型结构化结果、坐标换算或失败类型）。模型调用失败也保留输入及失败记录，不保存密钥或服务商错误原文。点击后另存 action.json，记录点击目标与 input_executed；它只证明调用输入，不等于职位/沟通已经成功。

inspect 默认不点击；click-card 在点击后再次调用千问检查右侧身份，只有核对一致才打印确认。遇窗口变化、内容加载未完成或身份无法核对时停止，不自动重复点击。

卡片点击采用两阶段定位：全屏识别卡片后，裁剪附近区域，单独识别标题文字链接，不直接点击卡片中心或学历/经验标签。保存 title-crop.png、title-target.png 和 title-target.json，后者记录裁剪边界及回算后的全屏像素。移动鼠标后读取实际位置核对，再点击。详情身份检查规范化全角括号、空格和地点分隔符；仅一侧缺少行政区时允许城市级匹配，不允许不同明确行政区、标题或公司不一致。

鼠标位置异常排查：先执行 move，它不调用千问、不会点击。启动后5秒切到 BOSS 窗口并保持前台，期间不要操作鼠标。失败时终端输出目标、实际位置及 Windows 物理坐标，pointer.json 保存在 boss-desktop 根目录（自动点击时保存在本次观察目录）。日志只包含坐标/屏幕尺寸/线程DPI状态，不含凭据。代码在导入输入库后显式设置当前线程为 Per-Monitor V2，减少宿主已有DPI配置带来的混用；设置是否成功会写入诊断。坐标不一致仍然停止，不能通过忽略校验或盲猜缩放倍数解决。

Windows参考：[线程级DPI感知](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext)。

当前 app/windows_input.py 使用 SetPhysicalCursorPos 直接设置屏幕物理像素，再通过 GetPhysicalCursorPos 核对；不再让生产流程依赖 SendInput 绝对坐标换算。pointer.json 的 input_event 包含 backend/target/before/api_success/win32_error/samples，可以区分 API 拒绝、鼠标始终不动和移动后又偏离；不通过多次输入自动纠偏。按钮与滚轮仍使用标准 SendInput，检查接受事件数量，不盲目重试。截图和角落停止检查保留 PyAutoGUI。移动/事件插入成功不代表网页业务成功，后续仍检查职位身份和回执。代码不改变系统权限、安全设置或鼠标限制范围，真实用户环境仍需先用 move 确认。

参考：[SendInput 返回值与限制](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)、[绝对鼠标坐标](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput)。

物理位置接口：[SetPhysicalCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setphysicalcursorpos)。此前本机只读检查确认主屏/虚拟桌面均为 2560×1600，光标限制范围覆盖整屏；仅凭这一事实与 SendInput accepted=1，仍不能证明输入实际生效。

### 运行环境隔离排查

`scripts/mouse_doctor.py` 只用标准库，兼容 Python 3.9+，不依赖项目和 PyAutoGUI。默认只读，报告 Python 来源、Windows 会话、窗口站及输入桌面访问状态；不会读取密钥或修改权限。`--probe` 为显式单次移动测试：等待5秒、核对 BOSS 前台窗口后才调用物理位置 API，记录 before/api_success/win32_error/after，不点击、不重试、不调用模型。

本机项目虚拟环境基于 Codex 内置 Python 3.12；另有原有 Anaconda Python 3.9。两者只读检查均显示 Session 1、WinSta0/Default、输入桌面可访问及 2560×1600。尚不能由此确认失败原因。可在用户自己的 PowerShell 对原有 Python 做一次隔离对照：

```powershell
& 'D:\app\Anaconda3\python.exe' .\scripts\mouse_doctor.py --probe
```

此命令仅测试标准库输入 API，不用该 Python 3.9 启动完整项目。结果保存在 artifacts/boss-desktop/environment.json 的 probe 部分。若原有 Python 正常而项目环境失败，再处理项目解释器；若同样失败，继续检查输入环境，不能将失败自动归因于 BOSS、DPI或某个软件。

## 自动循环的数据保存与查询

### 先单独验证完整正文采集

无需创建能力档案，先手动打开一个职位，最大化浏览器，确保正文没有横向截断，再运行：

```powershell
python -u -m app.boss_desktop collect-detail --detail-scrolls 10
```

此命令只采集当前选中的职位，不点击沟通。先确认正文顶部，再向下逐屏截图，最多滚动指定次数；仅当识别到任职要求末尾时标记完整。滚动无进展不代表完整，职位身份不符、弹窗或识别不确定时停止。短职位已完整显示时无需滚动；每次滚动优先使用最新识别到的详情区域坐标。

顶部判断同时检查模型标记和原文：出现从第 2 条以后开始的正文时，即使模型说已到顶部，仍须继续上滚。逐屏不进行技能判断，完整正文收集后才统一评估；提示词 desktop-v3 将同一句中的必需项和优先项拆开，学历/城市等硬条件不计入技能分母。2026-10-30 已在真实 BOSS 页面验证回顶和完整采集，71 项自动化测试通过；尚未用真实个人技能做匹配校准。

输出目录为 `artifacts/boss-desktop/collections/<本次ID>/`：`collection.json` 记录职位身份、状态、完整性及各屏观察目录，`detail.txt` 保存拼接的可见正文。原始截图和识别结果位于对应 `observations` 目录。异常时仍保留采集报告和已读取的正文证据。此命令不向业务数据库写入没有候选人归属的评分，也不虚构技能档案。

`run` 复用上述采集逻辑，采集完整后用全部正文进行一次需求评估。匹配决策由 Python 计算，`history.json` 的 `decision` 和分析接口的 `match_evidence.decision` 包含阈值、比较方式、逐项权重、匹配技能名称及未达标原因。正文不完整时分数为空；硬条件未知/失败时不能沟通。70 分在 `gte` 下通过，在 `gt` 下不通过。真实性和技能语义匹配仍须使用真实档案进一步校准。

run 现在会把采集到的职位写入 jobs、正文版本写入 job_snapshots、评分与依据写入 analyses，截图证据写入 artifacts；整个落库操作为一个事务。原文内容不变时复用快照；同档案/策略/模型/提示词版本不重复插入分析。正文不完整、硬条件未知或档案版本失效时记录 needs_review，不视为可投递。终端显示分析进度，history.json 中新增 job_id/snapshot_id/analysis_id。

当前仅有视觉组合身份（标题/公司/城市/薪资），没有读取平台职位ID；可能保守合并同名职位。canonical_url 是搜索页来源，不伪装成某个职位的详情链接，快照中 source_url_kind=search_page。后续需要更强的身份映射。

在 /docs 中 Authorize 后可用 GET /api/v1/jobs（支持 cursor/limit/platform_key/status）、GET /api/v1/jobs/{id}、GET /api/v1/analyses/{id} 查询。职位详情含正文快照与截图 artifact ID，GET /api/v1/artifacts/{id}/content 支持鉴权下载职位截图。分析查询会检查当前档案、策略和正文版本，返回 is_current；过期时 eligible 强制返回 false。分析查询不对旧模型判断进行静默覆盖，需要重新采集/分析。职位列表暂不提供按候选人分数筛选。

评分：必需权重3、优先权重1，有真实档案技能证据且满足完整要求的项记1，否则0；总分由Python计算。当前没有0.5等价技能映射。硬条件未知/不满足或要求不完整时不能沟通。职责与任职要求如何拆解及技能语义匹配仍依赖模型，需要人工校准。

明确希望发送沟通时，需先在策略中启用 auto_apply，并为上述 run 命令追加 --communicate。策略须允许 boss。默认 >=70，也支持策略 gt。点击前再次确认职位身份、按钮和画面；先持久化 communication_pending 后点击。打开聊天窗口不等于成功，只有明确回执才记 confirmed；未知结果不自动重试。如果沟通后职位列表仍可识别则继续，否则停止，请用户核查后返回列表。

记录保存在被Git忽略的 artifacts/boss-desktop/history.json。沟通与简历投递分开，不将沟通标为已投简历。沟通记录按候选人+标题/公司/城市/薪资去重，可能保守跳过同名职位；分析记录随档案或策略版本变化可重做。pending/unknown 均不自动重发。本模式按策略时区统计自身历史的日沟通次数，独立于后续简历申请额度。职位/分析已写数据库，但沟通状态仍使用本地日志，尚未接入后台任务队列。

Ctrl+C停止；PyAutoGUI保留屏幕角落紧急停止机制。desktop.lock 保证同一产物目录单执行器；崩溃留下锁时先确认旧进程停止再手动移除。不要从多个产物目录或多台机器同时控制同一桌面。仅支持主屏幕；DPI校验不一致会停止。

验证范围：用户附件已做真实千问识别，控制流程使用模拟桌面测试；尚未完成真实 BOSS 页面端到端联调，没有实际发送消息。不能据此宣称可稳定自动投递。平台登录、人工筛选、搜索条件设置仍由用户手动完成。

参考：[千问视觉接入](https://help.aliyun.com/en/model-studio/qwen-vl-compatible-with-openai)、[PyAutoGUI鼠标与截图](https://pyautogui.readthedocs.io/en/latest/quickstart.html)。
