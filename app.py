import os
import time
import logging
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    RichMenu, RichMenuArea, RichMenuBounds, RichMenuSize,MessageAction,
    MessageEvent, TextMessage, TextSendMessage, 
    PostbackEvent, QuickReply, QuickReplyButton, PostbackAction,
    TemplateSendMessage, ButtonsTemplate, DatetimePickerAction
)
from dotenv import load_dotenv
import requests
from WeatherBot import get_city_and_district, get_weather

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
        # 將用戶輸入的日期時間轉為 timestamp
        dt = time.strptime(datetime_str, "%Y-%m-%d %H:%M")
        dt_timestamp = int(time.mktime(dt))
        now_timestamp = int(time.time())

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

        # 處理大眾運輸模式
        if mode == 'transit':
            if time_type == 'arrival':
                params['arrival_time'] = dt_timestamp
            else:
                params['departure_time'] = dt_timestamp

        # 處理非大眾運輸模式
        else:
            if mode == 'driving':
                params['traffic_model'] = 'best_guess'

            if time_type == 'arrival':
                # 透過反推方式找最佳出發時間
                arrival_timestamp = dt_timestamp
                guess_departure = arrival_timestamp - 300  # 初始猜測為提早1小時
                for _ in range(10):
                    params['departure_time'] = guess_departure
                    response = requests.get(url, params=params).json()
                    element = response['rows'][0]['elements'][0]

                    if 'duration_in_traffic' in element:
                        duration_sec = element['duration_in_traffic']['value']
                    else:
                        duration_sec = element['duration']['value']

                    new_departure = arrival_timestamp - duration_sec
                    if abs(new_departure - guess_departure) < 30:
                        break
                    guess_departure = new_departure

                best_departure_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(guess_departure))
                duration_text = element['duration']['text']
                distance_text = element['distance']['text']

                return {
                    "duration_minutes": duration_sec // 60,
                    "duration_text": duration_text,
                    "best_departure_time": best_departure_str,
                    "distance_text": distance_text,
                    "estimated_arrival_time": datetime_str
                }
            else:
                # time_type == 'departure'
                params['departure_time'] = dt_timestamp

        # 正式發送請求
        response = requests.get(url, params=params).json()

        if response.get('status') != 'OK':
            return {"error": f"Google API 回傳異常: {response.get('status')}, {response.get('error_message', '')}"}
        if not response.get('rows') or not response['rows'][0].get('elements'):
            return {"error": "Google API 回傳資料異常，請檢查地址是否正確"}

        element = response['rows'][0]['elements'][0]
        if element.get('status') != 'OK':
            return {"error": f"路線查詢失敗：{element.get('status')}"}

        # 取得距離與時間
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

        estimated_arrival_timestamp = best_departure_time + duration_sec
        estimated_arrival_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(estimated_arrival_timestamp))

        return {
            "duration_minutes": duration_sec // 60,
            "duration_text": duration_text,
            "best_departure_time": best_departure_str,
            "estimated_arrival_time": estimated_arrival_str,
            "distance_text": distance_text,
            "distance_value": distance_value
        }

    except Exception as e:
        logger.exception("通勤計算發生未預期錯誤")
        return {"error": f"系統錯誤：{str(e)}"}
    
def create_rich_menu():
    rich_menu = RichMenu(
        size=RichMenuSize(width=2500, height=843),
        selected=True,
        name="功能選單",
        chat_bar_text="點擊開啟選單",
        areas=[
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=1250, height=843),
                action=MessageAction(label='切換到天氣查詢', text='切換到天氣查詢')
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=1250, y=0, width=1250, height=843),
                action=MessageAction(label='設定通勤', text='設定通勤')
            )
        ]
    )
    return rich_menu

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
    user_states.setdefault(user_id, 'start')

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
    elif text == "切換到天氣查詢":
         user_states[user_id] = 'awaiting_weather_location'
         reply = "請輸入你想查詢天氣的地點"

    elif state == 'awaiting_weather_location':
        user_data[user_id] = {'weather_location': text}
        user_states[user_id] = 'awaiting_weather_datetime'

        now = time.strftime("%Y-%m-%dT%H:%M")
        max_dt = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 60 * 60 * 24 * 30))

        message = TextSendMessage(
            text="請選擇你想查詢的日期與時間：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=DatetimePickerAction(
                    label="選擇時間",
                    data="weather_datetime",
                    mode="datetime",
                    initial=now,
                    min=now,
                    max=max_dt
                ))
            ])
        )
        line_bot_api.reply_message(event.reply_token, message)
        return

    else:
        reply = "請輸入透過選單來開始設定"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data
    params = event.postback.params

    now = time.strftime("%Y-%m-%dT%H:%M")
    max_dt = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 60 * 60 * 24 * 30))

    if data == "weather_datetime":
        dt = params.get("datetime")  # 格式 '2025-06-05T08:30'
        if dt:
            location = user_data[user_id]['weather_location']
            dt_val = dt.replace("T", " ")
            city_district = get_city_and_district(location)
            weather_info = get_weather(city_district["city"], city_district["district"], dt)

            reply_msg = f"""🌦 天氣查詢結果
━━━━━━━━━━━━━━
📍 地點：{location}
🕒 時間：{dt_val}
🌤 天氣狀況：
{weather_info}"""
            user_states[user_id] = 'start'
            user_data.pop(user_id, None)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
    
    elif data == "select_departure":
        user_states[user_id] = 'awaiting_datetime'
        user_data[user_id]['time_type'] = 'departure'

        message = TextSendMessage(
            text="請選擇出發日期與時間：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=DatetimePickerAction(
                    label="選擇出發時間",
                    data="set_datetime",
                    mode="datetime",
                    initial=now,
                    min=now,
                    max=max_dt
                ))
            ])
        )
        line_bot_api.reply_message(event.reply_token, message)

    elif data == "select_arrival":
        user_states[user_id] = 'awaiting_datetime'
        user_data[user_id]['time_type'] = 'arrival'

        message = TextSendMessage(
            text="請選擇抵達日期與時間：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=DatetimePickerAction(
                    label="選擇抵達時間",
                    data="set_datetime",
                    mode="datetime",
                    initial=now,
                    min=now,
                    max=max_dt
                ))
            ])
        )
        line_bot_api.reply_message(event.reply_token, message)

    elif data == "set_datetime":
        dt = params.get("datetime")  # 格式 '2025-06-05T08:30'
        if dt:
            user_data[user_id]['datetime'] = dt.replace("T", " ")
            dt_val = user_data[user_id]['datetime']
            commute_result = get_commute_info(
                user_data[user_id]['origin'],
                user_data[user_id]['destination'],
                dt_val,
                user_data[user_id]['mode'],
                user_data[user_id]['time_type']
            )
            mode_display = {
                'transit': '大眾運輸',
                'driving': '開車',
                'walking': '步行',
                'bicycling': '腳踏車'
            }

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
                # 取得兩地經緯度與行政區
                origin_info = get_city_and_district(user_data[user_id]['origin'])
                dest_info = get_city_and_district(user_data[user_id]['destination'])

                # 根據時間類型決定查詢時間點
                if user_data[user_id]['time_type'] == 'departure':
                    depart_time = dt_val.replace(" ", "T")
                    arrival_time = commute_result['estimated_arrival_time'].replace(" ", "T")
                else:
                    depart_time = commute_result['best_departure_time'].replace(" ", "T")
                    arrival_time = dt_val.replace(" ", "T")

                # 判斷是否為相同縣市和區域
                same_location = (
                    origin_info["city"] == dest_info["city"] and
                    origin_info["district"] == dest_info["district"]
                )

                # 查詢天氣：如果地點相同，只查一次
                if same_location:
                    weather_info = get_weather(origin_info["city"], origin_info["district"], depart_time)
                    origin_weather = dest_weather = weather_info
                else:
                    origin_weather = get_weather(origin_info["city"], origin_info["district"], depart_time)
                    dest_weather = get_weather(dest_info["city"], dest_info["district"], arrival_time)
                
                if same_location:
                    weather_section = f"🌤 天氣狀況：\n{origin_weather}"
                else:
                    if(user_data[user_id]['time_type'] == 'departure'):
                        weather_section = f"🌤 出發地天氣：\n{origin_weather}"
                    else:
                        weather_section = f"🌤 目的地天氣：\n{dest_weather}"

                if user_data[user_id]['time_type'] == 'departure':
                    reply_msg = f"""✅ 通勤提醒設定完成！
━━━━━━━━━━━━━━
📍 出發地：{user_data[user_id]['origin']}
🏁 目的地：{user_data[user_id]['destination']}
🚙 通勤方式：{mode_display[user_data[user_id]['mode']]}
🛣️ 總共里程：{commute_result['distance_text']}
⏰ 出發日期時間：{dt_val}
{weather_section}
━━━━━━━━━━━━━━
📣 根據當時路況：
🏁 預計抵達時間：{commute_result['estimated_arrival_time']}
⏱ 預估通勤時間：{commute_result['duration_text']}
{'' if same_location else f'🌤 目的地天氣：\n{dest_weather}'}"""
                else:
                    reply_msg = f"""✅ 通勤提醒設定完成！
━━━━━━━━━━━━━━
📍 出發地：{user_data[user_id]['origin']}
🏁 目的地：{user_data[user_id]['destination']}
🚙 通勤方式：{mode_display[user_data[user_id]['mode']]}
🛣️ 總共里程：{commute_result['distance_text']}
⏰ 抵達日期時間：{dt_val}
{weather_section}
━━━━━━━━━━━━━━
📣 根據當時路況：
🚪 建議出發時間：{commute_result['best_departure_time']}
⏱ 預估通勤時間：{commute_result['duration_text']}
{'' if same_location else f'🌤 出發地天氣：\n{origin_weather}'}"""
                user_states[user_id] = 'done'
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請重新選擇日期時間。"))

# 啟動服務
if __name__ == "__main__":
    # 建立 Rich Menu
    rich_menu_id = line_bot_api.create_rich_menu(create_rich_menu())
    with open("111.png", 'rb') as f:
        line_bot_api.set_rich_menu_image(rich_menu_id, "image/jpeg", f)
    line_bot_api.set_default_rich_menu(rich_menu_id)
    logger.info("啟動服務...")
    app.run(debug=True)


