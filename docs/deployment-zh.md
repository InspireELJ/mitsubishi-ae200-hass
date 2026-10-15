# 内部试点版本部署与回退

本分支版本：`0.2.0b1`。适用于 Home Assistant Core 2025.12 及更新版本；首个现场目标使用 Core 2026.10.1。

这是现场验收前的 beta。已测试协议、失败处理和打包；真实控制器与真实 HA 的端到端验收仍需部署后完成。该版本不新增温控、清洁或定时关机策略。

## 本版的改动

- 保留 `ae200` domain、已有 config entry、设备标识及 climate/sensor/switch 的 unique ID 计算方式。
- 保留现有实体名称、房间分配和自动化，部署时不要删除原有集成或修改实体注册表。
- 控制器整体断联时运行实体显示 unavailable；单个分组失败时只让该组运行实体 unavailable。
- 增加控制器状态、最后成功读取时间、每组 Communication problem。
- 缺失的开关/故障/滤网/遥控权限字段保留 unknown，不显示成正常。
- 未确认的 0% 湿度不再写入湿度测量，原始 0 值仍保留在诊断文件中。已有历史统计不会被删除或重写。
- 写命令必须收到有效 `setResponse`；协议报错会向 HA 报告失败。收到确认后仍以状态回读确认实际结果。
- 增加 HA 原生 Download diagnostics；下载只导出上次已采集的缓存，不触发额外请求。
- 将协议库打包在集成内部，不依赖未发布的 `pymelans` PyPI 包。唯一额外运行依赖保持 `websockets>=12.0`。

## 首次部署：手动替换文件

首个 beta 采用手动部署。先保留当前 HACS 仓库绑定，暂停对这个集成的上游自动更新；稳定后再迁移 HACS 的仓库绑定。不要同时加载两个相同 domain 的副本。

1. 在 HA 做一次正常备份，并导出当前空调实体清单。保留现有一键关闭、清洁保护和强制关闭规则。
2. 下载这个开发分支的 ZIP：
   `https://github.com/InspireELJ/mitsubishi-ae200-hass/archive/refs/heads/feat/inspire-monitoring-pilot.zip`
   也可以使用 CI 构建产物中的 `ae200-0.2.0b1.zip`；两者都只安装其中的 `custom_components/ae200`。
3. 解压到电脑上的一个临时目录。用 Samba、Studio Code Server 或现有文件传输方式，将文件放到 HA 配置目录的临时位置，例如：

   ```text
   /config/ae200-pilot-upload/custom_components/ae200/
   /config/ae200-pilot-upload/tools/install_ha.sh
   ```

4. 在可以访问 `/config` 的现有终端中执行：

   ```sh
   sh /config/ae200-pilot-upload/tools/install_ha.sh \
     /config/ae200-pilot-upload/custom_components/ae200
   ```

   安装器先暂存新文件，再把旧的整个 `ae200` 目录移动到 `/config/ae200-backups/<时间>/ae200`。切换失败时会恢复旧文件。它不改 `.storage`、设备/实体注册表、自动化或控制器设置，也不自动重启 HA。

5. 保存安装器显示的 Previous files 路径，然后执行：

   ```sh
   ha core check
   ```

   配置检查失败时先回退或检查日志，不要继续重启。没有 `ha` 命令的 HA 安装方式，请使用你当前安装类型的配置检查入口。

6. 在可观察现场结果的维护窗口，通过 HA 正常重启。重启期间自动化短暂暂停，因此避开必须执行的关机等关键时点。

用文件管理器替换也可以：先将整个旧目录移到 `/config/ae200-backups`，再放入新目录。不要把旧目录改名后留在 `custom_components` 下作为第二个集成副本。

## 部署后的验收

1. 设置 → 设备与服务 → Mitsubishi AE-200 / EW-50：原有控制器条目应直接加载。不要重新添加或删除条目。
2. 确认版本为 `0.2.0b1`；检查原有 climate 实体名称、数量、房间分配及自动化目标没有变化。
3. 控制器设备页应出现 Controller status / Last successful read；分组设备页增加 Communication problem。
4. 等待至少两个轮询周期。默认 30 秒，可在选项中调整，最小 15 秒。诊断中的 last_successful_read 才表示一次真实成功读取；HA last_updated 不具有相同含义。
5. 在集成条目的菜单选择 Download diagnostics。文件应包含 `inspire-ae200-ha-diagnostics-v1`，各组的 raw_attributes、quality 和最后成功读取时间。
6. 原本显示 0% 的湿度现在可能为 unknown；这是停止将未验证值当成测量，不能据此断言现场具有或不具有湿度传感器。
7. 先检查已有滤网提醒和故障标志。滤网提醒不自动清除，也不在本版增加 reset 操作。
8. 在确认合适的房间和时段，手动测试一次现有的开关/设定温度操作，观察真实设备与回读，并确认现有一键关闭仍能完成。若出现不识别的应答格式或异常，导出诊断并回退；不要在未验收时依赖 beta 执行无人值守的关键关机。

不需要为了验收而拔掉控制器或 M-NET 总线。整体失联、单组失联和拒绝写命令已由离线测试覆盖；现场如要测试网络中断，另选维护窗口。

## 回退

使用首次安装器输出的旧版本目录作为来源：

```sh
sh /config/ae200-pilot-upload/tools/install_ha.sh \
  /config/ae200-backups/<首次安装时间>/ae200
ha core check
```

然后正常重启 HA。回退也会备份当前 beta 文件。原有 config entry、实体命名和自动化没有被删除，因此无须重新建立。

本版新增的诊断实体在回退后可能保留为 unavailable 的注册表记录；它们不影响原有实体，可在确认回退稳定后按需清理。

## 后续迭代与 HACS

固定开发流程：改动协议库 → 同步 bundled library → 运行测试 → 构建归档 → 开发分支试点 → 验收后再合并/标记版本。

```sh
python scripts/sync_ha_library.py
python scripts/sync_ha_library.py --check
ruff check --config ruff.toml pymelans custom_components addon/rootfs/usr/bin scripts tests
PYTHONPATH=pymelans pytest tests/ -q -c /dev/null
python scripts/build_release.py
```

`dist/ae200-0.2.0b1.zip` 及其 SHA-256 文件是构建产物，不提交到源码仓库。HACS 迁移时应先保存当前文件，再仅调整 HACS 中的仓库下载来源；业务集成条目保持不动。本文不让 HACS 和手动安装器同时管理更新。

## 现场数据边界

不要提交真实控制器地址、凭据、序列号、许可注册码、房间名称清单、HA 配置备份或现场诊断 JSON 到公开 fork。脱敏的合成报文用于测试；现场文件通过现有内部渠道交换。
