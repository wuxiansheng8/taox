# services/translator.py
import httpx
import json
import asyncio
from typing import Dict, Any, Tuple

# 全局翻译通道状态管理
# is_ok: True (正常) / False (发生故障，已降级)
# last_probe: 上次探测主通道的时间
TRANSLATOR_STATE = {
    "is_primary_ok": True,
    "last_probe_time": None,
    "active_channel": "primary" # "primary" 或 "backup"
}

async def translate_google(text: str) -> str:
    """
    谷歌免 API Key 翻译
    """
    if not text.strip():
        return text
    
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": "zh-CN",
        "dt": "t",
        "q": text
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            raise Exception(f"Google translate HTTP error: {resp.status_code}")
        
        data = resp.json()
        translated_text = "".join([part[0] for part in data[0] if part[0]])
        return translated_text

async def translate_openai(text: str, api_key: str, base_url: str, model: str, stream: bool = False) -> str:
    """
    OpenAI 格式的 AI 翻译，兼容流式与非流式输入
    """
    if not text.strip():
        return text
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system", 
                "content": "你是一位专业的推特内容翻译官。请把下面的推文内容翻译为简明流畅的中文，保持排版、话题标签（#）和提及（@）不变。只输出翻译后的文本，不要带有任何解释或前言。"
            },
            {"role": "user", "content": text}
        ],
        "temperature": 0.3,
        "stream": stream
    }
    
    url = f"{base_url.rstrip('/')}/chat/completions"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        if not stream:
            # 非流式处理
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise Exception(f"OpenAI API error ({resp.status_code}): {resp.text}")
            result = resp.json()
            return result["choices"][0]["message"]["content"].strip()
        else:
            # 流式处理 (Stream=True)
            full_content = []
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    err_text = await response.aread()
                    raise Exception(f"OpenAI Stream API error ({response.status_code}): {err_text.decode('utf-8')}")
                
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            delta = data_json["choices"][0]["delta"]
                            if "content" in delta:
                                full_content.append(delta["content"])
                        except Exception:
                            continue
            return "".join(full_content).strip()

async def test_translation(provider: str, config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    测试单个翻译通道的连通性
    """
    test_text = "Hello world! Testing Twitter-to-TG translation service."
    try:
        if provider == "google":
            res = await translate_google(test_text)
            return True, f"测试成功：{res}"
        elif provider == "openai":
            api_key = config.get("api_key")
            base_url = config.get("base_url")
            model = config.get("model")
            if not api_key:
                return False, "未配置 API Key"
            # 兼容性测试，先测非流式
            res = await translate_openai(test_text, api_key, base_url, model, stream=False)
            return True, f"测试成功：{res}"
        else:
            return False, f"未知的翻译提供商: {provider}"
    except Exception as e:
        return False, str(e)

async def check_openai_balance(api_key: str, base_url: str) -> str:
    """
    查询 OpenAI 格式中转平台的余额。
    由于各种中转平台接口不尽相同，采取：
    1. 尝试官方 subscription/usage 接口；
    2. 如果失败则通过测试翻译确认 Key 是否有效。
    """
    if not api_key:
        return "未配置 API Key"
        
    headers = {"Authorization": f"Bearer {api_key}"}
    base_url_clean = base_url.rstrip('/')
    
    # 尝试官方查询余额接口
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. 查询总额度
            sub_url = f"{base_url_clean}/dashboard/billing/subscription"
            sub_resp = await client.get(sub_url, headers=headers)
            
            if sub_resp.status_code == 200:
                sub_data = sub_resp.json()
                total = sub_data.get("hard_limit_usd", 0)
                
                # 2. 查询已使用额度 (过去90天)
                import datetime
                end_date = datetime.date.today().isoformat()
                start_date = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
                
                usage_url = f"{base_url_clean}/dashboard/billing/usage?start_date={start_date}&end_date={end_date}"
                usage_resp = await client.get(usage_url, headers=headers)
                
                if usage_resp.status_code == 200:
                    usage_data = usage_resp.json()
                    used = usage_data.get("total_usage", 0) / 100.0 # 官方接口返回的是美分
                    remaining = total - used
                    return f"总额度: ${total:.2f} | 已使用: ${used:.2f} | 剩余额度: ${remaining:.2f}"
    except Exception:
        pass
        
    # 保底方案：发一次极简对话请求测试额度连通性
    try:
        test_url = f"{base_url_clean}/chat/completions"
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "1"}],
            "max_tokens": 1
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(test_url, json=payload, headers=headers)
            if resp.status_code == 200:
                return "API Key 有效，可用额度充足（该中转平台不支持查询具体余额数值）"
            else:
                return f"接口报错：{resp.status_code} - {resp.text}"
    except Exception as e:
        return f"连接额度查询接口失败: {str(e)}"

# 核心翻译调度函数 (包含主备自动降级切换)
async def translate_text(text: str, settings: Dict[str, Any]) -> str:
    """
    根据配置翻译推文，包含自动主备通道切换与异常捕获。
    """
    if not text.strip() or settings.get("translate_enabled") != "1":
        return text
        
    primary_prov = settings.get("translate_provider_primary", "google")
    backup_prov = settings.get("translate_provider_backup", "google")
    
    # 构造主用与备用配置参数
    config_primary = {
        "api_key": settings.get("openai_primary_api_key"),
        "base_url": settings.get("openai_primary_base_url"),
        "model": settings.get("openai_primary_model"),
        "stream": True # 默认流式读取
    }
    
    config_backup = {
        "api_key": settings.get("openai_backup_api_key"),
        "base_url": settings.get("openai_backup_base_url"),
        "model": settings.get("openai_backup_model"),
        "stream": True
    }
    
    # 如果主通道标记正常，优先用主通道
    if TRANSLATOR_STATE["is_primary_ok"]:
        try:
            if primary_prov == "google":
                return await translate_google(text)
            else:
                return await translate_openai(text, config_primary["api_key"], config_primary["base_url"], config_primary["model"], config_primary["stream"])
        except Exception as e:
            # 主通道报错，触发降级
            TRANSLATOR_STATE["is_primary_ok"] = False
            TRANSLATOR_STATE["active_channel"] = "backup"
            print(f"[翻译器警告] 主通道 {primary_prov} 异常，原因: {e}。已自动降级至备用通道 {backup_prov}。")
            
    # 主通道不正常，直接用备用通道
    try:
        if backup_prov == "google":
            return await translate_google(text)
        else:
            return await translate_openai(text, config_backup["api_key"], config_backup["base_url"], config_backup["model"], config_backup["stream"])
    except Exception as e:
        print(f"[翻译器错误] 备用通道也崩溃了: {e}。返回原推文。")
        return text
