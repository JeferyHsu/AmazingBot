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
LINE_CHANNEL_ACCESS_TOKEN = 'YOUR_LINE_CHANNEL_ACCESS_TOKEN'
LINE_CHANNEL_SECRET = 'YOUR_LINE_CHANNEL_SECRET'
GOOGLE_API_KEY = 'YOUR_GOOGLE_MAPS_API_KEY'

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
        user_data[user_id]['remind_time'] = text
        user_states[user_id] = 'done'
        reply = "✅ 通勤提醒已設定完成！將於每日 {} 提醒你。".format(text)

        # 建立排程任務
        hour, minute = map(int, text.split(":"))
        job_id = f"reminder_{user_id}"
        scheduler.add_job(send_daily_reminder, 'cron', hour=hour, minute=minute, args=[user_id], id=job_id, replace_existing=True)

    else:
        reply = "請輸入「設定通勤」來開始設定"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# --- 啟動服務 ---
if __name__ == "__main__":
    app.run(debug=True)
