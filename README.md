# 💧 微信 ClawBot 喝水提醒助手

基于 **iLink API** 直连微信 ClawBot，**无需搭建 OpenClaw 服务端**，扫码即用，定时推送喝水提醒。

## 使用方式

### 方式一：本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动
python bot.py
```

### 方式二：部署到 Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/PUT_YOUR_TEMPLATE_HERE)

或手动部署：
1. Fork 此仓库
2. 在 Railway 上选择 `Deploy from GitHub repo`
3. 部署后打开日志，扫描二维码绑定微信

## 首次使用流程

```
1. 运行脚本 → 终端显示二维码
2. 手机微信扫码 → 绑定 ClawBot
3. 打开微信「ClawBot」聊天窗口 → 发一条任意消息（如"你好"）
4. 脚本确认连接后 → 到点自动推送喝水提醒 💧
```

## 默认提醒时间

- ⏰ 09:00 / 11:00 / 14:00 / 16:00 / 17:00

可在 `bot.py` 顶部修改 `REMINDER_TIMES` 和 `REMINDER_MESSAGES` 自定义。

## 自动重连

iLink 连接每 24 小时过期，脚本会自动生成新二维码续期。
