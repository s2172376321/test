from flask import Flask, request, render_template, send_file, g, jsonify, redirect, url_for
from google.cloud import storage
from google.api_core.exceptions import Forbidden, NotFound, GoogleAPICallError
from flask import session  # 導入 session
from io import BytesIO
import pandas as pd
import io
import tempfile
import os
import base64
import logging
import json  # 確保導入 json 模組
from google.cloud import storage
from google.oauth2 import service_account  # 確保導入 service_account 模組




app = Flask(__name__)

# 設定 Google Cloud Storage
BUCKET_NAME = "harvest-data-storage1"
LOCATIONS_FILE = "採收位置.csv"
CROPS_FILE = "採收作物.csv"
NAMES_FILE = "姓名.csv"
HARVEST_FILE = "harvest_data.csv"  # 修改為單一 CSV 檔案

# 從環境變數中抓取 Base64 憑證
credentials_base64 = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')

# 檢查環境變數是否存在
if not credentials_base64:
    raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable is missing!")

try:
    # 修復 Base64 填充問題
    missing_padding = len(credentials_base64) % 4
    if missing_padding:
        credentials_base64 += '=' * (4 - missing_padding)

    # 解碼 Base64
    credentials_json = base64.b64decode(credentials_base64).decode('utf-8')

    # 載入 JSON
    credentials = json.loads(credentials_json)

    # 初始化 Google Cloud Storage 客戶端
    storage_client = storage.Client(credentials=service_account.Credentials.from_service_account_info(credentials))
    logging.info("Google Cloud Storage client initialized successfully!")
except Exception as e:
    logging.error(f"Failed to decode or parse credentials: {str(e)}")
    raise ValueError(f"Failed to decode or parse credentials: {str(e)}")



 


def download_csv_from_gcs(filename):
    """從 Google Cloud Storage 下載 CSV 檔案"""
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    try:
        content = blob.download_as_text(encoding="utf-8")
        df = pd.read_csv(io.StringIO(content))
        return df
    except Exception as e:
        print(f"❌ 無法下載 {filename}，錯誤: {str(e)}")
        return pd.DataFrame()

@app.route("/")
def index():
    """首頁路由，載入 index.html"""
    return render_template("index.html")

@app.route("/get_names_options", methods=["GET"])
def get_names_options():
    """從 Google Cloud Storage 取得姓名選項並過濾數字"""
    try:
        names_df = download_csv_from_gcs(NAMES_FILE)
        names = names_df.iloc[:, 0].dropna().astype(str)
        filtered_names = [name for name in names if not any(char.isdigit() for char in name)]
        return jsonify(filtered_names)
    except Exception as e:
        return jsonify({"error": f"無法取得姓名數據: {str(e)}"}), 500

@app.route("/get_locations_options", methods=["GET"])
def get_locations_options():
    """從 Google Cloud Storage 取得採收地點選項"""
    try:
        locations_df = download_csv_from_gcs(LOCATIONS_FILE)
        locations = locations_df.iloc[:, 0].dropna().tolist()
        return jsonify(locations)
    except Exception as e:
        return jsonify({"error": f"無法取得採收地點數據: {str(e)}"}), 500

@app.route("/get_crop_options", methods=["GET"])
def get_crop_options():
    """從 Google Cloud Storage 取得採收作物選項，並支援關鍵字篩選"""
    query = request.args.get("q", "").strip()
    try:
        crops_df = download_csv_from_gcs(CROPS_FILE)
        crops_df['筆劃'] = crops_df['採收作物'].apply(lambda x: sum(map(lambda ch: ord(ch), x)))
        sorted_crops = crops_df.sort_values(by=['中分類', '筆劃'])
        grouped_crops = sorted_crops.groupby('中分類')['採收作物'].apply(list).to_dict()
        
        # 如果有關鍵字，過濾符合條件的作物
        if query:
            filtered_crops = crops_df[crops_df['採收作物'].str.contains(query, na=False, case=False)]['採收作物'].tolist()
            return jsonify({"filtered": filtered_crops, "all": grouped_crops})
        
        return jsonify({"all": grouped_crops})
    except Exception as e:
        return jsonify({"error": f"無法取得採收作物數據: {str(e)}"}), 500
        
@app.route("/search_crop_options", methods=["GET"])
def search_crop_options():
    query = request.args.get("q", "").strip()
    try:
        crops_df = download_csv_from_gcs(CROPS_FILE)
        filtered_crops = crops_df[crops_df['採收作物'].str.contains(query, na=False, case=False)]['採收作物'].tolist()
        return jsonify({"filtered": filtered_crops})
    except Exception as e:
        return jsonify({"error": f"查詢錯誤: {str(e)}"}), 500

from datetime import datetime  # 確保導入 datetime 模組

@app.route("/submit_harvest", methods=["POST"])
def submit_harvest():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "未接收到資料"}), 400

    try:
        existing_df = download_csv_from_gcs(HARVEST_FILE)
        last_id = existing_df["ID"].max() if "ID" in existing_df.columns else 0
    except FileNotFoundError:
        existing_df = pd.DataFrame(columns=["ID", "姓名", "日期", "採收位置", "採收作物", "重量", "雞蛋類型", "已洗入庫", "破蛋"])
        last_id = 0

    records = []
    for record in data:
        # 日期格式處理
        date_str = record.get("日期", "")
        try:
            # 移除時間部分（如果有的話）
            date_str = date_str.split()[0]
            # 將連字號轉換為斜線
            date_str = date_str.replace('-', '/')
            # 解析日期
            date_obj = datetime.strptime(date_str, "%Y/%m/%d")
            formatted_date = date_obj.strftime("%Y/%m/%d")
        except ValueError as e:
            return jsonify({"success": False, "message": f"日期格式錯誤: {date_str}"}), 400

        location = record.get("採收位置")
        if location == "蛋雞舍":
            required_fields = ["姓名", "日期", "採收位置", "雞蛋類型", "已洗入庫", "破蛋"]
            missing_fields = [field for field in required_fields if not record.get(field)]
            if missing_fields:
                return jsonify({"success": False, "message": f"缺少欄位: {missing_fields}"}), 400

            records.append({
                "ID": last_id + 1,
                "姓名": record.get("姓名"),
                "日期": formatted_date,  # 使用格式化後的日期
                "採收位置": record.get("採收位置"),
                "採收作物": "-",
                "重量": "-",
                "雞蛋類型": record.get("雞蛋類型", "-"),
                "已洗入庫": record.get("已洗入庫", "-"),
                "破蛋": record.get("破蛋", "-")
            })
        else:
            required_fields = ["姓名", "日期", "採收位置", "採收作物", "重量"]
            missing_fields = [field for field in required_fields if not record.get(field)]
            if missing_fields:
                return jsonify({"success": False, "message": f"缺少欄位: {missing_fields}"}), 400

            records.append({
                "ID": last_id + 1,
                "姓名": record.get("姓名"),
                "日期": formatted_date,  # 使用格式化後的日期
                "採收位置": record.get("採收位置"),
                "採收作物": record.get("採收作物", "-"),
                "重量": record.get("重量", "-"),
                "雞蛋類型": "-",
                "已洗入庫": "-",
                "破蛋": "-"
            })
        last_id += 1

    new_df = pd.DataFrame(records)
    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    upload_csv_to_gcs(combined_df, HARVEST_FILE)

    return jsonify({"success": True, "message": "資料提交成功"})





def upload_csv_to_gcs(dataframe, filename):
    """將 CSV 檔案儲存到 Google Cloud Storage"""
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    try:
        # 直接上傳 CSV，不需要再合併
        output = io.StringIO()
        dataframe.to_csv(output, index=False, encoding="utf-8")
        blob.upload_from_string(output.getvalue(), content_type="text/csv")
        print(f"✅ 成功更新並上傳 {filename}")
    except Exception as e:
        print(f"❌ 無法上傳 {filename}，錯誤: {str(e)}")

if __name__ == "__main__":
    app.run(debug=True)
