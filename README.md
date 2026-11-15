# 瓷光光学测量比对簿 · Method Agree

比较两台反射率仪的校准关系、原始读数差异与适用范围，经过资格复核后交付可追溯的配对数据。

瓷光光学实验室的12位测量工程师将同一陶瓷试片送入新旧仪器，再把两列读数粘到表格。目前的三个痛点是：把高相关性当作一致、默认参考仪器没有误差、资格记录缺少适用范围。上游为独立试件的配对测量，本项目替代比对计算、范围解释与资格记录，下游设备团队读取批准档案。全部样本均为合成演示数据。

## 快速启动

```sh
cp .env.example .env
docker compose up -d --build
docker compose ps
# 重复执行幂等，不覆盖已存在自然键对应的用户改动
docker compose exec -T backend python -m scripts.seed_demo
```

前端 http://localhost:18211，后端 http://localhost:19211，后端文档 http://localhost:19211/docs。前端 `/api/` 由Nginx剥去前缀后代理；直接请求后端时不带`/api`。

结束：`docker compose down -v --remove-orphans`。该命令删除本项目演示数据库与卷。

## 角色与演示账号

共用密码：`Ciguang2026!`。

| 账号 | 角色 | 权限与典型动作 |
|---|---|---|
| admin@ciguang.demo | admin / 实验室负责人 | 全部六页；登记、导入、研究、资格提案、批准、下载 |
| analyst@ciguang.demo | analyst / 测量工程师 | 全部六页；登记、导入、研究、资格提案、下载；不能批准 |
| viewer@ciguang.demo | viewer / 设备观察员 | 全部六页只读、研究内逆校准试算、下载已批准档案；不能写入或批准 |

JWT有效期8小时，密码带盐PBKDF2-SHA256。审计记录写入、批准、导出和被拒绝的写入。登录每来源每分钟最多30次。开发演示账号与签名密钥在对外部署前需要替换。

## 一天的工作

1. 08:30 江光使用analyst登录，查看3台仪器与540对配对读数。
2. 08:35 打开`ci-optics-pairs.csv`对应的“N02 · 稳定光源配对”，核对CG1-024试片。
3. 08:40 新建“N02 · 晨间资格复核”，λ=1、允许误差±2个百分点、参考范围0–100%。
4. 08:42 提交队列，由worker计算实际位于范围内的180对样本。
5. 08:45 看Deming关系与Bland–Altman差值图，区分一致性界限和界限的bootstrap CI。
6. 08:50 对候选读数50%试算研究内逆校准；超出实际观测范围会被拒绝。
7. 09:00 提出参考10–85%的限定资格，并写明稳定光源条件。未达原值互换门槛时，只能提“仅供校准研究”。
8. 09:15 程瓷使用admin复核批准；许澄使用viewer下载带研究指纹的配对CSV。

## 方法、输入与失败态

输入为20–5000对独立试片的同量纲读数。CSV表头`sample_code,reference_value,candidate_value`，编号唯一，两侧值有限且在登记量程内。数据集导入后不可修改；需要更正时导入新版本。每片同一研究中只保留一对读数，不把重复测量当独立样本。

**Deming。** 本项目明确`λ=Var(Y测量误差)/Var(X测量误差)`，由用户给出0.01–100；λ=1表示两侧误差方差相同。不是直接用观测样本方差相除。设中心平方和为Sxx、Syy、Sxy，`D=Syy−λSxx`，`b=(D+sqrt(D²+4λSxy²))/(2Sxy)`，`a=mean(Y)−b mean(X)`。实现使用等价的稳定数值分支避免消减；逆校准`Xhat=(Y−a)/b`。常量、接近零协方差或近零斜率不提供逆校准。假设两侧误差独立、均值零且方差比在范围内稳定。[NIST Deming说明](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/demfit.htm)；不同软件可能使用相反方向的方差比，本项目全栈统一上述定义。

**差值与界限。** 差值`d=Y−X`，横轴`m=(X+Y)/2`，偏差`mean(d)`，一致性界限`mean(d)±1.96 SD(d)`（样本SD，ddof=1）。以完整配对为单位，有放回重采样200–2000次（seed73），保存偏差、两条界限及Deming参数的2.5/97.5百分位CI。CI描述估计量的不确定性，LoA描述差值分布；两者不混用。[NIST差值图与bootstrap说明](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/blandalt.htm)。此处1.96是本项目采用的正态近似系数；不是NIST示例采用的近似2。

**适用边界。** 经典整体界限需要近似正态、独立且差值波动随测量范围稳定；bootstrap CI不会修复异方差。系统按均值中位数分成高低两半，差值SD最大/最小比>1.8时提示异方差线索，阻止原值互换门槛；这是保守的演示诊断规则，不是正式异方差检验。异方差样本需进一步分层或专门建模，当前只作整体描述。未实施加权Deming。

**资格门槛。** 原值互换要求下界CI的下端≥−允许误差、上界CI的上端≤允许误差、无上述异方差提示；相关系数不参与门槛。即使通过，也要admin确认具体用途与范围。“仅供校准研究”批准不表示原值可互换或校准后满足个体测量精度。资格范围必须在实际观测X范围内。研究内逆校准同时检查输入Y和输出X范围，超出拒绝，不报告个体预测区间。

**复算。** 指纹为稳定排序试片编号、两侧全部输入值、仪器代码/量纲/量程、数据集标识、λ、允许误差、范围、bootstrap次数、seed与算法版本的规范JSON SHA256。图展示全部选中点，坐标范围由真实读数、拟合线、界限、CI与业务允许误差共同确定，不用固定步长抽样。

## 六页与五实体

- 比对总览：三种样本条件、最新差值图和研究摘要。
- 仪器与样本：Instrument登记、PairDataset导入、PairedReading搜索分页；同页“增量归并”视图处理两台仪器分开回传的读数/撤回（见下节）。
- 建立研究：ComparisonRun的λ、范围、允许误差和bootstrap配置、队列列表。
- 结果解释：双图、参数/界限CI、范围限制、逆校准试算。
- 资格档案：Qualification提案、负责人批准、获批CSV。
- 操作留痕：最近100条写入与交付记录。

五实体各有model/schema/service/controller/route/前端API/Pinia store/页面链路。共用业务组件DatasetSelect、MethodSummary、AgreementPlot、RunTable；共用PageTitle/StatusBadge/ErrorNotice/EmptyState。useTask与usePolling隔离恢复与轮询行为。

## 双侧增量归并与研究样本集冻结

两台仪器分开回传，人工配对容易把撤回值与迟到记录混入研究。样本页“增量归并”视图以MergeChannel绑定两台仪器，回传按**试片号+侧+从v1连续递增的版本**归并，事件分为读数reading与撤回withdrawal，全部落库留痕。

- **乱序暂存与连续推进**：版本缺口前的事件保持staged，补齐前版后自动按序推进（applied）；缺口与待补版本持久化，进程中断恢复后继续等待补齐。
- **重复回传幂等**：自然键`(channel,side,sample_code,version)`去重，内容相同的重复回传记duplicate，不重复计入、不改变当前生效版本。
- **同版异值隔离阻断**：同一版本收到不同载荷（读数异值、读数与撤回互斥）时首条事件转quarantined，该试片永久blocked，阻断原因保留双方载荷；阻断后的新回传一律隔离，需人工处置。
- **撤回与旧版规则**：撤回事件推进后该侧无生效读数，试片进入withdrawn；严格旧版的重传按stale隔离，旧版不得复活撤回值或任何历史读数；撤回之后的更高版本读数可正常恢复。
- **冻结不可变样本集**：单事务加锁冻结当前全部双侧完整配对为SampleSet，回传与冻结并发时每份样本集取同一时点的双侧状态；快照行保存双侧数值与版本号，后续补传不改变旧研究。未入选试片逐条写入SampleSetExclusion，原因分三类：`conflict`同版异值阻断、`missing_version`乱序暂存前版未补齐、`missing_side`单侧从未回传或该侧已撤回，并附两侧版本与暂存/隔离依据。
- **恢复依据**：MergeSpecimen保存两侧当前生效版本、事件种类、数值与block_reason，MergeEvent保存全部暂存/生效/隔离事件；重启后待补版本、撤回历史与阻断依据均不丢失。

对应实体：MergeChannel、MergeEvent、MergeSpecimen、SampleSet、SampleSetRow、SampleSetExclusion（schema v2，`backend/migrations/002_incremental_merge.sql`）。后端路由前缀`/merge`：通道CRUD、单条与批量事件、事件流查询、冻结、样本集明细与CSV导出；观察员只读、工程师与负责人可写。seed追加`N03 · 双侧增量回传演示`通道，覆盖完整/单侧/撤回/冲突/缺版与一份冻结样本集。

## 技术、环境与目录

| 层 | 技术 |
|---|---|
| 前端 | Vue3、TypeScript、Pinia、Vue Router、Vite6.4.6、Nginx1.27 |
| Python API | Python3.11、FastAPI、SQLAlchemy、PyMySQL、PyJWT |
| 算法 | NumPy；手写Deming闭式解和配对bootstrap；独立特征值分解oracle在测试产物 |
| 任务 | 单Python worker，MySQL任务状态为事实源，Redis唤醒；重启恢复queued/running |
| 数据服务 | MySQL8.0、Redis7 AOF |

恰好frontend/backend/worker/db/redis五服务，均真实healthcheck。backend等db/redis；worker另外等backend初始化；frontend等backend。命名卷为数据库、Redis、后端/worker共享产物；当前结果在DB、CSV按记录实时生成，共享产物卷保留扩展通道。初始schema由metadata建立，`backend/migrations/001_initial.sql`记录v1自然键；未加入生产schema升级工具。

`backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}`；`backend/scripts/seed_demo.py`；`frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}`。

| 环境变量 | 默认 |
|---|---|
| COMPOSE_PROJECT_NAME | method-agree，顶层name与容器命名支持中文父目录 |
| FRONTEND_PORT/BACKEND_PORT/DB_PORT | 18211/19211/17711 |
| DB_PASSWORD | 本地演示数据库密码 |
| JWT_SECRET | 独立项目签名密钥 |
| SEED_DEMO | true时启动自动执行seed；失败记录日志，不阻塞API启动 |

本地开发：db/redis启动后，在backend安装requirements，设DATABASE_URL/REDIS_URL/JWT_SECRET，运行`uvicorn src.main:app --reload --port 8000`和单独`python -m src.worker`。前端`npm ci && npm run dev`，Vite默认将/api代理至19211（如本地API跑8000需修改代理目标）。

## 共享枚举与seed

后端`backend/src/constants/enums.py`定义Role、RunStatus、QualificationMode、QualificationStatus；RunStatus用于run_service/worker，Mode/Status用于qualification schema/service，角色授权位于utils/auth。前端`frontend/src/constants/enums.ts`定义roles/statuses/modes，供App、StatusBadge、Qualifications及权限界面使用。

seed以邮箱、仪器code、数据集名、(dataset_id,sample_code)、研究名、资格标题作自然键，不覆盖已存在记录。3台仪器、3份各180对样本：稳定光源、系统偏差、宽范围异方差；3次λ=1研究与稳定光源限定资格。数值生成固定seed73，两侧均含测量噪声。

测试、截图与TESTING记录位于批次根`test-artifacts/gb-0007/`。增量归并的本地行为测试位于`tests/test_merge_service.py`（SQLite内存库，40项服务规则）与`tests/test_merge_api.py`（TestClient鉴权/校验/并发冻结，21项），运行时需在仓库根设置`PYTHONPATH=backend`。License：MIT，见LICENSE。
