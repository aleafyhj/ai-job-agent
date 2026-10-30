# BOSS直聘：截图与真实鼠标模式

已按新的需求增加独立命令行执行器，绕开 DOM 抓取这一实现路径。鼠标操作不能保证不触发平台风控；验证码、登录异常和弹窗不自动处理。用户先手动登录并打开职位搜索结果，将浏览器放到 Windows 主屏幕前台。

代码：app/desktop.py（截图、鼠标、滚轮、DPI/窗口校验），app/vision.py（千问视觉、结构化结果和评分），app/boss_desktop.py（循环与本地记录）。没有通过截图自动上传简历，也没有自动填写聊天内容。

## 模型配置

默认 qwen3-vl-plus，国内地址 https://dashscope.aliyuncs.com/compatible-mode/v1，自动读取已有系统环境变量 DASHSCOPE_API_KEY；也可使用 JOB_AGENT_VISION_API_KEY 覆盖。区域或模型不同可在 .env 设置 JOB_AGENT_VISION_BASE_URL / JOB_AGENT_VISION_MODEL。本机已用用户提供的截图真实调用成功，识别出 5 张卡片，正确标记详情未完整显示。未向模型上传简历、联系方式或数据库密码；截图本身仍可能包含网页个人信息。

## 使用

以下命令启动后有 5 秒切换窗口时间。窗口标题默认包含 BOSS直聘，可用 --title 指定更准确的标题片段。不要在执行期间移动/缩放窗口或操作其他应用。

```powershell
# 截取当前主屏幕，输出 artifacts/boss-desktop/screen.png
.\.venv\Scripts\python.exe -m app.boss_desktop screenshot

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

手动测试结果：screenshot 保存 screen.png；click 保存 click-before.png / click-after.png；scroll 保存 scroll-before.png / scroll-after.png，终端打印实际绝对路径（同名文件覆盖）。操作后窗口改变时可能只保留 before 图，并明确提示人工核查。reference-analysis.json 是开发时对用户附件调用千问的识别结果，不是本机实时截图或鼠标操作记录。

左右滚动点默认由千问从当前全屏截图识别；无法识别时停止。可用 --left-scroll 200 500 / --right-scroll 750 800 显式覆盖，这些只是归一化坐标示例，不是不同窗口布局通用值。每次只点一张卡，重新截图确认右侧身份，向上滚动并核对正文顶部，向下滚动读取详情，读完整后合并文本再评估。每页详情都检查职位身份；超过 --detail-scrolls（默认5）仍未读完整则不判定可沟通。遇到识别不确定会停止，不跳过关键核验。

## 识别与点击报告

每次列表/详情/全文评估调用千问时，独立保存到 observations/<时间戳-随机ID>/：screen.png（实际输入原图）、targets.png（卡片序号与像素坐标标记）、report.json（图像尺寸、散列、模型结构化结果、坐标换算或失败类型）。模型调用失败也保留输入及失败记录，不保存密钥或服务商错误原文。点击后另存 action.json，记录点击目标与 input_executed；它只证明调用输入，不等于职位/沟通已经成功。

inspect 默认不点击；click-card 在点击后再次调用千问检查右侧身份，只有核对一致才打印确认。遇窗口变化、内容加载未完成或身份无法核对时停止，不自动重复点击。

## 自动循环的数据保存与查询

run 现在会把采集到的职位写入 jobs、正文版本写入 job_snapshots、评分与依据写入 analyses，截图证据写入 artifacts；整个落库操作为一个事务。原文内容不变时复用快照；同档案/策略/模型/提示词版本不重复插入分析。正文不完整、硬条件未知或档案版本失效时记录 needs_review，不视为可投递。终端显示分析进度，history.json 中新增 job_id/snapshot_id/analysis_id。

当前仅有视觉组合身份（标题/公司/城市/薪资），没有读取平台职位ID；可能保守合并同名职位。canonical_url 是搜索页来源，不伪装成某个职位的详情链接，快照中 source_url_kind=search_page。后续需要更强的身份映射。

在 /docs 中 Authorize 后可用 GET /api/v1/jobs（支持 cursor/limit/platform_key/status）、GET /api/v1/jobs/{id}、GET /api/v1/analyses/{id} 查询。职位详情含正文快照与截图 artifact ID，GET /api/v1/artifacts/{id}/content 支持鉴权下载职位截图。分析查询会检查当前档案、策略和正文版本，返回 is_current；过期时 eligible 强制返回 false。分析查询不对旧模型判断进行静默覆盖，需要重新采集/分析。职位列表暂不提供按候选人分数筛选。

评分：必需权重3、优先权重1，有真实档案技能证据且满足完整要求的项记1，否则0；总分由Python计算。当前没有0.5等价技能映射。硬条件未知/不满足或要求不完整时不能沟通。职责与任职要求如何拆解及技能语义匹配仍依赖模型，需要人工校准。

明确希望发送沟通时，需先在策略中启用 auto_apply，并为上述 run 命令追加 --communicate。策略须允许 boss。默认 >=70，也支持策略 gt。点击前再次确认职位身份、按钮和画面；先持久化 communication_pending 后点击。打开聊天窗口不等于成功，只有明确回执才记 confirmed；未知结果不自动重试。如果沟通后职位列表仍可识别则继续，否则停止，请用户核查后返回列表。

记录保存在被Git忽略的 artifacts/boss-desktop/history.json。沟通与简历投递分开，不将沟通标为已投简历。沟通记录按候选人+标题/公司/城市/薪资去重，可能保守跳过同名职位；分析记录随档案或策略版本变化可重做。pending/unknown 均不自动重发。本模式按策略时区统计自身历史的日沟通次数，独立于后续简历申请额度。职位/分析已写数据库，但沟通状态仍使用本地日志，尚未接入后台任务队列。

Ctrl+C停止；PyAutoGUI保留屏幕角落紧急停止机制。desktop.lock 保证同一产物目录单执行器；崩溃留下锁时先确认旧进程停止再手动移除。不要从多个产物目录或多台机器同时控制同一桌面。仅支持主屏幕；DPI校验不一致会停止。

验证范围：用户附件已做真实千问识别，控制流程使用模拟桌面测试；尚未完成真实 BOSS 页面端到端联调，没有实际发送消息。不能据此宣称可稳定自动投递。平台登录、人工筛选、搜索条件设置仍由用户手动完成。

参考：[千问视觉接入](https://help.aliyun.com/en/model-studio/qwen-vl-compatible-with-openai)、[PyAutoGUI鼠标与截图](https://pyautogui.readthedocs.io/en/latest/quickstart.html)。
