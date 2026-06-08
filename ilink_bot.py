"""
iLink Bot 模块 - 负责微信登录、消息收发、定时提醒
以后台线程方式运行，通过队列与 Flask 主进程通信
"""

import requests
import json
import os
import base64
import random
import time
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
import threading
import qrcode
from io import BytesIO
import traceback
import tempfile

# 时区（必须在日志函数之前定义）
TZ = timezone(timedelta(hours=8))

# 运行日志（供 Web UI 显示）
bot_logs = []
MAX_LOGS = 100

def add_log(msg):
    """添加一条运行日志"""
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    bot_logs.append(entry)
    if len(bot_logs) > MAX_LOGS:
        bot_logs.pop(0)
    print(f"[ilink_bot] {msg}", flush=True)

def get_logs():
    return list(bot_logs)

# 全局状态
bot_state = {
    "status": "stopped",        # stopped | scanning | logged_in | error
    "bot_token": "",
    "bot_base_url": "",
    "user_from_id": "",
    "user_context_token": "",
    "login_time": 0,
    "error_msg": "",
    "qrcode_url": "",
}

# 线程锁保护全局可变状态
_state_lock = threading.Lock()

# 全局停止事件（模块级，bot_worker 和 stop() 都能访问）
stop_event = None

config = {
    "reminder_times": ["09:00", "11:00", "14:00", "16:00", "17:00"],
    "reminder_messages": [
        "[喝水提醒] 喝水时间到了！起来走走，喝杯水~",
        "[喝水提醒] 该喝水啦！每天8杯水，健康一辈子~",
        "[喝水提醒] 补充水分，活力满满！",
        "[喝水提醒] 喝水时间到，别让身体缺水哦~",
    ],
    "enabled": True,
}

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "bot_config.json")
STATS_FILE = os.path.join(os.path.dirname(__file__), "stats.json")
BASE_URL = "https://ilinkai.weixin.qq.com"

# 喝水统计
stats = {
    "streak_days": 0,
    "today_date": "",
    "today_cups": 0,
    "daily_goal": 8,
    "cup_ml": 250,
    "last_reminder_date": "",
}


def load_stats():
    global stats
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            stats.update(json.load(f))


def save_stats():
    """原子写入 stats.json"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATS_FILE) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATS_FILE)
    except:
        os.unlink(tmp)
        raise


def check_new_day():
    """检查是否新的一天，更新连续天数"""
    global stats
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if stats["today_date"] != today:
        # 计算连续
        if stats["today_date"]:
            yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
            if stats["today_date"] == yesterday:
                stats["streak_days"] += 1
            else:
                stats["streak_days"] = 1
        else:
            stats["streak_days"] = 1
        stats["today_date"] = today
        stats["today_cups"] = 0
        save_stats()


def record_drink():
    """记录一次喝水"""
    global stats
    check_new_day()
    stats["today_cups"] += 1
    stats["last_reminder_date"] = stats["today_date"]
    save_stats()
    return stats["today_cups"]


def build_fun_message():
    """生成趣味喝水提醒消息"""
    check_new_day()
    streak = stats["streak_days"]
    cups = stats["today_cups"]
    goal = stats["daily_goal"]
    ml = cups * stats["cup_ml"]
    pct = min(cups * 100 // goal, 100)

    # 进度条
    bar_len = 10
    filled = pct * bar_len // 100
    bar = "▓" * filled + "░" * (bar_len - filled)

    # 从用户配置中随机选一条提醒文案（fallback 默认文案）
    cheers = config.get("reminder_messages") or ["继续加油！", "你真棒！", "健康每一天~"]
    headline = random.choice(cheers)

    msg = f"""💧 喝水提醒 💧

🌟 {headline}

---
📅 {stats['today_date']}
🥤 今日喝水：{cups}/{goal} 杯 ({ml}ml)
📊 进度：[{bar}] {pct}%
🔥 连续坚持：{streak} 天
"""
    return msg


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            config.update(saved)


def save_config():
    """原子写入 bot_config.json"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CONFIG_FILE) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except:
        os.unlink(tmp)
        raise


def _derive_uin(token):
    """基于 token 生成固定的 UIN，避免每次请求都变化"""
    if not token:
        return "0"
    # 取 token 前 8 个字符的哈希作为固定 UIN
    import hashlib
    h = hashlib.md5(token[:8].encode()).hexdigest()
    return str(int(h, 16) % 0xFFFFFFFF)


def make_headers(token=None):
    uin = _derive_uin(token)
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(uin.encode()).decode(),
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": str((2 << 16) | (4 << 8) | 3),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def base_info():
    return {"channel_version": "2.4.3", "bot_agent": "water-reminder-web/1.0.0"}


def api_post(path, body, token=None, base_url=None):
    url = f"{base_url or BASE_URL}/{path}"
    try:
        r = requests.post(url, json=body, headers=make_headers(token), timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        return {"_error": f"HTTP {r.status_code}: {e}", "_status_code": r.status_code, "_response": r.text[:500]}
    except requests.exceptions.RequestException as e:
        return {"_error": f"网络错误: {e}"}
    except json.JSONDecodeError as e:
        return {"_error": f"JSON解析错误: {e}", "_response": r.text[:500]}
    except Exception as e:
        return {"_error": f"未知错误: {e}"}


def api_get(path, token=None, base_url=None):
    url = f"{base_url or BASE_URL}/{path}"
    try:
        r = requests.get(url, headers=make_headers(token), timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        return {"_error": f"HTTP {r.status_code}: {e}", "_status_code": r.status_code, "_response": r.text[:500]}
    except requests.exceptions.RequestException as e:
        return {"_error": f"网络错误: {e}"}
    except json.JSONDecodeError as e:
        return {"_error": f"JSON解析错误: {e}", "_response": r.text[:500]}
    except Exception as e:
        return {"_error": f"未知错误: {e}"}


def send_message(text):
    if not bot_state["user_from_id"] or not bot_state["user_context_token"]:
        add_log("发送失败：未连接用户")
        return False
    client_id = f"water-{random.randint(0, 0xFFFFFFFF):08x}"
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": bot_state["user_from_id"],
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "context_token": bot_state["user_context_token"],
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        },
        "base_info": base_info(),
    }
    r = api_post("ilink/bot/sendmessage", body, bot_state["bot_token"],
                 bot_state["bot_base_url"] or None)
    if r.get("_error"):
        add_log(f"发送失败: {r['_error']}")
        return False
    add_log(f"已发送消息")
    return True


def bot_worker():
    """Bot 后台工作线程（使用外层循环处理重连，避免递归）"""
    global bot_state, stop_event

    # 初始化（失败直接返回）
    try:
        print("[ilink_bot] bot_worker 线程启动", flush=True)
        add_log("Bot 线程启动")
        load_config()
        load_stats()
    except Exception as e:
        bot_state["status"] = "error"
        bot_state["error_msg"] = f"初始化失败: {e}"
        print(f"[ilink_bot] 初始化异常: {e}", flush=True)
        return

    stop_event = threading.Event()

    def login_flow():
        """登录流程，返回 True 表示登录成功"""
        bot_state["status"] = "scanning"
        while not stop_event.is_set():
            data = api_post("ilink/bot/get_bot_qrcode?bot_type=3", {})
            qrcode_val = data.get("qrcode", "")
            bot_state["qrcode_url"] = qrcode_val

            # 生成二维码图片
            qr_link = f"https://liteapp.weixin.qq.com/q/7GiQu1?qrcode={qrcode_val}&bot_type=3"
            qr_path = os.path.join(os.path.dirname(__file__), "qrcode.png")
            try:
                qr = qrcode.QRCode(border=2, box_size=10)
                qr.add_data(qr_link)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buf = BytesIO()
                img.save(buf, format="PNG")
                with open(qr_path, "wb") as f:
                    f.write(buf.getvalue())
                add_log(f"二维码已生成: {qr_path}")
            except Exception as e:
                add_log(f"二维码生成失败: {e}")

            deadline = time.time() + 300
            base = BASE_URL

            while time.time() < deadline and not stop_event.is_set():
                endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode_val, safe='')}"
                status = api_get(endpoint, None, base)
                state = status.get("status", "")

                if status.get("bot_token"):
                    # DEBUG: 打印完整返回，看有没有 from_user_id / context_token
                    print(f"[ilink_bot] 登录响应完整数据: {json.dumps(status, ensure_ascii=False)}", flush=True)
                    bot_state["bot_token"] = status["bot_token"]
                    bot_state["bot_base_url"] = status.get("baseurl") or status.get("base_url") or ""
                    bot_state["login_time"] = time.time()
                    print(f"[ilink_bot] 登录响应 ilink_user_id={status.get('ilink_user_id','')}", flush=True)
                    ctx = status.get("context_token") or status.get("ilink_context_token") or ""
                    if ctx:
                        bot_state["user_context_token"] = ctx
                        print(f"[ilink_bot] 从登录响应获取 context_token", flush=True)
                    else:
                        # context_token 缺失，等待用户发消息时补全
                        print("[ilink_bot] 登录响应无 context_token，等待用户消息时补全", flush=True)
                    return True

                if status.get("bind_redirect"):
                    bot_state["error_msg"] = "已有连接存在，请先断开其他设备"
                    return False

                if status.get("redirect_host"):
                    base = f"https://{status['redirect_host']}"
                    continue

                if state in ("need_verifycode", "verify_code_blocked") or status.get("need_verifycode"):
                    if state == "verify_code_blocked":
                        time.sleep(1)
                        continue
                    time.sleep(1)
                    continue

                if state == "expired":
                    break

                time.sleep(1)

        return False

    # 主循环（包在 try 里捕获异常）
    try:
        # ---- 外层循环：处理重连（避免递归）----
        while not stop_event.is_set():
            # 执行登录
            login_ok = False
            try:
                login_ok = login_flow()
            except Exception as e:
                add_log(f"登录异常: {e}")

            if not login_ok:
                if bot_state["status"] != "error":
                    bot_state["status"] = "error"
                    bot_state["error_msg"] = "登录失败或超时"
                stop_event.wait(timeout=10)
                continue

            bot_state["status"] = "logged_in"
            print("[ilink_bot] 登录成功，开始消息监听", flush=True)
            add_log("登录成功，开始消息监听")
            print(f"[ilink_bot] 提醒时间: {config['reminder_times']}", flush=True)
            print(f"[ilink_bot] 提醒开关: {config['enabled']}", flush=True)

            # ---- 内层循环：消息监听 + 定时提醒 ----
            buf = ""
            last_check_minute = -1
            retry_delay = 1   # 指数退避初始延迟（秒）
            error_count = 0   # getupdates 连续错误次数

            while not stop_event.is_set():
                # 定时提醒（放在最前面，确保不被任何 continue 跳过）
                now = datetime.now(TZ)
                minute_key = now.strftime("%H:%M")
                if config["enabled"] and minute_key != last_check_minute:
                    times = config.get("reminder_times", [])
                    matched = minute_key in times
                    print(f"[ilink_bot] 定时检查: minute_key={repr(minute_key)} times={times} matched={matched}", flush=True)
                    if matched:
                        try:
                            msg = build_fun_message()
                            ok = send_message(msg)
                            if ok:
                                add_log(f"定时推送 [{minute_key}] 已发送")
                                print(f"[ilink_bot] 定时推送 {minute_key} 成功", flush=True)
                            else:
                                add_log(f"定时推送 [{minute_key}] 发送失败")
                                print(f"[ilink_bot] 定时推送 {minute_key} 失败", flush=True)
                        except Exception as e:
                            err = traceback.format_exc()
                            add_log(f"定时推送 [{minute_key}] 异常: {e}")
                            print(f"[ilink_bot] 定时推送 {minute_key} 异常: {err}", flush=True)
                    last_check_minute = minute_key

                result = api_post(
                    "ilink/bot/getupdates",
                    {"get_updates_buf": buf, "base_info": base_info()},
                    bot_state["bot_token"],
                    bot_state["bot_base_url"] or None,
                )

                # 网络/API 错误 → 指数退避，连续 10 次错误触发重连
                if result.get("_error"):
                    # 长轮询超时是正常行为，不计入错误计数
                    if "Read timed out" in result["_error"]:
                        print("[ilink_bot] getupdates 长轮询超时，继续下一轮", flush=True)
                        time.sleep(0.5)
                        continue
                    error_count += 1
                    print(f"[ilink_bot] getupdates 错误({error_count}/10): {result['_error']}, 等待 {retry_delay}s", flush=True)
                    if error_count >= 10:
                        add_log("getupdates 持续异常，触发重连...")
                        break
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
                    continue
                retry_delay = 1    # 正常时重置退避
                error_count = 0    # 正常时重置错误计数
                buf = result.get("get_updates_buf") or buf

                for msg in result.get("msgs") or []:
                    from_id = msg["from_user_id"]
                    ctx = msg["context_token"]
                    text = msg.get("item_list", [{}])[0].get("text_item", {}).get("text", "")
                    print(f"[ilink_bot] 收到消息: from={from_id} text={text}", flush=True)
                    bot_state["user_context_token"] = ctx

                    if not bot_state["user_from_id"]:
                        bot_state["user_from_id"] = from_id
                        bot_state["user_context_token"] = ctx
                        add_log("收到用户首条消息，发送欢迎消息")
                        send_message("[喝水提醒] 连接成功！到点我会提醒你喝水，记得回复「喝了」来记录哦~")
                    else:
                        t = text.strip()
                        drink_kw = ["喝了", "喝水了", "+1", "ok", "OK", "Ok", "已喝", "done", "记录", "打卡", "喝水打卡"]
                        matched = [kw for kw in drink_kw if kw in t]
                        print(f"[ilink_bot] 关键词匹配: text={t} matched={matched}", flush=True)
                        if matched:
                            cups = record_drink()
                            goal = stats["daily_goal"]
                            reply = f"收到！今日已喝 {cups}/{goal} 杯，继续加油~"
                            add_log(f"用户回复喝水，今日 {cups}/{goal} 杯")
                            print(f"[ilink_bot] 发送回复: {reply}", flush=True)
                            send_message(reply)

                time.sleep(1)

            # 重连准备：彻底清空所有认证信息，避免旧 token 影响新登录
            if not stop_event.is_set():
                with _state_lock:
                    bot_state["bot_token"] = ""
                    bot_state["bot_base_url"] = ""
                    bot_state["user_from_id"] = ""
                    bot_state["user_context_token"] = ""
                    bot_state["qrcode_url"] = ""
                # 清理二维码文件
                qr_path = os.path.join(os.path.dirname(__file__), "qrcode.png")
                try:
                    if os.path.exists(qr_path):
                        os.remove(qr_path)
                except Exception:
                    pass
                add_log("重连准备完成，即将重新登录...")

        add_log("Bot 线程已退出")

    except Exception as e:
        err = traceback.format_exc()
        bot_state["status"] = "error"
        bot_state["error_msg"] = f"运行异常: {e}"
        print(f"[ilink_bot] 异常退出:\n{err}", flush=True)


_bot_thread = None


def start():
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        # 旧线程还在，强制停止再重启
        stop()
    # 彻底重置所有状态，避免旧 token 残留导致新设备扫码异常
    with _state_lock:
        bot_state["status"] = "starting"
        bot_state["error_msg"] = ""
        bot_state["bot_token"] = ""
        bot_state["bot_base_url"] = ""
        bot_state["user_from_id"] = ""
        bot_state["user_context_token"] = ""
        bot_state["qrcode_url"] = ""
    _bot_thread = threading.Thread(target=bot_worker, daemon=True)
    _bot_thread.start()
    print("[ilink_bot] start() 已创建新线程", flush=True)

    # 等 0.5 秒看线程是否还活着
    time.sleep(0.5)
    if not _bot_thread.is_alive():
        with _state_lock:
            bot_state["status"] = "error"
            bot_state["error_msg"] = "Bot线程启动后立即退出，请查看终端日志"
        print("[ilink_bot] 线程已退出！", flush=True)


def stop():
    """停止 Bot 线程"""
    global stop_event, _bot_thread
    with _state_lock:
        bot_state["status"] = "stopped"
        bot_state["bot_token"] = ""
        bot_state["bot_base_url"] = ""
        bot_state["user_from_id"] = ""
        bot_state["user_context_token"] = ""
        bot_state["qrcode_url"] = ""
        bot_state["error_msg"] = ""
    if stop_event:
        stop_event.set()  # 通知线程退出
    # 等待线程结束（最多5秒）
    if _bot_thread and _bot_thread.is_alive():
        _bot_thread.join(timeout=5)
    add_log("Bot 已停止")


def get_status():
    check_new_day()
    with _state_lock:
        return {
            "status": bot_state["status"],
            "user_connected": bool(bot_state["user_from_id"] and bot_state["user_context_token"]),
            "qrcode_url": bot_state["qrcode_url"],
            "error_msg": bot_state["error_msg"],
            "config": config,
            "stats": dict(stats),
        }


def update_config(new_config):
    global config
    if "reminder_times" in new_config:
        config["reminder_times"] = new_config["reminder_times"]
        add_log(f"提醒时间已更新: {config['reminder_times']}")
    if "reminder_messages" in new_config:
        config["reminder_messages"] = new_config["reminder_messages"]
    if "enabled" in new_config:
        config["enabled"] = new_config["enabled"]
        add_log(f"提醒开关: {config['enabled']}")
    save_config()


def test_send():
    """发送测试消息"""
    msg = build_fun_message()
    msg = "[测试推送]\n" + msg
    return send_message(msg)
