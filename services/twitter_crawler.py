# services/twitter_crawler.py
import os
import asyncio
import secrets
import logging
from typing import Tuple, List, Any
from twikit import Client

logger = logging.getLogger("taox")

def print(*args, **kwargs):
    """
    重写模块级 print，智能路由到 Logging 以供 Web 端 SSE 实时捕获。
    """
    msg = " ".join(str(arg) for arg in args)
    if "错误" in msg or "error" in msg.lower():
        logger.error(msg)
    elif "警告" in msg or "warn" in msg.lower():
        logger.warning(msg)
    else:
        logger.info(msg)

COOKIES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies")
os.makedirs(COOKIES_DIR, exist_ok=True)

def get_cookie_path(username: str) -> str:
    return os.path.join(COOKIES_DIR, f"cookies_{username}.json")

async def test_account_and_proxy(username: str, password: str, email: str, proxy: str = None) -> Tuple[bool, str]:
    """
    保存时默认联调测试：验证代理连通性及推特账号登录凭证。
    此处 password 参数实际传入的是 auth_token。
    """
    # 代理格式化检验
    proxy_url = proxy.strip() if proxy else None
    if proxy_url and not (proxy_url.startswith("http://") or proxy_url.startswith("https://")):
        return False, "代理格式必须以 http:// 或 https:// 开头"

    client = Client(language="en-US", proxy=proxy_url)
    cookie_path = get_cookie_path(username)
    auth_token = password.strip()

    try:
        # 如果存在旧 Cookie，尝试先清理以保证是全新登录测试
        if os.path.exists(cookie_path):
            os.remove(cookie_path)
            
        # 设置 auth_token 和 随机生成的 ct0 配合绕过 CSRF
        client.set_cookies({
            "auth_token": auth_token,
            "ct0": secrets.token_hex(16)
        })
        
        # 强行获取一次 Timeline，Token 如果失效或不存在，此步骤必抛出 401/403 异常
        await client.get_latest_timeline(count=1)
            
        # 登录成功，写入 Cookie
        client.save_cookies(cookie_path)
        return True, "验证通过"
    except Exception as e:
        err_msg = str(e)
        if "timeout" in err_msg.lower() or "connect" in err_msg.lower():
            return False, f"代理 IP 连接超时或失效: {err_msg}"
        return False, f"推特 Token 验证失败 (请确认 Token 没过期且用户名正确): {err_msg}"

async def fetch_home_tweets(username: str, password: str, email: str, proxy: str) -> List[Any]:
    """
    使用指定账号和绑定的代理抓取该账号「正在关注(Following)」最新的时间线(Timeline)。
    此处 password 参数实际为 auth_token。
    """
    proxy_url = proxy.strip() if proxy else None
    client = Client(language="en-US", proxy=proxy_url)
    cookie_path = get_cookie_path(username)
    auth_token = password.strip()
    
    # 尝试载入 Cookie 启动
    logged_in = False
    if os.path.exists(cookie_path):
        try:
            client.load_cookies(cookie_path)
            logged_in = True
        except Exception as e:
            print(f"[爬虫警告] 账号 {username} 载入 Cookie 失败: {e}，将尝试使用 Token 重新加载。")
            if os.path.exists(cookie_path):
                os.remove(cookie_path)

    # 如果没有载入成功，使用 Token 重新设置
    if not logged_in:
        client.set_cookies({
            "auth_token": auth_token,
            "ct0": secrets.token_hex(16)
        })
        client.save_cookies(cookie_path)
        print(f"[爬虫通知] 账号 {username} 成功通过 Auth Token 初始化并保存 Cookie。")

    # 获取小号 Timeline（按最新排序的关注流，拉取最新 30 条）
    tweets = await client.get_latest_timeline(count=30)
    return tweets
