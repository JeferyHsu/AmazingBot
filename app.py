from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import requests
import time
import logging
from apscheduler.schedulers.background import BackgroundScheduler

# 初始化日志配置
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# 填入你的 Line 與 Google API 金鑰
LINE_CHANNEL_ACCESS_TOKEN = 'pdxQwcQxz8sOVIXDT0mQVU6j2KnZK7Zf13E/wXxn/Wj+blTFU/XGijzBewUrHv79WkcQxPhM+s7v83fGrltXNk+Fdc8ISrQL7wwzawxXuDGqr193XZoVJ2U+4TQF+39XQidtMhLWmGQ7fmUu3GFJGQdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = 'e5d82a887b7ccff529e3037cff4a46d6'
GOOGLE_API_KEY = 'AIzaSyCZVRwyR7PP9vQltot84y9uFvMhhpm0dus'

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 暫存使用者狀態與資料（正式建議用資料庫）
user_states = {}
user_data = {}

# --- Google Distance Matrix 通勤計算 ---
def get_commute_info(origin, destination, arrival_time_str):
    try:
        logger.debug(f"開始計算通勤時間：{origin} -> {destination} 抵達 {arrival_time_str}")
        
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

        logger.debug(f"發送 Google API 請求：{params}")
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Google API 回應：{data}")

        if data['status'] != 'OK':
            return {"error": f"API 回傳狀態異常：{data['status']}"}

        element = data['rows'][0]['elements'][0]
        if element['status'] != 'OK':
            return {"error": f"路線計算失敗：{element['status']}"}

        duration_sec = element['duration']['value']
        duration_text = element['duration']['text']
        best_departure_time = arrival_timestamp - duration_sec
        best_departure_str = time.strftime("%H:%M", time.localtime(best_departure_time))

        return {
            "duration_minutes": duration_sec // 60,
            "duration_text": duration_text,
            "best_departure_time": best_departure_str
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"API 請求失敗: {str(e)}")
        return {"error": "地圖服務暫時不可用"}
    except Exception as e:
        logger.exception("通勤計算發生未預期錯誤")
        return {"error": f"系統錯誤：{str(e)}"}

# --- 傳送每日提醒 ---
def send_daily_reminder(user_id):
    try:
        logger.info(f"傳送每日提醒給用戶 {user_id}")
        data = user_data.get(user_id)
        if not data:
            logger.warning(f"找不到用戶 {user_id} 的資料")
            return
            
        result = get_commute_info(data['origin'], data['destination'], data['arrival_time'])
        if "error" in result:
            msg = f"🚨 通勤查詢失敗: {result['error']}"
        else:
            msg = f"🚗 今日建議你 {result['best_departure_time']} 出門\n預估通勤時間：{result['duration_text']}"
        
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
        logger.debug(f"已發送訊息給 {user_id}: {msg}")

    except Exception as e:
        logger.exception(f"傳送提醒時發生錯誤")

# --- Line Webhook ---
@app.route("/callback", methods=["POST"])
def callback():
    try:
        signature = request.headers['X-Line-Signature']
        body = request.get_data(as_text=True)
        logger.debug(f"收到 Line 訊息: {body}")
        handler.handle(body, signature)
        return 'OK'
    except Exception as e:
        logger.exception("處理 Webhook 時發生錯誤")
        return 'Error', 500

# --- Google Distance Matrix 通勤計算 ---
def get_commute_info(origin, destination, arrival_time_str, mode):
    today = time.strftime("%Y-%m-%d")
    try:
        arrival_dt = time.strptime(f"{today} {arrival_time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        return {"error": "抵達時間格式錯誤，請用 HH:MM（如 08:30）"}
    arrival_timestamp = int(time.mktime(arrival_dt))

    url = 'https://maps.googleapis.com/maps/api/distancematrix/json'
    params = {
        'origins': origin,
        'destinations': destination,
        'mode': mode,
        'key': GOOGLE_API_KEY,
        'language': 'zh-TW',
        'departure_time': 'now' if mode != 'transit' else None  # 預設使用當前時間
    }

    # 大眾運輸需指定 arrival_time，其他模式需計算出發時間
    if mode == 'transit':
        params['arrival_time'] = arrival_timestamp
    else:
        # 若用戶希望指定抵達時間，需反向計算出發時間
        params['departure_time'] = 'now'  # 此處需調整邏輯

    response = requests.get(url, params=params).json()
    try:
        element = response['rows'][0]['elements'][0]
        
        # 開車模式優先使用 duration_in_traffic
        if mode == 'driving' and 'duration_in_traffic' in element:
            duration_sec = element['duration_in_traffic']['value']
        else:
            duration_sec = element['duration']['value']
            
        duration_text = element['duration']['text']
        
        # 計算建議出發時間（適用所有模式）
        best_departure_time = arrival_timestamp - duration_sec
        best_departure_str = time.strftime("%H:%M", time.localtime(best_departure_time))

        return {
            "duration_minutes": duration_sec // 60,
            "duration_text": duration_text,
            "best_departure_time": best_departure_str
        }
    except Exception as e:
        return {"error": str(e)}

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
        except Exception:
            reply = "提醒時間格式錯誤，請用 HH:MM（如 07:00）"
    else:
        reply = "請輸入「設定通勤」來開始設定"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# --- 啟動服務 ---
if __name__ == "__main__":
    logger.info("啟動服務...")
    app.run(debug=True)
