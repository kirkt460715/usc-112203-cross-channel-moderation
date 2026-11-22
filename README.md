# 赛事跨频道互动的观看伤害计量与值守干预

样本内容用代号表示。房间及词种均为演练标识，适用负荷按当班资格和有效时段。

records 保存可离线使用的合成业务记录。每行一个 UTF-8 JSON 对象，id 在各数据表内唯一，跨表键沿用业务记录中的标识。catalog.json 保存行数及原始文件校验值。

| 记录 | 业务含义 |
| --- | --- |
| rooms.jsonl | 语言以值班覆盖资格为准。 |
| posts.jsonl | 同赛段分钟为半开区间，引用有自己生效时间，未知引用不当成没有引用。 |
| visits.jsonl | viewer 为有证据关联的观众，时间以该赛段为坐标。 |
| coverage.jsonl | 概率表示当期抽样覆盖。observed=false 表示台账缺失。 |
| reviews.jsonl | 申诉可以撤销对具体内容的标签，未审阅没有正式结论。 |
| shifts.jsonl | 值班员服务语种和承诺。基础最低在岗以房间语言为单位。 |
| policies.jsonl | 认领的干预窗口有实际起止，交接时保持房间最低要求。 |
| comparison_windows.jsonl | 名单分配于 assigned_part，跨房共用实际从 actual_part 开始。独立观测按房群与时段保留。 |
| joint_actions.jsonl | 对照保留约定与联合干预生效范围。approved=false 代表候选；修改对照约定须确认。 |
| effect_policy.jsonl | 保护的触发标准和证据范围由当班表登记。 |

## 核对资料

运行环境为 Python 3.11，使用标准库，无需下载依赖。用 `python3 -m unittest discover -v` 核对发行资料的完整性与约定的输入关系。
