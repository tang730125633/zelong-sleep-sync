# Zelong Sleep Sync · 泽龙睡眠同步

**让 Apple Watch 的睡眠记录，来到自己的服务器和 AI 助手身边**

用 iPhone 原生快捷指令读取睡眠<br>
打开微信，就触发一次上传<br>
网站和 AI 从自己的数据库读取结果

不依赖 Health Auto Export 订阅
需要自己的 HTTPS 服务，以及首次健康读取授权

[安装与部署](docs/安装与部署.md) · [实践方法与踩坑](docs/实践方法与踩坑.md) · [API 说明](docs/API.md)

![泽龙与橘猫：睡眠同步工作原理](docs/睡眠自动同步原理.png?v=zel-brand-2)

## 为什么做这个

手表记录了数据，不代表数据已经来到 AI 身边

这个项目的起点，是原有导出停止更新
我们需要一条自己能检查的同步路径
从手机读取，到服务端保存，再到 AI 查询
每一步都有可以核对的结果

微信在这里仅是系统自动化的触发条件
它不接收健康数据，也不需要微信机器人

## 目前做到哪里

| 能力 | 状态 |
| --- | --- |
| iPhone 原生读取睡眠 | 原项目真机验证通过 |
| 打开微信触发上传 | 原项目真机验证通过 |
| 上传身份校验、去重与保存 | 原项目实测，独立版测试通过 |
| 只读 MCP 查询 | 原项目实测，独立版 HTTP 测试通过 |
| 独立版重新部署到另一台手机 | 尚未验收，需要使用者验证 |
| 步数、心率、运动与 Keep | 尚未完成，不属于当前交付范围 |
| 锁屏、长期后台、定时运行 | 未保证，不能当作已验证能力 |

这里发布的是从实际项目提取的独立版本
保留睡眠处理逻辑，替换掉私人书桌的业务外壳
**不包含原网站前端、真实健康记录、账号或密钥**

独立版上传接口只接受睡眠样本
部分底层模块保留其他指标的解析逻辑
这不代表相关指标已经完成手机同步

## 最短使用路径

需要 Python 3.10+、iPhone「快捷指令」和自己的 HTTPS 域名
生成可导入的签名文件，需要支持 `shortcuts sign` 的 Mac

```sh
git clone https://github.com/tang730125633/zelong-sleep-sync.git
cd zelong-sleep-sync
python3 test_bridge.py
python3 test_http.py
python3 server/app.py
```

服务默认监听 `127.0.0.1:8768`
首次启动在 `data/` 中生成两个独立密钥

- `upload.key`：给手机上传使用
- `read.key`：给自己查询和 MCP 使用

先配置 HTTPS 反向代理，再生成自己的快捷指令

```sh
python3 build_shortcuts.py \
  --endpoint https://你的域名/api/health/import

shortcuts sign --mode anyone \
  --input dist/睡眠同步.unsigned.shortcut \
  --output dist/睡眠同步.shortcut
```

将签名文件传到 iPhone，导入时填写自己的上传密钥
先手动运行成功，再设置「打开微信 → 立即运行睡眠同步」
完整步骤见 [安装与部署](docs/安装与部署.md)

## 技术结构

![泽龙与橘猫：睡眠同步技术架构](docs/睡眠自动同步技术架构.png?v=zel-brand-2)

图中 `zelong.vip` 与 `/wechat/api/…` 是原项目的部署案例
本仓库独立版使用你自己的域名与 `/api/…`
`server/app.py` 是独立 HTTP 入口，不依赖原来的私人书桌服务

| 文件 | 用途 |
| --- | --- |
| `build_shortcuts.py` | 生成不含密钥的原生快捷指令 |
| `server/app.py` | 独立上传、查询与 MCP 接口 |
| `server/native_health.py` | 手机样本转换、分期识别、时间归并 |
| `server/wellbeing.py` | 事务保存、日汇总、同步状态 |
| `server/sleep_detail.py` | 分期存储和明细查询 |
| `server/health_mcp.py` | 只读 AI 查询工具 |
| `server/health_metrics.py` | 共用日期、单位与校验逻辑 |
| `test_bridge.py`、`test_http.py` | 使用合成数据验证，不连接真实服务 |

## 怎样确认成功

```json
{"imported": 44, "changed": 0}
```

这是回执示例，不是个人健康数据

`imported` 是处理后的日汇总、明细等记录数量
不是原始样本数量，也不是睡眠天数

`changed: 0` 表示数据没有变化
重复上传不会重复累计
`changed` 也不等于新增天数，明细修订可能计入变更

收到成功回执，还可以继续查询数据库日期和 MCP
仅仅打开微信、看到运行通知，都不等于上传成功

## 已知边界

- 默认按 UTC+8 处理日期，中午作为睡眠日分界，跨时区使用前需要调整和测试
- 导出窗口是两天前零点到本次运行，不是严格的 48 小时
- 缺少设备来源时，明确标为「Apple 健康（设备来源未提供）」
- 缺失不是零，不生成手表没电时未采集的数据
- 大批历史样本可能触发 iOS 分享限制，需要缩小日期窗口分批补传
- 每次打开微信都可能触发同步，目前没有频率限制或自动重试队列
- 完成后保留结果弹窗，需要点「完成」
- 这是单用户自部署参考实现，不是多用户健康平台或医疗判断工具
- Python 自带 HTTP 服务仅绑定本机，需要反向代理提供 HTTPS、限流和连接保护

## 原理图

- [用户视角 SVG](docs/睡眠自动同步原理.svg)
- [技术架构 SVG](docs/睡眠自动同步技术架构.svg)
- [人物风格、参考来源与生成提示词](docs/视觉说明.md)

## 作者与许可

唐泽龙 · [zelong.vip](https://zelong.vip) · MIT License
