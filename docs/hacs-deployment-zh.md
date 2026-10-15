# 公司版本的 HACS 部署、更新与回退

使用 `InspireELJ/mitsubishi-ae200-hass` 的自定义仓库和 GitHub Releases。当前已发布版本为 `0.2.1b7`，修复五类既有可选读取在开始后被取消时的缓存质量：保留旧 raw/成功时刻为 stale，冷缓存 unknown，记录安全 CancelledError 并传播取消，按完成时刻冷却且不重试。控制上下文仅标当前记录，不覆盖未尝试的旧记录或继续后续 GET。组轮询错误代码改为受限 ASCII token，read_errors 导出再过滤 type/code 并建立独立两层字典，额外 message/URL 等字段丢弃，调用者不能经返回值修改客户端错误缓存。无新 API、实体、请求、轮询节奏、控制或自动化；这些分支以合成离线测试验证，不声称真实泄漏、攻击或现场故障注入。b7 已完成正常运行的 HA 部署回归：集成与内置库版本相符，原实体标识、运行设置、本地权限及自动化保留，既有 Clock 动作与缓存保持原 schema/解释；没有注入现场取消、断线或故障，也未为测试启用默认禁用的命令传感器，取消、异常代码和副本隔离仍依据离线测试；b6 daylight 的实际 HA 动作已通过：11 项 raw 与缓存及协议样本一致，立即重复被冷却拒绝，缓存仅 invocation status 变为 cooldown，其余六项字段与采样时间保持；原实体、运行设置、限制及自动化保留，不推 DST 或调整 Clock。最低 HA Core 版本为 `2025.12.0`。本流程保留 ae200 domain、既有实体标识与自动化。

b5 的三个气候布尔诊断同时要求实体可用且 group quality 精确为 fresh。失败或 outcome_unknown 的最后命令可额外显示白名单错误类型及受限短代码；消息、URL、连接详情、未知类型和非法代码不展示，原命令状态枚举与读回规则保持。

内部使用只需把公司仓库添加到各 HA 的 HACS，无须申请进入公共商店。沿用当前公开 fork；现场地址、凭据、序列号和原始诊断文件继续保留在内部。

## 一次性准备公司 GitHub 仓库

1. 仓库首页右侧 About → 齿轮，保存 Description 和 Topics。建议 Topics：`home-assistant`、`hacs`、`mitsubishi`、`hvac`。
2. Settings → General → Features：确认 Issues 已开启。
3. 等待公司仓库 PR 的 test、hassfest、hacs 检查通过。只在公司仓库操作。
4. 将公司仓库的开发 PR 合并到公司 `main`。若 PR 仍是 Draft，先在 PR 页面选择 Ready for review，再合并。
5. 版本号变更合并到公司 `main` 后会自动启动 **Publish HACS release**。需要手动补跑时，打开 Actions → **Publish HACS release** → **Run workflow**，选择 `main`，运行。
6. 工作流会重跑 CI；全部通过后校验版本一致性和归档校验和，创建 `v0.2.1b7` 标签及 Pre-release，并附上手动安装 ZIP 和 SHA-256。
7. 在 Releases 确认 `v0.2.1b7` 已发布、标有 Pre-release，且目标是公司 `main` 的已测试提交。Draft release 不会提供给 HACS。

首次合并前，默认分支尚无这个工作流，因此 Actions 页面可能还没有 Run workflow 按钮。合并完成后再进入 Actions；首个版本正常情况下自动发布。

GitHub 托管的 Ubuntu runner 即可运行发布。工作流使用仓库内置 `GITHUB_TOKEN`，只在 publish job 申请 `contents: write`；不需要创建 PAT、OAuth App 或手工上传长期密钥。现有 CI 能运行时不必重新配置 Actions。若发布报 `Resource not accessible by integration`，再由组织管理员核查仓库/组织的 Actions 权限策略。

工作流只允许从 `InspireELJ/mitsubishi-ae200-hass` 的 `main` 发布。它不执行 Sync fork、不打开上游 PR/Issue、不改其他仓库，也不覆盖已经发布的版本标签。

## 首次把现有 HA 切换到公司下载来源

在公司 Release 发布完成后，选择可观察现场结果的维护时段操作。HACS 下载与 HA 集成配置是两个不同层面。

1. 在 HA 执行正常备份，记录现有空调实体和自动化目标。将当前整个 `/config/custom_components/ae200` 目录备份到 `/config/ae200-backups` 下，作为首次回退来源。不要把备份副本放在 `custom_components` 下。
2. 打开 **HACS**，找到当前已下载的 Mitsubishi AE-200 仓库，在这一行的菜单选择 **Remove**。这会移除 HA 本地的旧下载文件；保留“设置 → 设备与服务”里的 AE-200 集成条目。完成公司版下载前先保持 HA 运行。
3. HACS 右上角菜单 → **Custom repositories**，添加：

   ```text
   https://github.com/InspireELJ/mitsubishi-ae200-hass
   ```

   类型选择 **Integration**。

4. 找到公司仓库，核对 Repository 链接中的 owner 是 `InspireELJ`。选择 Download。若版本选择器能直接显示 `v0.2.1b7`，选此版本；若 beta 尚未显示，首次可以选择公司 `main`，它在上述合并后应已包含同一 `0.2.1b7` 代码。下载完成前不要重启。
5. 在现有 HA 终端运行 `ha core check`；没有 `ha` 命令的安装类型，使用其正常配置检查入口。通过后正常重启 HA。
6. 核对已加载版本、原有实体标识、数量、房间分配、自动化目标及新增健康状态，并按[现场验收](deployment-zh.md#部署后的验收)下载诊断。`medium_1`、`medium_2` 分别保留控制器报告的 `MID1`、`MID2`；它们不是百分比，也不代表已发现每台室内机的全部可用风档。
7. 继续执行下一节，为这个公司仓库开启 beta 更新并固定到 Release。

如果旧来源从未由 HACS 管理，可以跳过第二步；已有文件仍先备份，然后让公司 HACS 下载接管。HACS 不会仅凭已有文件自动识别其版本。

如果公司下载失败，在首次备份目录恢复原来的整个 `ae200` 目录，检查配置后再重启。若出现同一 domain 被其他仓库占用的提示，先确认旧的 HACS 下载来源已移除；不要通过更改 domain、删除业务集成或手改 `.storage` 绕过。

## 开启 beta 更新与选择版本

HACS 2.x 使用仓库对应的 **Pre-release** 开关控制 beta 更新。开关实体默认可能处于禁用状态。

1. 首次公司版下载并重启后，进入 HA **设置 → 设备与服务 → 实体**。
2. 按 Integration 筛选 **HACS**，显示禁用实体；找到这个 Mitsubishi 公司仓库对应的 **Pre-release** 开关。核对它属于公司仓库，启用实体并打开开关。
3. 返回 HACS 的公司仓库菜单，选择 **Update information**。
4. 若 HA **设置 → 更新** 中已有公司集成的更新卡，先核对 Installed 与 Latest 明确显示的版本，再使用 **Update**。也可选择 **Redownload → Need a different version? → v0.2.1b7 → Download**。部分前端提供 Show beta versions 入口；最终以明确显示的目标 Release 标签为准。
5. 下载后正常重启 HA，并核对版本。首次如果使用了 `main`，这一步让 HACS 记录为明确的已发布版本。

更新信息只刷新版本元数据；Download/Redownload 才会替换代码。更新后的正常重启仍然需要完成。

一次 `v0.2.0b10` 升级的实际观察是：HACS 下载页默认仍显示 b9，展开 **Need a different version?** 后，b10 位于 Release 列表末尾；明确选中 `v0.2.0b10` 后才开始下载。不要从默认显示或列表顺序推断目标版本，也不据此猜测排序或缓存原因。本轮采用新的 `0.2.1b1` patch 预发布序列，保留全部既有标签与资产；仍需显式核对所选 Release，不承诺新序列会改变该界面行为。

`0.2.1b1` 实际升级已通过设置中的更新卡进行：核对 Latest 为目标版本后点击 Update，重启后实际加载的 manifest 与内置库均为 `0.2.1b1`。两个运行趋势通道的 HA 响应均与各自缓存精确一致，原有实体标识、本地限制和自动化保留。此验收不替代下一版本的重启、版本及功能核对。

## 后续迭代

开发分支修改并提高版本号 → 同步 bundled library → CI 通过 → 合并公司 `main` → 自动运行 Publish HACS release → HA 的公司 HACS 更新实体提示新版本 → 下载 → 正常重启 → 现场验收。

每次发布都提高版本，例如 `0.2.1b6` → `0.2.1b7`，同步修改 HA manifest、`pymelans/pyproject.toml` 和 canonical `const.py`，运行库同步脚本，并补充对应 Changelog。发布脚本会拒绝版本不一致、缺少说明或改变 domain 的包。已经发布的标签和资产保持不变；修复通过新版本发布。

Beta 阶段采用人工安装更新。稳定版本发布后，可以关闭仓库的 Pre-release 开关，仅接收正式版本。

## 回退

已有公司历史 Release 时，在公司 HACS 条目选择 Redownload 和上一版本，再正常重启。首次 beta 没有更早的公司 Release 时，使用首次文件备份或 HA 正常备份恢复。

文件回退期间暂停对这个集成的更新，以免立即下载新版本覆盖。现有 config entry、实体注册表和自动化作为持久配置保留；本流程不要求重新添加控制器。

## 如果 HACS 要求 GitHub 重新认证

沿用已存在的 HACS 连接即可。只有 HA 显示 HACS 认证修复时，才进入该修复流程，复制其显示的 device code，在自己的浏览器打开 `https://github.com/login/device`、登录 GitHub、输入 code 并确认 HACS 授权，然后回到 HA 完成。账户密码、device code 和 token 都由你在自己的认证页面处理。

这个 HACS 授权与用于协作开发的 ChatGPT GitHub 连接是两条独立连接。公司仓库为公开仓库时，HACS 标准下载流程不需要新增仓库写权限。

## 官方依据

- [HACS 自定义仓库](https://www.hacs.xyz/docs/faq/custom_repositories/)
- [下载、指定版本、移除下载来源](https://www.hacs.xyz/docs/use/repositories/dashboard/)
- [Pre-release 开关实体](https://www.hacs.xyz/docs/use/entities/switch/)
- [HACS 集成结构与 Releases](https://www.hacs.xyz/docs/publish/integration/)

Clock 诊断仅解释控制器报告的日期与时钟字段；是否已经包含夏令时调整尚未验证，不自行加减 shift，也不将两次独立读取拼成调整后的时间。b5 的失败和过期分支以离线合同测试验证，没有对现场实施故障注入。
