# 全文匹配与复核（desktop-v6）

截图模型仅转录可见正文。收集完整正文后，单独发送正文和最小能力档案给千问，返回结构化要求；Python 校验依据、计算分数及资格。不再要求末屏截图同时承担全文评分和职位身份重新识别。

## 要求分类

| category | 内容 | 是否进入技术分数 |
|---|---|---|
| technical | 技术、工具、明确技术职责 | 必需权重3、优先权重1 |
| hard | 学历、专业、毕业年份、地点等硬条件 | 否；必需硬条件单独通过/失败/未知 |
| soft | 沟通、团队协作、学习能力等 | 否 |
| other | 薪资、福利、公司背景等 | 否 |

soft/other 的 importance 固定为 preferred 占位，不表示它们是技术加分项。后端发现明显学历/软能力/福利误归为技术时纠正类别并要求复核，不只依赖提示词。

要求 quote 必须是原文连续片段；混合必需/优先句、AND要求应拆开，OR保留任一选项语义。重复引用不重复加权并标记复核。常见技术概念的未拆复合句会被规则标记，但规则不是通用自然语言解析器，仍可能漏检。多个不同招聘方向须先选择具体方向，不合并分数。

## 证据与分数

每项状态为 matched（已满足）、unmatched（有明确不满足证据）或 unknown（证据不足）。没写在档案里的技能不能直接断言不会；没有毕业年份不能直接判定不符合应届生要求。

- 技术证据引用 skill_indices，返回原始技能名称及项目证据。索引越界、无证据、重复索引不能证明匹配。
- min_level/min_years 与档案显式熟练度、年限比较；事实不足为 unknown，明确低于要求为 unmatched。
- 对常见技术概念增加证据一致性检查，例如 RAG 项目不能直接证明大模型微调能力。此检查覆盖有限词汇，不能代替人工核对所有语义。
- 硬条件指向允许的 profile_field；该事实缺失时强制 unknown。具体学历/地点等语义比较仍依赖模型。
- score 是已证实覆盖权重/全部技术权重 ×100；未知项保留在分母。score_upper_bound 假设所有未知项均满足，是理论上界，不是预测分数或置信区间。
- score 低不等于候选人能力差，可能是缺少档案证据。没有技术项、正文不完整或存在多个招聘方向时无可用总分。

达到阈值、必需硬条件通过且没有需要复核的问题才具备匹配资格。必需技术项未知、引用错误、分类冲突、复合项未拆、提取不确定等会设置 needs_review。模拟档案无论多少分都不能沟通；沟通仍须显式启用，评分不会自动执行鼠标输入。

## 保存与兼容

分析接口 match_evidence.decision 包含 score、score_upper_bound、weights、hard_conditions、needs_review、reasons 及校验后的逐项 requirements。analyses.requirements 保留模型原始结构化结果，decision.requirements 保留校验后的类别/状态及原始技能证据，供对比追溯。

提示词/评分版本为 desktop-v6。滚动截图合并后先按 Unicode/空白规范键删除重复行，高度相似的长行保留信息更完整的一条；格式差异映射回真实连续原文。正文区标题之前的真实短技能标签保留展示但以 `scoring_exclusion=header_skill_tags` 排除计分，带动作语义的岗位使命不会误排除。技术复合项必须同时具备对应概念证据，OR 只作用于直接技能选项，不能用 Python/C++ 语言经验代替同句的算法、编码规范、RAG、Agent 等能力。直接技能名还必须由正确的 canonical_name 引用。旧分析保留，但接口 is_current/eligible 会将旧版本视为过期；run 的历史去重也要求评分版本一致。需要复核的结果在数据库中为 needs_review，具体原因保存至 uncertainties。

## 离线回放

```powershell
$demo = Get-Content .\artifacts\boss-desktop\demo-resources.json | ConvertFrom-Json
python -u -m scripts.replay_matching --profile-id $demo.profile_id --policy-id $demo.policy_id --limit 3
```

只读能力档案、策略及已采集正文，调用现有模型，在 artifacts/boss-desktop/replays/<ID>/report.json 保存原始评估和旧/新分数。不会操作桌面、调用沟通、上传简历或覆盖数据库分析。默认3份，最多10份，避免无界模型调用。

2026-11-01：回放3个已采集职位，均触发复核；首个包含多个岗位，不再合并评分，另外两份分别出现毕业身份事实未知和必需技术证据不足。回放并未证明模型已经准确提取所有要求。当前阶段完成结构化校验与可追溯评分，仍需更多人工标注样本校准漏提取和语义误匹配后，才能开启真实自动沟通。
