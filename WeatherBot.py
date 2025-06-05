import requests
from dotenv import load_dotenv
import os
from datetime import datetime
import json

# 載入 .env 中的 API 金鑰
CWB_API_KEY = os.getenv('CWB_API_KEY')

# 取得縣市與區（鄉鎮）
def get_city_and_district(place_name):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": place_name,
        "format": "json",
        "addressdetails": 1,
        "limit": 1
    }
    headers = {
        "User-Agent": "LineCommuteBot/1.0 (test@example.com)"
    }

    try:
        response = requests.get(url, params=params, headers=headers)
        data = response.json()

        if not data:
            return {"city": "未知縣市", "district": "未知鄉鎮區", "error": "找不到地址"}

        address = data[0]["address"]
        district = (
            address.get("town") or
            address.get("city_district") or
            address.get("suburb") or
            address.get("village") or
            address.get("municipality") or
            "未知鄉鎮區"
        )
        city = (
            address.get("city") or
            address.get("county") or
            address.get("state") or
            "未知縣市"
        )

        return {
            "city": city,
            "district": district,
            "lat": data[0]["lat"],
            "lon": data[0]["lon"]
        }
    except Exception as e:
        return {"city": "未知縣市", "district": "未知鄉鎮區", "error": str(e)}

# 查詢天氣（目前只支援桃園市）
def get_weather(city, district, time):
    try:
        if city == "桃園市":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-007?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "新北市":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-071?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "臺北市":  
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-063?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "臺中市":          
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-075?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "臺南市":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-079?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "高雄市":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-067?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "基隆市":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-051?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "新竹市": 
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-055?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "新竹縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-011?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "嘉義市":  
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-059?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "宜蘭縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-003?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "花蓮縣": 
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-043?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "臺東縣":     
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-039?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "苗栗縣":  
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-015?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "彰化縣":  
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-019?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "南投縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-023?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "雲林縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-027?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "嘉義縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-031?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "屏東縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-035?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "澎湖縣": 
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-047?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "金門縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-087?Authorization={CWB_API_KEY}&locationName={district}"
        elif city == "連江縣":
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-083?Authorization={CWB_API_KEY}&locationName={district}"
        response = requests.get(url)
        data = response.json()

        target_time = datetime.fromisoformat(time)

        weather_text = None
        min_temp = None
        max_temp = None
        pop_text = "天數過多無法預測"

        for location in data["records"]["Locations"]:
            for loc in location["Location"]:
                for element in loc["WeatherElement"]:
                    for entry in element["Time"]:
                        start = datetime.fromisoformat(entry["StartTime"].replace("+08:00", ""))
                        end = datetime.fromisoformat(entry["EndTime"].replace("+08:00", ""))
                        if not (start <= target_time < end):
                            continue

                        # 天氣現象
                        if element["ElementName"] == "天氣現象" and not weather_text:
                            weather_text = entry["ElementValue"][0].get("Weather")

                        # 最低溫度
                        elif element["ElementName"] == "最低溫度" and not min_temp:
                            min_temp = entry["ElementValue"][0].get("MinTemperature")

                        # 最高溫度
                        elif element["ElementName"] == "最高溫度" and not max_temp:
                            max_temp = entry["ElementValue"][0].get("MaxTemperature")

                        # 降雨機率
                        elif element["ElementName"] == "12小時降雨機率":
                            value = entry["ElementValue"][0].get("ProbabilityOfPrecipitation")
                            if value and value != "-":
                                pop_text = f"降雨機率：{value}%"

        result_parts = []
        if weather_text:
            result_parts.append(weather_text)
        if min_temp and max_temp:
            result_parts.append(f"氣溫範圍攝氏 {min_temp}~{max_temp} 度")
        if pop_text:
            result_parts.append(pop_text)

        return "\n".join(result_parts) if result_parts else "查無該時間的天氣資料"

    except Exception as e:
        return f"天氣資料錯誤：{e}"

# 測試程式區
#if __name__ == "__main__":
    place = "板橋火車站"
    info = get_city_and_district(place)

    print("\n🔍 地址解析結果：")
    print(info)
    time= "2025-06-06T20:00:00"  # 使用 ISO 8601 格式的時間字串
    weather = get_weather(info["city"], info["district"],time)
    print(f"\n📍 {info['city']} {info['district']} 的天氣：")
    print(weather)
