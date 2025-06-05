from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, StickerMessage, ImageMessage, 
    VideoMessage, LocationMessage, TextSendMessage, StickerSendMessage
    ,TextSendMessage,TemplateSendMessage, ButtonsTemplate, MessageAction
)
from linebot.models import RichMenu, RichMenuArea, RichMenuBounds, RichMenuSize
from google import genai
import os
import json
import base64
import hashlib
import hmac
import requests
from dotenv import load_dotenv

app = Flask(__name__)

# 載入環境變數
load_dotenv()

# LINE Bot API設定
line_bot_api = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini API設定
gemini_api_key = os.getenv('GEMINI_API_KEY')

# 儲存對話歷史的字典
conversation_history = {}

# 添加用戶狀態追踪
user_states = {}

@app.after_request
def log_response(response):
    print(f"回應狀態碼: {response.status_code}")
    return response

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    # 手動驗證簽名（用於調試）
    channel_secret = 'f14f0b2b60c46355df297e112dfa348f'
    hash = hmac.new(channel_secret.encode('utf-8'),
                   body.encode('utf-8'), hashlib.sha256).digest()
    signature_calculated = base64.b64encode(hash).decode('utf-8')
    signature_received = request.headers['X-Line-Signature']
    
    print(f"Calculated signature: {signature_calculated}")
    print(f"Received signature: {signature_received}")
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    print(f"User ID: {user_id}")
    
    # 儲存用戶訊息到歷史記錄
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({"role": "user", "content": user_message})
    
    # 檢查是否是功能選單命令
    if user_message.lower() == "功能選單":
        # 發送按鈕模板
        buttons_template = ButtonsTemplate(
            title='功能選單',
            text='請選擇您要使用的功能',
            actions=[
                MessageAction(label='天氣查詢', text='切換到天氣查詢'),
                MessageAction(label='一般對話', text='切換到一般對話'),
                MessageAction(label='設定通勤', text='設定通勤')
            ]
        )
        template_message = TemplateSendMessage(
            alt_text='功能選單',
            template=buttons_template
        )
        line_bot_api.reply_message(event.reply_token, template_message)
        return
    
    # 處理模式切換
    if user_message == "切換到天氣查詢":
        user_states[user_id] = "weather"
        ai_response = "已切換到天氣查詢模式。\n請輸入城市名稱：\n\n大台北地區：\n臺北市,新北市,基隆市\n\n桃竹苗地區：\n桃園市,新竹縣,新竹市,苗栗縣\n\n中彰投地區：\n臺中市,彰化縣,南投縣\n\n雲嘉南地區：\n雲林縣,嘉義縣,嘉義市,臺南市\n\n南部地區：\n高雄市,屏東縣\n\n東部地區：\n宜蘭縣,花蓮縣,臺東縣\n\n離島地區：\n澎湖縣,金門縣,連江縣"
    elif user_message == "切換到一般對話":
        user_states[user_id] = "chat"
        ai_response = "已切換到一般對話模式。\n您可以問我任何問題。"
    elif user_message == "這個沒用":
        ai_response = "我就說這個沒用了"
    # 根據用戶當前狀態處理訊息
    elif user_id in user_states and user_states[user_id] == "weather":
        # 在天氣查詢模式下，直接將用戶輸入視為地點名稱
        location = user_message
        weather_info = get_weather(location)
        ai_response = weather_info
    else:
        # 預設為一般對話模式，使用Gemini API
        try:
            response = gemini_api_key.models.generate_content(
                model="gemini-2.0-flash",
                contents=[user_message]
            )
            ai_response = response.text
        except Exception as e:
            ai_response = f"抱歉，處理您的請求時出現錯誤: {str(e)}"
    
    # 儲存AI回應到歷史記錄
    conversation_history[user_id].append({"role": "model", "content": ai_response})
    
    # 回覆用戶
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=ai_response)
    )

# 提取地點函數
def extract_location(text):
    # 將"台"轉換為"臺"
    replaced_text = text.replace("台北市", "臺北市")
    print(replaced_text)

    location_mapping = {
        "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", 
        "金門縣", "連江縣", "臺北市", "新北市",
        "桃園市", "臺中市", "臺南市", "高雄市", 
        "基隆市", "新竹縣", "新竹市", "苗栗縣",
        "彰化縣", "南投縣", "雲林縣", "嘉義縣", 
        "嘉義市", "屏東縣",
    }
    
    for loc in location_mapping.keys():
        if loc in text:
            return location_mapping[loc]
        # 如果用戶只輸入了縣市名稱的前兩個字
        elif loc[:2] in text:
            return loc
    return None

# 獲取天氣資訊函數
def get_weather(location):
    # 這裡使用氣象API獲取天氣資訊
    CWB_API_KEY = os.getenv('CWB_API_KEY')
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={CWB_API_KEY}&locationName={location}"
    
    try:
        response = requests.get(url)
        data = response.json()
        if not data.get("records") or not data["records"].get("location") or len(data["records"]["location"]) == 0:
            return f"抱歉，找不到{location}的天氣資訊"     
           
        # 解析API回應並格式化天氣資訊
        weather_elements = data["records"]["location"][0]["weatherElement"]
        weather_state = weather_elements[0]["time"][0]["parameter"]["parameterName"]
        min_temp = weather_elements[2]["time"][0]["parameter"]["parameterName"]
        max_temp = weather_elements[4]["time"][0]["parameter"]["parameterName"]
        
        return f"{location}目前天氣狀況「{weather_state}」，溫度 {min_temp} 到 {max_temp} 度"
    except (KeyError, IndexError) as e:
        return f"抱歉，無法獲取{location}的天氣資訊：資料格式錯誤 ({str(e)})"

@handler.add(MessageEvent, message=StickerMessage)
def handle_sticker_message(event):
    # 回覆貼圖
    line_bot_api.reply_message(
        event.reply_token,
        StickerSendMessage(package_id=11537, sticker_id=52002734)
    )

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    
    # 儲存用戶訊息到歷史記錄
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({"role": "user", "content": "[使用者傳送了一張圖片]"})
    
    # 回覆用戶
    response_text = "我收到了您的圖片！"
    
    # 儲存AI回應到歷史記錄
    conversation_history[user_id].append({"role": "model", "content": response_text})
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

@handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
    user_id = event.source.user_id
    
    # 儲存用戶訊息到歷史記錄
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({"role": "user", "content": "[使用者傳送了一段影片]"})
    
    # 回覆用戶
    response_text = "我收到了您的影片！"
    
    # 儲存AI回應到歷史記錄
    conversation_history[user_id].append({"role": "model", "content": response_text})
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    address = event.message.address
    
    # 儲存用戶訊息到歷史記錄
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({"role": "user", "content": f"[使用者分享了位置: {address}]"})
    
    # 回覆用戶
    response_text = f"您的位置是：{address}，座標：({latitude}, {longitude})"
    
    # 儲存AI回應到歷史記錄
    conversation_history[user_id].append({"role": "model", "content": response_text})
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=response_text)
    )

# 創建Rich Menu
def create_rich_menu():
    rich_menu = RichMenu(
        size=RichMenuSize(width=2500, height=843),
        selected=True,
        name="功能選單",
        chat_bar_text="點擊開啟選單",
        areas=[
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=833, height=843),
                action=MessageAction(label='天氣查詢', text='切換到天氣查詢')
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=833, y=0, width=833, height=843),
                action=MessageAction(label='一般對話', text='切換到一般對話')
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=1666, y=0, width=833, height=843),
                action=MessageAction(label='這個沒用', text='我就說這個沒用了')
            )
        ]
    )
    return rich_menu

# RESTful API for conversation history
@app.route("/history/<user_id>", methods=['GET'])
def get_history(user_id):
    if user_id in conversation_history:
        return jsonify(conversation_history[user_id])
    return jsonify([])

@app.route("/history/<user_id>", methods=['DELETE'])
def delete_history(user_id):
    if user_id in conversation_history:
        conversation_history.pop(user_id)
    return "History deleted"

if __name__ == "__main__":
    # 創建並上傳Rich Menu
    rich_menu_id = line_bot_api.create_rich_menu(create_rich_menu())
    
    # 上傳Rich Menu圖片（需要準備一張2500x843像素的圖片）
    with open("111.png", 'rb') as f:
        line_bot_api.set_rich_menu_image(rich_menu_id, "image/jpeg", f)
    
    # 將Rich Menu設為預設
    line_bot_api.set_default_rich_menu(rich_menu_id)
    
    app.run(debug=True, port=8000)

