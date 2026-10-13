#!/usr/bin/env python3
"""report.py — 共识合并票型报告（04 册 §2.1；零分片拉取）。

输入全部来自仓内文件：users/*/manifest.json(v2) + 各 latest 上传的 index
sidecar（blocks[].ct/label/rows）+ baseline/cell_type_catalog.json（覆盖率
分母）+ consensus/community_anno/current→vN/default.json（当前共识）。
计票口径：每用户 latest 上传（03 册 §4.4，防灌票）；decisions.pinned 的显式
引用在 emit 侧生效（报告只按 latest）。

输出：report.md（stdout 重定向）——每组一行：原始标签 | catalog 细胞数 |
票型分布（label × cells × 人数）| 当前共识 | 建议动作；按「异票细胞数」降序
（总票 - 最大票 = 争议度；unknown/粗标签天然浮顶——它们票型最分裂）。
"""
import json
import os
import re
import sys

MANIFEST_RE = re.compile(r'^annotations/datasets/([a-z0-9_]+)/users/ghid_(\d+)/manifest\.json$')


def read_json(path):
    try:
        with open(path, 'rb') as fh:
            return json.loads(fh.read().decode('utf-8'))
    except OSError:
        return None
    except Exception:
        return None


def latest_uploads(manifest):
    """每用户 latest 上传（at 降序取首；at 缺失按 dict 序末位兜底）。"""
    uploads = [
        {'pushId': k[2:] if k.startswith('p_') else k, **v}
        for k, v in (manifest.get('uploads') or {}).items()
    ]
    uploads.sort(key=lambda u: str(u.get('at') or ''), reverse=True)
    return uploads


def collect(repo, namespace):
    ns_dir = os.path.join(repo, 'annotations/datasets', namespace)
    catalog = read_json(os.path.join(ns_dir, 'baseline/cell_type_catalog.json')) or []
    catalog_cells = {item['label']: item['cells'] for item in catalog if isinstance(item, dict) and 'label' in item and 'cells' in item}

    current = read_json(os.path.join(ns_dir, 'consensus/community_anno/current.json')) or {}
    consensus = {}
    v = current.get('version')
    if isinstance(v, int) and v >= 0:
        default = read_json(os.path.join(ns_dir, f'consensus/community_anno/v{v}/default.json')) or {}
        for pair in default.get('map') or []:
            if isinstance(pair, list) and len(pair) == 2:
                consensus[pair[0]] = pair[1]

    # 票型聚合：orig_ct → {label → {'cells': n, 'users': set()}}
    tally = {}
    users_dir = os.path.join(ns_dir, 'users')
    contributors = 0
    for ghid in sorted(os.listdir(users_dir)) if os.path.isdir(users_dir) else []:
        manifest = read_json(os.path.join(users_dir, ghid, 'manifest.json'))
        if not manifest or manifest.get('version') != 2:
            continue
        uploads = latest_uploads(manifest)
        if not uploads:
            continue
        latest = uploads[0]
        contributors += 1
        blocks_ref = latest.get('blocksRef')
        if not blocks_ref:
            continue
        idx = read_json(os.path.join(users_dir, ghid, blocks_ref))
        if not idx:
            print(f'⚠️ index sidecar 不可读：{ghid}/{blocks_ref}', file=sys.stderr)
            continue
        for b in idx.get('blocks') or []:
            ct, label, rows = b.get('ct'), b.get('label'), b.get('rows') or 0
            if not ct or not label:
                continue
            bucket = tally.setdefault(ct, {})
            slot = bucket.setdefault(label, {'cells': 0, 'users': set()})
            slot['cells'] += rows
            slot['users'].add(ghid)
    return catalog_cells, consensus, tally, contributors


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else '.'
    namespace = sys.argv[2] if len(sys.argv) > 2 else 'homo_sapiens'
    catalog_cells, consensus, tally, contributors = collect(repo, namespace)

    lines = [
        f'# 共识票型报告 · {namespace}',
        '',
        f'- 计票口径：每用户 latest 上传（v2 uploads；防灌票）',
        f'- 贡献者：{contributors} · 有票组：{len(tally)} · baseline 组：{len(catalog_cells)}',
        '',
        '| 原始 cell_type | catalog 细胞 | 票型分布（label · cells · 人数） | 当前共识 | 建议动作 |',
        '|---|---|---|---|---|',
    ]
    ranked = []
    for ct, bucket in tally.items():
        total = sum(s['cells'] for s in bucket.values())
        top = max(s['cells'] for s in bucket.values())
        dissent = total - top  # 异票细胞数（争议度）
        ranked.append((dissent, total, ct, bucket))
    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    for dissent, total, ct, bucket in ranked:
        dist = ' · '.join(
            f'{label}: {s["cells"]} ({len(s["users"])})'
            for label, s in sorted(bucket.items(), key=lambda kv: -kv[1]['cells'])
        )
        cur = consensus.get(ct, '（原始成立）')
        suggestion = '维持原始' if len(bucket) == 1 and next(iter(bucket)) == ct else '裁定组映射 / 拆分'
        lines.append(f'| {ct} | {catalog_cells.get(ct, "—")} | {dist} | {cur} | {suggestion} |')
    lines.append('')
    lines.append('> 建议动作为机器提示（组映射 vs 拆分），裁定一律人工；拆分引用既有块见 decisions.json 约定（emit.py 校验）。')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
