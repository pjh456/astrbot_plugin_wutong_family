# AstrBot 插件：wutong-family

将 QQ 的自然语言查询转发到 wutong-family 后端 API，并把结果返回到 QQ。

## 使用方式

- 命令：`/查 统计各区域用户数量`

## 配置

插件配置见 `_conf_schema.json`，通过 AstrBot WebUI 或配置文件设置：

- `base_url`：后端地址（默认 `http://127.0.0.1:8000`）
- `mode`：`session`（对话式）或 `data`（单次查询）
- `api_token`：可选的 API Token
- `timeout`：请求超时秒数
- `preview_rows`：结果预览行数

## 后端依赖

- 需要 wutong-family 后端已启动
- 默认使用 `/api/chat/sessions/...` 或 `/api/data/natural_language/`
