import json
import os
import subprocess
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any

import requests
from requests import Response


def execute(cmd: str) -> str:
    """
    执行命令，获得返回结果
    """
    try:
        with os.popen(cmd) as p:
            return p.readline().strip()
    except Exception as err:
        print(str(err))
        return ""











# 私有属性
_cf_ip = None  # 上次优选 ip
_onlyonce = True  # 当总开关用
_version = None  # 获取本地上次 version

# 高级参数
# -dd 禁用下载测速；禁用后测速结果会按延迟排序 (默认按下载速度排序)；(默认 启用)
# -t 4 延迟测速次数；单个 IP 延迟测速的次数；(默认 4 次)
_additional_args = '-dd -t 1'
_cf_path = '/plugins.v2/zytcloudflareip'
_result_file = os.path.join(_cf_path, "result_hosts.txt")
_binary_name = 'CloudflareST'

if __name__ == '__main__':

    # 判断目录是否存在
    cf_path = Path(_cf_path)
    if not cf_path.exists():
        os.mkdir(_cf_path)


    # 获取CloudflareSpeedTest最新版本
    response = requests.get(url="https://api.github.com/repos/XIU2/CloudflareSpeedTest/releases/latest", timeout=10)
    release_version = json.loads(response.text)["tag_name"]
    if not release_version:
        exit(250)
    if release_version != _version: # 版本升级了,更新一次
        print(f'更新版本 {_version} -> {release_version}')

        # 我群晖=x86_64
        uname = execute('uname -m')
        arch = 'amd64' if uname == 'x86_64' else 'arm64'
        cf_file_name = f'CloudflareST_linux_{arch}.tar.gz'
        # wget -N https://github.com/XIU2/CloudflareSpeedTest/releases/download/v2.2.3/CloudflareST_linux_amd64.tar.gz
        download_url = f'https://ghproxy.com/https://github.com/XIU2/CloudflareSpeedTest/releases/download/{release_version}/{cf_file_name}'

        # 删除旧压缩包
        if Path(f'{_cf_path}/{cf_file_name}').exists():
            os.system(f'rm -rf {_cf_path}/{cf_file_name}')
        # 删除旧二进制
        if Path(f'{_cf_path}/{_binary_name}').exists():
            os.system(f'rm -rf {_cf_path}/{_binary_name}')
        # 首次下载或下载新版压缩包
        # os.system(f'wget -P {_cf_path} https://ghproxy.com/{download_url}')
        # os.system(f'wget -P {_cf_path} {download_url}')
        # os.system(f'wget -N -P {_cf_path} {download_url}')
        # os.system(f'curl -sS -O {_cf_path}/{cf_file_name} {download_url}')

        # 发送 GET 请求
        response = requests.get(download_url)
        # 检查响应状态码，确保请求成功
        response.raise_for_status()

        # 打开本地文件以二进制写入模式
        with open(f'{_cf_path}/{cf_file_name}', 'wb') as file:
            # 将响应内容写入本地文件
            file.write(response.content)
        print(f"文件 {_cf_path}/{cf_file_name} 下载成功。")

        # 判断是否下载好安装包
        if Path(f'{_cf_path}/{cf_file_name}').exists():
            # 解压
            os.system(f"tar -zxf {_cf_path}/{cf_file_name} -C {_cf_path}")
        if not Path(f'{_cf_path}/{_binary_name}').exists():
            exit(251)

    print("开始进行CLoudflare CDN优选，请耐心等待")
    # 执行优选命令，-dd不测速
    cf_command = f'./{_binary_name} {_additional_args} -o {_result_file}'
    print(f'正在执行优选命令 {cf_command}')
    os.system(cf_command)

    # 获取优选后最优ip
    best_ip = execute("sed -n '2,1p' " + _result_file + " | awk -F, '{print $1}'")
    print(f"\n获取到最优ip==>[{best_ip}]")

    if best_ip == _cf_ip:
        print(f"CloudflareSpeedTest CDN优选ip未变，不做处理")
        exit(252)

    # __os_install(download_url, cf_file_name, release_version, f"tar -zxf {_cf_path}/{cf_file_name} -C {_cf_path}")
