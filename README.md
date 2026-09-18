# 行知教研吧（xgz-bbs）

校内教师教研协作平台：**课题组发帖 · 评论 · 派任务 · 交材料 · 传附件**。

用 Python + Flask + SQLite 写的一套单机内网应用，部署在群晖 NAS 上，校内老师用浏览器访问。
典型场景：省级课题组，组长发布话题与任务，组员完成并提交附件，组长审核打分。

---

## 快速开始

```bash
# 本地开发
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python tools/seed_demo.py --reset     # 灌演示数据
.venv/Scripts/python server.py --port 8009          # 启动
```

Windows 上直接双击 `启动.bat` 也行。

| 演示账号 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123`（**初始默认值**） | 系统管理员 |
| `zhangls`（张老师） | `123456` | 课题组组长 |
| `wuls` `lils` `chenls` `wangls` `zhouls` | `123456` | 组员 |

> 上面的 `admin123` 只是首次建库时的初始密码。**线上实例的 admin 密码已由管理员自行修改，
> 此处与任何文档都不留存**；万一忘记，用 `tools/reset_admin.py` 重置即可。
> 跑冒烟测试时若 admin 改过密码，用环境变量传入：
> `BBS_ADMIN_PASSWORD=<当前密码> .venv/Scripts/python tools/smoke_test.py`

---

## 功能

**已实现（第一期全部）**

- 账号：管理员建号 / 批量粘贴名单导入 / 改密 / 停用
- 课题组：自建组、拉人入组、设组长、公开组或仅本组可见、归档
- 话题：讨论 / 公告 / 任务三种类型，置顶、加精、关闭、编辑删除
- **富文本正文**：加粗 / 斜体 / 下划线 / 删除线、项目符号与编号列表、引用、代码块、链接。
  粘贴进来的内容一律按纯文本收（从 Word、网页复制不会带进一堆乱样式）
- 回复：楼层式，支持楼中楼；`@姓名` 会触发通知；发帖与回复都能点「表情」插入表情
- 话题类型标签由管理员在后台自助增删（改名字 / 换颜色 / 调顺序 / 停用 / 删除）
- 任务：发任务时指派多人、设截止日期与满分；组员标记开始、提交文字 + 附件；
  组长逐人「确认通过（可打分）」或「打回（必填原因）」；打回后组员可重新提交
- 附件：拖拽上传、进度条、页内预览图片/PDF、按课题组隔离下载权限
- 通知：被回复、被 @、被指派、被打回、被审核
- **打包下载课题材料**：组长一键把整个课题的话题正文、全部讨论、任务完成情况、
  提交附件打成一个 zip（结题时直接用）
- 搜索：话题 / 回复内容 / 附件文件名
- 任务得分榜、我的任务（逾期标红）

**规划中**：全文检索（FTS5）、课题进度看板、企业微信推送、NAS 直存。

---

## 技术选型（有意为之的克制）

| 选择 | 原因 |
|---|---|
| Flask + Jinja2 服务端渲染 | 无 npm、无构建步骤，一套代码看得懂就改得动 |
| 手写一份 CSS | 不引前端框架，学校没人接手也不会烂掉 |
| SQLite + 原生 `sqlite3` | 十来个表，SQL 比 ORM 好读好改；上百人规模绰绰有余 |
| waitress 托管 | Windows / Linux 都能跑，替代 Flask 自带的开发服务器 |

**目录结构**

```
xgz-bbs/
├─ server.py            启动入口（--port / --data-dir / --host）
├─ 启动.bat             Windows 双击启动
├─ config.py            端口、上传上限、路径等集中配置
├─ app/
│  ├─ __init__.py       应用工厂 create_app()
│  ├─ schema.sql        全部建表语句（12 张表）
│  ├─ db.py             SQLite 连接 + 查询糖 + 轻量加字段迁移
│  ├─ models.py         共用查询
│  ├─ auth.py           密码哈希 + 权限装饰器
│  ├─ kinds.py          话题类型标签的唯一访问层（清单在库里）
│  ├─ richtext.py       富文本白名单清洗 / 纯文本提取 / 摘要
│  ├─ utils.py          附件落盘、时区、打包导出
│  ├─ views/            8 个蓝图：auth boards topics tasks files notify search admin
│  ├─ templates/        Jinja2 模板
│  └─ static/           app.css + app.js
├─ tools/               演示数据 / 冒烟测试 / 线上安全验收 / 备份 / 数据盘点 / 重置密码 / 换行符检查
├─ deploy/              群晖 NAS 部署脚本
└─ docs/                设计方案 · 部署说明 · UI 原型
```

---

## 关键设计

1. **任务就是话题**：`topics.kind='task'`，扩展信息放 `tasks` 表 1:1 挂着，
   讨论 / 附件 / 回复这一整套逻辑直接复用。
2. **状态挂在「人 × 任务」上**：`task_assignees` 表，一个任务指派 3 人就有 3 条记录，
   各自独立提交、独立审核、独立打分。
3. **附件多态关联**：`attachable_type + attachable_id`，一份上传逻辑同时服务
   帖子附件、任务说明附件、作业附件。
4. **附件一律 uuid 落盘**，原始文件名只存数据库 —— 防路径穿越、防中文乱码。
5. **权限收口在两个装饰器**：`@login_required`、`@board_role_required('leader')`，
   视图里不写 if 判断。
6. **富文本用白名单清洗，库里当成不可信来源**：入库前洗一次、渲染时再洗一次
   （`app/richtext.py`）。只放行 `p/br/strong/em/u/s/ul/ol/li/blockquote/code/pre/a` 等少量标签，
   属性只留 `<a>` 的 href；`href` 只认 http/https/mailto/tel 与相对路径，
   `javascript:`、`onerror`、`style` 一律丢弃，`<script>/<iframe>` 连内容一起丢。
   正文按行记格式（`body_format` = `text` / `html`），老帖保持纯文本渲染，一行数据不动。
7. 另有轻量 CSRF 校验、密码 pbkdf2 加盐哈希、附件扩展名黑名单。

---

## 文档

- [`docs/设计方案.md`](docs/设计方案.md) —— 完整设计：数据模型、权限矩阵、路由表
- [`docs/部署说明.md`](docs/部署说明.md) —— 群晖 NAS 部署、开机自启、备份、排障
- [`docs/prototype.html`](docs/prototype.html) —— 可点击 UI 原型（5 屏）

---

## 自检

```bash
# 本地 / 演示环境（会写数据，先复位）
python tools/seed_demo.py --reset
python tools/smoke_test.py                              # 全站冒烟 355 项，需先启动服务

# 线上（已有真实数据）——只读 + 隔离账号，绝不碰真实内容
BBS_SMOKE_BASE=http://192.168.10.201:8009 BBS_ADMIN_PASSWORD=<admin密码> \
  python tools/live_check.py

# 运维
python tools/backup_db.py --keep 30    # 安全备份（VACUUM INTO）并回读校验
python tools/inspect_data.py           # 只读盘点：数据量 + 各账号内容足迹
python tools/check_eol.py              # 检查 deploy/*.sh 是不是 LF 换行
python tools/reset_admin.py --list
```

> ⚠️ **线上有真实数据之后，不要再跑 `smoke_test.py`。** 它会确认/打回真实的提交、
> 调整真实的指派、在真实话题里发回复、还建测试账号 —— 在生产库上这些都是破坏性的。
> 线上验收请改用 `tools/live_check.py`（只读断言 + 一个用完即删的临时账号）。
