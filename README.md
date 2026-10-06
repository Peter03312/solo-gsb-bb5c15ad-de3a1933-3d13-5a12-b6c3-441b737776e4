# 考古发掘层位记录系统（Archaeo Strata）

一个用于田野考古**探方—层位—遗物批次**登记的轻量 Web 应用。系统以“覆盖 / 被覆盖”关系维护层位图（Harris 矩阵的简化形式），在登记关系时强制做**成环检测**与**发掘顺序一致性校验**；矛盾操作不会静默丢失，而是给出**冲突路径**并**保留草稿**。所有操作写入只增不改的审计日志，支持按探方回放层位演变、导出 CSV / JSON 审计记录。

## 功能一览

- **探方管理**：创建探方（编号唯一），按探方组织全部数据。
- **层位登记**：编号 + 发掘顺序（同一探方内唯一），层位有“开放 / 已关闭”两种状态。
- **覆盖关系层位图**：
  - 边 `上层 → 下层` 表示“覆盖”，自动分层绘制 SVG（自上而下为晚 → 早）。
  - 新增关系执行两道校验：
    1. **DAG 防环**：候选边若使图成环，BFS 求出并展示具体冲突路径（如 `① → ③ → ② → ①`）。
    2. **发掘顺序一致**：覆盖方（上层）的发掘序号必须更大（更晚发掘），否则拒绝并展示矛盾双方。
  - 自环、跨探方、重复关系同样被拒绝。
- **遗物批次**：只能归入**当前开放层位**；层位一经关闭即**不可补录**（服务端强校验，绕过前端禁用选项也会被拒）。
- **草稿保留**：任何因矛盾被拒的关系/遗物登记都存入草稿箱，记录原因与冲突路径，可“取出重试”或丢弃；相同内容日后成功提交时草稿自动核销。
- **演变回放**：按探方以时间轴逐事件回放（开放层位 → 建立关系 → 登记遗物 → 关闭层位 → 冲突事件），可播放 / 暂停 / 单步 / 拖动进度，新节点与新边高亮，冲突帧显示警示条。
- **审计记录**：全部成功、冲突、信息类操作只增记录；可按探方 / 结果筛选，导出带 UTF-8 BOM 的 CSV（Excel 友好）或 JSON。

## 快速开始（Docker Compose，推荐）

```bash
docker compose up -d --build
# 打开 http://localhost:8000
```

数据持久化在命名卷 `archaeo-data`（容器内 `/data/archaeo.db`）。停止：

```bash
docker compose down          # 保留数据
docker compose down -v       # 同时删除数据卷
```

## 本地开发运行

需要 Python 3.11+：

```bash
pip install -r requirements-dev.txt
export FLASK_APP=wsgi.py
flask --debug run            # 开发服务器，默认 http://127.0.0.1:5000

# 或直接
python wsgi.py
```

数据库默认位于 `instance/archaeo.db`，首次请求时自动建表，也可手动初始化：

```bash
flask init-db
```

生产单机可用 gunicorn（同 Dockerfile）：

```bash
gunicorn --bind 0.0.0.0:8000 --workers 3 wsgi:app
```

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ARCH_DB_PATH` | `instance/archaeo.db` | SQLite 数据库文件路径（容器内为 `/data/archaeo.db`） |
| `ARCH_SECRET_KEY` | `dev-insecure-secret` | Flask session 签名密钥，**生产部署务必修改**（见 compose 文件） |
| `TZ` | 容器内 `Asia/Shanghai` | 审计时间以 UTC 存储，页面按浏览器/容器时区显示 |

## 业务规则速查

- 发掘顺序号越小 = 越早发掘 = 越靠下（被覆盖方）。
- 合法关系：`上层（序号大） → 下层（序号小）`，且全图不得出现回路。
- 顺序校验与防环相互独立：即使顺序方向正确，只要会成环依然拒绝（反之亦然），便于在审计中区分两类矛盾。
- 层位关闭不可重开（田野记录的封存语义）；关闭后遗物登记只能存草稿。

## 运行测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

测试覆盖：环检测 / 顺序校验图算法、完整登记流程、两类矛盾的草稿与审计留痕、关闭层位补录拦截、回放帧生成、CSV/JSON 导出。

## 项目结构

```
app/
  __init__.py     # 应用工厂、配置、模板过滤器
  db.py           # SQLite 连接与建表脚本
  graph.py        # 邻接表 / BFS 路径 / 成环检测 / 关系校验
  layout.py       # 最长路径分层布局（SVG 坐标）
  replay.py       # 由业务表推导逐事件回放帧
  routes.py       # 全部 HTTP 路由与业务编排
  utils.py        # 时间戳 / JSON 辅助
  templates/      # Jinja2 模板（含层位图 SVG 宏、回放页）
tests/            # pytest 单元 + 端到端测试
wsgi.py           # gunicorn 入口
Dockerfile / docker-compose.yml
```

## 说明与边界

- 本系统定位为单现场 / 小团队的记录工具，SQLite 零外部依赖、便于归档备份；如需多实例并发或在线协作，建议把数据库迁到 PostgreSQL 并引入用户认证。
- 审计日志只增不改，删除探方/层位的操作未开放，以保证考古记录可追溯。
