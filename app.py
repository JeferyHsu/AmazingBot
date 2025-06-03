import os
import time
import logging
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    PostbackEvent, QuickReply, QuickReplyButton, PostbackAction,
    TemplateSendMessage, ButtonsTemplate, DatetimePickerAction
)
from dotenv import load_dotenv
import requests

# 初始化日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 載入環境變數
load_dotenv()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 暫存用戶狀態與資料（正式建議用資料庫）
user_states = {}
user_data = {}

# Google Distance Matrix 查詢
def get_commute_info(origin, destination, datetime_str, mode, time_type):
    try:
        dt = time.strptime(datetime_str, "%Y-%m-%d %H:%M")
        dt_timestamp = int(time.mktime(dt))
        now_timestamp = int(time.time())
        
        # 檢查是否為未來時間
        if dt_timestamp <= now_timestamp:
            return {"error": "選擇的時間必須是未來時間，請重新設定"}
        
        url = 'https://maps.googleapis.com/maps/api/distancematrix/json'
        params = {
            'origins': origin,
            'destinations': destination,
            'mode': mode,
            'key': GOOGLE_API_KEY,
            'language': 'zh-TW'
        }

        if mode == 'transit':
            # 大眾運輸直接使用 arrival_time/departure_time
            if time_type == 'arrival':
                params['arrival_time'] = dt_timestamp
            else:
                params['departure_time'] = dt_timestamp
        else:
            # 非大眾運輸：用反推法模擬未來路況
            if time_type == 'arrival':
                # 目標抵達時間
                target_arrival = dt_timestamp
                # 初始猜測：抵達時間 - 30分鐘
                guess_departure = target_arrival - 1800
                
                # 最多迭代 5 次
                for _ in range(5):
                    params['departure_time'] = guess_departure
                    if mode == 'driving':
                        params['traffic_model'] = 'best_guess'
                    
                    response = requests.get(url, params=params).json()
                    element = response['rows'][0]['elements'][0]
                    
                    # 取得預估通勤時間
                    if mode == 'driving' and 'duration_in_traffic' in element:
                        duration_sec = element['duration_in_traffic']['value']
                    else:
                        duration_sec = element['duration']['value']
                    
                    # 計算新的出發時間
                    new_departure = target_arrival - duration_sec
                    if abs(new_departure - guess_departure) < 60:  # 收斂到1分鐘內
                        break
                    guess_departure = new_departure
                
                # 最終出發時間
                best_departure_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(guess_departure))
                duration_text = element['duration']['text']
                distance_text = element['distance']['text']
                
                return {
                    "duration_minutes": duration_sec // 60,
                    "duration_text": duration_text,
                    "best_departure_time": best_departure_str,
                    "distance_text": distance_text,
                    "estimated_arrival_time": datetime_str  # 用戶指定的抵達時間
                }
            else:
                # 用戶選的是出發時間，直接查詢
                params['departure_time'] = dt_timestamp
        
        # 執行查詢
        logger.debug(f"發送 Google API 請求：{params}")
        response = requests.get(url, params=params).json()
        logger.debug(f"Google API 回應：{response}")

        if response.get('status') != 'OK':
            return {"error": f"Google API 回傳異常: {response.get('status')}, {response.get('error_message', '')}"}
        if not response.get('rows') or not response['rows'][0].get('elements'):
            return {"error": "Google API 回傳資料異常，請檢查地址是否正確"}
        element = response['rows'][0]['elements'][0]
        if element.get('status') != 'OK':
            return {"error": f"路線查詢失敗：{element.get('status')}"}

        # 取得距離資訊
        distance_text = element['distance']['text']
        distance_value = element['distance']['value']

        if mode == 'driving' and 'duration_in_traffic' in element:
            duration_sec = element['duration_in_traffic']['value']
            duration_text = element['duration_in_traffic']['text']
        else:
            duration_sec = element['duration']['value']
            duration_text = element['duration']['text']

        best_departure_time = dt_timestamp if time_type == 'departure' else dt_timestamp - duration_sec
        best_departure_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(best_departure_time))

        return {
            "duration_minutes": duration_sec // 60,
            "duration_text": duration_text,
            "best_departure_time": best_departure_str,
            "distance_text": distance_text,
            "distance_value": distance_value
        }
    except Exception as e:
        logger.exception("通勤計算發生未預期錯誤")
        return {"error": f"系統錯誤：{str(e)}"}


# Webhook
@app.route("/callback", methods=["POST"])
def callback():
    try:
        signature = request.headers['X-Line-Signature']
        body = request.get_data(as_text=True)
        handler.handle(body, signature)
        return 'OK'
    except Exception as e:
        logger.exception("處理 Webhook 時發生錯誤")
        return 'Error', 500

# 處理文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    state = user_states.get(user_id, 'start')
    mode_map = {'1': 'transit', '2': 'driving', '3': 'walking', '4': 'bicycling'}

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
        if text not in mode_map:
            reply = "請輸入正確的數字（1~4）"
        else:
            user_data[user_id]['mode'] = mode_map[text]
            user_states[user_id] = 'awaiting_time_type'
            reply = TextSendMessage(
                text="請選擇你要設定的是『出發』還是『抵達』日期時間？",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label="出發", data="select_departure")),
                    QuickReplyButton(action=PostbackAction(label="抵達", data="select_arrival")),
                ])
            )
            line_bot_api.reply_message(event.reply_token, reply)
            return
    elif state == 'awaiting_remind':
        try:
            hour, minute = map(int, text.split(":"))
            assert 0 <= hour < 24 and 0 <= minute < 60
            user_data[user_id]['remind_time'] = text
            # 顯示設定總結
            mode_display = {
                'transit': '大眾運輸',
                'driving': '開車',
                'walking': '步行',
                'bicycling': '腳踏車'
            }
            dt_type = "出發" if user_data[user_id]['time_type'] == 'departure' else "抵達"
            dt_val = user_data[user_id]['datetime']
            commute_result = get_commute_info(
                user_data[user_id]['origin'],
                user_data[user_id]['destination'],
                dt_val,
                user_data[user_id]['mode'],
                user_data[user_id]['time_type']
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
                if user_data[user_id]['time_type'] == 'departure':
                    reply_msg = f"""✅ 通勤提醒設定完成！
━━━━━━━━━━━━━━
📍 出發地：{user_data[user_id]['origin']}
🏁 目的地：{user_data[user_id]['destination']}
🚙 通勤方式：{mode_display[user_data[user_id]['mode']]}
🛣️ 總共里程：{commute_result['distance_text']}
⏰ 出發日期時間：{dt_val}
🔔 每日提醒時間：{text}
━━━━━━━━━━━━━━
📣 根據目前路況：
🏁 預計抵達時間：{commute_result['estimated_arrival_time']}
⏱ 預估通勤時間：{commute_result['duration_text']}"""
                else:
                    reply_msg = f"""✅ 通勤提醒設定完成！
━━━━━━━━━━━━━━
📍 出發地：{user_data[user_id]['origin']}
🏁 目的地：{user_data[user_id]['destination']}
🚙 通勤方式：{mode_display[user_data[user_id]['mode']]}
🛣️ 總共里程：{commute_result['distance_text']}
⏰ {dt_type}日期時間：{dt_val}
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

# 處理 Postback（Datetime Picker 與出發/抵達選擇）
@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data
    params = event.postback.params

    if data == "select_departure":
        user_states[user_id] = 'awaiting_datetime'
        user_data[user_id]['time_type'] = 'departure'
        now = time.strftime("%Y-%m-%dT%H:%M")
        max_dt = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 60*60*24*30))
        message = TemplateSendMessage(
            alt_text="選擇出發日期時間",
            template=ButtonsTemplate(
                title="選擇出發日期時間",
                text="請選擇出發日期與時間",
                actions=[
                    DatetimePickerAction(
                        label="出發日期時間",
                        data="set_datetime",
                        mode="datetime",
                        initial=now,
                        min=now,
                        max=max_dt
                    )
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
    elif data == "select_arrival":
        user_states[user_id] = 'awaiting_datetime'
        user_data[user_id]['time_type'] = 'arrival'
        now = time.strftime("%Y-%m-%dT%H:%M")
        max_dt = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 60*60*24*30))
        message = TemplateSendMessage(
            alt_text="選擇抵達日期時間",
            template=ButtonsTemplate(
                title="選擇抵達日期時間",
                text="請選擇抵達日期與時間",
                actions=[
                    DatetimePickerAction(
                        label="抵達日期時間",
                        data="set_datetime",
                        mode="datetime",
                        initial=now,
                        min=now,
                        max=max_dt
                    )
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
    elif data == "set_datetime":
        dt = params.get("datetime")  # 格式 '2025-06-05T08:30'
        if dt:
            user_data[user_id]['datetime'] = dt.replace("T", " ")
            user_states[user_id] = 'awaiting_remind'
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"你選擇的日期時間是：{dt.replace('T',' ')}\n請輸入每日提醒時間（例如 07:00）")
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請重新選擇日期時間。")
            )

# 啟動服務
if __name__ == "__main__":
    logger.info("啟動服務...")
    app.run(debug=True)
