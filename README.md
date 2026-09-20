# 文本发布前脱敏 API（FastAPI）

客户端提交**原文**与**多条正则脱敏规则**（含优先级与替换模板），服务端：

1. 找出每条有效规则的**全部命中**；
2. 对重叠区间按 **优先级 → 起始位置 → 规则顺序** 进行**确定性消解**，只替换胜出区间；
3. 返回脱敏文本、每个命中的处理状态，以及原文 ↔ 结果的区间映射（逐段 `segments` + 每个命中的坐标）；
4. 非法正则 / 非法替换模板**逐条报错**（带错误位置），不影响其他规则；
5. 纯内存处理，无数据库；提供健康检查。

## 一条命令启动

需要 Docker 与 Docker Compose（v2）。在项目根目录执行：

```bash
docker compose up --build -d
```

镜像构建时已安装全部依赖并完成初始化，容器启动即提供服务。停止：

```bash
docker compose down
```

## 访问地址与端口

| 用途 | 地址 |
| --- | --- |
| 健康检查 | http://localhost:8000/health |
| 脱敏接口 | `POST http://localhost:8000/api/v1/desensitize` |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI Schema | http://localhost:8000/openapi.json |

健康检查返回：`{"status":"ok"}`

## 必要配置（可选环境变量）

均有默认值，不配置即可直接运行：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | **宿主机**映射端口（`docker compose` 中 `${PORT:-8000}:8000`）。如 `PORT=9000 docker compose up --build -d` |
| `WORKERS` | `1` | Uvicorn worker 进程数；服务无共享状态，多 worker 安全 |

容器内监听固定为 `0.0.0.0:8000`。镜像内置 `HEALTHCHECK`，`docker ps` 可直接观察健康状态。

## 请求

`POST /api/v1/desensitize`

```json
{
  "text": "张三手机13800001111，工号99",
  "rules": [
    {"pattern": "1[3-9]\\d{9}", "template": "[手机]", "priority": 10},
    {"pattern": "\\d+", "template": "#", "priority": 1},
    {"pattern": "(?P<name>张)三", "template": "${name}某"}
  ]
}
```

规则字段：

| 字段 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `pattern` | 是 | — | Python 正则表达式（`re` 语法，支持命名组） |
| `template` | 否 | `***` | 替换模板，支持回引（见下） |
| `priority` | 否 | `0` | 优先级，**数值越大越优先**；同值按规则提交顺序 |
| `ignorecase` | 否 | `false` | 是否忽略大小写（`re.IGNORECASE`） |

### 替换模板回引语法

- Python 风格：`\1`、`\g<2>`、`\g<name>`
- `$` 风格（自动转换）：`$1`、`${12}`、`${name}`、`$<name>`、`$name`（字母/下划线开头）、`$&`（整条匹配）
- 多位数字建议始终使用 `${12}` / `\g<12>`，避免 `$12` 的歧义
- 未形成引用的 `$` 按字面量保留；正则中没有对应组（编号或命名）的模板会被判为非法模板并逐条报错

## 重叠消解规则（确定性）

所有命中按下列键排序后贪心选取，区间以**半开区间** `[start, end)` 判定：

1. **优先级**：`priority` 大者胜；
2. **起始位置**：相同优先级时，起点更早者胜；
3. **规则顺序**：起点也相同时，请求数组中更早提交的规则胜。

落败命中的状态为 `suppressed_overlap`，并通过 `suppressed_by_hit_id` / `suppressed_by_rule_index` 指向胜出者；胜出者状态为 `replaced`，仅胜出区间参与替换。**零宽匹配**（如 `a*` 产生的空匹配）与任何区间都不重叠，按插入处理。

## 响应（节选说明）

```json
{
  "original_text": "...",
  "masked_text": "脱敏后全文",
  "summary": {
    "rules_total": 3, "rules_valid": 3, "rules_invalid": 0,
    "hits_total": 3, "hits_replaced": 2, "hits_suppressed": 1
  },
  "rule_errors": [
    {"rule_index": 3, "kind": "pattern", "code": "invalid_regex",
     "message": "missing ), unterminated subpattern at position 0", "position": 0}
  ],
  "rule_reports": [
    {"rule_index": 0, "pattern": "...", "priority": 10, "valid": true,
     "total_matches": 2, "replaced_matches": 2, "suppressed_matches": 0}
  ],
  "hits": [
    {
      "hit_id": 1,
      "rule_index": 1,
      "status": "replaced",
      "source_start": 4, "source_end": 15,
      "match_text": "13800001111",
      "replacement": "[手机]",
      "result_start": 4, "result_end": 8,
      "groups": {},
      "suppressed_by_hit_id": null,
      "suppressed_by_rule_index": null
    }
  ],
  "segments": [
    {"type": "text", "source_start": 0, "source_end": 4,
     "result_start": 0, "result_end": 4, "text": "张三手机",
     "hit_id": null, "rule_index": null},
    {"type": "replacement", "source_start": 4, "source_end": 15,
     "result_start": 4, "result_end": 8, "text": "[手机]",
     "hit_id": 1, "rule_index": 0}
  ]
}
```

- `hits` 按 `(source_start, source_end, rule_index)` 稳定排序，`hit_id` 即该顺序下的序号（0 起）；
- 落败命中的 `replacement` / `result_start` / `result_end` 均为 `null`；
- `segments` 是全文对齐切片（未改动原文段 `text` + 替换段 `replacement`），拼接即得 `masked_text`，可据此在原文坐标与结果坐标间双向映射。

## 非法规则的处理

- HTTP 状态码仍为 `200`：非法规则是业务结果而非请求格式错误。
- `rule_errors` 逐条给出 `rule_index`、`kind`（`pattern`/`template`）、错误信息与字符位置；
- 非法规则不产生任何命中，其他规则正常匹配、消解与替换；
- 请求体本身不合法（如 `rules` 为空、字段类型错误）返回 `422`。

## 本地开发（不使用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行测试（需 `pytest`、`httpx`）：

```bash
pip install pytest httpx
python -m pytest -q
```

## 项目结构

```
app/
  __init__.py
  main.py        # FastAPI 路由与响应组装
  schemas.py     # Pydantic 请求/响应模型
  engine.py      # 匹配、确定性重叠消解、区间映射
  templates.py   # $ 风格回引归一化与模板静态校验
tests/           # 引擎与 API 测试（25 个用例）
Dockerfile       # 依赖与初始化纳入镜像
docker-entrypoint.sh
docker-compose.yml
requirements.txt
```

## 说明与边界

- 匹配基于 Unicode 字符串索引；按字符（码点）计偏移，非字节。
- 默认模板为 `***`；模板长度与原文不一致时，后续区间的结果坐标会相应平移，已由 `segments` / `hits` 精确给出。
- 服务不持久化任何内容，每次请求相互独立。
