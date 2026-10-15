# 不依赖 Java 获取 AE-200 诊断信息

运行接口与老式 Java 管理页是两条不同路径。已有 HA 集成通过 `/b_xmlproc/` WebSocket 及 `MIMEReceiveServlet` XML 通讯；读取运行数据不要求成功启动 Java 插件。

## 路径一：HA 原生诊断

安装内部 beta 后，在设置 → 设备与服务 → Mitsubishi AE-200 / EW-50 的条目菜单中选择 Download diagnostics。

此导出只读取内存缓存，不访问账户/密码、也不发送新的控制器请求。它保留每个 group 实际返回的属性及不受当前模型识别的字段，省去打开原厂页面。默认去除控制器连接地址、分组名称及常见敏感字段。

它目前不探测未知 XML 命令，也不能凭现有 Mnet 响应推断固件、许可证、Thermo-ON、运行计时或能源能力。下一批点位依据真实报文逐项确认。

## 路径二：现代浏览器直接只读探测

1. 用 Chrome 或 Edge 打开控制器自身的 HTTP 网页。即使旧页面显示 Java 不受支持，也可以使用页面的 Console。
2. 打开仓库的 `tools/ae200_readonly_browser.js`，全选复制，在该控制器页面的 Console 执行一次。
3. 脚本先对三个已知页面做 GET，然后尝试现有 WebSocket 分组列表和逐组读取。每次请求至少相隔 1.1 秒；最多读取 50 个已发现的组，不扫描未知组。
4. 等候 JSON 自动下载。对于约 18 组的现场，一般需要几十秒；连接失败会延长等候。两个连续分组失败后会停止，并保留部分结果。

已知页面：

```text
http://<AE200_IP>/control/index.html
http://<AE200_IP>/mobile/index.html
http://<AE200_IP>/init/en/administrator.html
```

前两个是官方手册中的新式控制页面入口，是否存在、能否加载、是否已配置需在现场确认。200 状态可能只是通用页面或登录页，不能当作现代 UI 可用的证明；手机页面还可能要求已登记的一般用户。

若分组列表接口不支持，可将脚本开头 `GROUPS = []` 改为已有 HA 清单中的 group_id 后再执行。不要填满 1-50 去盲扫。该脚本只发送 `getRequest`，不尝试登录、重置口令、写设置、猜测新字段、升级固件或更改许可。

导出 JSON 包含控制器内网地址和原始运行字段，只在内部交换。若页面自身不可达或 WebSocket 被拒绝，返回错误本身就是诊断依据，不需要先更换 Java 环境。

## Initial Setting Tool 和默认账号

Initial Setting Tool 是独立的 Windows 工具；浏览器的 Java 插件不是它的必要组成部分。截图中的独立程序与老式网页也应分开排查。

官方较早的服务资料，以及非英国 E 系列相应手册，列出维护用户默认值：

```text
User ID: initial
Password: init
```

这是出厂默认信息，不是确认过的现场口令。新版 A 系列、部分英国型号和固件存在序列号/DP 派生规则，且现场口令可被修改。先确认完整型号和固件；如符合上述范围，可以手动核对一次默认值。不要在代码中自动尝试默认密码，也不要批量猜测。

Maintenance user、Building manager 与一般网页用户的权限/账号范围不同，不能把其中一个界面的登录失败推广为所有接口不可用。忘记维护密码不阻止一个已经能读取运行状态的 HA 集成继续采集。

### 新版控制页提示用户名或密码错误

`/control/index.html` 的 Integrated Centralized Control Web 使用独立的用户设置。不能假设在旧版 `/en/administrator.html` 或 `/init/en/administrator.html` 中修改 `administrator` 后，新版也会接受同一密码。官方 WT07996X13 第 114 页明确区分新版账号与 LCD、Web Browser for Initial Settings 的账号。

对非英国的 AE-200E/EW-50E，官方手册第 25 页列出所有版本的 Building manager 出厂默认值为 `administrator` / `admin`。型号、地区符合且尚未设置过新版账号时，可以在新版页手动核对一次；这不是现场密码保证。英国机型和 A 系列存在不同规则，不要仅根据版本 8.00 以上就套用序列号或 DP 派生密码。

新版登录成功后，在新版的 Settings → User registration 中修改 Building manager。此处与旧版的 System Settings → User Registration 不是同一个页面。新版 Building manager 密码按这版手册要求为 3 至 10 位英文字母或数字，区分大小写。旧版修改流程中的 Edit → OK → Save Settings 也需完整执行，但完成旧版保存仍不能证明新版账号已同步。

若符合范围的默认值也被拒绝，可能已经有人单独设置过新版账号。先查内部保管记录或向原安装商确认；官方手册要求忘记新版用户名或密码时联系经销商，没有据此确认的自助重置步骤。不要通过恢复出厂或导入未知备份来解决登录问题。已有 HA 运行数据采集与这个网页登录问题可分别推进。

需要配置备份、许可清单或修改初始参数时，再通过现场 LCD、已保存的合法维护账号/资料或原安装商处理。不要为了获取监控数据先做恢复出厂或固件升级。

## 资料

- [官方完整资料入口](https://www.mitsubishi-electric.co.nz/commercial/c/11706/ae-200e-ae-50e-ew-50e)
- [Integrated Centralized Control Web，WT07996X13](https://www.mitsubishi-electric.co.nz/materials/PRODUCT/Controls/AE-200E_AE-50E_EW-50E/02_Manuals/1_Operation/OM_AE-200E_AE-50E_EW-50E_WT07996X13_Integrated_Centralized_Control_Web.pdf)：PC 与手机登录路径及用户范围。
- [AE-200/AE-50 Instruction Book，WT07982X09](https://www.mitsubishitechinfo.ca/sites/default/files/IM_AE-200_AE-50_Instruction%20Book_WT07982X09_E.pdf)：维护用户、不同型号/固件/地区的默认密码说明。
- [Service Handbook，HWE1402B](https://www.mitsubishitechinfo.ca/sites/default/files/SH_AE-200_AE-50_EW-50_HWE1402B_202303_0.pdf)：维护接口的默认用户信息。
- [HA diagnostics 开发文档](https://developers.home-assistant.io/docs/core/integration/diagnostics/)：原生诊断导出入口与敏感信息处理。
