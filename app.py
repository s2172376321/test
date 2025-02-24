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

# 獲取環境變數
credentials_base64 = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')

# 檢查環境變數是否存在
if not credentials_base64:
    raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable is missing!")

try:
    # 解碼 Base64 並載入 JSON
    credentials_json = base64.b64decode(credentials_base64).decode('utf-8')
    credentials = json.loads(credentials_json)

    # 使用憑證初始化 Google Cloud Storage 客戶端
    storage_client = storage.Client(credentials=service_account.Credentials.from_service_account_info(credentials))
except Exception as e:
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

    records = []
    for record in data:
        location = record.get("採收位置")
        if location == "蛋雞舍":
            # 檢查必填欄位
            required_fields = ["姓名", "日期", "採收位置", "雞蛋類型", "已洗入庫", "破蛋"]
            missing_fields = [field for field in required_fields if not record.get(field)]
            if missing_fields:
                return jsonify({"success": False, "message": f"缺少欄位: {missing_fields}"}), 400

            # 將日期轉換為僅保留日期部分
            date_str = record.get("日期")
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")  # 假設日期格式為 "YYYY-MM-DD HH:MM:SS"
                date_only = date_obj.strftime("%Y-%m-%d")  # 轉換為 "YYYY-MM-DD"
            except ValueError:
                date_only = date_str  # 如果格式不正確，保留原始值

            records.append({
                "姓名": record.get("姓名"),
                "日期": date_only,  # 使用僅日期的格式
                "採收位置": record.get("採收位置"),
                "採收作物": "-",  # 蛋雞舍無採收作物，補上 "-"
                "重量": "-",  # 蛋雞舍無重量，補上 "-"
                "雞蛋類型": record.get("雞蛋類型", "-"),
                "已洗入庫": record.get("已洗入庫", "-"),
                "破蛋": record.get("破蛋", "-")
            })
        else:
            # 檢查必填欄位
            required_fields = ["姓名", "日期", "採收位置", "採收作物", "重量"]
            missing_fields = [field for field in required_fields if not record.get(field)]
            if missing_fields:
                return jsonify({"success": False, "message": f"缺少欄位: {missing_fields}"}), 400

            # 將日期轉換為僅保留日期部分
            date_str = record.get("日期")
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")  # 假設日期格式為 "YYYY-MM-DD HH:MM:SS"
                date_only = date_obj.strftime("%Y-%m-%d")  # 轉換為 "YYYY-MM-DD"
            except ValueError:
                date_only = date_str  # 如果格式不正確，保留原始值

            records.append({
                "姓名": record.get("姓名"),
                "日期": date_only,  # 使用僅日期的格式
                "採收位置": record.get("採收位置"),
                "採收作物": record.get("採收作物", "-"),
                "重量": record.get("重量", "-"),
                "雞蛋類型": "-",  # 非蛋雞舍無雞蛋類型，補上 "-"
                "已洗入庫": "-",  # 非蛋雞舍無已洗入庫，補上 "-"
                "破蛋": "-"  # 非蛋雞舍無破蛋，補上 "-"
            })

    # 儲存到 Google Cloud Storage
    if records:
        df = pd.DataFrame(records)
        upload_csv_to_gcs(df, HARVEST_FILE)

    return jsonify({"success": True, "message": "資料提交成功"})



def upload_csv_to_gcs(dataframe, filename):
    """將 CSV 檔案接續儲存到 Google Cloud Storage"""
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    try:
        # 檢查檔案是否已存在於 GCS
        if blob.exists():
            existing_data = io.StringIO(blob.download_as_text(encoding="utf-8"))
            existing_df = pd.read_csv(existing_data)
            # 合併舊資料與新資料
            dataframe = pd.concat([existing_df, dataframe], ignore_index=True)

        # 上傳新的合併後 CSV
        output = io.StringIO()
        dataframe.to_csv(output, index=False, encoding="utf-8")
        blob.upload_from_string(output.getvalue(), content_type="text/csv")
        print(f"✅ 成功更新並上傳 {filename}")
    except Exception as e:
        print(f"❌ 無法上傳 {filename}，錯誤: {str(e)}")

if __name__ == "__main__":
    app.run(debug=True)
