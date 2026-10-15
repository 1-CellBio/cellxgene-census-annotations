#!/usr/bin/env python3
"""emit.py — decisions.json → consensus vN+1（04 册 §2.3；用户目录零改动 D5）。

decisions.json 形状（04 册 §2.2）：
  { "targetVersion": 4,
    "groups": { "T cell": "T cell (coarse, needs split)", "unknown": "unknown" },
    "splits": [ { "ct": "T cell", "label": "CD8-positive, alpha-beta T cell",
                  "from": { "ghid": "ghid_222123526", "pushId": "x", "block": 12 },
                  "note": "采纳该用户拆分" } ],
    "include": [ { "ghid": "ghid_…", "pushId": "old", "as": "pinned" } ] }

pushId 一律写裸 id（如 "x"）——emit 内部以 f'p_{push_id}' 拼 uploads 键（历史版
本档示例误带 p_ 前缀，照抄会查不到上传）。

产出（只写 consensus/community_anno/）：
  v<N+1>/default.json       diff-only 组映射（groups 里 label≠ct 才进；缺席=原始成立）
  v<N+1>/overrides.NNN.tsv  splits 引用块抽取的稀疏组块（同贡献组块格式）
  v<N+1>/overrides.index.json
  v<N+1>/provenance.json    contributions（latest 默认 + pinned 显式）+ basedOn
  current.json 前移

校验（fail-closed）：splits.from 与 include 引用的 (ghid,pushId) 必须真实存在；
splits 引用块在 index sidecar 中定位（ct/label 匹配）；组块红线（2 万行/1MB）
沿用。emit 后应跑 validate.py 终审。
"""
import hashlib
import json
import os
import re
import shutil
import sys

SHARD_MAX_ROWS = 20000
SHARD_MAX_BYTES = 1048576
BLOCK_HEADER = '#BLOCK\t'
MANIFEST_RE = re.compile(r'^ghid_(\d+)$')


def read_json(path):
    with open(path, 'rb') as fh:
        return json.loads(fh.read().decode('utf-8'))


def read_block_joinids(repo, namespace, ghid, blocks_ref, want_ct, want_label):
    """从 index sidecar 定位 (ct,label) 块 → 拉其分片 → 抽该块的 joinid 集。"""
    ns_dir = os.path.join(repo, 'annotations/datasets', namespace)
    idx = read_json(os.path.join(ns_dir, 'users', ghid, blocks_ref))
    shard_files = None
    for b in idx.get('blocks') or []:
        if b.get('ct') == want_ct and b.get('label') == want_label:
            shard_files = b.get('shards') or []
            break
    if shard_files is None:
        raise SystemExit(f'❌ splits 引用块不存在：{ghid} {blocks_ref} ({want_ct} / {want_label})')
    ids = []
    for sf in shard_files:
        text = open(os.path.join(ns_dir, 'users', ghid, sf), encoding='utf-8').read()
        cur_ct = cur_label = None
        for line in text.split('\n'):
            if not line:
                continue
            if line.startswith(BLOCK_HEADER):
                parts = line.split('\t')
                if len(parts) == 4:
                    cur_ct, cur_label = parts[1], parts[2]
                continue
            if cur_ct == want_ct and cur_label == want_label:
                ids.append(line)
    ids.sort(key=lambda s: (len(s), s))  # 数值序
    return ids


def emit(repo, namespace, decisions_path):
    ns_dir = os.path.join(repo, 'annotations/datasets', namespace)
    cons_dir = os.path.join(ns_dir, 'consensus/community_anno')
    decisions = read_json(decisions_path)
    target = decisions.get('targetVersion')
    current = read_json(os.path.join(cons_dir, 'current.json'))
    prev = current.get('version', 0)
    if not isinstance(target, int) or target != prev + 1:
        raise SystemExit(f'❌ targetVersion={target} 必须等于 current+1（={prev + 1}）')

    users_dir = os.path.join(ns_dir, 'users')
    manifests = {}
    for ghid in sorted(os.listdir(users_dir)) if os.path.isdir(users_dir) else []:
        mp = os.path.join(users_dir, ghid, 'manifest.json')
        if os.path.isfile(mp):
            manifests[ghid] = read_json(mp)

    def uploads_of(ghid):
        m = manifests.get(ghid)
        if not m or m.get('version') != 2:
            return {}
        return m.get('uploads') or {}

    # ── contributions：全用户 latest（默认）+ include pinned（显式）────────
    contributions = []
    included = set()
    for ghid, m in manifests.items():
        ups = [
            {'pushId': k[2:] if k.startswith('p_') else k, **v}
            for k, v in (m.get('uploads') or {}).items()
        ]
        if not ups:
            continue
        ups.sort(key=lambda u: str(u.get('at') or ''), reverse=True)
        latest = ups[0]
        contributions.append({'ghid': ghid, 'pushId': latest['pushId'], 'rows': latest.get('rows') or 0, 'includedAs': 'latest'})
        included.add((ghid, latest['pushId']))
    for inc in decisions.get('include') or []:
        ghid, push_id = inc.get('ghid'), inc.get('pushId')
        if f'p_{push_id}' not in uploads_of(ghid or ''):
            raise SystemExit(f'❌ include 引用不存在的上传：{ghid}/{push_id}')
        if (ghid, push_id) not in included:
            contributions.append({'ghid': ghid, 'pushId': push_id, 'rows': uploads_of(ghid)[f'p_{push_id}'].get('rows') or 0, 'includedAs': inc.get('as') or 'pinned'})

    # ── default.json（diff-only：groups 里 label≠ct 才进 map）──────────────
    groups = decisions.get('groups') or {}
    mapping = [[ct, label] for ct, label in sorted(groups.items()) if ct != label]

    # ── overrides：splits 引用块 → 组块分片（同贡献格式）──────────────────
    # B3：validate.py 要求同分片内块间 (ct,label) 严格字典序且不得重复头——
    # decisions.splits 是人工书写顺序（顺 report.py 异票降序写 = 天然逆序），写片前
    # 统一排序；同 (ct,label) 多 split（采纳多贡献者拆分 = 核心场景）合并：joinIds
    # 并集去重后按 (len,s) 数值序重排（与单块抽取 read_block_joinids 的排序口径一致）。
    # 完全重复（并集无新增）打印 ⚠ 不拒；fail-closed 引用校验全部原样保留。
    merged_blocks = {}
    for sp in decisions.get('splits') or []:
        frm = sp.get('from') or {}
        ghid, push_id = frm.get('ghid'), frm.get('pushId')
        ups = uploads_of(ghid or '')
        entry = ups.get(f'p_{push_id}')
        if not entry:
            raise SystemExit(f'❌ splits.from 引用不存在的上传：{ghid}/{push_id}')
        ids = read_block_joinids(repo, namespace, ghid, entry.get('blocksRef'), sp.get('ct'), sp.get('label'))
        if not ids:
            raise SystemExit(f'❌ splits 引用块抽取为空：{ghid}/{push_id} ({sp.get("ct")} / {sp.get("label")})')
        key = (sp.get('ct'), sp.get('label'))
        before = len(merged_blocks.setdefault(key, set()))
        merged_blocks[key].update(ids)
        if len(merged_blocks[key]) == before:
            print(f'   ⚠ 同 (ct,label) split 无新增 id（完全重复）：{key[0]} / {key[1]}')
    merged_count = len(decisions.get('splits') or []) - len(merged_blocks)
    override_blocks = [
        {'ct': k[0], 'label': k[1], 'joinIds': sorted(v, key=lambda s: (len(s), s))}
        for k, v in sorted(merged_blocks.items())
    ]

    out_dir = os.path.join(cons_dir, f'v{target}')
    # 上次崩溃残留清理（收窄到目标 vN 目录）：target==current+1 已保证 vN 是尚未发布
    # 的新版本，残留只可能来自中断的 emit——只清该目录，绝不清整个 consensus/
    #（历史版本不可动）。
    if os.path.isdir(out_dir):
        # 防御（审查 P3）：provenance.json 在场=该版本已到达 emit 终段（甚至可能
        # current.json 被人工回滚过）——不自动删，拒绝并交人工核实（fail-closed）。
        if os.path.isfile(os.path.join(out_dir, 'provenance.json')):
            raise SystemExit(
                f'❌ 目标目录 v{target} 已含完整 emit 产物（provenance.json 在场）——'
                f'若为上次中断的残留请人工删除该目录后重跑；若 current.json 曾被回滚'
                f'请先恢复，不得自动覆盖已发布版本。')
        shutil.rmtree(out_dir)
        print(f'   ⚠ 清理上次 emit 的残留目录 v{target}')
    os.makedirs(out_dir, exist_ok=True)
    json.dump({'version': target, 'censusVersion': '2025-11-08', 'map': mapping},
              open(os.path.join(out_dir, 'default.json'), 'w'), ensure_ascii=False, indent=1)

    # overrides 组块分片（双红线；块内数值序已排序）
    override_files = []
    shard_idx = 0
    cur_lines = []
    cur_bytes = 0
    block_meta = []
    for b in override_blocks:
        header = f'#BLOCK\t{b["ct"]}\t{b["label"]}\t{len(b["joinIds"])}\n'
        hbytes = len(header.encode('utf-8'))
        block_started = False  # 块内首 id（或续块 flush 后首个 id）须写头
        for jid in b['joinIds']:
            line_bytes = len(jid.encode('utf-8')) + 1
            need_header = not block_started
            # 行数与字节预算逐行判断（need_header 只在段首计头字节——续块重发头）
            projected_rows = len(cur_lines) + 1
            projected_bytes = cur_bytes + (hbytes if need_header else 0) + line_bytes
            if projected_rows > SHARD_MAX_ROWS or (cur_lines and projected_bytes > SHARD_MAX_BYTES):
                override_files.append((shard_idx, cur_lines[:], block_meta[:]))
                shard_idx += 1
                cur_lines = []
                cur_bytes = 0
                block_meta = []
                need_header = True  # 续块重发完整头
            if need_header:
                cur_lines.append(header.rstrip('\n'))
                cur_bytes += hbytes
                block_meta.append({'ct': b['ct'], 'label': b['label'], 'count': len(b['joinIds']), 'segStart': len(cur_lines) - 1, 'ids': []})
                block_started = True
            cur_lines.append(jid)
            cur_bytes += line_bytes
            block_meta[-1]['ids'].append(jid)
    if cur_lines:
        override_files.append((shard_idx, cur_lines[:], block_meta[:]))

    ov_index_blocks = []
    for si, _lines, meta in override_files:
        # 文本从 meta 重建（cur_lines 只作行数/字节记账）：头 count=本段实际行数
        # ——与 js buildBlockShards 的续块语义逐字一致（分片独立可解析）
        parts = []
        for m in meta:
            parts.append(f'#BLOCK\t{m["ct"]}\t{m["label"]}\t{len(m["ids"])}\n' + '\n'.join(m['ids']) + '\n')
        content = ''.join(parts)
        fname = f'overrides.{str(si).zfill(3)}.tsv'
        open(os.path.join(out_dir, fname), 'w', encoding='utf-8').write(content)
        for m in meta:
            # A2 双向锚定：shards 写相对 vN 目录的裸文件名（如 "overrides.000.tsv"）——
            # 主仓 client（src/services/annotCommunity/client.js getConsensusOverrides）按
            # overridesRef 所在目录前缀解析分片路径；两侧任何改动须同步。
            ov_index_blocks.append({'ct': m['ct'], 'label': m['label'], 'rows': len(m['ids']), 'shards': [fname]})

    if override_files:
        json.dump({'field': 'community_anno', 'blocks': ov_index_blocks,
                   'totals': {'blocks': len(ov_index_blocks), 'rows': sum(b['rows'] for b in ov_index_blocks)}},
                  open(os.path.join(out_dir, 'overrides.index.json'), 'w'), ensure_ascii=False, indent=1)

    json.dump({
        'version': target, 'decidedAt': decisions.get('decidedAt') or None, 'decider': decisions.get('decider') or '1-CellBio',
        'basedOn': {'censusVersion': '2025-11-08', 'prevVersion': prev},
        'contributions': sorted(contributions, key=lambda c: (c['ghid'], c['pushId'])),
        'groupsChanged': len(mapping), 'splits': len(override_blocks),
    }, open(os.path.join(out_dir, 'provenance.json'), 'w'), ensure_ascii=False, indent=1)

    json.dump({'field': 'community_anno', 'version': target, 'updatedAt': decisions.get('decidedAt') or None},
              open(os.path.join(cons_dir, 'current.json'), 'w'), indent=1)
    print(f'✅ emit v{target}：groups={len(mapping)} splits={len(override_blocks)} contributions={len(contributions)}')
    if merged_count > 0:
        print(f'   同 (ct,label) 多 split 合并：{merged_count} 处（joinIds 并集；provenance splits=去重后块数）')
    print(f'   下一步：python3 scripts/validate.py {repo}（终审）→ git add annotations/ && commit')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('用法: emit.py <repo> <namespace> <decisions.json>', file=sys.stderr)
        sys.exit(2)
    emit(sys.argv[1], sys.argv[2], sys.argv[3])
