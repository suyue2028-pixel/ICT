# 三端运行、48小时观察与恢复

## 当前入口与启动

- 前端：<http://127.0.0.1:5173/>，追溯问答：<http://127.0.0.1:5173/qa>
- 后端 Swagger：<http://127.0.0.1:8010/docs>；就绪：<http://127.0.0.1:8010/health/ready>
- Agent 9001 为服务内部接口，需要服务凭据；前端使用普通用户短期令牌。

确保 Docker Desktop 已运行。Windows 本地三端停止后，使用现有脚本恢复；当前运行时先查看 status。

```powershell
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py status
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py start --no-build --mode live
# 只重启登记的 Agent，保留索引和其他进程
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py restart-agent --mode live
# 停止本脚本管理的 Windows 前端、Agent、同步进程；Docker 后台仍运行
& D:\cit\.venv\Scripts\python.exe D:\cit\ICT-team\scripts\run_fullstack_windows.py stop
# 停止本项目 Docker 服务并保留数据
cd D:\cit\后端开发
& 'C:\Users\lx\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe' compose stop
```

再次启动用上面的 `start --no-build --mode live`，它使用现有 Windows Compose overlay、原数据库卷和原始后端目录，不创建旧工程副本。已运行的登记进程不能重复 start，可用 stop 后 start。用户令牌过期时用 `run_fullstack_windows.py token` 更新，不发送管理密钥到浏览器。

后端代码更新后，从源目录运行 `scripts/deploy-timeliness.py`：先备份并校验dump、保存旧镜像标签，再构建、增量迁移、重建api/worker/beat；它是本机部署工具，文件名沿用历史。早期restore-backend命令现已增加迁移兼容保护，实际只读检查正确拒绝不认识0007的旧快照，不停止服务。不要绕过该保护手动切换旧镜像。

日志与测试：

```powershell
cd D:\cit\后端开发
& 'C:\Users\lx\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe' compose logs --tail 100 api worker beat
Get-Content D:\cit\ICT-team\reports\local\windows-fullstack\logs\agent.log -Tail 50
& D:\cit\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
& D:\cit\.venv\Scripts\python.exe -m alembic check
& D:\cit\.venv\Scripts\python.exe scripts\verify-upgrade-runtime.py
```

F5 继续使用已有 `backend/main.py` 的 uvicorn 调试配置（8011），数据库及任务服务用 Docker。正式本机联调走 8010，不能把 8011 与前端默认网关混用。

## 48小时观察

开始：北京时间 **2026-10-09 14:28:51**；计划结束：**2026-10-11 14:28:51**。ID：`bfb1b544-95e8-4011-b4ba-2ef3790da55e`。目前为 observing。

同一个 beat 新增每60秒的采样任务，复用 worker/Redis；持久化 `runtime_observations`、`runtime_samples`（新迁移0007）。记录各来源尝试/成功/新内容、失败、延迟、待采集与待索引数、outbox、API/Agent健康及 beat 心跳。采样任务收到消息只证明该时刻 worker/消息通路可用，不代表整分钟连续可用。没有观察窗口时不请求 Agent 健康接口。

采样间隔大于150秒记录未知区间；关机、Docker停止、队列拥塞或采样器异常均可能形成缺口，不自动归因为网络或主机故障。结束时最多标记 finished_pending_review 或 finished_with_gaps_or_failures，不自动宣布 SLA 通过。观察主要覆盖后端/Agent/任务链路，前端启动就绪另作即时检查。

在 2026-10-09 14:52 的检查中，已有19条采样、两段缺口，合计约446.7秒；原因尚未确认，不能抹去。采样中实际测得的组件失败数当时为0，并不能把缺失区间算作健康。最新状态以以下命令和数据库记录为准。

```powershell
cd D:\cit\后端开发
& D:\cit\.venv\Scripts\python.exe scripts\observe-runtime.py status
& D:\cit\.venv\Scripts\python.exe scripts\observe-runtime.py samples
# 无活动观察时启动新窗口；已有活动窗口时复用，避免重复采样
& D:\cit\.venv\Scripts\python.exe scripts\observe-runtime.py start --hours 48
```

本地状态/导出：`D:\cit\后端开发\output\runtime-observation.json`、`runtime-observation-samples-page1.json`。完整分页接口需要管理鉴权：`GET /api/v1/ops/observations/{id}/samples`。无需一直开终端，Docker/worker/beat 必须运行；停止期间不能采集。

## 恢复演练及性能

一致性 PostgreSQL 快照在独立数据库 `verify_restore_20261009t063458z_bcb1a724` 恢复成功，各表行数、迁移0007一致；通过独立数据库依赖调用 FastAPI 文档列表返回200，1076条真实文档与恢复库查询一致。原库未覆盖。数据库和备份保留供审查。

- PostgreSQL 结果：`D:\cit\后端开发\output\recovery-20261009T063458Z.json`
- dump：同目录 `recovery-20261009T063458Z.dump`，属于私密本地备份，勿提交。
- Agent SQLite 在线只读备份：`D:\cit\ICT-team\reports\local\windows-fullstack\index-recovery-20261009T063810Z.db`；quick_check=ok，所有表行数一致，原索引未替换。
- 完整主机、证书/凭据、模型缓存丢失后的恢复尚未演练；不因此声称灾难恢复全部通过。

恢复脚本每次创建新的专用验证库/文件，拒绝覆盖；不删除旧库或卷。命令：`scripts/verify-recovery.py`、团队目录的 `scripts/verify_index_recovery.py`。

性能实测20次列表读取，并发上限2、每页20条，全部200；25.23—109.19ms，中位44.33ms。20条虚构流水线批次3次，首次新增20、随后两次各20未变化；56.77—169.69ms，全部事务回滚。该结果不等于 Celery 端到端吞吐量或容量上限。报告 `output/bounded-performance.json`；本轮不报告小样本 P95。
