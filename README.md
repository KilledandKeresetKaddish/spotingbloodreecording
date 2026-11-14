# 🌙 月 · 记录

自己的经期 / 健康记录，跑在自己的服务器上，用手机和电脑的浏览器打开。

- **日历是主页**：上面一条是这个周期 —— 月经、spotting、排卵期、排卵日、LH 阳性，再加最多 2 个自选记号
- **记录页**：点一下 = 记上，长按（电脑上右键）= 填细节、写自己的内容；每次改动都能撤销
- **分类和项目**都能自己加、改名、排序；不用的只隐藏，不删除
- **周期页**：每个周期从月经第一天对齐着比
- **导出** JSON / CSV，导入时合并

规则和取舍写在 [SPEC.md](SPEC.md)。

## 本机试跑

需要 Python 3.12+。

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
HEALTH_DB=data/health.db uvicorn app.main:create_app --factory --reload
# 打开 http://127.0.0.1:8000
pytest
```

## 放到服务器上（Docker + Caddy）

App 里面没有登录，所以它只监听服务器自己的 `127.0.0.1`，由 Caddy 加上密码再转发出去。

```sh
git clone https://github.com/KilledandKeresetKaddish/spotingbloodreecording.git
cd spotingbloodreecording
cp .env.example .env                           # .env 只放在服务器上
mkdir -p data && sudo chown 10001:10001 data   # 容器里跑程序的用户是 10001
docker compose up -d --build
```

Caddyfile（Caddy 2.8 起这个指令叫 `basic_auth`，更早的版本叫 `basicauth`；密码要先用 `caddy hash-password` 生成哈希）：

```caddy
health.example.com {
	basic_auth {
		me <caddy hash-password 生成的哈希>
	}
	reverse_proxy 127.0.0.1:8000
}
```

如果 Caddy 也跑在 Docker 里：先看它在哪个网络（`docker inspect <caddy 容器> --format '{{json .NetworkSettings.Networks}}'`），再在服务器上建一个 `docker-compose.override.yml`（已经在 `.gitignore` 里）让这个 app 也加入那个网络：

```yaml
services:
  health:
    networks: [default, caddy]
networks:
  caddy:
    external: true
    name: caddy_default   # 换成 Caddy 所在的网络名
```

Caddyfile 里就写 `reverse_proxy health-log:8000`。`127.0.0.1` 上的端口可以留着，方便在服务器上用 curl 测。

更新：

```sh
git pull && docker compose up -d --build
```

## 备份

数据就是 `data/health.db` 这一个文件。程序开着的时候别直接复制它（WAL 模式下可能缺最近的改动），用 SQLite 自己的备份：

```sh
docker compose exec health python -c "import sqlite3; sqlite3.connect('/data/health.db').backup(sqlite3.connect('/data/backup.db'))"
# 得到 data/backup.db
```

或者在设置页点「导出 JSON」，这个文件以后也能再导入。

## 数据格式

导出的 JSON 是 `format: "health-log"`、`version: 1`，里面有 `settings`、`categories`、`items`、`entries`。别的 app 的数据先转成这个格式，再从设置页导入（合并，不用勾「从备份还原」）。

Ovumcy：

```sh
python tools/ovumcy_to_healthlog.py ovumcy-export.json health-log-import.json
```

Flo（账户数据导出里那个大 JSON）：

```sh
python tools/flo_to_healthlog.py flo-export.json health-log-import.json --before 2026-05-09
```

`--before` 只留那天之前的 Flo 记录，用在后面已经有别的 app（比如 Ovumcy）记录的时候。

两个脚本都会打印每样东西去了哪里。对应规则写在脚本开头；没有对应项目的（比如心情评分）都留在那天的笔记里，不会丢。转出来的文件是个人数据，别放进仓库。

## 目录

```
app/
  main.py        API 和网页
  cycles.py      周期、预测、排卵期的规则（纯函数，好测）
  db.py          SQLite 连接和迁移
  seed.py        默认的分类、项目、设置
  migrations/    数据库结构
web/             网页（原生 JS，不用打包）
tests/           pytest
```

## 隐私

仓库可能是公开的，所以代码、测试、文档里都不放个人数据（测试用的是编出来的 2030 年日期）。`.env`、`data/`、`*.db` 和导出文件都在 `.gitignore` 里。
