# 供水漏损分诊实验

决策树与模型选择的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。

### 两队合并校对
同一批管段可由两队分头校对：从原批次建立合校会话，系统留存共同底稿，并生成左右两份可编辑的校对副本。原批次只读、不再被整表覆盖。
- 合并口径：按 `segment_id` 逐段到底稿做三方比较。只有一侧改动则直接取其修订；两侧改成同值视为一致；两侧对同一字段改成不同值即「同项异改」，一侧删除另一侧改动即「删改相撞」，两队各自新增同一段号且内容不同即「同号新增异文」。
- 裁决：三类冲突只由负责人裁决，不按上传先后取舍。裁决前列出共同底稿与两侧内容及各自来源修订号；可选取一侧、自定义值或统一删除。
- 裁决失效：来源副本在裁决后再次改动，原裁决自动作废并重新打开冲突，必须重新核对后再裁决。
- 并发：所有副本写操作都带版本号，过期版本被拒绝（409），确认操作同样只能确认当前版本；任何改动都会使本队确认失效。
- 结案：只有全部冲突已裁决且整批通过可试验校验（字段合法、每类标签≥8、可完成一次默认参数训练）时，负责人才可结案；结案与生成可试验批次在同一事务生效。重复或中断后重试沿用同一次合并，不会重复成批。
- 保留：原批次、原分析任务、原分发简报均保留；新批次可如常发起试验。页面可编辑副本、提交裁决，并追查每段的合并来源与逐项改动日志。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18208；后端文档 http://localhost:19208/docs；数据库宿主端口 17708。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载；合校裁决与结案|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；编辑合校副本、确认与读取，但不能裁决或结案|
|viewer@demo.local|Demo123!|只读与下载；可查看合校副本、冲突与来源|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；PostgreSQL 16；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。合校实体为 MergeSession / ProofCopy / ProofEdit / MergeConflict（backend/src/models/merge.py），合并口径在 backend/src/services/merge.py，页面在 frontend/src/pages/merges.js。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=water-triage；FRONTEND_PORT=18208；BACKEND_PORT=19208；DB_PORT=17708；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1009/，源码不含日志/截图/测试报告。
合校接口：`GET/POST /merges`、`GET /merges/{id}`、`PUT/DELETE /merges/{id}/copies/{side}/segments/{segment_id}`、`POST /merges/{id}/copies/{side}/segments`、`POST /merges/{id}/copies/{side}/restore/{segment_id}`、`POST /merges/{id}/copies/{side}/confirm`、`POST /merges/{id}/conflicts/{conflict_id}/decide`、`POST /merges/{id}/close`、`GET /merges/{id}/edits`。`side` 取 `left`/`right`；副本写操作均需携带当前 `version`。演示 seed 会建立一次「演示合校 · 夜间流量复检」，内含同项异改、删改相撞和同号新增异文三类待裁决冲突。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
