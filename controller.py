import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone

from memos.api import _api_style, list_memos, upFile, upTextAttachment, upMemo, deleteMemo
from memos.util import timeToUnix

STATE_FILE = 'flomo/import-state.json'


def _record(time_text, content):
    timestamp = int(time.mktime(datetime.strptime(time_text, "%Y-%m-%d %H:%M:%S").timetuple()))
    created = datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')
    return created, content


def _can_create_memo(flomo):
    return flomo['content'] != "None" or flomo['filePath'] != "None"


def _uploaded_prefix_size(flomo_data, api_style):
    """按创建时间对齐导入前缀，并识别已存在的重复 Memo。"""
    source_times = [_record(flomo['time'], '')[0] for flomo in flomo_data]
    remote_times = sorted(memo.get('createTime', '') for memo in list_memos(api_style))
    source_index = remote_index = extra_remote = 0

    while source_index < len(source_times) and remote_index < len(remote_times):
        source_time = source_times[source_index]
        remote_time = remote_times[remote_index]
        if source_time == remote_time:
            source_index += 1
            remote_index += 1
        elif remote_time < source_time:
            # 已写入源数据之外的记录，或中断重试留下的重复记录。
            extra_remote += 1
            remote_index += 1
        else:
            raise RuntimeError('远端缺少较早的源笔记，已停止以防打乱导入顺序')

    if remote_index != len(remote_times):
        raise RuntimeError('远端存在晚于本次导出范围的笔记，已停止以防重复写入')
    if extra_remote:
        print(f'检测到并跳过 {extra_remote} 条远端重复或额外 Memo')
    return source_index


def _memo_content(flomo):
    content = '' if flomo['content'] == "None" else '\n'.join(flomo['content'])
    if len(content.encode('utf-8')) <= 8192:
        return content, None
    filename = 'flomo-' + flomo['time'].replace(':', '').replace(' ', '-') + '.md'
    summary = f'原笔记内容超过 Memos 限制，完整内容见附件：{filename}'
    return summary, (filename, content)


def _stable_id(prefix, value):
    return f'{prefix}-{hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]}'


def _source_hash(importable):
    source = [flomo for _, flomo in importable]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _save_state(state):
    temporary = STATE_FILE + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as state_file:
        json.dump(state, state_file, ensure_ascii=False, indent=2)
    os.replace(temporary, STATE_FILE)


def _load_or_create_state(importable, api_style):
    source_hash = _source_hash(importable)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding='utf-8') as state_file:
            state = json.load(state_file)
        if state.get('source_hash') != source_hash:
            raise RuntimeError('导出文件已变化，不能复用已有上传状态文件')
        return state

    completed = _uploaded_prefix_size([flomo for _, flomo in importable], api_style)
    state = {'version': 1, 'source_hash': source_hash, 'next_importable': completed}
    _save_state(state)
    return state


def add(limit=None, delay=0.5, progress_every=25):
    # 倒叙渐新插入符合时间感官
    flomoData = sorted(json.load(open("flomo/myMemos.json", encoding="UTF-8")), key=lambda k: k['time'])
    importable = [(position, flomo) for position, flomo in enumerate(flomoData, start=1) if _can_create_memo(flomo)]
    api_style = _api_style()
    print(f'检测到 Memos API：{api_style}')
    if api_style != 'v1':
        raise RuntimeError('当前导入状态保护仅支持 Memos /api/v1 接口')
    state = _load_or_create_state(importable, api_style)
    uploaded = state['next_importable']
    print(f'上传状态已确认，已完成 {uploaded} 条')

    pending = importable[uploaded:]
    if limit is not None:
        pending = pending[:limit]

    for importable_index, (position, flomo) in enumerate(pending, start=uploaded):

        # 无内容纯图片则跳过, 按你的需要是否注释
        # if flomo['content'] == 'None':
        #     continue

        # 时间处理 本来想拿unix来当memo创建时间 但好像做不到修改时间
        # unix = timeToUnix(flomo['time'])

        memo_id = _stable_id('flomo', f'{position}|{flomo["time"]}')
        memo_name = f'memos/{memo_id}'
        msg, text_attachment = _memo_content(flomo)
        ct = int(time.mktime(datetime.strptime(flomo['time'], "%Y-%m-%d %H:%M:%S").timetuple()))
        # 先创建带稳定 ID 的 Memo。重试时服务端返回既有 Memo，不会生成副本。
        upMemo(ct, msg, [], api_style, memo_id)
        time.sleep(delay)

        if flomo['filePath'] != "None":
            for attachment_index, f in enumerate(flomo['filePath']):
                attachment_id = _stable_id('file', f'{memo_id}|file|{attachment_index}|{f}')
                upFile(f, api_style, attachment_id, memo_name)
                time.sleep(delay)
        if text_attachment:
            filename, content = text_attachment
            attachment_id = _stable_id('file', f'{memo_id}|markdown')
            upTextAttachment(filename, content, api_style, attachment_id, memo_name)
            time.sleep(delay)
        state['next_importable'] = importable_index + 1
        _save_state(state)
        if position % progress_every == 0 or position == len(flomoData):
            print(f'已完成 {position}/{len(flomoData)} {flomo["time"]}')


def delete(many):
    for i in range(many):
        deleteMemo(i)


if __name__ == '__main__':
    # delete(400)   # 删除ID小于many的memo, 删除了memo就能一键删除图片啦, 防止不满意的你!!!
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='本次最多上传的笔记数')
    parser.add_argument('--delay', type=float, default=0.5, help='每次请求后的等待秒数')
    parser.add_argument('--progress-every', type=int, default=25, help='每完成多少条显示一次进度')
    args = parser.parse_args()
    add(args.limit, args.delay, args.progress_every)
