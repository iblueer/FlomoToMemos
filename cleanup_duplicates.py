"""清理本次并发导入产生的严格重复 Memo。"""

import argparse
import collections
import json

from controller import _can_create_memo, _record
from memos.api import _api_style, deleteMemo, list_memos


def _attachment_signature(memo):
    return tuple(sorted(
        (
            attachment.get('filename', ''),
            attachment.get('type', ''),
            attachment.get('size', ''),
            attachment.get('externalLink', ''),
        )
        for attachment in memo.get('attachments', [])
    ))


def _memo_signature(memo):
    return (
        memo.get('content', ''),
        memo.get('visibility', ''),
        memo.get('state', ''),
        _attachment_signature(memo),
    )


def find_candidates():
    items = sorted(json.load(open('flomo/myMemos.json', encoding='utf-8')), key=lambda item: item['time'])
    source_counts = collections.Counter(
        _record(item['time'], '')[0]
        for item in items
        if _can_create_memo(item)
    )
    api_style = _api_style()
    groups = collections.defaultdict(list)
    for memo in list_memos(api_style):
        groups[memo.get('createTime', '')].append(memo)

    candidates = []
    for timestamp, records in groups.items():
        # 只处理“源数据一条、远端两条、所有可见字段都相同”的组。
        if source_counts[timestamp] != 1 or len(records) != 2:
            continue
        if len({_memo_signature(memo) for memo in records}) != 1:
            continue
        keep, duplicate = sorted(records, key=lambda memo: memo['name'])
        candidates.append((timestamp, keep, duplicate))
    return api_style, sorted(candidates)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--delete', action='store_true', help='实际删除严格重复的 Memo')
    args = parser.parse_args()

    api_style, candidates = find_candidates()
    print(f'严格重复候选：{len(candidates)}')
    if not args.delete:
        return

    for index, (_, _, duplicate) in enumerate(candidates, start=1):
        memo_id = duplicate['name'].rsplit('/', 1)[1]
        deleteMemo(memo_id, api_style)
        if index % 25 == 0 or index == len(candidates):
            print(f'已删除 {index}/{len(candidates)}')


if __name__ == '__main__':
    main()
