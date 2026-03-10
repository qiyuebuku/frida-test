#!/usr/bin/env python3
"""
保存基金API请求数据到文件
从logcat中提取完整的clientRequestHX请求并保存为JSON
"""

import subprocess
import json
import sys
from datetime import datetime


def capture_and_save_api_data(output_file='/tmp/fund_api_requests.json'):
    """捕获并保存API请求数据"""

    # 读取最近的logcat
    result = subprocess.run(
        ['/mnt/d/123pan/Downloads/一加Ace6/adb命令行/adb.exe', '-s', '3B15BJ00GZL00000',
         'logcat', '-d', '-s', 'THSHook:I'],
        capture_output=True,
        text=True,
        errors='ignore'
    )

    lines = result.stdout.split('\n')

    # 提取所有clientRequestHX请求
    requests = []
    for line in lines:
        if '>>> clientRequestHX called with:' in line:
            try:
                json_str = line.split('>>> clientRequestHX called with: ')[1].strip()
                data = json.loads(json_str)

                # 提取请求信息
                if data and len(data) > 0:
                    req_data = data[0].get('data', {})
                    requests.append({
                        'timestamp': line.split()[0] + ' ' + line.split()[1],
                        'method': req_data.get('method'),
                        'url': req_data.get('url'),
                        'params': req_data.get('params', {}),
                        'Header': req_data.get('Header', {}),
                        'K5type': req_data.get('K5type'),
                        'needToken': req_data.get('needToken', False),
                        'callbackId': data[0].get('callbackId')
                    })
            except Exception as e:
                print(f'解析失败: {e}', file=sys.stderr)
                continue

    # 去重（同一个URL可能被调用多次）
    unique_urls = {}
    for req in requests:
        url = req['url']
        if url not in unique_urls:
            unique_urls[url] = req

    # 保存到文件
    output_data = {
        'captured_at': datetime.now().isoformat(),
        'total_requests': len(requests),
        'unique_requests': len(unique_urls),
        'requests': list(unique_urls.values())
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f'✓ 已保存 {len(unique_urls)} 个唯一API请求到: {output_file}')

    # 显示摘要
    print('\n=== API请求摘要 ===')
    for i, req in enumerate(unique_urls.values(), 1):
        marker = ' ★★★ 持仓API' if 'fundposition' in req['url'] else ''
        print(f'{i}. {req["method"]} {req["url"]}{marker}')

    return output_data


if __name__ == '__main__':
    data = capture_and_save_api_data()

    # 查找持仓API
    holdings_api = next((r for r in data['requests'] if 'fundposition' in r['url']), None)

    if holdings_api:
        print('\n\n=== 持仓详情API ===')
        print(json.dumps(holdings_api, ensure_ascii=False, indent=2))
