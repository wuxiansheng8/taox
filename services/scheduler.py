# services/scheduler.py
import asyncio
import html
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

logger = logging.getLogger("taox")

def print(*args, **kwargs):
    """
    重写模块级 print，将输出智能路由到 Python Logging 系统以供 Web 端 SSE 实时捕获。
    同时根据日志前缀关键字自动区分日志等级。
    """
    msg = " ".join(str(arg) for arg in args)
    if "错误" in msg or "failure" in msg.lower():
        logger.error(msg)
    elif "警告" in msg or "warn" in msg.lower():
        logger.warning(msg)
    else:
        logger.info(msg)
from database.crud import (
    get_settings, get_active_accounts, update_account_status,
    add_metric, is_tweet_forwarded, mark_tweet_forwarded, get_remarks,
    add_pending_tweet, get_all_pending_tweets, remove_pending_tweet,
    cleanup_old_data
)
from services.twitter_crawler import fetch_home_tweets
from services.translator import translate_text, TRANSLATOR_STATE, test_translation
from services.telegram_bot import send_tweet_to_telegram

# 全局运行状态看板数据
SCHEDULER_STATE = {
    "is_running": False,
    "next_scan_time": None,
    "active_accounts_count": 0,
    "last_error": None
}

# 缓存已加载的备注配置
remarks_cache = {}

# 协程任务全局控制引用
_scheduler_task = None
_recovery_task = None
_sender_task = None
_cleanup_task = None

# 高并发去重与流式发送队列
tg_send_queue = asyncio.Queue()
QUEUED_TWEET_IDS = set()
ACCOUNT_FAIL_COUNT = {} # 记录每个推特账号连续失败的计数器，防止网络抖动误伤账号池

def update_remarks_cache():
    global remarks_cache
    try:
        remarks_list = get_remarks()
        remarks_cache = {
            r["username"].lower(): {
                "remark_name": r["remark_name"],
                "is_highlight": r["is_highlight"]
            }
            for r in remarks_list
        }
    except Exception as e:
        print(f"[调度器警告] 刷新备注缓存失败: {e}")

async def start_scheduler():
    global _scheduler_task, _recovery_task, _sender_task, _cleanup_task
    
    # 动态检测所有后台任务的真实健康状态 (是否创建，且未运行完毕)
    tasks_healthy = (
        _scheduler_task and not _scheduler_task.done() and
        _recovery_task and not _recovery_task.done() and
        _sender_task and not _sender_task.done() and
        _cleanup_task and not _cleanup_task.done()
    )
    
    # 如果运行状态正常且所有子任务都在线，则直接返回
    if SCHEDULER_STATE["is_running"] and tasks_healthy:
        return
        
    # 否则说明发生了异常关闭或某个关键协程崩溃，先执行彻底清理，防残留
    await stop_scheduler()
    
    SCHEDULER_STATE["is_running"] = True
    
    # 从 SQLite 中读取持久化队列，并恢复至内存 asyncio.Queue 中
    try:
        pending_list = get_all_pending_tweets()
        if pending_list:
            import json
            print(f"[调度器] 从 SQLite 中检测到 {len(pending_list)} 条挂起的待发送推文，正在重新载入队列...")
            for p in pending_list:
                try:
                    job = json.loads(p["job_json"])
                    tweet_id = p["tweet_id"]
                    if not is_tweet_forwarded(tweet_id) and tweet_id not in QUEUED_TWEET_IDS:
                        QUEUED_TWEET_IDS.add(tweet_id)
                        await tg_send_queue.put(job)
                except Exception as je:
                    print(f"[调度器警告] 载入持久化推文任务出错: {je}")
    except Exception as dbe:
        print(f"[调度器错误] 读取持久化推文队列失败: {dbe}")
        
    # 重新激活所有后台子协程
    _scheduler_task = asyncio.create_task(scheduler_loop())
    _recovery_task = asyncio.create_task(translator_recovery_loop())
    _sender_task = asyncio.create_task(tg_sender_worker())
    _cleanup_task = asyncio.create_task(cleanup_scheduler_loop())

async def stop_scheduler():
    global _scheduler_task, _recovery_task, _sender_task, _cleanup_task
    SCHEDULER_STATE["is_running"] = False
    
    # 强制中止并显式等待各协程任务彻底退出，防止状态不同步产生的竞争
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
        
    if _recovery_task:
        _recovery_task.cancel()
        try:
            await _recovery_task
        except asyncio.CancelledError:
            pass
        _recovery_task = None
        
    if _sender_task:
        _sender_task.cancel()
        try:
            await _sender_task
        except asyncio.CancelledError:
            pass
        _sender_task = None
        
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None
        
    # 清空缓存与待发队列
    while not tg_send_queue.empty():
        try:
            tg_send_queue.get_nowait()
            tg_send_queue.task_done()
        except (asyncio.QueueEmpty, ValueError):
            break
    QUEUED_TWEET_IDS.clear()

async def translator_recovery_loop():
    """
    备用翻译器运行期间，每 30 分钟自动探测一次主通道，恢复后切回主用。
    """
    try:
        while True:
            await asyncio.sleep(1800) # 30分钟
            if not TRANSLATOR_STATE["is_primary_ok"]:
                print("[翻译器调度] 开始探测主用翻译通道是否恢复...")
                settings = get_settings()
                primary_prov = settings.get("translate_provider_primary", "google")
                
                config_primary = {
                    "api_key": settings.get("openai_primary_api_key"),
                    "base_url": settings.get("openai_primary_base_url"),
                    "model": settings.get("openai_primary_model")
                }
                
                success, _ = await test_translation(primary_prov, config_primary)
                if success:
                    TRANSLATOR_STATE["is_primary_ok"] = True
                    TRANSLATOR_STATE["active_channel"] = "primary"
                    print(f"[翻译器恢复] 检测到主用翻译通道 {primary_prov} 已恢复正常！已切回主用。")
    except asyncio.CancelledError:
        print("[翻译器调度] 恢复检测循环任务已安全取消退出。")

async def scheduler_loop():
    """
    推特抓取主轮询逻辑（生产者）：只做快速抓取和入队，不因发送/翻译阻碍下轮抓取。
    """
    current_account_index = 0
    
    while SCHEDULER_STATE["is_running"]:
        settings = get_settings()
        update_remarks_cache()
        
        try:
            interval = int(settings.get("polling_interval", 10))
        except ValueError:
            interval = 10
            
        next_dt = datetime.now() + timedelta(seconds=interval)
        SCHEDULER_STATE["next_scan_time"] = next_dt.isoformat()
            
        active_accounts = get_active_accounts()
        SCHEDULER_STATE["active_accounts_count"] = len(active_accounts)
        
        if not active_accounts:
            SCHEDULER_STATE["last_error"] = "无可用活跃推特账号，轮询暂停"
            await asyncio.sleep(5)
            continue
            
        if current_account_index >= len(active_accounts):
            current_account_index = 0
            
        account = active_accounts[current_account_index]
        username = account["username"]
        password = account["password"]
        email = account["email"]
        proxy = account["proxy"]
        
        try:
            print(f"[调度器] 开始使用账号 {username} 抓取「正在关注」最新 Timeline...")
            # 抓取正在关注最新推文 (30 条)
            tweets = await fetch_home_tweets(username, password, email, proxy)
            
            add_metric("success", account_used=username)
            update_account_status(username, "active")
            ACCOUNT_FAIL_COUNT[username] = 0 # 成功后立刻清零连续失败计数
            SCHEDULER_STATE["last_error"] = None
            
            for tweet in reversed(tweets):
                tweet_id = str(tweet.id)
                
                # 校验：数据库已发送，或者已在内存待发队列中，直接过滤
                if is_tweet_forwarded(tweet_id) or tweet_id in QUEUED_TWEET_IDS:
                    continue
                    
                author_username = tweet.user.screen_name
                author_display_name = tweet.user.name
                
                remark_info = remarks_cache.get(author_username.lower())
                remark_name = remark_info["remark_name"] if remark_info else None
                is_highlight = remark_info["is_highlight"] if remark_info else 0
                is_reply = tweet.in_reply_to is not None
                
                # 回复过滤规则
                if is_reply:
                    if not (remark_info and is_highlight == 1):
                        mark_tweet_forwarded(tweet_id) # 忽略的直接置为已处理
                        continue
                
                # 提取转发（Retweet）与引用（Quote）对象，兼容多版本 Twikit
                retweeted_tweet = None
                if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet:
                    retweeted_tweet = tweet.retweeted_tweet
                elif hasattr(tweet, 'retweeted_status') and tweet.retweeted_status:
                    retweeted_tweet = tweet.retweeted_status
                    
                # 确定主推文文本与转发人，并根据备注缓存自动查询转发人的备注
                retweet_author_name = None
                if retweeted_tweet:
                    raw_text = retweeted_tweet.text
                    ret_user = retweeted_tweet.user
                    ret_username = ret_user.screen_name
                    ret_display_name = ret_user.name
                    
                    ret_remark_info = remarks_cache.get(ret_username.lower())
                    if ret_remark_info:
                        retweet_author_name = f"【{ret_remark_info['remark_name']}】(@{ret_username})"
                    else:
                        retweet_author_name = f"{ret_display_name} (@{ret_username})"
                        
                    # 如果是转发，媒体资源以被转发推文为准
                    target_media_tweet = retweeted_tweet
                else:
                    raw_text = tweet.text
                    target_media_tweet = tweet
                
                # 确定引用推文（如果是转发，需要检查被转发的推文是否是引用推文）
                quoted_tweet = None
                active_tweet_for_quote = retweeted_tweet if retweeted_tweet else tweet
                
                if hasattr(active_tweet_for_quote, 'quoted_tweet') and active_tweet_for_quote.quoted_tweet:
                    quoted_tweet = active_tweet_for_quote.quoted_tweet
                elif hasattr(active_tweet_for_quote, 'quoted_status') and active_tweet_for_quote.quoted_status:
                    quoted_tweet = active_tweet_for_quote.quoted_status
                
                quoted_author_name = None
                quoted_raw_text = None
                if quoted_tweet:
                    q_user = quoted_tweet.user
                    q_username = q_user.screen_name
                    q_display_name = q_user.name
                    quoted_raw_text = quoted_tweet.text
                    
                    # 检查被引用者是否有备注
                    q_remark_info = remarks_cache.get(q_username.lower())
                    if q_remark_info:
                        quoted_author_name = f"【{q_remark_info['remark_name']}】(@{q_username})"
                    else:
                        quoted_author_name = f"{q_display_name} (@{q_username})"
                
                # 提取媒体资源
                media_list = []
                if target_media_tweet.media:
                    for media_item in target_media_tweet.media:
                        if media_item.type == "photo":
                            media_list.append({"type": "photo", "url": media_item.media_url_https})
                        elif media_item.type in ["video", "animated_gif"]:
                            if hasattr(media_item, "streams") and media_item.streams:
                                media_list.append({"type": "video", "url": media_item.streams[-1].url})
                
                # 打包并持久化压入队列，防止重启丢失
                job_data = {
                    "tweet_id": tweet_id,
                    "author_username": author_username,
                    "author_display_name": author_display_name,
                    "remark_name": remark_name,
                    "retweet_author_name": retweet_author_name,
                    "quoted_author_name": quoted_author_name,
                    "raw_text": raw_text,
                    "quoted_raw_text": quoted_raw_text,
                    "media_list": media_list,
                    "retry_count": 0
                }
                import json
                try:
                    add_pending_tweet(tweet_id, json.dumps(job_data))
                except Exception as dbe:
                    print(f"[调度器警告] 持久化推文 {tweet_id} 到待发队列失败: {dbe}")
                    
                QUEUED_TWEET_IDS.add(tweet_id)
                await tg_send_queue.put(job_data)
            
        except Exception as e:
            err_msg = str(e)
            print(f"[调度器错误] 使用账号 {username} 抓取失败: {err_msg}")
            add_metric("failure", error_message=err_msg, account_used=username)
            
            # 累加本账号的连续失败计数
            fail_count = ACCOUNT_FAIL_COUNT.get(username, 0) + 1
            ACCOUNT_FAIL_COUNT[username] = fail_count
            
            if fail_count >= 3:
                # 只有连续失败达 3 次时，才在数据库中正式标红挂起该账号
                if "proxy" in err_msg.lower() or "connect" in err_msg.lower() or "timeout" in err_msg.lower():
                    update_account_status(username, "error_proxy", error_message=err_msg)
                    print(f"[调度器警告] 账号 {username} 连续失败 3 次，因【代理故障】被正式标红挂起！")
                else:
                    update_account_status(username, "error_credentials", error_message=err_msg)
                    print(f"[调度器警告] 账号 {username} 连续失败 3 次，因【凭证/X封锁】被正式标红挂起！")
            else:
                # 1~2 次失败只在终端发出警告，数据库状态仍维持 active 供下轮使用
                print(f"[调度器提示] 账号 {username} 发生抓取失败 (本轮为第 {fail_count}/3 次连续失败)。暂不标红，等待后续轮换重试。")
                
            SCHEDULER_STATE["last_error"] = f"账号 {username} 抓取失败 (第 {fail_count}/3 次): {err_msg[:50]}"
            
        current_account_index += 1
        
        # 休眠，准备下一次纯抓取
        now = datetime.now()
        sleep_seconds = (next_dt - now).total_seconds()
        if sleep_seconds > 0:
            await asyncio.sleep(sleep_seconds)
        else:
            await asyncio.sleep(1)

async def tg_sender_worker():
    """
    异步发送队列消费者：负责翻译、拼接、发送媒体、降级等，与抓取解耦，独立限速运行。
    """
    try:
        while True:
            # 阻塞式从队列中获取推送任务
            job = await tg_send_queue.get()
            tweet_id = job["tweet_id"]
            
            # 双重防线
            if is_tweet_forwarded(tweet_id):
                QUEUED_TWEET_IDS.discard(tweet_id)
                tg_send_queue.task_done()
                continue
                
            settings = get_settings()
            tg_token = settings.get("tg_token")
            tg_chat_id = settings.get("tg_chat_id")
            
            if not tg_token or not tg_chat_id:
                await asyncio.sleep(5)
                await tg_send_queue.put(job)
                tg_send_queue.task_done()
                continue
            
            # 1. 限制原始纯文本和引用纯文本长度
            raw_text = job["raw_text"]
            if raw_text and len(raw_text) > 3000:
                raw_text = raw_text[:2990] + "..."
                
            quoted_raw_text = job.get("quoted_raw_text")
            if quoted_raw_text and len(quoted_raw_text) > 1000:
                quoted_raw_text = quoted_raw_text[:990] + "..."
            
            # 2. 翻译正文与被引用文本
            translated_text = ""
            if settings.get("translate_enabled") == "1":
                try:
                    translated_text = await translate_text(raw_text, settings)
                except Exception as te:
                    print(f"[发送队列警告] 翻译正文失败: {te}")
                    translated_text = "[翻译失败]"
            if translated_text and len(translated_text) > 3000:
                translated_text = translated_text[:2990] + "..."
                
            quoted_translated_text = ""
            if quoted_raw_text and settings.get("translate_enabled") == "1":
                try:
                    quoted_translated_text = await translate_text(quoted_raw_text, settings)
                except Exception as te:
                    print(f"[发送队列警告] 翻译引用文本失败: {te}")
                    quoted_translated_text = "[翻译失败]"
            if quoted_translated_text and len(quoted_translated_text) > 1000:
                quoted_translated_text = quoted_translated_text[:990] + "..."
            
            # 3. HTML 转义
            esc_author_username = html.escape(job["author_username"] or "")
            esc_author_display_name = html.escape(job["author_display_name"] or "")
            esc_remark_name = html.escape(job["remark_name"]) if job["remark_name"] else None
            esc_retweet_author_name = html.escape(job.get("retweet_author_name") or "") if job.get("retweet_author_name") else None
            esc_quoted_author_name = html.escape(job.get("quoted_author_name") or "") if job.get("quoted_author_name") else None
            
            esc_translated_text = html.escape(translated_text or "")
            esc_raw_text = html.escape(raw_text or "")
            esc_quoted_translated_text = html.escape(quoted_translated_text or "")
            esc_quoted_raw_text = html.escape(quoted_raw_text or "")
            
            # 4. 排版拼接 (严格匹配效果图排版)
            header_lines = []
            if esc_remark_name:
                header_lines.append(f"备注：【{esc_remark_name}】")
            header_lines.append(f"用户名：{esc_author_display_name} @{esc_author_username}")
            if esc_retweet_author_name:
                header_lines.append(f"转发自：{esc_retweet_author_name}")
                
            header = "\n".join(header_lines)
            
            # 拼装主贴内容
            if esc_translated_text and esc_translated_text != "[翻译失败]":
                body = esc_translated_text
            else:
                body = esc_raw_text
                
            # 拼装被引用贴内容
            quote_block = ""
            if esc_quoted_author_name:
                if esc_quoted_translated_text and esc_quoted_translated_text != "[翻译失败]":
                    q_body = esc_quoted_translated_text
                else:
                    q_body = esc_quoted_raw_text
                quote_block = f"\n\n引用自：{esc_quoted_author_name}\n{q_body}"
                
            tg_text = f"{header}\n{body}{quote_block}"
            
            # 5. 生成推文原始 URL 并调用发送
            tweet_url = f"https://x.com/{job['author_username']}/status/{tweet_id}"
            success = await send_tweet_to_telegram(tg_token, tg_chat_id, tg_text, job["media_list"], tweet_url)
            
            if success:
                try:
                    remove_pending_tweet(tweet_id)
                except Exception as dbe:
                    print(f"[发送队列警告] 移除持久化待发记录失败: {dbe}")
                mark_tweet_forwarded(tweet_id)
            else:
                # 累加任务重试次数，防止因配置错误/死链等引起死循环重试卡死队列
                retry_count = job.get("retry_count", 0) + 1
                if retry_count >= 5:
                    print(f"[发送队列严重错误] 转发推文 {tweet_id} 连续失败已达 5 次。放弃重试，将其标记为已处理！")
                    try:
                        remove_pending_tweet(tweet_id)
                    except Exception as dbe:
                        print(f"[发送队列警告] 移除持久化待发记录失败: {dbe}")
                    
                    # 标记为已处理，防止被下一轮扫描重新抓取入队
                    mark_tweet_forwarded(tweet_id)
                    
                    QUEUED_TWEET_IDS.discard(tweet_id)
                    tg_send_queue.task_done()
                    continue
                    
                job["retry_count"] = retry_count
                # 同步更新回 SQLite 持久化队列中，保证重启后重试计数不被重置
                import json
                try:
                    add_pending_tweet(tweet_id, json.dumps(job))
                except Exception as dbe:
                    print(f"[发送队列警告] 同步待发任务重试计数至 SQLite 失败: {dbe}")
                    
                print(f"[发送队列警告] 转发推文 {tweet_id} 失败 (已重试 {retry_count}/5 次)，将在 2 秒后放入队列尾部重新重试。")
                await asyncio.sleep(2)
                await tg_send_queue.put(job)
                tg_send_queue.task_done()
                continue
                
            # 消费成功清理
            QUEUED_TWEET_IDS.discard(tweet_id)
            tg_send_queue.task_done()
            
            # 缓冲避让 TG 频控
            await asyncio.sleep(2.5)
            
    except asyncio.CancelledError:
        print("[发送队列] 消费者队列服务已安全退出。")
    except Exception as e:
        print(f"[发送队列严重错误] 消费者发生崩溃，将在 5 秒后尝试重启: {e}")
        await asyncio.sleep(5)
        global _sender_task
        if SCHEDULER_STATE["is_running"]:
            _sender_task = asyncio.create_task(tg_sender_worker())

async def cleanup_scheduler_loop():
    """
    每日凌晨 3:00 自动执行一次历史脏数据和去重记录清理 (保留最近 3 天数据)
    """
    try:
        while True:
            now = datetime.now()
            # 计算下一次清扫时间 (如果已过 3 点则设为明天的 3 点，否则为今天的 3 点)
            target_time = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if now >= target_time:
                target_time += timedelta(days=1)
                
            sleep_seconds = (target_time - now).total_seconds()
            print(f"[数据清理] 每日清理自动协程已就绪，将在 {sleep_seconds:.1f} 秒后运行。")
            await asyncio.sleep(sleep_seconds)
            
            print("[数据清理] 开始执行每日凌晨 3:00 数据库历史数据自动清理...")
            try:
                # 保留 3 天
                deleted_fw, deleted_pd = cleanup_old_data(days_threshold=3)
                print(f"[数据清理] 清理完成！已清除 {deleted_fw} 条已发送历史记录，{deleted_pd} 条历史脏队列记录。")
            except Exception as ce:
                print(f"[数据清理错误] 数据库每日自动清理执行失败: {ce}")
                
    except asyncio.CancelledError:
        print("[数据清理] 每日清理自动协程服务已安全取消退出。")
