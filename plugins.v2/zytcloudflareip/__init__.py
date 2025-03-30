from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType
import os
import time
import socket
from app.utils.system import SystemUtils
import requests
import json
from app.core.event import eventmanager, Event
from app.schemas.types import EventType, NotificationType


class ZYTCloudflareIP(_PluginBase):

    # 插件名称
    plugin_name = "Cloudflare优选IP"
    # 插件描述
    plugin_desc = "使用简单的python脚本"
    # 插件图标
    plugin_icon = "Cloudflare_A.png"
    # 插件版本
    plugin_version = "1.0.1"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "zytcloudflareip_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _cron = None
    _onlyonce = False
    _notify = False
    _cmd = None

    HOSTS_TEMPLATE = """# Cloudflare IP Start Update time: {update_time}{content}
        # Cloudflare IP End"""
    HOST_PATH = '/etc/hosts'

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._notify = config.get("notify")
            self._onlyonce = config.get("onlyonce")
            self._cmd = config.get("cmd")

            # 加载模块
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._enabled and self._cron:
                try:
                    self._scheduler.add_job(
                        func=self.run,
                        trigger=CronTrigger.from_crontab(self._cron),
                        name="cloudflare优选IP",
                    )
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")

            if self._onlyonce:
                logger.info(f"cloudflare优选IP服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.run,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                             + timedelta(seconds=3),
                    name="cloudflare优选IP",
                )
                # 关闭一次性开关
                self._onlyonce = False
                self.update_config(
                    {
                        "onlyonce": False,
                        "cron": self._cron,
                        "enabled": self._enabled,
                        "notify": self._notify,
                        "cmd": self._cmd,
                    }
                )

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.logger.info_jobs()
                self._scheduler.start()

    def __os_install(self, download_url, release_version, gz_file_path, binary_file_path, unzip_command, _binary_name, cur_version_file_path):
        # 删除 .gz
        if os.path.exists(gz_file_path):
            os.remove(gz_file_path)
        # os.system(f'wget -P {_cf_path} https://ghproxy.com/{download_url}')
        # os.system(f'curl {download_url} -O {gz_file_path}')
        try:
            # 发送 HTTP GET 请求
            response = requests.get(download_url, stream=True)
            # 检查响应状态码
            response.raise_for_status()

            # 以二进制写入模式打开文件
            with open(gz_file_path, 'wb') as file0:
                # 分块写入文件
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file0.write(chunk)
            logger.info(f"{gz_file_path} 下载压缩包成功")
        except requests.exceptions.RequestException as e:
            logger.info(f"下载过程中出现错误: {e}")
        except Exception as e:
            logger.info(f"发生未知错误: {e}")

        # 判断是否下载好安装包
        if os.path.exists(gz_file_path):
            if os.path.exists(binary_file_path):
                os.remove(binary_file_path)
            # 解压
            os.system(f'{unzip_command}')
            # 删除压缩包
            os.remove(gz_file_path)
        # 是否有命令行文件
        if os.path.exists(binary_file_path):
            logger.info(f"{_binary_name}更新成功，当前版本：{release_version}")
            with open(cur_version_file_path, 'w') as file1:
                file1.write(release_version)
        else:
            logger.info(f"CloudflareSpeedTest安装失败，请检查")
            exit(-10)

    def check_tcp_connection(self, index, ip, timeout=5):
        if not ip:
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result80 = sock.connect_ex((ip, 80))
        result443 = sock.connect_ex((ip, 443))
        if result80 == 0 and result443 == 0:
            logger.info(f"    index={index} {ip} 80,443 的TCP连接均正常")
            sock.close()
            return True
        sock.close()
        if result80 != 0:
            logger.info(f"    index={index} {ip} 80 的TCP连接失败")
        elif result443 != 0:
            logger.info(f"    index={index} {ip} 443 的TCP连接失败")
        return False
    
    def get_new_version_by_github(self):
        try:
            response = requests.request("get",
                                        "https://api.github.com/repos/XIU2/CloudflareSpeedTest/releases/latest")
            new_version = f"{response.json()['tag_name']}".strip()
            return new_version
        except Exception as err:
            logger.info(f'获取github 最新版本出错:{err}')
        return None

    def get_best_id_and_check_tcp(self, index, _result_file):
        ip = SystemUtils.execute(f"sed -n '{index},1p' " + _result_file + " | awk -F, '{logger.info $1}'")
        if self.check_tcp_connection(index, ip):
            return ip
        else:
            return None

    def is_cloudflare_domain(self, domain, retry=3):
        try:
            ip = socket.gethostbyname(domain)
        except socket.gaierror as e:
            logger.info(f"错误: 无法解析域名 {domain}，错误信息: {e},retry={4 - retry}")
            ip = None
        if ip:
            try:
                response = requests.get(url=f'https://ip.zxinc.org/api.php?type=json&ip={ip}',
                                        timeout=10)
                local = json.loads(response.text)['data']['local']
                if local and 'CloudFlare节点' in local:
                    return True
            except Exception as e:
                logger.info(f"错误: 无法获取{domain} {ip}，local信息: {e},retry={4 - retry}")
        if retry > 0:
            return self.is_cloudflare_domain(domain, retry - 1)
        return False

    # 写入系统host信息
    def append_host_file(self, append_content: str) -> None:
        hostFile = self.HOST_PATH
        # 拆分路径和文件名
        directory = os.path.dirname(hostFile)
        if not os.path.exists(directory):
            os.makedirs(directory)
        # 如果目录不存在，创建目录
        if not os.path.exists(hostFile):
            # 创建空文件
            try:
                open(hostFile, 'w').close()
                logger.info(f"成功创建文件：{hostFile}")
            except Exception as e:
                logger.info(f"创建文件时出错：{e}")

        origin = ""
        with open(hostFile, "r", encoding="utf-8") as f:
            # 之前是否已经写过dns信息
            flag = False
            for eachLine in f.readlines():
                if r"# Cloudflare IP Start" in eachLine:
                    flag = True
                elif r"# Cloudflare IP End" in eachLine:
                    flag = False
                else:
                    if not flag:
                        origin = origin + eachLine
            # 写入新的host记录
            origin = origin.strip()
            origin = origin + '\n' + append_content
        with open(hostFile, "w", encoding="utf-8") as f:
            f.write(origin)
        logger.info(f'---CloudFlare IP已写入{self.HOST_PATH}:')
        logger.info(origin)

    @eventmanager.register(EventType.PluginAction)
    def run(self, event: Event = None):
        # class _PluginBase(metaclass=ABCMeta)
        # 插件模块基类，通过继续该类实现插件功能
        # 除内置属性外，还有以下方法可以扩展或调用：
        # - stop_service() 停止插件服务
        # - get_config() 获取配置信息
        # - update_config() 更新配置信息
        # - init_plugin() 生效配置信息
        # - get_data_path() 获取插件数据保存目录
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "cloudflareip":
                return
            logger.info("收到cloudflareip命令，开始Cloudflare优选IP ...")
            self.post_message(
                mtype=NotificationType.SiteMessage, title=f"开始Cloudflare优选IP ...")

        msg = ""
        success = False
        try:
            # 官网 https://github.com/XIU2/CloudflareSpeedTest
            # 1.定义工作目录
            # 获取脚本所在的文件夹绝对路径
            script_dir = self.get_data_path()
            # script_dir = os.path.dirname(os.path.abspath(__file__))

            INPUT_LINES_DOMAINS = self._cmd.split("\n")
                
            result_check_domains_file = f'{script_dir}/cloudflare_speed_test_result.txt'
            _cf_path = f'{script_dir}/CloudflareSpeedTest'
            _additional_args = ''
            _download_prefix = 'https://github.com/XIU2/CloudflareSpeedTest/releases/download'
            _binary_name = 'CloudflareST'
            _cf_ipv4 = os.path.join(_cf_path, "ipv4.txt")
            _cf_ipv6 = os.path.join(_cf_path, "ipv6.txt")
            # _result_file = "result_hosts.txt"
            _result_file = os.path.join(_cf_path, "result_hosts.txt")
            uname = SystemUtils.execute('uname -m')
            arch = 'amd64' if uname == 'x86_64' else 'arm64'
            cf_file_name = f'CloudflareST_linux_{arch}.tar.gz'
            gz_file_path = os.path.join(_cf_path, cf_file_name)
            binary_file_path = os.path.join(_cf_path, _binary_name)
            cur_version_file_path = os.path.join(_cf_path, 'cache_cur_version.txt')

            unzip_command = f"tar -zxf {_cf_path}/{cf_file_name} -C {_cf_path}"

            # 2.检查脚本更新
            now = time.time()
            datetime0 = datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H:%M:%S")
            # 创建目录
            if not os.path.exists(_cf_path):
                os.mkdir(_cf_path)
            # 首次运行,先从国内gitee下载一份v2.2.5
            # 是否有命令行文件
            if not os.path.exists(binary_file_path):
                logger.info("---首次运行,先从国内gitee下载一份 v2.2.5")
                lanzouyun_download_url = 'https://gitee.com/abcdef789/cloudflare_speed_test/raw/master/CloudflareST_linux_amd64.tar.gz' if uname == 'x86_64' else 'https://gitee.com/abcdef789/cloudflare_speed_test/raw/master/CloudflareST_linux_arm64.tar.gz'
                self.__os_install(lanzouyun_download_url, 'v2.2.5', gz_file_path, binary_file_path, unzip_command, _binary_name, cur_version_file_path)
            # 获取github CloudflareSpeedTest最新版本,如果有就更新
            new_version = self.get_new_version_by_github()
            cur_version = None
            if os.path.exists(cur_version_file_path):
                with open(cur_version_file_path, 'r') as file:
                    cur_version = file.readline().strip()
            if new_version and new_version != cur_version:
                download_url = f'{_download_prefix}/{new_version}/{cf_file_name}'
                self.__os_install(download_url, new_version, gz_file_path, binary_file_path, unzip_command, _binary_name, cur_version_file_path)

            # 3.运行CloudflareST,找出最快 IP
            if os.path.exists(binary_file_path):
                logger.info(f"---开始进行CLoudflare IP优选 {datetime0} ")
                if not os.path.exists(_result_file):
                    with open(_result_file, 'w') as file:
                        pass  # 这里使用 pass 语句，因为我们只是想创建一个空文件，不需要写入任何内容
                cf_command = f'cd {_cf_path} && chmod a+x {_binary_name} && ./{_binary_name} {_additional_args} -o {_result_file} >/dev/null 2>&1'
                os.system(cf_command)
                time.sleep(1)
            best_ip = None
            if os.path.exists(_result_file):
                for i in range(2, 12):
                    result_ip = self.get_best_id_and_check_tcp(i, _result_file)
                    if result_ip:
                        best_ip = result_ip
                        break

            # 4.读取待替换域名列表,检查是否为优选ip,批量替换
            if best_ip:
                logger.info(f"---获取到最优ip {best_ip},开始筛选CloudFlare 域名")
                # 开始替换
                to_check_domains_content = f'#---以下域名已替换为CF 优选IP[{best_ip}] {datetime0}'  # 待写入.txt的内容
                cf_domains_set = set()
                not_cf_domains_set = set()

                for eachLine in INPUT_LINES_DOMAINS:
                    domain = eachLine.strip()
                    if not domain:
                        pass
                    elif domain.startswith('#---'):
                        pass
                    elif domain.startswith('#'):  # 注释行,添加到最前
                        to_check_domains_content = f"{eachLine}\n{to_check_domains_content}"
                    else:  # 正常domain
                        if self.is_cloudflare_domain(domain):
                            cf_domains_set.add(domain)
                        else:
                            not_cf_domains_set.add(domain)
                if cf_domains_set:
                    hosts_content = ""  # 待写入 host 文件的内容
                    for domain in cf_domains_set:
                        hosts_content += f"\n{best_ip}	{domain}"
                        to_check_domains_content += f"\n{domain}"
                    hosts_content = self.HOSTS_TEMPLATE.format(content=hosts_content, update_time=datetime0)
                    self.append_host_file(hosts_content)
                if not_cf_domains_set:
                    to_check_domains_content += f'#---以下域名不是CloudFlare IP,跳过配置到hosts {datetime0}'  # 待写入.txt的内容
                    logger.info(f'---以下域名不是CloudFlare IP,跳过配置到hosts:')
                    for domain in not_cf_domains_set:
                        to_check_domains_content += f"\n{domain}"
                        logger.info(domain)
                # 更新TO_CHECK_DOMAINS
                with open(result_check_domains_file, "w", encoding="utf-8") as f:
                    f.write(to_check_domains_content)

                logger.info(f'\nSUCCESS End script, 共耗时:{int(time.time() - now)}s')
                success = True
        except Exception as e:
            success = False
            logger.error(f"cloudflare优选IP出错: {e}")
            msg = f"{e}"

        # 发送通知
        if self._notify:
            if success:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【cloudflare优选IP成功】")
            else:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【cloudflare优选IP出错】", text=msg
                )

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
                定义远程控制命令
                :return: 命令关键字、事件、描述、附带数据
                """
        return [{
            "cmd": "/cloudflareip",
            "event": EventType.PluginAction,
            "desc": "Cloudflare优选IP",
            "data": {
                "action": "cloudflareip"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
                   {
                       "component": "VForm",
                       "content": [
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 3},
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "enabled",
                                                   "label": "启用插件",
                                               },
                                           }
                                       ],
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 3},
                                       "content": [
                                           {
                                               "component": "VCronField",
                                               "props": {"model": "cron", "label": "执行周期"},
                                           }
                                       ],
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 3},
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "notify",
                                                   "label": "开启通知",
                                               },
                                           }
                                       ],
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 3},
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "onlyonce",
                                                   "label": "立即运行一次",
                                               },
                                           }
                                       ],
                                   },
                               ],
                           },
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                       },
                                       "content": [
                                           {
                                               "component": "VTextarea",
                                               "props": {
                                                   "model": "cmd",
                                                   "rows": "2",
                                                   "label": "域名",
                                                   "placeholder": "待优选域名，一行一个,#开头的会忽略",
                                               },
                                           }
                                       ],
                                   }
                               ],
                           },
                       ],
                   }
               ], {"enabled": False, "request_method": "POST", "webhook_url": ""}

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
