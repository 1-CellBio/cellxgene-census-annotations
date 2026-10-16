# cellxgene-census-annotations

census 社区细胞注释共享仓（1Cellbio 注释社区数据层）。

- **join 键**：`join_id = str(soma_joinid)`（census build × organism 内唯一；命名空间按 organism 目录隔离）
- **布局（v2）**：`annotations/datasets/<organism>/` 下——`users/ghid_<GitHub数字id>/` 每人 `manifest.json`（`uploads` append-only 注册表 + `legacy_fields` 冻结）+ `community_anno/`（`p_<pushId>.NNN.tsv` 组块分片 + `p_<pushId>.index.json` + `p_<pushId>.bloom`）+ `<field>.comments.json`；`baseline/{meta,cell_type_catalog}.json`（census 底座 pin）；`consensus/community_anno/`（`current.json` → `vN/` 版本目录：default/overrides/provenance，只增不改）
- **定量边界**：见 `annotations/config.json` 的 `repoPolicy`（分片 ≤20000 行且 ≤1048576B、label ≤120 码点、评论 ≤500 码点等）
- **License**：数据 CC-BY-4.0（署名）；代码 MIT
- **datasets 计数**：config.json 的 contributors/fields 由维护者手工更新（发现期实际走服务端 /contributors 端点，本表仅供概览）
- **治理改动**：moderation//ontology//schemas/config.json 的变更要求 author 在 repo variable MAINTAINERS 白名单内（反引号略——模板字面量转义噪声）（CI 强制，未配置=全拒）
- **校验**：`scripts/validate.py`（CI 全量，all-or-nothing + JSON Schema 实校验；服务端发布是第一道，CI 防直推/PR 绕过；`tools/**` 改动同触发）

由 1Cellbio 维护的模板生成（`scripts/annot-community/generate-repo-template.js`）；改动定量边界请走模板再生成，勿手改 config.json 数值。
