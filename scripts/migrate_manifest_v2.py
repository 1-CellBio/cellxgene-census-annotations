#!/usr/bin/env python3
"""migrate_manifest_v2.py — v1 manifest → v2（05 册 §1：形状变换，内容零丢失）。

- fields → legacy_fields（原条目原样冻结）；uploads = {}；version = 2；
- 其余键（username/githubUserId/login/orcid/censusVersion/updatedAt/slice/pushes）
  原样保留；
- 幂等：已是 v2 的 manifest 跳过（exit code 汇总迁移数）；
- 前置：validate.py 须为 v2 版本（门禁先行）——迁移后跑 validate.py 全绿才可提交。
"""
import json
import os
import sys

def migrate(repo):
    migrated = 0
    skipped = 0
    for root, _dirs, files in os.walk(os.path.join(repo, 'annotations')):
        if 'manifest.json' not in files:
            continue
        path = os.path.join(root, 'manifest.json')
        with open(path, 'rb') as fh:
            manifest = json.loads(fh.read().decode('utf-8'))
        if manifest.get('version') == 2:
            skipped += 1
            continue
        new = dict(manifest)  # 身份/slice/pushes/censusVersion 等原样
        new['version'] = 2
        new['legacy_fields'] = manifest.get('fields') or {}
        new.pop('fields', None)
        new.setdefault('uploads', {})
        # 键序：version/username/githubUserId/login 在前，legacy_fields/uploads 相邻
        ordered = {k: new[k] for k in (
            'version', 'username', 'githubUserId', 'login', 'orcid',
            'censusVersion', 'updatedAt',
        ) if k in new}
        ordered.update({k: v for k, v in new.items()
                        if k not in ordered and k not in ('legacy_fields', 'uploads')})
        ordered['legacy_fields'] = new['legacy_fields']
        ordered['uploads'] = new['uploads']
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(ordered, fh, ensure_ascii=False, indent=1)
            fh.write('\n')
        migrated += 1
        print(f'migrated: {os.path.relpath(path, repo)}')
    print(f'✅ 迁移完成（migrated={migrated} skipped_v2={skipped}）')

if __name__ == '__main__':
    migrate(sys.argv[1] if len(sys.argv) > 1 else '.')
