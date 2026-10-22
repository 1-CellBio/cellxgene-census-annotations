# cellxgene-census-annotations

[![Data license: CC BY 4.0](https://img.shields.io/badge/data-CC_BY_4.0-blue.svg)](./LICENSE)
[![Code license: MIT](https://img.shields.io/badge/code-MIT-green.svg)](./LICENSE-CODE)
[![validate](https://github.com/1-CellBio/cellxgene-census-annotations/actions/workflows/validate.yml/badge.svg)](https://github.com/1-CellBio/cellxgene-census-annotations/actions/workflows/validate.yml)

> **EN** — Community cell-type annotations for the **CELLxGENE Census**, published by the
> [1Cellbio](https://github.com/1-CellBio) single-cell analysis platform.
> **ZH** — **CELLxGENE Census 社区细胞注释共享仓**，由 [1Cellbio](https://github.com/1-CellBio)
> 单细胞分析平台的注释社区发布。

**English** · [中文](#中文)

---

## English

### What this is

A **data-only** repository of community-contributed cell-type annotations, keyed to
[CELLxGENE Census](https://cellxgene.cziscience.com/census/) cells by `soma_joinid`.
All data is written by the 1Cellbio publishing service on behalf of signed-in
contributors — this repository accepts no manual data edits.

Organisms (namespace directories): `homo_sapiens, mus_musculus`.

### Join key

`join_id = str(soma_joinid)` — unique within a Census build × organism. Namespaces are
isolated per organism under `annotations/datasets/<organism>/`.

### How data flows

1. A contributor annotates cells in the 1Cellbio app (human labels, AI-assisted labels,
   or both).
2. The 1Cellbio server validates, shards and pushes the labeled data on their behalf
   into `users/ghid_<GitHub id>/`; each contributor's `manifest.json` is an append-only
   registry of their uploads.
3. Community contributions are distilled into a versioned consensus layer
   (`consensus/community_anno/vN/`: default / overrides / provenance). Versions are
   append-only — once published they never change; `current.json` always points to the
   latest.

### Repository layout

```text
annotations/
├── config.json                    # repoPolicy — all quantitative limits (single source)
├── schemas/                       # JSON Schemas (manifest / origin / comments) + TSV contract
└── datasets/<organism>/           # homo_sapiens, mus_musculus
    ├── baseline/                  # pinned Census build (meta + cell_type_catalog)
    ├── users/ghid_<id>/           # per-contributor manifest.json (append-only uploads registry)
    │   └── community_anno/        # p_<pushId>.NNN.tsv shards + .index.json + .bloom
    ├── consensus/community_anno/  # current.json → vN/ (default / overrides / provenance)
    ├── moderation/                # label aliases (maintainer-governed)
    └── ontology/                  # ontology mappings (maintainer-governed)
scripts/validate.py                # CI validator — structural + JSON Schema v2, all-or-nothing
```

### Validation & governance

- **Two gates.** The 1Cellbio publishing service validates before writing; CI
  (`scripts/validate.py`) re-validates the whole tree on every push — all-or-nothing,
  with JSON Schema v2. Direct pushes or PRs cannot bypass either gate.
- **Provenance.** Data under `users/ghid_<id>/` may only be committed under that
  contributor's own GitHub identity (the commit email's noreply id must equal `ghid`);
  CI enforces this.
- **Governed surfaces.** Changes to `moderation/`, `ontology/`, `schemas/` or
  `config.json` require the author to be on the `MAINTAINERS` repository-variable
  allowlist. Fail-closed: if unset, every governance change is rejected.
- **Quantitative limits** (headline numbers; the single source is
  `annotations/config.json` → `repoPolicy`): shards ≤ 20000 rows and
  1048576 bytes · 1000000 rows per user field · labels ≤
  120 code points · comments ≤ 500 code points.
- **Counts.** `datasets` contributors/fields in `config.json` are updated manually by
  maintainers for overview purposes; live discovery goes through the 1Cellbio server's
  `/contributors` endpoint.

### License

- **Data** — everything under `annotations/` — is licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (full legal text in
  [`LICENSE`](./LICENSE)). **Attribution**: credit the individual contributors (the
  `ghid_*` directory name and the `login` recorded in its `manifest.json`) and this
  repository.
- **Code** (`scripts/`, `tools/`, CI) is licensed under [MIT](./LICENSE-CODE).

### Maintainers

Maintained by [1-CellBio](https://github.com/1-CellBio). Repository content is generated
from a versioned template — to change quantitative limits, change the contract first
and regenerate; never hand-edit `config.json` values.

---

## 中文

### 这是什么

社区共建的细胞类型注释**纯数据仓**，按 `soma_joinid` 对齐
[CELLxGENE Census](https://cellxgene.cziscience.com/census/) 细胞。全部数据由
1Cellbio 发布服务代表已登录贡献者写入——本仓不接受手工数据改动。

生物体命名空间目录：`homo_sapiens, mus_musculus`。

### join 键

`join_id = str(soma_joinid)`：census build × organism 内唯一；命名空间按 organism
目录隔离（`annotations/datasets/<organism>/`）。

### 数据如何流动

1. 贡献者在 1Cellbio 应用里注释细胞（人工、AI 辅助或两者结合）。
2. 1Cellbio 服务端校验、分片后代表贡献者推入 `users/ghid_<GitHub id>/`；每人的
   `manifest.json` 是只增不改的上传注册表。
3. 社区贡献汇聚为版本化共识层（`consensus/community_anno/vN/`：default /
   overrides / provenance）。版本只增不改——发布后不再变化；`current.json` 恒指向
   最新版本。

### 仓库布局

```text
annotations/
├── config.json                    # repoPolicy——全部定量边界（单源）
├── schemas/                       # JSON Schema（manifest / origin / comments）+ TSV 契约
└── datasets/<organism>/           # homo_sapiens, mus_musculus
    ├── baseline/                  # census 底座 pin（meta + cell_type_catalog）
    ├── users/ghid_<id>/           # 每贡献者 manifest.json（uploads 只增注册表）
    │   └── community_anno/        # p_<pushId>.NNN.tsv 组块分片 + .index.json + .bloom
    ├── consensus/community_anno/  # current.json → vN/（default / overrides / provenance）
    ├── moderation/                # 标签别名（维护者治理）
    └── ontology/                  # 本体映射（维护者治理）
scripts/validate.py                # CI 校验器——结构 + JSON Schema v2，all-or-nothing
```

### 校验与治理

- **两道闸门**：1Cellbio 发布服务先校验；CI（`scripts/validate.py`）对每次推送
  全量复校（all-or-nothing + JSON Schema v2）——直推 / PR 都绕不过。
- **溯源**：`users/ghid_<id>/` 下的数据只能以该贡献者自己的 GitHub 身份提交
  （commit email 的 noreply id 必须等于 `ghid`），CI 强制。
- **治理面**：`moderation/`、`ontology/`、`schemas/`、`config.json` 的变更要求
  作者在 repo variable `MAINTAINERS` 白名单内；未配置 = 全拒（fail-closed）。
- **定量边界**（摘要；单源在 `annotations/config.json` → `repoPolicy`）：分片
  ≤ 20000 行且 ≤ 1048576 B · 单人单字段 ≤
  1000000 行 · 标签 ≤ 120 码点 · 评论 ≤
  500 码点。
- **计数**：`config.json` 里 `datasets` 的 contributors/fields 由维护者手工更新、
  仅供概览；发现期实际走 1Cellbio 服务端 `/contributors` 端点。

### 许可

- **数据**（`annotations/` 下一切内容）按 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
  授权（法律全文见 [`LICENSE`](./LICENSE)）。**署名要求**：复用时注明贡献者
  （`ghid_*` 目录与其 `manifest.json` 里的 `login`）及本仓库来源。
- **代码**（`scripts/`、`tools/`、CI）按 [MIT](./LICENSE-CODE) 授权。

### 维护

由 [1-CellBio](https://github.com/1-CellBio) 维护。仓库内容由版本化模板生成——改
定量边界请先改契约再重新生成，勿手改 `config.json` 数值。
