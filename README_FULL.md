# NewAPI Logs Reader

一个面向 NewAPI 的只读日志浏览器，默认在 `3333` 端口提供 WebUI。

运行：`pip install -r requirements.txt && python server.py`，然后打开 <http://127.0.0.1:3333>。未配置 `DB_DSN` 时自动使用演示数据。

环境变量支持 `sqlite:///...`、`postgresql://...`、`mysql://...`，还可用 `LOG_DB_DSN` 指定独立日志库。请使用只读账号。

NewAPI 常规 `logs` 表主要保存计费和请求元数据，历史 prompt/response 通常不会写入。服务会探测可选的 `payload_logs` 表；不存在时，详情页会明确提示无法从历史日志推导正文。

功能包括分页、中文搜索、模型/用户/状态筛选、`model:gpt-4` 与 `status:500` 语法、日志详情抽屉、真实字段统计和数据库列探测。
