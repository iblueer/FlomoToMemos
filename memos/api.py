import base64
import os
import time
from datetime import datetime, timezone

import requests

from memos.util import getType

# 结尾不要带斜杠，例如：https://memos.example.com
Host = 'https://memos.thatcoder.cn'
_SESSION = requests.Session()
# 当前环境的 HTTPS 代理会间歇性中断；站点直连正常。
_SESSION.trust_env = False


class MemosApiError(RuntimeError):
    pass


def _token():
    with open('token.txt', encoding='utf-8') as token_file:
        return token_file.read().strip()


def _headers():
    return {
        'Accept': 'application/json, text/plain, */*',
        'Authorization': f'Bearer {_token()}',
    }


def _response_json(response):
    if not response.ok:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:500]
        raise MemosApiError(f'{response.request.method} {response.url} 返回 {response.status_code}: {detail}')
    try:
        return response.json()
    except ValueError as exc:
        raise MemosApiError(f'{response.request.method} {response.url} 未返回 JSON') from exc


def _request(method, path, **kwargs):
    headers = _headers()
    headers.update(kwargs.pop('headers', {}))
    for attempt in range(10):
        response = _SESSION.request(method, f'{Host}{path}', headers=headers, timeout=60, **kwargs)
        # 429 明确表示该请求未写入，重试不会造成重复 Memo。
        if response.status_code != 429 or attempt == 9:
            return response
        retry_after = response.headers.get('Retry-After', '1')
        try:
            time.sleep(max(float(retry_after), 1))
        except ValueError:
            time.sleep(1)


def _api_style():
    """识别目标站点使用的 Memos REST API 版本。"""
    candidates = (
        ('v1', '/api/v1/memos?pageSize=1'),       # Memos 0.30 / 0.31
        ('current', '/api/memos?pageSize=1'),     # Memos 26.10+
        ('legacy', '/api/v1/memo'),               # Memos 0.19.1
    )
    results = []
    for style, path in candidates:
        try:
            response = _request('GET', path)
        except requests.RequestException as exc:
            results.append(f'{path}: {exc}')
            continue
        if response.status_code == 200:
            return style
        results.append(f'{path}: HTTP {response.status_code}')
    raise MemosApiError('无法识别 Memos API：' + '；'.join(results))


def _iso_time(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def list_memos(api_style):
    """读取当前用户全部可见 Memo，供断点续传时核对已上传内容。"""
    if api_style == 'legacy':
        raise MemosApiError('Memos 0.19.1 不支持自动断点续传，请保留本次上传进程直至完成')

    api_base = '/api/v1' if api_style == 'v1' else '/api'
    params = {'pageSize': 1000}
    memos = []
    while True:
        response = _request('GET', f'{api_base}/memos', params=params)
        data = _response_json(response)
        memos.extend(data.get('memos', []))
        page_token = data.get('nextPageToken')
        if not page_token:
            return memos
        params['pageToken'] = page_token


def _file_data(file_path):
    local_path = os.path.join('flomo', file_path)
    with open(local_path, 'rb') as file_handle:
        data = file_handle.read()
    filename = os.path.basename(file_path) or f'匿名文件_{int(datetime.now().timestamp())}'
    mime_type = getType(os.path.splitext(filename)[1].lower())
    return local_path, filename, mime_type, data


def _existing_attachment(api_base, attachment_id):
    response = _request('GET', f'{api_base}/attachments/{attachment_id}')
    return _response_json(response)


def _attachment_already_exists(response):
    if response.status_code == 409:
        return True
    if response.status_code != 500:
        return False
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    return 'UNIQUE constraint failed: attachment.uid' in str(detail)


def _reuse_existing_attachment(api_base, attachment_id, memo_name):
    attachment = _existing_attachment(api_base, attachment_id)
    if attachment.get('memo') not in ('', memo_name):
        raise MemosApiError('已有附件绑定到了不同 Memo，已停止以防误关联')
    return attachment


def _upload_attachment_chunked(filename, mime_type, data, attachment_id, memo_name):
    """按 Memos v0.31 的协议上传，单个请求不携带完整文件。"""
    api_base = '/api/v1'
    attachment = {'filename': filename, 'type': mime_type}
    if memo_name:
        attachment['memo'] = memo_name
    start = {
        'spec': {
            'attachment': attachment,
            'attachmentId': attachment_id,
            'totalSize': str(len(data)),
        },
        'writeOffset': '0',
        'finishWrite': len(data) == 0,
    }
    response = _request('POST', f'{api_base}/attachments:upload', json=start)
    if _attachment_already_exists(response):
        return _reuse_existing_attachment(api_base, attachment_id, memo_name)
    upload = _response_json(response)
    if upload.get('attachment'):
        return upload['attachment']
    upload_id = upload['uploadId']
    offset = int(upload.get('committedSize', 0))
    chunk_size = int(upload['maxChunkSize'])
    if chunk_size <= 0:
        raise MemosApiError('Memos 返回了无效的附件分块大小')

    while offset < len(data):
        chunk = data[offset:offset + chunk_size]
        finish_write = offset + len(chunk) == len(data)
        payload = {
            'uploadId': upload_id,
            'writeOffset': str(offset),
            'data': base64.b64encode(chunk).decode('ascii'),
            'finishWrite': finish_write,
        }
        response = _request('POST', f'{api_base}/attachments:upload', json=payload)
        if _attachment_already_exists(response):
            return _reuse_existing_attachment(api_base, attachment_id, memo_name)
        upload = _response_json(response)
        if upload.get('attachment'):
            return upload['attachment']
        new_offset = int(upload.get('committedSize', offset))
        if new_offset <= offset:
            raise MemosApiError('Memos 未确认附件分块写入')
        offset = new_offset

    if upload.get('attachment'):
        return upload['attachment']
    raise MemosApiError('Memos 未返回已完成的附件')


def _upload_attachment(filename, mime_type, data, api_style, attachment_id=None, memo_name=None):
    """上传给定字节数据，并返回可直接传给 upMemo 的附件信息。"""

    if api_style == 'legacy':
        files = {'file': (filename, data, mime_type)}
        response = _request('POST', '/api/v1/resource/blob', files=files)
        return _response_json(response)

    if api_style == 'v1' and attachment_id:
        return _upload_attachment_chunked(filename, mime_type, data, attachment_id, memo_name)

    api_base = '/api/v1' if api_style == 'v1' else '/api'
    payload = {
        'filename': filename,
        'type': mime_type,
        'content': base64.b64encode(data).decode('ascii'),
    }
    if memo_name:
        payload['memo'] = memo_name
    params = {'attachmentId': attachment_id} if attachment_id else None
    response = _request('POST', f'{api_base}/attachments', params=params, json=payload)
    if attachment_id and _attachment_already_exists(response):
        return _existing_attachment(api_base, attachment_id)
    return _response_json(response)


def upFile(file_path, api_style=None, attachment_id=None, memo_name=None):
    """上传导出文件中的附件。"""
    api_style = api_style or _api_style()
    _, filename, mime_type, data = _file_data(file_path)
    return _upload_attachment(filename, mime_type, data, api_style, attachment_id, memo_name)


def upTextAttachment(filename, content, api_style=None, attachment_id=None, memo_name=None):
    """将超长文本作为 Markdown 附件上传。"""
    api_style = api_style or _api_style()
    return _upload_attachment(
        filename,
        'text/markdown',
        content.encode('utf-8'),
        api_style,
        attachment_id,
        memo_name,
    )


def upMemo(timestamp, message, attachments, api_style=None, memo_id=None):
    """创建一条私有 Memo，并保留 flomo 的创建时间。"""
    api_style = api_style or _api_style()
    if api_style == 'legacy':
        resource_ids = [attachment['id'] for attachment in attachments]
        payload = {
            'createdTs': timestamp,
            'updatedTs': timestamp,
            'content': message,
            'visibility': 'PRIVATE',
        }
        if resource_ids:
            payload['resourceIdList'] = resource_ids
        response = _request('POST', '/api/v1/memo', json=payload)
        return _response_json(response)

    api_base = '/api/v1' if api_style == 'v1' else '/api'
    payload = {
        'content': message,
        'visibility': 'PRIVATE',
        'createTime': _iso_time(timestamp),
        'updateTime': _iso_time(timestamp),
    }
    if attachments:
        try:
            payload['attachments'] = [{'name': attachment['name']} for attachment in attachments]
        except KeyError as exc:
            raise MemosApiError('新版 Memos 的附件响应缺少 name 字段') from exc
    params = {'memoId': memo_id} if memo_id else None
    response = _request('POST', f'{api_base}/memos', params=params, json=payload)
    if response.status_code == 409 and memo_id:
        response = _request('GET', f'{api_base}/memos/{memo_id}')
    return _response_json(response)


def deleteMemo(memo_id, api_style=None):
    api_style = api_style or _api_style()
    if api_style == 'legacy':
        path = f'/api/v1/memo/{memo_id}'
    else:
        api_base = '/api/v1' if api_style == 'v1' else '/api'
        path = f'{api_base}/memos/{memo_id}'
    response = _request('DELETE', path)
    if not response.ok:
        _response_json(response)
    return response.text
