import os
import time
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv

# 載入 .env
load_dotenv()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

app = Flask(__name__)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 用戶狀態與資料（正式建議用資料庫）
user_states = {}
user_data = {}

# --- Google Distance Matrix 通勤計算 ---
def get_commute_info(origin, destination, arrival_time_str, mode):
    today = time.strftime("%Y-%m-%d")
    try:
        arrival_dt = time.strptime(f"{today} {arrival_time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        return {"error": "抵達時間格式錯誤，請用 HH:MM（如 08:30）"}
    arrival_timestamp = int(time.mktime(arrival_dt))

    url = 'https://maps.googleapis.com/maps/api/distancematrix/json'
    params_base = {
        'origins': origin,
        'destinations': destination,
        'mode': mode,
        'key': GOOGLE_API_KEY,
        'language': 'zh-TW'
    }

    if mode == 'transit':
        params = params_base.copy()
        params['arrival_time'] = arrival_timestamp
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
    else:
        # 反推法：迭代查詢
        guess_departure = arrival_timestamp - 1800
        for _ in range(5):
            params = params_base.copy()
            params['departure_time'] = guess_departure
            if mode == 'driving':
                params['traffic_model'] = 'best_guess'
            response = requests.get(url, params=params).json()
            try:
                element = response['rows'][0]['elements'][0]
                if mode == 'driving' and 'duration_in_traffic' in element:
                    duration_sec = element['duration_in_traffic']['value']
                    duration_text = element['duration_in_traffic']['text']
                else:
                    duration_sec = element['duration']['value']
                    duration_text = element['duration']['text']
                new_departure = arrival_timestamp - duration_sec
                if abs(new_departure - guess_departure) < 60:
                    break
                guess_departure = new_departure
            except Exception as e:
                return {"error": str(e)}
        best_departure_str = time.strftime("%H:%M", time.localtime(guess_departure))
        return {
            "duration_minutes": duration_sec // 60,
            "duration_text": duration_text,
            "best_departure_time": best_departure_str
        }

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
        user_states[user_id] = 'awaiting_mode'
        reply = "請選擇通勤方式：\n1. 大眾運輸\n2. 開車\n3. 步行\n4. 腳踏車\n請輸入數字（例如 1）"
    
    elif state == 'awaiting_mode':
        mode_map = {'1': 'transit', '2': 'driving', '3': 'walking', '4': 'bicycling'}
        if text not in mode_map:
            reply = "請輸入正確的數字（1~4）"
        else:
            user_data[user_id]['mode'] = mode_map[text]
            user_states[user_id] = 'awaiting_arrival'
            reply = "請輸入希望抵達時間（例如 08:30）"
    
    elif state == 'awaiting_arrival':
        try:
            time.strptime(text, "%H:%M")
            user_data[user_id]['arrival_time'] = text
            user_states[user_id] = 'awaiting_remind'
            reply = "請輸入每日提醒時間（例如 07:00）"
        except Exception:
            reply = "時間格式錯誤，請用 HH:MM（如 08:30）"
    
    elif state == 'awaiting_remind':
        try:
            hour, minute = map(int, text.split(":"))
            assert 0 <= hour < 24 and 0 <= minute < 60
            user_data[user_id]['remind_time'] = text

            commute_result = get_commute_info(
                user_data[user_id]['origin'],
                user_data[user_id]['destination'],
                user_data[user_id]['arrival_time'],
                user_data[user_id]['mode']
            )

            if "error" in commute_result:
                reply_msg = f"""❌ 設定失敗：{commute_result['error']}
━━━━━━━━━━━━━━
💡 可能原因：
1. 地址輸入不正確
2. 路線不存在
3. API 暫時故障

請重新輸入「設定通勤」開始設定"""
                user_states[user_id] = 'start'
                user_data.pop(user_id, None)
            else:
                mode_display = {
                    'transit': '大眾運輸',
                    'driving': '開車',
                    'walking': '步行',
                    'bicycling': '腳踏車'
                }
                reply_msg = f"""✅ 通勤提醒設定完成！
━━━━━━━━━━━━━━
📍 出發地：{user_data[user_id]['origin']}
🏁 目的地：{user_data[user_id]['destination']}
🚙 通勤方式：{mode_display[user_data[user_id]['mode']]}
⏰ 希望抵達時間：{user_data[user_id]['arrival_time']}
🔔 每日提醒時間：{text}
━━━━━━━━━━━━━━
📣 根據目前路況：
🚪 建議出發時間：{commute_result['best_departure_time']}
⏱ 預估通勤時間：{commute_result['duration_text']}"""
                user_states[user_id] = 'done'
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
            return
        except Exception:
            reply = "提醒時間格式錯誤，請用 HH:MM（如 07:00）"
    else:
        reply = "請輸入「設定通勤」來開始設定"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# --- 啟動服務 ---
if __name__ == "__main__":
    app.run(debug=True)
