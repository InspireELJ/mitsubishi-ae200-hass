# AE-200 在 Home Assistant 中的操作指南

适用于本仓库的原生 HACS 集成。使用现有已配置实体和控制器；可用模式、设备族与诊断内容仍取决于硬件、能力报告及数据质量。

以下 `YOUR_...` 都是占位符：实体选现有 HA 实体，entry 选当前控制器，组号和成员地址换成已配置数字，日期换成明确的 `YYYY-MM-DD`。group_id 与 M-NET address 是不同编号；数字范围合格不等于已配置。无需删除重建集成或现有实体。

## 日常控制：使用现有 HA 动作

优先在现有 climate 卡片上操作。模式、风档、导风位置、温限和步长以该实体实际显示的选项为准。

| 操作 | HA 标准动作与字段 |
|---|---|
| 开、关 | `climate.turn_on` / `climate.turn_off`，target 为现有 climate 实体 |
| 改模式 | `climate.set_hvac_mode`，字段 `hvac_mode`；自动模式在本集成映射为 `heat_cool`，不是 `auto` |
| 改温度 | `climate.set_temperature`，字段 `temperature`；只提供温度时不额外开机或改模式 |
| 模式与温度一起设置 | `climate.set_temperature` 同时给 `hvac_mode`、`temperature`；非 off 模式会组合开机、模式与温度，全部校验后一次发送 |
| 改风档 | `climate.set_fan_mode`，字段 `fan_mode`；只用实体 `fan_modes` 中的选项 |
| 改垂直导风板 | `climate.set_swing_mode`，字段 `swing_mode`；只用实体 `swing_modes` 中的选项 |

例如，单次关闭现有分组：

```yaml
action: climate.turn_off
target:
  entity_id: climate.YOUR_EXISTING_ZONE
```

`medium_1` / `medium_2` 是控制器风档标记，不是百分比。`fixed_horizontal` 是垂直导风板的一种送风位置，不表示独立水平摆风机构。日常选择 FAN 时只设置模式/开关，不附带温度。开机或设为制冷模式，不代表压缩机已运行。

现有本地遥控 switch 仍通过 `switch.turn_on/off` 操作。其 on 状态只代表 RemoCon 汇总禁止，不等于四项功能全部锁定；开关只写 RemoCon 单字段。需要分项设置时使用下文 `set_local_restrictions`。

## 可选诊断：需要时再读取

集成提供 **10 个 `ae200` 动作：9 个只读、1 个权限写入**。全部要求显式 `config_entry_id`，并要求接收响应。HA 动作界面可查看返回结果；脚本中的单个动作需配置 `response_variable`。以下表格只列 entry 以外的字段，不接受任意额外参数。

| 动作名称，均加 `ae200.` 前缀 | entry 以外的必填字段 | 用途与边界 |
|---|---|---|
| `read_member` | `address` | 一个已由拓扑确认的 IC 成员状态；返回 raw 字段，不推导整组物理运行 |
| `read_control_context` | `group_id` | 读全局及该组停机标志；两部分独立质量，不解除停机 |
| `read_today_schedule` | `group_id` | 读该组返回的今日计划；不写入、不执行、不预测下一次运行 |
| `read_controller_clock` | 无 | 六项控制器报告的日期/时钟字段；无时区或夏令时推断 |
| `read_weekly_pattern` | `group_id`、`season`、`pattern` | 一个周模式组合；season 1–5、pattern 1–7，不把编号当星期 |
| `read_member_runtime` | `address`、`date_iso`、`data_name` | data_name 只能是 `FAN_TIME` 或 `THERMO_TIME` |
| `read_member_temperature_history` | `address`、`date_iso` | 固定 `ROOM_TEMP`，不接受 data_name 字段 |
| `read_member_setpoint_history` | `address`、`date_iso`、`data_name` | data_name 只能是 `COOL_SET_TEMP` 或 `HEAT_SET_TEMP` |
| `read_daylight_settings` | 无 | 11 项 SummerTime XML-decoded raw 配置，不判断 DST 或调整 Clock |
| `set_local_restrictions` | `group_id`，及至少一项下述布尔字段 | **会写入本地权限**，不是只读诊断；规则及返回状态见下一节 |

group_id 范围 1–50；address 范围 1–250，仍须通过配置/拓扑 allowlist。日历史日期须严格 Gregorian `YYYY-MM-DD`，当前本地保护窗口为 1970–2037；不是控制器历史保留期或有数据的保证。

例如，读取一次停机上下文，作为脚本中的动作片段：

```yaml
action: ae200.read_control_context
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  group_id: YOUR_CONFIGURED_GROUP_NUMBER
response_variable: ae200_context
```

读日运行历史时只选一个成员、一天和一个通道：

```yaml
action: ae200.read_member_runtime
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  address: YOUR_VERIFIED_MNET_ADDRESS
  date_iso: "YOUR_YYYY-MM-DD"
  data_name: FAN_TIME
response_variable: ae200_runtime
```

### 先看质量，再看值

- 多数只读响应先看 `read_status`、`quality`、`last_successful_read`、`error` 与 `issues`。fresh 只说明近期成功读取，不能证明传感器或历史采集有效；stale 是旧样本，unknown 是证据不足，不等于 false 或零。
- `read_control_context` 要分别看 `global.quality` 和 `group.quality`。`control_inhibited=true` 表示至少一个新鲜停机标志为 true；false 需要三项都为新鲜 false；其他情况 unknown。此结果不证明停机原因或物理状态。
- 今日/周计划为空只表示本次返回零条。Schedule 标志与 schedule_available 是不同字段，均不证明激活、执行或下一次运行。
- 运行历史有 48 项时，可给 nullable `reported_minutes`；零不证明空闲或采集启用。室温/设点历史只保留字符串和位置标签，unit、aggregation、timezone unknown；48 个空热设点字符串不等于零、未制热或不支持。
- Clock 与 SummerTime 是独立采样，不能拼成原子快照。daylight 只保留 CountryCode 及两组 Month/Day/Hour/Minute/ShiftMin 原始字段；缺字段附 issue，部分 fresh 不等于规则正确，不输出 enabled、current_dst、timezone 或计算后日期。

### 冷却与缓存

建议每次动作完成后至少等 **15 秒**再调用同类动作，冷却报错时等待，不连续点击。三个日历史动作共用一套控制器级冷却，换成员、日期、通道或历史动作也不能绕过。Clock、daylight、排程、成员状态和权限动作各有其对应限制；底层仍共用串行限频。

daylight 立即重读会报冷却，缓存 read_status 可变为 cooldown，但 quality、原始样本及 attempt/success 时间保持；这不等于又读取了一次配置。

无需为诊断建立轮询或自动化。下载集成诊断文件只导出内存缓存，**不发送新请求**；下载时间不是各字段的采样时间。

## 分项本地权限：与中央控制分开

`ae200.set_local_restrictions` 只适用于已配置、新鲜且精确符合 IC / NORMAL / OLD、SubModel 显式空字符串的组。停机前提未知或不满足时会拒绝，不自动解除停机。

| 可选字段，至少提供一个 | true / false |
|---|---|
| `power_prohibited` | 禁止 / 允许本地开关 |
| `mode_prohibited` | 禁止 / 允许本地改模式 |
| `temperature_prohibited` | 禁止 / 允许本地改设定温度 |
| `filter_reset_prohibited` | 禁止 / 允许本地复位滤网提醒；**不会实际复位提醒** |

使用真正的 YAML 布尔 `true` / `false`，不是引号包围的字符串。未提供的功能保持其新鲜基线中的有效限制。例子只设置滤网复位权限，不发送滤网复位：

```yaml
action: ae200.set_local_restrictions
data:
  config_entry_id: YOUR_CONFIG_ENTRY_ID
  group_id: YOUR_CONFIGURED_GROUP_NUMBER
  filter_reset_prohibited: true
response_variable: ae200_restriction_result
```

返回 `status` 的含义：`no_change` 没有写入；`confirmed` 有 ACK 且独立读回匹配；`accepted_unconfirmed` 有 ACK 但读回未完成；`not_yet_confirmed` 读回暂不匹配；`outcome_unknown` 可能已发送但缺少可靠 ACK；`failed` 失败。后两类不要凭空认为原值未变，也不要自动重复写入。`after` 是当次读回样本，不是日后查看时的实时权限。

现有 switch 的四项 `local_*_prohibited` 属性仅在可用、新鲜时解释当前有效限制；unknown 不当作允许。报文 ACK 和读回不证明墙遥控器物理按键行为；不同功能与设备族需各自验证。共享锁不能阻止外部 BMS 同时修改。

## 现有提醒与维护流程：读取不会清除

| 看到的现象 | 现有流程 |
|---|---|
| Filter cleaning required 为 on | 先确认组新鲜/可用，再按现场维护流程检查和清洁；提醒保持是正常数据，不通过诊断动作清除。改 filter-reset 权限不等于复位。 |
| Fault 为 on | 保留新鲜组/成员 raw 与采样时间供维护排查；原始 AlarmCode 不按某个固定值硬编码成正常。现有动作不清故障，也不提供完整故障历史。 |
| Communication problem 或 unavailable | 查看 Controller status、Last successful read、组质量/错误；保留缓存证据。旧 false 和 unknown 都不能证明当前无问题。 |
| 想核对命令 | 查看当次响应，或按需查看现有默认禁用的 Last command status 诊断；它只读缓存。expected_attributes 是历史期望，不是实时状态，ACK 不等于物理动作已完成。 |

climate 附加属性 `filter_sign`、`error_sign`、`local_remote_prohibited` 仅实体可用且组质量 fresh 时给出识别后的布尔值；否则 unknown。读取和版本更新不会自动清除 FilterSign 提醒。

## 硬件与证据边界

普通 IC 的中央控制不能直接推广为所有设备族支持。NEW 温度写入仍被拒绝；其读取/离线 planner 不等于公共写能力。LC/通风、ATW、TWO_SETPOINT 和其他家族仍需代表性硬件与报文。

许可 active 不证明端点、硬件或有效测量。零湿度、空室外温度和跨设备族占位字段不证明相应传感器存在。历史分钟不是实时压缩机状态，室温/设点 raw 也不是已经验证的 HA 温度统计或能源计量。

进一步阅读：[能力范围](capabilities.md)、[诊断状态质量](diagnostic-state-quality.md)、[分项本地权限](local-restrictions.md)、[daylight 配置](controller-daylight-settings.md)和[HACS 部署与回退](hacs-deployment-zh.md)。本指南使用现有功能，不要求新增自动化或替换实体。
