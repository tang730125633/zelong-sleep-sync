# API 与数据约定

独立服务端口默认 8768，只监听 127.0.0.1
以下路径需要通过自己的 HTTPS 域名访问
上传和读取使用不同凭证

| 接口 | 身份 | 用途 |
| --- | --- | --- |
| `POST /api/health/import` | `upload.key` | 上传原生睡眠样本 |
| `GET /api/health` | `read.key` | 日汇总与同步状态 |
| `GET /api/health/sleep?day=YYYY-MM-DD` | `read.key` | 单日明细 |
| `POST /api/health/mcp` | `read.key` | 无状态只读 MCP |

请求头：`Authorization: Bearer <对应密钥>`
POST 需要 `Content-Type: application/json`
密钥不要放在 URL 里
独立服务不提供浏览器登录、CORS 或原项目网页

## 手机上传格式

下例是合成样本，不是用户数据

```json
{
  "sync_source": "iphone-native",
  "sync_interval": 86400,
  "data": {
    "native": {
      "version": 1,
      "start": "2026-01-01T00:00:00+08:00",
      "end": "2026-01-03T12:00:00+08:00",
      "batches": [[{
        "kind": "Sleep",
        "value": "核心睡眠",
        "unit": "",
        "source": "",
        "start": "2026-01-01T23:00:00+08:00",
        "end": "2026-01-02T06:00:00+08:00"
      }]]
    }
  }
}
```

`sync_interval` 是同步状态判断的时间参考
它不会给 iPhone 创建定时任务

样本需有合法时区、类型和起止时间
独立 HTTP 入口只接受 `Sleep`
空批次、冲突分期、无可用完整记录会被拒绝
导出起点为当地零点，最多 9 天
iOS 自己的分享限制可能更早出现

成功回执：

```json
{"imported": 2, "changed": 2}
```

数值是转换后的日汇总与明细记录数
同一原始样本可能对应一条日汇总和一条明细
重复请求正常返回 200，但 `changed` 为 0
保存错误不会返回成功回执

## MCP 查询示例

请求体：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_health_history",
    "arguments": {
      "metric": "sleep_analysis",
      "start": "2026-01-01",
      "end": "2026-01-03"
    }
  }
}
```

工具：`get_health_overview`、`get_health_history`、`get_workouts`
当前原生导入不支持训练，`get_workouts` 不会凭空产生 Keep 记录
历史日期范围限制为 0—90 天
工具均为只读，不会改动手机或健康数据库

客户端需要支持带 Bearer 请求头的 HTTP MCP
客户端配置格式不同，这里不提供假定适配所有客户端的配置
可先用 `test_http.py` 验证服务，再接自己的 AI 客户端

## 数据和安全边界

SQLite 保存三类主要睡眠数据：

- `sleep_days`：每天的汇总
- `sleep_segments`：分期与区间
- `health_sync`：接收时间、状态、次数与变化数

授权失败不会读取或导入
非法导入只记录错误状态，不覆盖已有睡眠数据
`last success` 只能证明最后一次成功，不能证明未来每次都成功
服务器没有收到的手机请求，也不会自动进入接收统计

服务是单用户参考实现
共享给他人前，需要独立身份、数据隔离与运维设计
不要共用同一份健康数据库和读取密钥
