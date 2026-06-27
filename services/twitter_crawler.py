# services/twitter_crawler.py
import os
import asyncio
from typing import Tuple, List, Any
from twikit import Client

COOKIES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies")
os.makedirs(COOKIES_DIR, exist_ok=True)

def get_cookie_path(username: str) -> str:
    return os.path.join(COOKIES_DIR, f"cookies_{username}.json")

async def test_account_and_proxy(username: str, password: str, email: str, proxy: str = None) -> Tuple[bool, str]:
    """
    保存时默认联调测试：验证代理连通性及推特账号登录凭证。
    测试成功会直接保存/更新本地 Cookie。
    """
    # 代理格式化检验
    proxy_url = proxy.strip() if proxy else None
    if proxy_url and not (proxy_url.startswith("http://") or proxy_url.startswith("https://")):
        return False, "代理格式必须以 http:// 或 https:// 开头"

    client = Client(language="en-US", proxy=proxy_url)
    cookie_path = get_cookie_path(username)

    # 尝试登录测试
    try:
        # 如果存在旧 Cookie，尝试先清理以保证是全新登录测试
        if os.path.exists(cookie_path):
            os.remove(cookie_path)
            
        await client.login(
            auth_info_1=username,
            auth_info_2=email,
            password=password
        )
        
        # 登录成功，写入 Cookie
        client.save_cookies(cookie_path)
        return True, "验证通过"
    except Exception as e:
        err_msg = str(e)
        if "timeout" in err_msg.lower() or "connect" in err_msg.lower():
            return False, f"代理 IP 连接超时或失效: {err_msg}"
        return False, f"推特凭证校验失败: {err_msg}"

async def fetch_list_tweets(username: str, password: str, email: str, proxy: str, list_id: str) -> List[Any]:
    """
    使用指定账号和绑定的代理抓取指定推特列表的推文。
    具备 Cookie 失效自动重新登录与自愈功能。
    """
    proxy_url = proxy.strip() if proxy else None
    client = Client(language="en-US", proxy=proxy_url)
    cookie_path = get_cookie_path(username)
    
    # 尝试载入 Cookie 启动
    logged_in = False
    if os.path.exists(cookie_path):
        try:
            client.load_cookies(cookie_path)
            logged_in = True
        except Exception as e:
            print(f"[爬虫警告] 账号 {username} 载入 Cookie 失败: {e}，将尝试密码登录自愈。")
            if os.path.exists(cookie_path):
                os.remove(cookie_path)

    # 如果没有载入成功，执行密码登录
    if not logged_in:
        await client.login(
            auth_info_1=username,
            auth_info_2=email,
            password=password
        )
        client.save_cookies(cookie_path)
        print(f"[爬虫通知] 账号 {username} 成功通过密码登录并保存 Cookie。")

    # 执行列表获取 (获取最新 30 条即可)
    tweets = await client.get_list_tweets(list_id, count=30)
    return tweets
