import json
import re
import time
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType
from app.utils.http import RequestUtils


class ZYTInvitesSignin(_PluginBase):
    # 插件名称
    plugin_name = "药丸签到(zyt)"
    # 插件描述
    plugin_desc = "药丸论坛签到。"
    # 插件图标
    plugin_icon = "invites.png"
    # 插件版本
    plugin_version = "1.4.1.3"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/thsrite"
    # 插件配置项ID前缀
    plugin_config_prefix = "zytinvitessignin_"
    # 加载顺序
    plugin_order = 24
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _cron = None
    _cookie = None
    _user_agent = None
    _onlyonce = False
    _use_proxy = False
    _notify = False
    _only_notify_error = False
    _history_days = None
    _main_url = None

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._cookie = config.get("cookie")
            self._user_agent = config.get("user_agent")
            self._use_proxy = config.get("use_proxy")
            self._notify = config.get("notify")
            self._only_notify_error = config.get("only_notify_error")
            self._onlyonce = config.get("onlyonce")
            self._history_days = config.get("history_days") or 30
            self._main_url = config.get("main_url") or 'https://.www.invites.fun'

        if self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info(f"药丸签到服务启动，立即运行一次")
            self._scheduler.add_job(func=self.__signin, trigger='date',
                                    run_date=datetime.now(
                                        tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="药丸签到")
            # 关闭一次性开关
            self._onlyonce = False
            self.update_config({
                "onlyonce": False,
                "cron": self._cron,
                "enabled": self._enabled,
                "cookie": self._cookie,
                "user_agent": self._user_agent,
                "use_proxy": self._use_proxy,
                "notify": self._notify,
                "only_notify_error": self._only_notify_error,
                "history_days": self._history_days,
                "main_url": self._main_url,
            })

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def __signin(self):
        """
        药丸签到
        """
        # res = RequestUtils(
        #     cookies=site.get("cookie"),
        #     ua=site.get("ua") or settings.USER_AGENT,
        #     proxies=settings.PROXY if site.get("proxy") else None
        # ).get_res(url=page_url)

        res = RequestUtils(cookies=self._cookie, ua=self._user_agent,
                           proxies=settings.PROXY if self._use_proxy else None).get_res(
            url=self._main_url)
        if not res or res.status_code != 200:
            self.send_error_notify("请求药丸错误")
            return

        # 获取csrfToken
        pattern = r'"csrfToken":"(.*?)"'
        csrfToken = re.findall(pattern, res.text)
        if not csrfToken:
            self.send_error_notify("请求csrfToken失败")
            return

        csrfToken = csrfToken[0]
        logger.info(f"获取csrfToken成功 {csrfToken}")

        # 获取userid
        pattern = r'"userId":(\d+)'
        match = re.search(pattern, res.text)

        if match:
            userId = match.group(1)
            logger.info(f"获取userid成功 {userId}")
        else:
            self.send_error_notify("未找到userId")
            return

        # headers = {
        #     "X-Csrf-Token": csrfToken,
        #     "X-Http-Method-Override": "PATCH",
        #     "Cookie": self._cookie
        # }
        # 请求头信息
        headers = {
            "authority": self._main_url.replace('https://', '', 1),
            "method": "POST",
            "path": "/api/users/" + userId,
            "scheme": "https",
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json; charset=UTF-8",
            "cookie": self._cookie,
            "dnt": "1",
            "origin": self._main_url,  # https://www.invites.fun
            "referer": self._main_url,
            # "sec-ch-ua": "\"Google Chrome\";v=\"129\", \"Not=A?Brand\";v=\"8\", \"Chromium\";v=\"129\"",
            # "sec-ch-ua-mobile": "?1",
            # "sec-ch-ua-platform": "\"Android\"",
            # "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sec-gpc": "1",
            "user-agent": self._user_agent,
            "x-csrf-token": csrfToken,
            "x-http-method-override": "PATCH"
        }

        data = {
            "data": {
                "type": "users",
                "attributes": {
                    "canCheckin": False,
                    "totalContinuousCheckIn": 1
                },
                "id": userId
            }
        }

        # 请求的 URL "api/users/{}"
        # check_in_url = self.site.page_sign_in.format(user_id)

        # 开始签到
        # POST请求
        res = RequestUtils(cookies=self._cookie, ua=self._user_agent,
                           proxies=settings.PROXY if self._use_proxy else None,
                           headers=headers).post_res(url=f"{self._main_url}/api/users/{userId}",
                                                     json=data)

        if not res or res.status_code != 200:
            logger.error("药丸签到失败")

            # 发送通知
            if self._notify or self._only_notify_error:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【药丸签到任务失败】",
                    text="签到失败，请检查cookie是否失效")
            return

        sign_dict = json.loads(res.text)
        money = sign_dict['data']['attributes']['money']
        totalContinuousCheckIn = sign_dict['data']['attributes']['totalContinuousCheckIn']

        # 发送通知
        if self._notify:
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="【药丸签到任务完成】",
                text=f"累计签到 {totalContinuousCheckIn} \n"
                     f"剩余药丸 {money}")

        # 读取历史记录
        history = self.get_data('history') or []

        history.append({
            "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
            "totalContinuousCheckIn": totalContinuousCheckIn,
            "money": money
        })

        thirty_days_ago = time.time() - int(self._history_days) * 24 * 60 * 60
        history = [record for record in history if
                   datetime.strptime(record["date"],
                                     '%Y-%m-%d %H:%M:%S').timestamp() >= thirty_days_ago]
        # 保存签到历史
        self.save_data(key="history", value=history)

    def send_error_notify(self, msg_content):
        logger.error(msg_content)
        if self._only_notify_error:
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="【药丸签到任务失败】",
                text=msg_content)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            return [{
                "id": "ZYTInvitesSignin",
                "name": "药丸签到服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__signin,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
                   {
                       'component': 'VForm',
                       'content': [
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'enabled',
                                                   'label': '启用插件',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'use_proxy',
                                                   'label': '代理',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'notify',
                                                   'label': '开启通知',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'only_notify_error',
                                                   'label': '只开启失败通知',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'onlyonce',
                                                   'label': '立即运行一次',
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'cron',
                                                   'label': '签到周期'
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'history_days',
                                                   'label': '保留历史天数'
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'main_url',
                                                   'label': 'https://www.invites.fun'
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'cookie',
                                                   'label': '药丸cookie'
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'user_agent',
                                                   'label': 'user_agent'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                       },
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '整点定时签到失败？不妨换个时间试试'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           }
                       ]
                   }
               ], {
                   "enabled": False,
                   "onlyonce": False,
                   "use_proxy": False,
                   "notify": False,
                   "only_notify_error": False,
                   "cookie": "",
                   "history_days": 30,
                   "main_url": 'https://www.invites.fun',
                   "cron": "0 9 * * *"
               }

    def get_page(self) -> List[dict]:
        # 查询同步详情
        historys = self.get_data('history')
        if not historys:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]

        if not isinstance(historys, list):
            historys = [historys]

        # 按照签到时间倒序
        historys = sorted(historys, key=lambda x: x.get("date") or 0, reverse=True)

        # 签到消息
        sign_msgs = [
            {
                'component': 'tr',
                'props': {
                    'class': 'text-sm'
                },
                'content': [
                    {
                        'component': 'td',
                        'props': {
                            'class': 'whitespace-nowrap break-keep text-high-emphasis'
                        },
                        'text': history.get("date")
                    },
                    {
                        'component': 'td',
                        'text': history.get("totalContinuousCheckIn")
                    },
                    {
                        'component': 'td',
                        'text': history.get("money")
                    }
                ]
            } for history in historys
        ]

        # 拼装页面
        return [
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                        },
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'hover': True
                                },
                                'content': [
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '时间'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '连续签到次数'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '剩余药丸'
                                            },
                                        ]
                                    },
                                    {
                                        'component': 'tbody',
                                        'content': sign_msgs
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

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
