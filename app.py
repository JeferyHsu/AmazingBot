from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import requests
import time
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# 替換為你的 Line 設定
LINE_CHANNEL_ACCESS_TOKEN = 'pdxQwcQxz8sOVIXDT0mQVU6j2KnZK7Zf13E/wXxn/Wj+blTFU/XGijzBewUrHv79WkcQxPhM+s7v83fGrltXNk+Fdc8ISrQL7wwzawxXuDGqr193XZoVJ2U+4TQF+39XQidtMhLWmGQ7fmUu3GFJGQdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = 'e5d82a887b7ccff529e3037cff4a46d6'
GOOGLE_API_KEY = 'AIzaSyDAANZbEZu5ULFF-IEShWwyfSy51dpMHtU'

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 使用者資料暫存（正式應使用 DB）
user_states = {}
user_data = {}

# --- Distance Matrix 通勤計算 ---
def get_commute_info(origin, destination, arrival_time_str):
    today = time.strftime("%Y-%m-%d")
    arrival_dt = time.strptime(f"{today} {arrival_time_str}", "%Y-%m-%d %H:%M")
    arrival_timestamp = int(time.mktime(arrival_dt))

    url = 'https://maps.googleapis.com/maps/api/distancematrix/json'
    params = {
        'origins': origin,
        'destinations': destination,
        'mode': 'transit',
        'arrival_time': arrival_timestamp,
        'key': GOOGLE_API_KEY,
        'language': 'zh-TW'
    }

    response = requests.get(url, params=params).json()
    try:
        element = response['rows'][0]['elements'][0]
        duration_sec = element['duration']['value']
        duration_text = element['duration']['text']
        best_departure_time = arrival_timestamp - duration_sec
        best_departure_str = time.strftime("%H:%M", time.localtime(best_departure_time))

        return {
            "duration_minutes": duration_sec // 60,
            "duration_text": duration_text,
            "best_departure_time": best_departure_str
        }
    except Exception as e:
        return {"error": str(e)}

# --- 傳送每日提醒 ---
def send_daily_reminder(user_id):
    data = user_data.get(user_id)
    if not data: return
    result = get_commute_info(data['origin'], data['destination'], data['arrival_time'])
    if "error" in result:
        msg = f"🚨 通勤查詢失敗: {result['error']}"
    else:
        msg = f"🚗 今日建議你 {result['best_departure_time']} 出門\n預估通勤時間：{result['duration_text']}"
    line_bot_api.push_message(user_id, TextSendMessage(text=msg))

# --- Line Webhook ---
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    handler.handle(body, signature)
    return 'OK'

# --- 處理訊息 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    state = user_states.get(user_id, 'start')

    if text.lower() in ["設定通勤", "start"]:
        user_states[user_id] = 'awaiting_origin'
        user_data[user_id] = {}
        reply = "請輸入出發地"
    
    elif state == 'awaiting_origin':
        user_data[user_id]['origin'] = text
        user_states[user_id] = 'awaiting_destination'
        reply = "請輸入目的地"
    
    elif state == 'awaiting_destination':
        user_data[user_id]['destination'] = text
        user_states[user_id] = 'awaiting_arrival'
        reply = "請輸入希望抵達時間（例如 08:30）"
    
    elif state == 'awaiting_arrival':
        user_data[user_id]['arrival_time'] = text
        user_states[user_id] = 'awaiting_remind'
        reply = "請輸入每日提醒時間（例如 07:00）"
    
    elif state == 'awaiting_remind':
        # 驗證時間格式
        try:
            hour, minute = map(int, text.split(":"))
            assert 0 <= hour < 24 and 0 <= minute < 60
        except Exception:
            reply = "提醒時間格式錯誤，請用 HH:MM（如 07:00）"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        user_data[user_id]['remind_time'] = text

        # 立即計算通勤建議
        commute_result = get_commute_info(
            user_data[user_id]['origin'],
            user_data[user_id]['destination'],
            user_data[user_id]['arrival_time']
        )

        if "error" in commute_result:
            reply_msg = f"❌ 設定失敗：{commute_result['error']}\n請重新輸入「設定通勤」開始設定"
            user_states[user_id] = 'start'
            user_data.pop(user_id, None)
        else:
            reply_msg = f"""✅ 通勤提醒設定完成！以下是您的設定：
━━━━━━━━━━━━━━
📍 出發地：{user_data[user_id]['origin']}
🏁 目的地：{user_data[user_id]['destination']}
⏰ 希望抵達時間：{user_data[user_id]['arrival_time']}
🔔 每日提醒時間：{text}
━━━━━━━━━━━━━━
📣 根據目前路況：
🚪 建議出發時間：{commute_result['best_departure_time']}
⏱ 預估通勤時間：{commute_result['duration_text']}
"""
            user_states[user_id] = 'done'
            # 建立排程任務
            job_id = f"reminder_{user_id}"
            scheduler.add_job(
                send_daily_reminder, 
                'cron', 
                hour=hour, 
                minute=minute, 
                args=[user_id], 
                id=job_id, 
                replace_existing=True
            )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
        return

    else:
        reply = "請輸入「設定通勤」來開始設定"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# --- 啟動服務 ---
if __name__ == "__main__":
    app.run(debug=True)
