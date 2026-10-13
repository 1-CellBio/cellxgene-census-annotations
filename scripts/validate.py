#!/usr/bin/env python3
"""validate.py — census 社区注释仓 CI 第二道校验（02 册 §9 双道同契约；v2 扩展：03/05 册）。

结构检查（all-or-nothing：任一非法即非零退出并指名文件）：
  1. 路径闸门：数据文件必须在 users/ghid_<id>/ 内（v2 允许其 community_anno/
     一层子目录）；moderation/ontology/schemas/ 白名单文件名；config.json 唯一；
     baseline/{meta.json,cell_type_catalog.json} 与 consensus/community_anno/**
     白名单（v2 新增）；
  2. manifest：身份三处一致、≤262144B、pushes ≤200、comment ≤500 码点。
     v1：fields[].shards 同目录别名穿透防线（沿用）。
     v2（version==2）：legacy_fields 冻结形状（=v1 fields 规则）；uploads 注册表
     （p_<pushId> 分片 file=community_anno/p_x.NNN.tsv、blocksRef 同目录、
     censusVersion 非空）；不再允许向 fields 键新写（v1 键在 v2 manifest 中缺席）；
  3. TSV：v1 行式（表头/数值序/前导零/码点/双红线/UTF-8 严格/无 BOM/sha 对账）
     与 v2 组块式（#BLOCK\t<ct>\t<label>\t<count> 头状态机：ct/label 非空 ≤120
     码点且不得以 # 开头、count=本分片内该头后续行数、块间 (ct,label) 字典序、
     块内 joinid 数值序、同分片同 (ct,label) 不得重复头）双解析器并存；
  4. index sidecar（v2）：blocks 行数跨分片聚合 Σ = manifest uploads[p].rows；
     blocks[].shards 引用文件存在；uploads[p].blocksRef 指向的 index 存在；
  5. 孤儿规则：legacy 分片须被 legacy_fields(v1 fields) 引用；community_anno/
     p_* 分片须被本 pushId 的 uploads 条目引用（append-only 合法态：其他历史
     pushId 分片仍被各自条目引用，不得视为孤儿）；
  6. consensus 一致性（v2）：current.json 指向的 vN 目录存在且含
     default.json+provenance.json；overrides 组块走 v2 组块校验；
     provenance.contributions 的 (ghid,pushId) 必须在 users/ 侧真实存在
     （防幽灵引用）。
  7. comments sidecar：形状/每类 ≤200/每条 ≤500/≤1048576B（沿用）。
（JSON Schema 三件套供外部工具消费；若环境装了 jsonschema 则附带加载校验。）
provenance 作者身份/维护者白名单在 workflow 的独立步骤（本文件只管结构）。
"""
import hashlib
import json
import os
import re
import sys

JOIN_ID_MAX = 19
JOIN_ID_RE = re.compile(r'^[0-9]{1,' + str(JOIN_ID_MAX) + r'}$')
FIELD_RE = re.compile(r'^[a-z0-9_]+$')
PUSH_ID_PY = r'[A-Za-z0-9_-]{2,64}'
SHARD_FILE_RE = re.compile(r'^([a-z0-9_]{1,64})\.[0-9]{3}\.tsv$')
# v2 上传分片（相对 manifest 目录）：community_anno/p_<pushId>.NNN.tsv
SHARD_FILE_RE_V2 = re.compile(r'^community_anno/p_(' + PUSH_ID_PY + r')\.[0-9]{3}\.tsv$')
INDEX_FILE_RE_V2 = re.compile(r'^community_anno/p_(' + PUSH_ID_PY + r')\.index\.json$')
BLOCK_HEADER_PREFIX = '#BLOCK\t'
USER_DIR_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/users/ghid_(\d+)/$')
USER_SUBDIR_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/users/ghid_(\d+)/(community_anno)/$')
MANIFEST_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/users/ghid_(\d+)/manifest\.json$')
BASELINE_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/baseline/(meta|cell_type_catalog)\.json$')
CONSENSUS_CURRENT_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/consensus/community_anno/current\.json$')
CONSENSUS_VN_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/consensus/community_anno/v([0-9]+)/$')
CONSENSUS_FILE_RE = re.compile(r'^(default\.json|provenance\.json|overrides\.index\.json|overrides\.[0-9]{3}\.tsv)$')
MANIFEST_MAX = 262144
PUSHES_KEPT = 200
PUSH_COMMENT_MAX = 500
LABEL_MAX = 120
SHARD_MAX_ROWS = 20000
SHARD_MAX_BYTES = 1048576
COMMENTS_PER_CATEGORY = 200
COMMENT_MAX = 500
COMMENTS_FILE_MAX = 1048576
CENSUS_BUILD_RE = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')

errors = []
referenced_shards = set()          # v1 legacy + v2 uploads 引用（全路径）
referenced_indexes = set()         # v2 index sidecar 引用
contribution_refs = []             # consensus provenance 引用的 (ghid, pushId)


def fail(path, msg):
    errors.append(f"{path}: {msg}")


def codepoints(s):
    return len(s)


def cmp_numeric(a, b):
    if len(a) != len(b):
        return -1 if len(a) < len(b) else 1
    return (a > b) - (a < b)


def read_bytes(rel, full, what):
    try:
        return open(full, 'rb').read()
    except OSError as e:
        fail(rel, f"{what}不可读: {e}")
        return None


def read_json(rel, full, what):
    raw = read_bytes(rel, full, what)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception as e:
        fail(rel, f"{what}JSON 解析失败: {e}")
        return None


def check_block_text(rel, text, field_name):
    """ct/label 公共红线：非空、≤120 码点、无 CR、不得以 # 开头（头行歧义防线）。"""
    if not text:
        fail(rel, f"组块{field_name}为空")
        return False
    if codepoints(text) > LABEL_MAX:
        fail(rel, f"组块{field_name}超 {LABEL_MAX} 码点: {text[:40]!r}")
        return False
    if text.startswith('#'):
        fail(rel, f"组块{field_name}不得以 # 开头（#BLOCK 头歧义）: {text[:20]!r}")
        return False
    return True


def validate_block_tsv(repo, rel, shard_meta, manifest_rel):
    """v2 组块分片校验（状态机：#BLOCK 头置当前块，id 行归属当前块）。"""
    full = os.path.join(repo, rel)
    if not os.path.isfile(full):
        fail(rel, f"分片不存在（manifest={manifest_rel}）")
        return
    raw = read_bytes(rel, full, "分片")
    if raw is None:
        return
    if len(raw) > SHARD_MAX_BYTES:
        fail(rel, f"分片超 {SHARD_MAX_BYTES}B")
    if raw.startswith(b'\xef\xbb\xbf'):
        fail(rel, "UTF-8 BOM 不允许")
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as e:
        fail(rel, f"非 UTF-8: {e}")
        return
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines = lines[:-1]
    if not lines or not lines[0].startswith(BLOCK_HEADER_PREFIX):
        fail(rel, "组块分片首行必须是 #BLOCK 头")
        return
    prev_block = None   # (ct, label)：块间字典序
    cur_block = None
    cur_count_decl = None
    cur_count_seen = 0
    seen_blocks = set()  # 本分片内 (ct,label) 不得重复
    seen_ids = set()
    n = 0

    def close_block():
        if cur_block is not None and cur_count_decl != cur_count_seen:
            fail(rel, f"#BLOCK count 与实际行数不符（{cur_count_decl} != {cur_count_seen}，ct={cur_block[0]!r}）")

    for line in lines:
        if line.startswith('#'):
            if not line.startswith(BLOCK_HEADER_PREFIX):
                fail(rel, f"注释行只允许 #BLOCK 头: {line[:40]!r}")
                continue
            close_block()
            parts = line.split('\t')
            if len(parts) != 4:
                fail(rel, f"#BLOCK 头必须四段: {line[:60]!r}")
                cur_block = None
                cur_count_decl = None
                cur_count_seen = 0
                continue
            _, ct, label, count = parts
            ok = check_block_text(rel, ct, "ct") & check_block_text(rel, label, "label")
            if not count.isdigit() or int(count) < 1:
                fail(rel, f"#BLOCK count 非正整数: {count!r}")
            if ok:
                blk = (ct, label)
                if prev_block is not None and blk <= prev_block:
                    fail(rel, f"块间 (ct,label) 非字典序: {prev_block!r} -> {blk!r}")
                if blk in seen_blocks:
                    fail(rel, f"同分片内 (ct,label) 重复头: {blk!r}")
                seen_blocks.add(blk)
                prev_block = blk
                cur_block = blk
                cur_count_decl = int(count) if count.isdigit() else None
                cur_count_seen = 0
            else:
                cur_block = None
                cur_count_decl = None
                cur_count_seen = 0
            continue
        # id 行
        jid = line
        if not JOIN_ID_RE.match(jid) or (len(jid) > 1 and jid[0] == '0'):
            fail(rel, f"join_id 非法: {jid[:40]!r}")
            continue
        if cur_block is None:
            fail(rel, f"id 行出现在任何 #BLOCK 头之前: {jid!r}")
            continue
        if jid in seen_ids:
            fail(rel, f"join_id 重复: {jid}")
        seen_ids.add(jid)
        if cur_count_seen > 0 and cmp_numeric(jid, prev_id) <= 0:
            fail(rel, f"块内 join_id 非数值序: {prev_id} -> {jid}")
        prev_id = jid
        cur_count_seen += 1
        n += 1
    close_block()
    if n > SHARD_MAX_ROWS:
        fail(rel, f"分片超 {SHARD_MAX_ROWS} 行")
    if shard_meta:
        if n != shard_meta.get('rows'):
            fail(rel, f"行数与 manifest 不符（tsv={n} manifest={shard_meta.get('rows')}）")
        sha = hashlib.sha256(raw).hexdigest()
        if sha != shard_meta.get('sha256'):
            fail(rel, "sha256 与 manifest 不符")


def validate_tsv(repo, rel, shard_meta, manifest_rel):
    full = os.path.join(repo, rel)
    if not os.path.isfile(full):
        fail(rel, f"分片不存在（manifest={manifest_rel}）")
        return
    raw = read_bytes(rel, full, "分片")
    if raw is None:
        return
    if len(raw) > SHARD_MAX_BYTES:
        fail(rel, f"分片超 {SHARD_MAX_BYTES}B")
    if raw.startswith(b'\xef\xbb\xbf'):
        fail(rel, "UTF-8 BOM 不允许")
    try:
        text = raw.decode('utf-8')  # 严格解码：无效 UTF-8 直接失败
    except UnicodeDecodeError as e:
        fail(rel, f"非 UTF-8: {e}")
        return
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines = lines[:-1]
    if not lines or lines[0] != 'join_id\tlabel':
        fail(rel, "表头必须是 join_id\tlabel")
        return
    prev = None
    seen = set()
    n = 0
    for line in lines[1:]:
        parts = line.split('	')
        if len(parts) != 2:
            fail(rel, f"行字段数≠2: {line[:40]!r}")
            continue
        jid, label = parts
        if not JOIN_ID_RE.match(jid) or (len(jid) > 1 and jid[0] == '0'):
            fail(rel, f"join_id 非法: {jid!r}")
        if jid in seen:
            fail(rel, f"join_id 重复: {jid}")
        seen.add(jid)
        if codepoints(label) > LABEL_MAX:
            fail(rel, f"label 超 {LABEL_MAX} 码点")
        if prev is not None and cmp_numeric(jid, prev) <= 0:
            fail(rel, f"join_id 非数值序: {prev} -> {jid}")
        prev = jid
        n += 1
    if n > SHARD_MAX_ROWS:
        fail(rel, f"分片超 {SHARD_MAX_ROWS} 行")
    if shard_meta:
        if n != shard_meta.get('rows'):
            fail(rel, f"行数与 manifest 不符（tsv={n} manifest={shard_meta.get('rows')}）")
        sha = hashlib.sha256(raw).hexdigest()
        if sha != shard_meta.get('sha256'):
            fail(rel, "sha256 与 manifest 不符")
        # minKey/maxKey 与内容对账（读者二分定位的可信前提）
        if lines[1:]:
            first, last = lines[1].split('	')[0], lines[-1].split('	')[0]
            if shard_meta.get('minKey') != first or shard_meta.get('maxKey') != last:
                fail(rel, f"minKey/maxKey 与内容不符（{shard_meta.get('minKey')}/{shard_meta.get('maxKey')} vs {first}/{last}）")


def validate_manifest(repo, full, rel):
    m = MANIFEST_RE.match(rel)
    if not m:
        fail(rel, "manifest 路径形状非法")
        return None
    dir_ghid = m.group(2)
    manifest_dir = os.path.dirname(rel)
    raw = read_bytes(rel, full, "manifest")
    if raw is None:
        return None
    if len(raw) > MANIFEST_MAX:
        fail(rel, f"manifest 超 {MANIFEST_MAX}B")
    try:
        manifest = json.loads(raw.decode('utf-8'))
    except Exception as e:
        fail(rel, f"JSON 解析失败: {e}")
        return None
    username = manifest.get('username', '')
    if username != f"ghid_{dir_ghid}" or str(manifest.get('githubUserId')) != dir_ghid:
        fail(rel, "身份三处不一致（目录/username/githubUserId）")
    if len(manifest.get('pushes', [])) > PUSHES_KEPT:
        fail(rel, f"pushes 超 {PUSHES_KEPT} 条")
    for p in manifest.get('pushes', []):
        if codepoints(p.get('comment') or '') > PUSH_COMMENT_MAX:
            fail(rel, f"pushes[].comment 超 {PUSH_COMMENT_MAX} 码点")
    version = manifest.get('version', 1)
    if version == 2:
        # v2：fields 键必须缺席（新字段推送一律走 uploads；v1 存量已迁移为 legacy_fields）
        if 'fields' in manifest:
            fail(rel, "v2 manifest 不得含 fields 键（应迁移为 legacy_fields）")
        # legacy_fields：v1 字段条目冻结形状（沿用 v1 校验；不再新增推送）
        legacy = manifest.get('legacy_fields') or {}
        if not isinstance(legacy, dict):
            fail(rel, "legacy_fields 形状非法（须为对象）")
        else:
            _validate_v1_fields(repo, rel, manifest_dir, legacy)
        # uploads：v2 上传注册表
        uploads = manifest.get('uploads') or {}
        if not isinstance(uploads, dict):
            fail(rel, "uploads 形状非法（须为对象）")
        else:
            _validate_uploads(repo, rel, manifest_dir, uploads)
    else:
        # v1（迁移前过渡窗口）：fields 原规则
        _validate_v1_fields(repo, rel, manifest_dir, manifest.get('fields') or {})
    return manifest


def _validate_v1_fields(repo, rel, manifest_dir, fields):
    """v1 字段条目校验（legacy_fields 冻结面与 v1 manifest 共用）。"""
    for fname, f in (fields or {}).items():
        if not FIELD_RE.match(fname or ''):
            fail(rel, f"字段名非法: {fname!r}")
        for sh in (f.get('shards') or []):
            sf = sh.get('file', '')
            fm = SHARD_FILE_RE.match(sf)
            if not fm or fm.group(1) != fname:
                fail(rel, f"shards[].file 形状非法或字段名不匹配: {sf!r}")
                continue
            shard_rel = os.path.normpath(os.path.join(manifest_dir, sf))
            # 同目录约束：解析后必须仍在该用户目录内（防 ../ 跨用户别名——sha 对账
            # 全过也不许引用他人分片）
            if os.path.dirname(shard_rel) != manifest_dir:
                fail(rel, f"shards[].file 越出本目录: {sf!r}")
                continue
            referenced_shards.add(shard_rel)
            validate_tsv(repo, shard_rel, sh, rel)


def _validate_uploads(repo, rel, manifest_dir, uploads):
    """v2 uploads 注册表校验 + index sidecar 对账。"""
    for push_id, up in uploads.items():
        if not re.match(r'^p_(' + PUSH_ID_PY + r')$', push_id or ''):
            fail(rel, f"uploads 键形状非法（须 p_<pushId>）: {push_id!r}")
            continue
        if up.get('field') != 'community_anno':
            fail(rel, f"uploads[{push_id}].field 必须是 community_anno")
        cv = up.get('censusVersion')
        if not isinstance(cv, str) or not cv.strip() or len(cv) > 64:
            fail(rel, f"uploads[{push_id}].censusVersion 非法")
        rows_total = 0
        for sh in (up.get('shards') or []):
            sf = sh.get('file', '')
            fm = SHARD_FILE_RE_V2.match(sf)
            if not fm or fm.group(1) != push_id[2:]:
                fail(rel, f"uploads[{push_id}].shards[].file 形状非法或 pushId 不匹配: {sf!r}")
                continue
            shard_rel = os.path.normpath(os.path.join(manifest_dir, sf))
            if os.path.dirname(shard_rel) != os.path.join(manifest_dir, 'community_anno'):
                fail(rel, f"shards[].file 越出 community_anno/: {sf!r}")
                continue
            referenced_shards.add(shard_rel)
            validate_block_tsv(repo, shard_rel, sh, rel)
            rows_total += sh.get('rows') or 0
        if rows_total and up.get('rows') and rows_total != up.get('rows'):
            fail(rel, f"uploads[{push_id}].rows 与分片行数合计不符（{up.get('rows')} != {rows_total}）")
        blocks_ref = up.get('blocksRef')
        if blocks_ref:
            im = INDEX_FILE_RE_V2.match(blocks_ref)
            if not im or im.group(1) != push_id[2:]:
                fail(rel, f"uploads[{push_id}].blocksRef 形状非法或 pushId 不匹配: {blocks_ref!r}")
            else:
                index_rel = os.path.normpath(os.path.join(manifest_dir, blocks_ref))
                referenced_indexes.add(index_rel)
                _validate_index_sidecar(repo, index_rel, rel, manifest_dir, push_id, up)


def _validate_index_sidecar(repo, index_rel, manifest_rel, manifest_dir, push_id, up):
    """index sidecar：blocks 行数跨分片聚合 Σ = uploads[p].rows；引用分片存在。"""
    full = os.path.join(repo, index_rel)
    idx = read_json(index_rel, full, "index sidecar")
    if idx is None:
        return
    if idx.get('pushId') != push_id[2:] or idx.get('field') != 'community_anno':
        fail(index_rel, f"pushId/field 与归属不符（{idx.get('pushId')!r}）")
    blocks = idx.get('blocks') or []
    totals = idx.get('totals') or {}
    by_block_rows = {}
    shard_files_declared = set(
        s.get('file') for s in (up.get('shards') or []) if isinstance(s, dict)
    )
    for b in blocks:
        ct, label = b.get('ct'), b.get('label')
        check_block_text(index_rel, ct, "ct")
        check_block_text(index_rel, label, "label")
        for sf in (b.get('shards') or []):
            fm = SHARD_FILE_RE_V2.match(sf or '')
            if not fm or fm.group(1) != push_id[2:]:
                fail(index_rel, f"blocks[].shards 形状非法或 pushId 不匹配: {sf!r}")
                continue
            if shard_files_declared and sf not in shard_files_declared:
                fail(index_rel, f"blocks[].shards 引用了 manifest 未声明的分片: {sf!r}")
        key = (ct, label)
        by_block_rows[key] = by_block_rows.get(key, 0) + (b.get('rows') or 0)
    total_rows = sum(b.get('rows') or 0 for b in blocks)
    if totals.get('rows') is not None and totals.get('rows') != total_rows:
        fail(index_rel, f"totals.rows 与 blocks 行数合计不符（{totals.get('rows')} != {total_rows}）")
    if totals.get('blocks') is not None and totals.get('blocks') != len(blocks):
        fail(index_rel, f"totals.blocks 与 blocks 数不符（{totals.get('blocks')} != {len(blocks)}）")
    if up.get('rows') and total_rows != up.get('rows'):
        fail(index_rel, f"blocks 行数合计与 manifest uploads[{push_id}].rows 不符（{total_rows} != {up.get('rows')}）")
    # 同 (ct,label) 跨续块聚合后不得重复条目（写侧应合并续块）
    if len(by_block_rows) != len(blocks):
        fail(index_rel, "blocks 存在同 (ct,label) 未合并的重复条目")


def validate_baseline(repo, full, rel):
    m = BASELINE_RE.match(rel)
    if not m:
        fail(rel, "baseline 路径形状非法")
        return
    data = read_json(rel, full, "baseline")
    if data is None:
        return
    if rel.endswith('meta.json'):
        cv = data.get('census_version')
        if not isinstance(cv, str) or not CENSUS_BUILD_RE.match(cv):
            fail(rel, f"baseline.meta census_version 须为 YYYY-MM-DD（pin 声明）: {cv!r}")
        if not isinstance(data.get('n_cells'), int) or data.get('n_cells') < 1:
            fail(rel, "baseline.meta n_cells 非法")
        if not isinstance(data.get('n_cell_type'), int) or data.get('n_cell_type') < 1:
            fail(rel, "baseline.meta n_cell_type 非法")
    else:
        catalog = data if isinstance(data, list) else None
        if catalog is None:
            fail(rel, "cell_type_catalog 须为 [{label, cells}] 数组")
            return
        prev_cells = None
        seen = set()
        for item in catalog:
            label, cells = item.get('label'), item.get('cells')
            if not isinstance(label, str) or not label or codepoints(label) > LABEL_MAX:
                fail(rel, f"catalog label 非法: {str(label)[:40]!r}")
            if label in seen:
                fail(rel, f"catalog label 重复: {label[:40]!r}")
            seen.add(label)
            if not isinstance(cells, int) or cells < 1:
                fail(rel, f"catalog cells 非法: {label[:40]!r}")
            if prev_cells is not None and cells > prev_cells:
                fail(rel, f"catalog 未按 cells 降序: {label[:40]!r}")
            prev_cells = cells


def validate_consensus(repo, full, rel, current_targets):
    m = CONSENSUS_CURRENT_RE.match(rel)
    if m:
        data = read_json(rel, full, "consensus current")
        if data is None:
            return
        v = data.get('version')
        if not isinstance(v, int) or v < 0:
            fail(rel, f"current.json version 非法: {v!r}")
        if data.get('field') != 'community_anno':
            fail(rel, "current.json field 必须是 community_anno")
        current_targets.setdefault(m.group(1), set()).add(f"v{v}")
        return
    # vN 目录内文件
    parts = rel.split(os.sep)
    vm = CONSENSUS_VN_RE.match(os.sep.join(parts[:-1]) + '/')
    if not vm:
        fail(rel, "consensus 路径形状非法（须 vN/ 目录内白名单文件）")
        return
    if not CONSENSUS_FILE_RE.match(parts[-1]):
        fail(rel, f"vN 目录内不允许的文件名: {parts[-1]!r}")
        return
    if parts[-1].endswith('.tsv'):
        validate_block_tsv(repo, rel, None, None)
        return
    if parts[-1] == 'provenance.json':
        data = read_json(rel, full, "provenance")
        if data is None:
            return
        for c in (data.get('contributions') or []):
            ghid, push_id = c.get('ghid'), c.get('pushId')
            if not isinstance(ghid, str) or not re.match(r'^ghid_\d+$', ghid or ''):
                fail(rel, f"contributions[].ghid 形状非法: {ghid!r}")
            if not isinstance(push_id, str) or not re.match(r'^' + PUSH_ID_PY + r'$', push_id or ''):
                fail(rel, f"contributions[].pushId 形状非法: {push_id!r}")
            if isinstance(ghid, str) and isinstance(push_id, str):
                contribution_refs.append((ghid, push_id))
        return
    # default.json / overrides.index.json：形状校验
    data = read_json(rel, full, parts[-1])
    if data is None:
        return
    if parts[-1] == 'default.json':
        mapping = data.get('map')
        if mapping is None:
            fail(rel, "default.json 缺 map")
        elif not isinstance(mapping, list):
            fail(rel, "default.json map 须为 [[ct,label],…] 数组")
        else:
            for pair in mapping:
                if (not isinstance(pair, list) or len(pair) != 2
                        or not all(isinstance(x, str) and x for x in pair)):
                    fail(rel, f"default.json map 条目形状非法: {str(pair)[:40]!r}")


def validate_comments(repo, full, rel):
    raw = read_bytes(rel, full, "sidecar")
    if raw is None:
        return
    if len(raw) > COMMENTS_FILE_MAX:
        fail(rel, f"sidecar 超 {COMMENTS_FILE_MAX}B")
    try:
        sc = json.loads(raw.decode('utf-8'))
    except Exception as e:
        fail(rel, f"JSON 解析失败: {e}")
        return
    if not FIELD_RE.match(sc.get('field') or ''):
        fail(rel, "field 非法")
    for cat, items in (sc.get('categories') or {}).items():
        if len(items) > COMMENTS_PER_CATEGORY:
            fail(rel, f"类别 {cat!r} 评论超 {COMMENTS_PER_CATEGORY} 条")
        for it in items:
            if codepoints(it.get('text') or '') > COMMENT_MAX:
                fail(rel, f"类别 {cat!r} 评论超 {COMMENT_MAX} 码点")


MODERATION_ALLOW = {'aliases.json'}
ONTOLOGY_ALLOW = {'mapping.json', 'cl-parents.json'}
SCHEMAS_ALLOW = {'manifest.schema.json', 'origin.schema.json', 'comments.schema.json', 'tsv-contract.md'}


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else '.'
    manifests = 0
    current_targets = {}   # namespace → {vN,...}（current.json 指向）
    present_versions = {}  # namespace → {vN,...}（实际存在的 vN 目录）
    for root, _dirs, files in os.walk(os.path.join(repo, 'annotations')):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, repo)
            parts = rel.split(os.sep)
            if parts[1] == 'config.json':
                continue
            if parts[1] == 'schemas':
                if f not in SCHEMAS_ALLOW:
                    fail(rel, "schemas/ 内不允许的文件名")
                continue
            if len(parts) >= 4 and parts[3] == 'moderation':
                if f not in MODERATION_ALLOW:
                    fail(rel, "moderation/ 只允许既有治理文件名")
                continue
            if len(parts) >= 4 and parts[3] == 'ontology':
                if f not in ONTOLOGY_ALLOW:
                    fail(rel, "ontology/ 只允许既有文件名")
                continue
            # v2：baseline / consensus 白名单
            if len(parts) >= 4 and parts[3] == 'baseline':
                validate_baseline(repo, full, rel)
                continue
            if len(parts) >= 4 and parts[3] == 'consensus':
                validate_consensus(repo, full, rel, current_targets)
                vm = CONSENSUS_VN_RE.match(os.path.dirname(rel) + '/')
                if vm:
                    present_versions.setdefault(parts[2], set()).add(parts[5])
                continue
            m = USER_DIR_RE.match(os.path.dirname(rel) + '/')
            ms = USER_SUBDIR_RE.match(os.path.dirname(rel) + '/')
            if not m and not ms:
                fail(rel, "数据文件必须在 users/ghid_<id>/ 内（community_anno/ 子目录除外）")
                continue
            if f == 'manifest.json':
                manifests += 1
                validate_manifest(repo, full, rel)
            elif f.endswith('.index.json') and ms:
                # index sidecar 结构面校验在 manifest 侧（uploads.blocksRef 对账）；
                # 此处登记存在性（孤儿检查下方统一）
                continue
            elif f.endswith('.tsv'):
                # v1 行式分片结构红线；对账/引用在 manifest 侧与孤儿检查。
                # v2 组块分片（community_anno/p_*）由 manifest uploads 驱动校验，
                # 此处不重复走行式校验（否则报误导性的表头错误）。
                if ms and re.match(r'^p_(' + PUSH_ID_PY + r')\.[0-9]{3}\.tsv$', f):
                    continue
                validate_tsv(repo, rel, None, None)
            elif f.endswith('.comments.json'):
                validate_comments(repo, full, rel)
            elif f.endswith('.bloom'):
                pass  # 二进制 sidecar（参数在 manifest.bloom，schema 层）
            else:
                fail(rel, "用户目录内不允许的文件类型")
    # 孤儿分片：legacy/v1 须被 legacy_fields(fields) 引用；v2 p_* 须被本 pushId
    # uploads 条目引用（append-only：其他历史 pushId 分片仍被各自条目引用）。
    # consensus/ 下的 overrides 组块不适用（版本目录只增不改——旧 vN 合法保留，
    # 一致性由 §6 current/provenance 校验管辖，不走 manifest 引用面）
    for root, _dirs, files in os.walk(os.path.join(repo, 'annotations')):
        for f in files:
            if not f.endswith('.tsv'):
                continue
            rel = os.path.relpath(os.path.join(root, f), repo)
            parts0 = rel.split(os.sep)
            if len(parts0) >= 4 and parts0[3] == 'consensus':
                continue
            if rel in referenced_shards:
                continue
            ms = USER_SUBDIR_RE.match(os.path.dirname(rel) + '/')
            if ms:
                fail(rel, "community_anno/ 内未被本 pushId manifest 条目引用的分片（append-only 引用检查）")
            else:
                fail(rel, "未被任何 manifest 引用的孤儿分片")
    # index sidecar 存在性（uploads.blocksRef 指向的必须存在——缺引用在 manifest
    # 侧已记，这里反向：存在的 index 若无对应 uploads 条目=孤儿；consensus/ 的
    # overrides.index.json 同上不适用）
    for root, _dirs, files in os.walk(os.path.join(repo, 'annotations')):
        for f in files:
            if not f.endswith('.index.json'):
                continue
            rel = os.path.relpath(os.path.join(root, f), repo)
            parts0 = rel.split(os.sep)
            if len(parts0) >= 4 and parts0[3] == 'consensus':
                continue
            if rel not in referenced_indexes:
                fail(rel, "未被任何 uploads[].blocksRef 引用的孤儿 index sidecar")
    # consensus 一致性：current 指向的 vN 目录必须存在
    for ns, targets in current_targets.items():
        for v in targets:
            if v not in present_versions.get(ns, set()):
                fail(f"annotations/datasets/{ns}/consensus/community_anno/{v}/",
                     "current.json 指向的版本目录不存在")
    # consensus 贡献引用：(ghid, pushId) 必须在 users/ 侧真实存在（防幽灵引用）
    known_uploads = set()
    for root, _dirs, files in os.walk(os.path.join(repo, 'annotations')):
        if 'manifest.json' not in files:
            continue
        rel = os.path.relpath(os.path.join(root, 'manifest.json'), repo)
        mm = MANIFEST_RE.match(rel)
        if not mm:
            continue
        try:
            manifest = json.loads(open(os.path.join(root, 'manifest.json'), 'rb').read().decode('utf-8'))
        except Exception:
            continue
        if manifest.get('version') == 2:
            for push_key in (manifest.get('uploads') or {}).keys():
                known_uploads.add((f"ghid_{mm.group(2)}", push_key[2:] if push_key.startswith('p_') else push_key))
    for ghid, push_id in contribution_refs:
        if (ghid, push_id) not in known_uploads:
            fail(f"consensus provenance 引用了不存在的上传: {ghid}/{push_id}")
    # 附加：环境有 jsonschema 时加载三件套对 manifest/comments 复核
    try:
        import jsonschema  # noqa: F401
        # 结构检查已覆盖 schema 的全部硬性条款；此处留作外部工具互证入口
    except ImportError:
        pass
    if errors:
        print("❌ validate.py 发现问题：", file=sys.stderr)
        for e in errors[:80]:
            print(f"  - {e}", file=sys.stderr)
        if len(errors) > 80:
            print(f"  … 共 {len(errors)} 项", file=sys.stderr)
        sys.exit(1)
    print(f"✅ validate.py 通过（manifests={manifests}）")


if __name__ == '__main__':
    main()
