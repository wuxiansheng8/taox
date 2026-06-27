# services/telegram_bot.py
import httpx
import json
import os
import asyncio
import logging
from typing import List, Dict, Any

logger = logging.getLogger("taox")

def print(*args, **kwargs):
    """
    重写模块级 print，智能投递到日志系统并区分级别以供 Web 端 SSE 实时捕获。
    """
    msg = " ".join(str(arg) for arg in args)
    if "错误" in msg or "error" in msg.lower():
        logger.error(msg)
    elif "警告" in msg or "warn" in msg.lower():
        logger.warning(msg)
    else:
        logger.info(msg)

TEXT_LIMIT = 4000
CAPTION_LIMIT = 1000

async def _execute_tg_api_call(url: str, data: Dict[str, Any] = None, files: Dict[str, Any] = None, json_payload: Dict[str, Any] = None, max_retries: int = 3) -> bool:
    """
    底层统一执行 Telegram API 发送请求，包含：
    1. 429 频率限制拦截，自动获取 retry_after 并挂起重试；
    2. 网络超时/连接异常的指数退避重试；
    3. 400 Bad Request 等业务错误的详细原因提取与日志记录。
    """
    attempt = 0
    backoff = 2.0 # 初始指数级退避秒数
    rate_limit_attempts = 0 # 记录 429 限流重试次数
    
    while attempt < max_retries:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if json_payload is not None:
                    resp = await client.post(url, json=json_payload)
                elif files is not None:
                    resp = await client.post(url, data=data, files=files)
                else:
                    resp = await client.post(url, data=data)
                
                # 1. 200 OK 发送成功
                if resp.status_code == 200:
                    return True
                
                # 2. 429 Too Many Requests 频率限制，解析 retry_after
                if resp.status_code == 429:
                    rate_limit_attempts += 1
                    if rate_limit_attempts > 3:
                        print("[Telegram错误] 连续触发 429 限流超过 3 次，放弃本轮重试。")
                        return False
                        
                    try:
                        resp_data = resp.json()
                        retry_after = resp_data.get("parameters", {}).get("retry_after", 5)
                    except Exception:
                        retry_after = 5
                    print(f"[Telegram警告] 触发 TG API 限流 (429) (第 {rate_limit_attempts}/3 次尝试)。将在 {retry_after} 秒后自动重试...")
                    await asyncio.sleep(retry_after)
                    continue
                
                # 3. 400 或其他常规业务错误 (说明参数或 Token 不对，重试无用)
                try:
                    resp_data = resp.json()
                    err_desc = resp_data.get("description", "无说明")
                except Exception:
                    err_desc = resp.text
                
                print(f"[Telegram错误] 接口调用返回失败 (状态码: {resp.status_code})。错误详情: {err_desc}")
                return False
                
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as ne:
            attempt += 1
            print(f"[Telegram警告] 连接 TG 接口超时或网络异常 (第 {attempt}/{max_retries} 次重试): {ne}")
            if attempt < max_retries:
                await asyncio.sleep(backoff)
                backoff *= 2.0
            else:
                print("[Telegram错误] 网络连续超时，消息发送失败。")
                return False
        except Exception as e:
            print(f"[Telegram错误] 发送消息出现未知异常: {e}")
            return False
            
    return False

def split_text(text: str, limit: int = TEXT_LIMIT) -> List[str]:
    """
    按换行或空格安全拆分文本，避免在中间切断 HTML 实体，用于极致保底。
    """
    if len(text) <= limit:
        return [text]
        
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
            
        # 1. 尝试寻找切分点前的最后一个换行
        split_idx = text.rfind('\n', 0, limit)
        if split_idx == -1:
            # 2. 如果没换行，尝试按空格切
            split_idx = text.rfind(' ', 0, limit)
            
        # 如果找不到切分点，或者切分点太靠前，直接在 limit 处强行切割，但必须进行实体避让
        if split_idx == -1 or split_idx < limit * 0.7:
            split_idx = limit
            # 回退以避免截断 HTML 实体 (&xxx;)
            amp_idx = text.rfind('&', split_idx - 10, split_idx)
            if amp_idx != -1:
                semicolon_idx = text.find(';', amp_idx, split_idx)
                if semicolon_idx == -1:
                    # 说明 '&' 符号对应的实体跨越了截断点，将分割位置移到 '&' 之前
                    split_idx = amp_idx
                    
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip()
        
    return chunks

async def send_text_chunks(token: str, chat_id: str, text: str, reply_markup: Dict[str, Any] = None) -> bool:
    """
    发送可能包含分片的纯文本消息，支持在最后一片挂载 inline_keyboard
    """
    chunks = split_text(text, TEXT_LIMIT)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        # 仅在最后一个文本分片上挂载“查看原文”按钮
        if i == len(chunks) - 1 and reply_markup:
            payload["reply_markup"] = reply_markup
            
        success = await _execute_tg_api_call(url, json_payload=payload)
        if not success:
            return False
    return True

async def send_media(token: str, chat_id: str, caption: str, media_urls: List[Dict[str, str]]) -> bool:
    """
    发送多媒体资源（支持单图/单视频、多图/多视频组），支持下载并打包为 multipart/form-data
    1. 数量 = 1：调用 sendPhoto 或 sendVideo (兼容性最佳，API 规范)
    2. 数量 > 1：调用 sendMediaGroup
    """
    if not media_urls:
        return False
        
    try:
        # 1. 单个媒体发送 (sendPhoto 或 sendVideo)
        if len(media_urls) == 1:
            media_item = media_urls[0]
            url = media_item["url"]
            m_type = media_item["type"] # 'photo' 或 'video'
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        return False
                    
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            size_mb = int(content_length) / (1024 * 1024)
                        except (ValueError, TypeError):
                            size_mb = 0
                        if size_mb > 50.0:
                            print(f"[Telegram警告] 单个媒体文件体积超限 ({size_mb:.1f}MB > 50MB)，放弃发送该媒体。")
                            return False
                    
                    file_content = await response.aread()
                
                file_name = "media.jpg" if m_type == "photo" else "media.mp4"
                file_field = "photo" if m_type == "photo" else "video"
                files = {file_field: (file_name, file_content)}
                
                api_method = "sendPhoto" if m_type == "photo" else "sendVideo"
                api_url = f"https://api.telegram.org/bot{token}/{api_method}"
                
                data = {"chat_id": chat_id}
                if caption:
                    data["caption"] = caption
                    data["parse_mode"] = "HTML"
                    
                return await _execute_tg_api_call(api_url, data=data, files=files)

        # 2. 多个媒体发送 (sendMediaGroup)
        files = {}
        media_group = []
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i, media_item in enumerate(media_urls):
                url = media_item["url"]
                m_type = media_item["type"] # 'photo' 或 'video'

                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        continue
                    
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            size_mb = int(content_length) / (1024 * 1024)
                        except (ValueError, TypeError):
                            size_mb = 0
                        if size_mb > 50.0:
                            print(f"[Telegram警告] 媒体包成员 {i} 文件大小超限 ({size_mb:.1f}MB > 50MB)，跳过下载。")
                            continue
                    
                    file_content = await response.aread()

                file_name = f"media_{i}.jpg" if m_type == "photo" else f"media_{i}.mp4"
                file_key = f"file_{i}"
                files[file_key] = (file_name, file_content)

                media_group_item = {
                    "type": m_type,
                    "media": f"attach://{file_key}"
                }
                
                # 仅在第一个媒体中添加 Caption (如果提供)
                if i == 0 and caption:
                    media_group_item["caption"] = caption
                    media_group_item["parse_mode"] = "HTML"

                media_group.append(media_group_item)

        if not media_group:
            return False

        # 3. 边界二次检查：如果下载或超过 50MB 过滤后，媒体包内仅剩 1 个文件，自动改走单媒体投递通道
        if len(media_group) == 1:
            single_item = media_group[0]
            m_type = single_item["type"] # 'photo' 或 'video'
            
            # 从缓存字典中取出唯一一个文件键值对
            file_key = list(files.keys())[0]
            file_name, file_bytes = files[file_key]
            
            file_field = "photo" if m_type == "photo" else "video"
            single_files = {file_field: (file_name, file_bytes)}
            
            api_method = "sendPhoto" if m_type == "photo" else "sendVideo"
            api_url = f"https://api.telegram.org/bot{token}/{api_method}"
            
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "HTML"
                
            print(f"[Telegram通知] 过滤后媒体包仅剩 1 个文件，系统已自动转换为【{api_method}】单通道安全发送。")
            return await _execute_tg_api_call(api_url, data=data, files=single_files)

        # 4. 正常多媒体发送
        url = f"https://api.telegram.org/bot{token}/sendMediaGroup"
        data = {
            "chat_id": chat_id,
            "media": json.dumps(media_group)
        }
        return await _execute_tg_api_call(url, data=data, files=files)

    except Exception as e:
        print(f"[Telegram发送多媒体错误] send_media 异常: {e}")
        return False

async def test_telegram_connection(token: str, chat_id: str) -> bool:
    """
    测试 Telegram 机器人的连通性
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "✅ Twitter-to-TG 转发器测试：机器人在 Telegram 连接成功！",
        "parse_mode": "HTML"
    }
    return await _execute_tg_api_call(url, json_payload=payload, max_retries=1)

async def send_tweet_to_telegram(token: str, chat_id: str, text: str, media_urls: List[Dict[str, str]] = None, tweet_url: str = None) -> bool:
    """
    向 Telegram 发送消息，完美支持文字、单图、多图（Media Group）以及视频。
    并且支持在消息底部挂载原生态的“查看原文 ↗”内联按钮。
    """
    if not token or not chat_id:
        return False

    # 1. 构造 Telegram 行内键盘按钮 (用于无媒体及独立发正文消息)
    reply_markup = None
    if tweet_url:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "查看原文", "url": tweet_url}
                ]
            ]
        }

    # 2. 无媒体情况
    if not media_urls:
        return await send_text_chunks(token, chat_id, text, reply_markup)

    # 3. 预先拼接带链接的最终 Caption 文本，以其最终长度决定发送策略
    caption_text = text
    if tweet_url:
        caption_text = f"{text}\n\n🔗 <a href='{tweet_url}'>查看原文</a>"

    # 如果最终的 Caption 长度在 1000 字符以内，走完美的图文合一发送
    if len(caption_text) <= CAPTION_LIMIT:
        success = await send_media(token, chat_id, caption_text, media_urls)
        if success:
            return True
        # 降级：多媒体包发送失败时，降级发纯文本，确保字先送达
        print("[Telegram警告] 媒体包发送失败，正在降级为纯文本发送...")
        return await send_text_chunks(token, chat_id, text + "\n\n⚠️ (多媒体下载/发送失败，已降级为纯文本)", reply_markup)

    # 4. 如果最终的 Caption 长度超过了 1000 字符，自动分拆：先发完整正文（挂载按钮），再发媒体组
    print(f"[Telegram通知] 发现长推文或链接拼接后超限 ({len(caption_text)} 字符) 并带有媒体，已自动采用【先发正文，再发媒体】的拆分投递...")
    # 发送正文时挂载“查看原文”按钮
    text_ok = await send_text_chunks(token, chat_id, text, reply_markup)
    if not text_ok:
        return False

    # 正文发送成功，紧随其后发送不带 caption 的媒体组
    media_ok = await send_media(token, chat_id, "", media_urls)
    if not media_ok:
        # 如果文本发出去了但媒体失败了，我们依然返回 True，以防重试机制导致文本被不断重复发送
        print("[Telegram警告] 正文发送成功，但后续媒体发送失败。")
        return True
        
    return True
