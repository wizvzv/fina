"""
Flask Web 管理后台 - 喝水提醒系统
"""
import sys
import os
import threading
import traceback
from io import BytesIO

# 确保能导入 ilink_bot
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, render_template, jsonify, request, send_from_directory, send_file, Response
import ilink_bot
import qrcode

BASE_DIR = os.path.dirname(__file__)
app = Flask(__name__)


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


@app.route("/api/qrcode")
def api_qrcode():
    """实时生成并返回二维码图片"""
    hash_val = ilink_bot.bot_state.get("qrcode_url", "")
    if not hash_val:
        return jsonify({"ok": False, "message": "二维码尚未生成，请先点击启动"}), 404
    qr_link = f"https://liteapp.weixin.qq.com/q/7GiQu1?qrcode={hash_val}&bot_type=3"
    try:
        qr = qrcode.QRCode(border=2, box_size=10)
        qr.add_data(qr_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(buf.getvalue(), mimetype="image/png")
    except Exception as e:
        print(f"[qrcode] 生成失败: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify(ilink_bot.get_status())

@app.route("/api/logs")
def api_logs():
    """获取运行日志"""
    return jsonify({"logs": ilink_bot.get_logs()})


@app.route("/api/start", methods=["POST"])
def api_start():
    """启动bot（直接调用，start内部自己开线程）"""
    try:
        # 重置状态
        ilink_bot.bot_state["status"] = "starting"
        ilink_bot.bot_state["error_msg"] = ""
        ilink_bot.start()
        return jsonify({"ok": True})
    except Exception as e:
        err = traceback.format_exc()
        print(f"[start error] {err}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/debug", methods=["POST"])
def api_debug():
    """调试接口 - 直接测试bot启动"""
    import traceback, io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            import ilink_bot as ib
            print(f"import ok, bot_state.status={ib.bot_state['status']}")
            ib.start()
            import time
            time.sleep(3)
            print(f"after 3s: status={ib.bot_state['status']}")
            print(f"qrcode_url={ib.bot_state['qrcode_url'][:20]}")
            print(f"error_msg={ib.bot_state['error_msg']}")
        except Exception as e:
            traceback.print_exc()
    return jsonify({"log": buf.getvalue()})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    ilink_bot.stop()
    return jsonify({"ok": True})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "请求格式错误"}), 400
        # 输入校验
        if "reminder_times" in data:
            times = data["reminder_times"]
            if not isinstance(times, list):
                return jsonify({"ok": False, "error": "reminder_times 必须是列表"}), 400
            for t in times:
                if not isinstance(t, str) or len(t) != 5 or t[2] != ":":
                    return jsonify({"ok": False, "error": f"无效的时间格式: {t}"}), 400
        if "reminder_messages" in data:
            msgs = data["reminder_messages"]
            if not isinstance(msgs, list):
                return jsonify({"ok": False, "error": "reminder_messages 必须是列表"}), 400
            for m in msgs:
                if not isinstance(m, str) or not m.strip():
                    return jsonify({"ok": False, "error": "提醒消息不能为空"}), 400
        if "enabled" in data and not isinstance(data["enabled"], bool):
            return jsonify({"ok": False, "error": "enabled 必须是布尔值"}), 400
        ilink_bot.update_config(data)
        return jsonify({"ok": True})
    return jsonify(ilink_bot.config)


@app.route("/api/test", methods=["POST"])
def api_test():
    ok = ilink_bot.test_send()
    return jsonify({"ok": ok, "message": "发送成功" if ok else "发送失败，请先连接微信"})


@app.route("/api/drink", methods=["POST"])
def api_drink():
    """手动记录喝水"""
    cups = ilink_bot.record_drink()
    return jsonify({"ok": True, "cups": cups})


@app.route("/api/reset_stats", methods=["POST"])
def api_reset_stats():
    """重置统计"""
    ilink_bot.stats.update({
        "streak_days": 0,
        "today_date": "",
        "today_cups": 0,
        "daily_goal": 8,
        "cup_ml": 250,
        "last_reminder_date": "",
    })
    ilink_bot.save_stats()
    return jsonify({"ok": True})


@app.route("/api/goal", methods=["POST"])
def api_goal():
    """设置每日目标杯数"""
    data = request.json
    goal = int(data.get("goal", 8))
    ilink_bot.stats["daily_goal"] = goal
    ilink_bot.save_stats()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"  [OK] 管理后台已启动: http://localhost:{port}")
    print(f"  [OK] 用浏览器打开上面的地址进行管理")
    app.run(host="0.0.0.0", port=port, debug=False)
