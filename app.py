import os
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()

app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# 讀取金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 用戶資料暫存（正式環境建議用資料庫）
user_states = {}
user_data = {}

# 支援交通方式
TRANSPORT_MODES = {
    '1': 'transit',
    '2': 'driving',
    '3': 'walking',
    '4': 'bicycling'
}
TRANSPORT_NAMES = {
    'transit': '大眾運輸',
    'driving': '開車',
    'walking': '步行',
    'bicycling': '自行車'
}

# --- Google Maps 通勤計算 ---
def get_commute_info(origin, destination, arrival_time_str, mode='transit'):
    try:
        # 台灣時區
        tw_tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(tw_tz)
        arrival_dt = tw_tz.localize(
            datetime.strptime(f"{now.date()} {arrival_time_str}", "%Y-%m-%d %H:%M")
        )
        arrival_timestamp = int(arrival_dt.timestamp())

        url = 'https://maps.googleapis.com/maps/api/distancematrix/json'
        params = {
            'origins': origin,
            'destinations': destination,
            'mode': mode,
            'arrival_time': arrival_timestamp,
            'key': GOOGLE_API_KEY,
            'language': 'zh-TW'
        }

        response = requests.get(url, params=params).json()

        if response['status'] != 'OK':
            return {"error": f"Google Maps API 錯誤: {response.get('error_message', '未知錯誤')}"}
        element = response['rows'][0]['elements'][0]
        if element['status'] != 'OK':
            return {"error": f"路線規劃失敗: {element['status']}"}
        duration_sec = element['duration']['value']
        duration_text = element['duration']['text']
        best_departure_time = arrival_timestamp - duration_sec
        best_departure_str = datetime.fromtimestamp(best_departure_time, tw_tz).strftime("%H:%M")

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
    result = get_commute_info(
        data['origin'],
        data['destination'],
        data['arrival_time'],
        data.get('mode', 'transit')
    )
    if "error" in result:
        msg = f"🚨 通勤查詢失敗: {result['error']}"
    else:
        mode_name = TRANSPORT_NAMES.get(data.get('mode', 'transit'), '大眾運輸')
        msg = (
            f"🚗 今日建議你 {result['best_departure_time']} 出門\n"
            f"交通方式：{mode_name}\n"
            f"預估通勤時間：{result['duration_text']}"
        )
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
        reply = "請輸入出發地（例如：台北車站）"

    elif state == 'awaiting_origin':
        user_data[user_id]['origin'] = text
        user_states[user_id] = 'awaiting_destination'
        reply = "請輸入目的地（例如：新竹火車站）"

    elif state == 'awaiting_destination':
        user_data[user_id]['destination'] = text
        user_states[user_id] = 'awaiting_arrival'
        reply = "請輸入希望抵達時間（例如 08:30）"

    elif state == 'awaiting_arrival':
        user_data[user_id]['arrival_time'] = text
        user_states[user_id] = 'awaiting_transport'
        reply = (
            "請選擇交通方式，輸入數字：\n"
            "1. 大眾運輸\n"
            "2. 開車\n"
            "3. 步行\n"
            "4. 自行車"
        )

    elif state == 'awaiting_transport':
        mode = TRANSPORT_MODES.get(text, 'transit')
        user_data[user_id]['mode'] = mode
        user_states[user_id] = 'awaiting_remind'
        reply = "請輸入每日提醒時間（例如 07:00）"

    elif state == 'awaiting_remind':
        user_data[user_id]['remind_time'] = text
        user_states[user_id] = 'done'
        reply = "✅ 通勤提醒已設定完成！將於每日 {} 提醒你。".format(text)

        # 建立排程任務
        try:
            hour, minute = map(int, text.split(":"))
            job_id = f"reminder_{user_id}"
            scheduler.add_job(
                send_daily_reminder,
                'cron',
                hour=hour,
                minute=minute,
                args=[user_id],
                id=job_id,
                replace_existing=True,
                timezone='Asia/Taipei'
            )
        except Exception as e:
            reply += f"\n⚠️ 排程設定失敗：{str(e)}"

    else:
        reply = "請輸入「設定通勤」來開始設定"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# --- 啟動服務 ---
if __name__ == "__main__":
    app.run(debug=True)
