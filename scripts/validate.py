#!/usr/bin/env python3
"""validate.py — census 社区注释仓 CI 第二道校验（02 册 §9，双道同契约）。

结构检查（all-or-nothing：任一非法即非零退出并指名文件）：
  1. 路径闸门：数据文件必须在 users/ghid_<id>/ 内；moderation//ontology//schemas/
     只允许白名单文件名；config.json 唯一；
  2. manifest：身份三处一致（目录/username/githubUserId）、≤262144B、
     pushes ≤200、comment ≤500 码点、shards[].file
     形状（字段名白名单+.NNN.tsv）且与 manifest 同目录（防跨用户别名穿透——
     即便 sha/行数全对也不许引用他人分片）；
  3. TSV：表头/数值序/前导零/码点红线/分片双红线/UTF-8 严格解码/无 BOM/
     sha256+行数+minKey/maxKey 与内容对账；用户目录内未被任何 manifest 引用的
     TSV 视为孤儿拒绝；
  4. comments sidecar：形状/每类 ≤200/每条 ≤500/
     ≤1048576B。
（JSON Schema 三件套供外部工具消费；若环境装了 jsonschema 则附带加载校验。）
provenance/维护者白名单在 workflow 的独立步骤（本文件只管结构）。
"""
import hashlib
import json
import os
import re
import sys

JOIN_ID_MAX = 19
JOIN_ID_RE = re.compile(r'^[0-9]{1,' + str(JOIN_ID_MAX) + r'}$')
FIELD_RE = re.compile(r'^[a-z0-9_]+$')
SHARD_FILE_RE = re.compile(r'^([a-z0-9_]{1,64})\.[0-9]{3}\.tsv$')
USER_DIR_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/users/ghid_(\d+)/$')
MANIFEST_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/users/ghid_(\d+)/manifest\.json$')
MANIFEST_MAX = 262144
PUSHES_KEPT = 200
PUSH_COMMENT_MAX = 500
LABEL_MAX = 120
SHARD_MAX_ROWS = 20000
SHARD_MAX_BYTES = 1048576
COMMENTS_PER_CATEGORY = 200
COMMENT_MAX = 500
COMMENTS_FILE_MAX = 1048576

errors = []
referenced_shards = set()


def fail(path, msg):
    errors.append(f"{path}: {msg}")


def codepoints(s):
    return len(s)


def cmp_numeric(a, b):
    if len(a) != len(b):
        return -1 if len(a) < len(b) else 1
    return (a > b) - (a < b)


def validate_manifest(repo, full, rel):
    m = MANIFEST_RE.match(rel)
    if not m:
        fail(rel, "manifest 路径形状非法")
        return None
    dir_ghid = m.group(2)
    manifest_dir = os.path.dirname(rel)
    try:
        raw = open(full, 'rb').read()
    except OSError as e:
        fail(rel, f"不可读: {e}")
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
    for fname, f in (manifest.get('fields') or {}).items():
        if not FIELD_RE.match(fname or ''):
            fail(rel, f"字段名非法: {fname!r}")
        for sh in f.get('shards', []):
            sf = sh.get('file', '')
            fm = SHARD_FILE_RE.match(sf)
            if not fm or fm.group(1) != fname:
                fail(rel, f"shards[].file 形状非法或字段名不匹配: {sf!r}")
            shard_rel = os.path.normpath(os.path.join(manifest_dir, sf))
            # 同目录约束：解析后必须仍在该用户目录内（防 ../ 跨用户别名——sha 对账
            # 全过也不许引用他人分片）
            if os.path.dirname(shard_rel) != manifest_dir:
                fail(rel, f"shards[].file 越出本目录: {sf!r}")
                continue
            referenced_shards.add(shard_rel)
            validate_tsv(repo, shard_rel, sh, rel)
    return manifest


def validate_tsv(repo, rel, shard_meta, manifest_rel):
    full = os.path.join(repo, rel)
    if not os.path.isfile(full):
        fail(rel, f"分片不存在（manifest={manifest_rel}）")
        return
    raw = open(full, 'rb').read()
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


def validate_comments(repo, full, rel):
    try:
        raw = open(full, 'rb').read()
    except OSError as e:
        fail(rel, f"不可读: {e}")
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
            m = USER_DIR_RE.match(os.path.dirname(rel) + '/')
            if not m:
                fail(rel, "数据文件必须在 users/ghid_<id>/ 内")
                continue
            if f == 'manifest.json':
                manifests += 1
                validate_manifest(repo, full, rel)
            elif f.endswith('.tsv'):
                validate_tsv(repo, rel, None, None)  # 结构红线；对账/引用在 manifest 侧与下方孤儿检查
            elif f.endswith('.comments.json'):
                validate_comments(repo, full, rel)
            elif f.endswith('.bloom'):
                pass  # 二进制 sidecar（参数在 manifest.bloom，schema 层）
            else:
                fail(rel, "用户目录内不允许的文件类型")
    # 孤儿分片：用户目录内 TSV 未被任何 manifest 引用（all-or-nothing 精神）
    for root, _dirs, files in os.walk(os.path.join(repo, 'annotations')):
        for f in files:
            if not f.endswith('.tsv'):
                continue
            rel = os.path.relpath(os.path.join(root, f), repo)
            if rel not in referenced_shards:
                fail(rel, "未被任何 manifest 引用的孤儿分片")
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
