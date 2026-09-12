# -*- coding: utf-8 -*-
"""
app.py — 系統統一入口（合併專案一 + 專案二版）
=====================================================
以專案二的 app.py（2D AI 試穿版）為骨架，整併：
  - web.py 的 OAuth 註冊 (_register_oauth)、防破圖 404 保護網、
    全域 /api/ 例外保護網
  - routes.py（核心站台）、ai_style_routes.py（Gemini 結構化穿搭建議）
    兩個 Blueprint 的註冊
  - ai_tryon_routes.py 的 2D 試穿 + 商家登入
  - chat_routes.py 的買家↔商家聊天室
  - models.py 的 SQLAlchemy 初始化（Garment / ChatThread / ChatMessage /
    User / Store / Order / OrderItem，見步驟一）
  - database.py 的 sqlite3 專家知識庫（style_system.db），啟動時建表/遷移

【本檔跟原本兩份 app.py / web.py 比，做了幾個必要的修正，
  全部會在下面對應段落用「🔧 [合併修正]」註解標出，並在訊息最後總結】
"""
import os
import sys

# 🔧 [整理結構] app.py 拆分後，routes.py / models.py / database.py 等
# 分別搬進 routes/、models/、services/ 資料夾。這幾支檔案內部彼此
# 用的都是 `import database`、`from models import ...` 這種頂層寫法，
# 沒有改成套件相對匯入，所以在這裡把這幾個資料夾加進 sys.path，
# 讓底下所有 import 都能照舊找到，不用動任何一支被搬移檔案的內容。
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for _sub in ("routes", "models", "services"):
    _p = os.path.join(_BASE_DIR, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import uuid
import warnings

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException
from authlib.integrations.flask_client import OAuth
from google import genai
from google.genai import types
from PIL import Image
from rembg import remove

# ==========================================
# 🌟 環境變數與全域設定
# ==========================================
load_dotenv()
os.environ['ORT_LOGGING_LEVEL'] = '3'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
warnings.filterwarnings("ignore", category=FutureWarning)

# 🔧 [合併修正 1] 原本專案二 app.py 在「模組載入當下」就做
#     `if not GEMINI_API_KEY: raise ValueError(...)`，只要沒設環境變數，
#     整個 Flask app 直接無法啟動——不是只有 smart_assistant 這個功能壞掉，
#     而是連 /login、/analysis 這些完全不相關的頁面也一起打不開。
#     專案一的 ai_models.py / ai_style_routes.py 對同一件事的作法是
#     「沒設就印警告、該功能呼叫時才報錯」，讓其他功能可以正常運作。
#     這裡改成同樣的寬容作法，並且統一改用 ai_models.py / database.py
#     都在用的 GENAI_API_KEY 這個變數名稱（原本專案二用的是
#     GEMINI_API_KEY，兩個環境變數同時存在只會讓人搞混要設哪一個）。
GENAI_API_KEY = os.environ.get("GENAI_API_KEY", "").strip()
_smart_assistant_client = None
if GENAI_API_KEY:
    _smart_assistant_client = genai.Client(api_key=GENAI_API_KEY)
else:
    print(">> ⚠️ Warning: 未設定 GENAI_API_KEY，/api/smart_assistant 會回傳"
          "「AI 造型師暫時無法使用」，其餘功能不受影響。")

app = Flask(__name__)
CORS(app)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config["SESSION_COOKIE_SECURE"] = False
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# 🔧 [合併修正 2] 統一只用一個環境變數名稱 SECRET_KEY（原本專案一
#     web.py 用 SECRET_KEY、專案二 app.py 用 FLASK_SECRET_KEY，
#     兩者管的是同一件事——session cookie 簽章，包含商家登入、OAuth
#     登入、聊天室匿名 buyer_id 全部共用同一把金鑰，只能有一個名稱，
#     否則兩邊各自讀到不同的值，session 會忽好忽壞、難以除錯）。
#     保留專案二原本「沒設值就給警告」的提醒，但預設值不要一樣是
#     隨機值——隨機值代表每次重啟大家都要重新登入，開發階段體驗很差，
#     所以沿用專案一 web.py 的固定預設字串，正式上線前務必在 .env 覆蓋。
app.secret_key = os.environ.get("SECRET_KEY", "thesis_final_ultimate_v2026")
if not os.environ.get("SECRET_KEY"):
    print("⚠️ 沒有設定 SECRET_KEY 環境變數，session 用固定的開發用預設值簽章，"
          "正式上線前請在 .env 加一行固定的 SECRET_KEY=<隨便一段夠長的亂數字串>。")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
CC_LIBRARY_FOLDER = os.path.join(STATIC_FOLDER, 'cc_library')
CC_CATALOG_PATH = os.path.join(CC_LIBRARY_FOLDER, 'catalog.json')  # 遷移腳本讀這個當來源

# 🔧 [合併修正 3] UPLOAD_FOLDER 這個 app.config 鍵值，routes.py／
#     ai_models.py 裡有十幾處 `current_app.config['UPLOAD_FOLDER']`
#     直接在用（大頭照上傳、AI 分析用的臉部照片、body_tracking 照片…）。
#     專案二的 app.py 原型完全沒有設定這個鍵，代表在這次合併之前，
#     只要拿專案二的 app.py 單獨跑，這些上傳功能會直接 KeyError 500。
#     這裡照專案一 web.py 的設定補上，兩邊資料存在同一個
#     static/uploads 資料夾。
UPLOAD_FOLDER = os.path.join(STATIC_FOLDER, 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

for folder in [STATIC_FOLDER, CC_LIBRARY_FOLDER, UPLOAD_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)


# ==========================================
# 🔐 OAuth 註冊（原封不動搬自 web.py 的 _register_oauth）
# ==========================================
def _register_oauth(flask_app: Flask) -> OAuth:
    oauth_instance = OAuth(flask_app)

    oauth_instance.register(
        name='google',
        client_id=os.environ.get('GOOGLE_CLIENT_ID'),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )
    oauth_instance.register(
        name='line',
        client_id=os.environ.get('LINE_CLIENT_ID'),
        client_secret=os.environ.get('LINE_CLIENT_SECRET'),
        server_metadata_url='https://access.line.me/.well-known/openid-configuration',
        access_token_url='https://api.line.me/oauth2/v2.1/token',
        authorize_url='https://access.line.me/oauth2/v2.1/authorize',
        api_base_url='https://api.line.me/v2/',
        client_kwargs={'scope': 'profile'}
    )
    oauth_instance.register(
        name='facebook',
        client_id=os.environ.get('FACEBOOK_CLIENT_ID'),
        client_secret=os.environ.get('FACEBOOK_CLIENT_SECRET'),
        access_token_url='https://graph.facebook.com/v19.0/oauth/access_token',
        authorize_url='https://www.facebook.com/v19.0/dialog/oauth',
        api_base_url='https://graph.facebook.com/v19.0/',
        client_kwargs={'scope': 'public_profile'}
    )

    # 存入 app.extensions，routes.py 透過 current_app.extensions['oauth']
    # 這個 LocalProxy 動態取得同一個實例，兩邊不需要互相 import。
    flask_app.extensions['oauth'] = oauth_instance
    return oauth_instance


_register_oauth(app)


# ==========================================
# 🩹 防破圖 (Fallback Image) 全域保護網（原封不動搬自 web.py）
# ==========================================
_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
_PLACEHOLDER_IMAGE_PATH = os.path.join(STATIC_FOLDER, 'default_placeholder.png')


@app.errorhandler(404)
def handle_missing_static_image(err):
    path = request.path
    if path.startswith('/static/') and path.lower().endswith(_IMAGE_EXTENSIONS):
        if os.path.exists(_PLACEHOLDER_IMAGE_PATH):
            return send_file(_PLACEHOLDER_IMAGE_PATH, mimetype='image/png'), 200
    return err


# ==========================================
# 🛡️ 全域 /api/ 錯誤保護網（原封不動搬自 web.py）
# ==========================================
@app.errorhandler(Exception)
def handle_api_exception(err):
    if request.path.startswith('/api/'):
        app.logger.exception("未預期的 API 例外")
        status_code = err.code if isinstance(err, HTTPException) else 500
        return jsonify({"status": "error", "msg": str(err)}), status_code
    raise err


# ==========================================
# 🗄️ SQLAlchemy 初始化（app.db — Garment / 聊天室 / User / Store / Order）
# ==========================================
from models import db, Garment

os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)  # 🔧 [整理結構]
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
with app.app_context():
    # create_all() 只在資料表不存在時建立，不會動到既有資料，安全。
    db.create_all()

# ==========================================
# 🗄️ sqlite3 專家知識庫（style_system.db）：建表 / 自動補欄位
# ==========================================
# 🔧 [修復：/trends 頁面 500 錯誤 sqlite3.OperationalError: no such column]
# 這兩行原本只寫在檔案最下面的 `if __name__ == '__main__':` 區塊裡，只有
# 用「python app.py」直接執行這支檔案時才會跑到。只要是用 `flask run`、
# PyCharm 的 Flask 執行設定、或任何 WSGI 伺服器（gunicorn 等）啟動——這些
# 方式都是「匯入 app.py 這個模組」而不是把它當成主程式執行，模組最下面
# `if __name__ == '__main__':` 底下的程式碼完全不會被執行到，資料庫欄位
# 遷移（auto_migrate_db()）就永遠不會跑，既有的 style_system.db 檔案裡
# 自然不會有 trends 表的新欄位（source/location/prediction 等），一碰到
# 讀寫這些欄位的功能（例如 sync_ai_trends_to_db()）就會直接 500。
# 跟上面 SQLAlchemy 的 db.create_all() 用同一種寫法：搬到模組頂層、跟著
# app 一起初始化，不管用哪種方式啟動都保證會跑到一次。
from database import init_db, auto_migrate_db
try:
    init_db()
    _fixed_count = auto_migrate_db()
    print(f"✅ 資料庫檢查完成 (自動修補了 {_fixed_count} 個欄位)")
except Exception as _db_init_err:
    print(f"⚠️ 資料庫警告: {_db_init_err} (但不影響主程式啟動)")


# ==========================================
# 🆕 [新增：探索發現] 全站功能造訪紀錄
# ==========================================
# 在真正處理請求「之前」記錄這次造訪，只認登入使用者、只認
# resolve_feature_key_from_path() 認得出來的功能頁面（見 database.py
# 的 FEATURE_PAGES 清單，刻意排除訂單/歷史紀錄/賣家中心等後台管理
# 頁面），且只記 GET（避免表單送出、AJAX 輪詢等非「瀏覽頁面」的請求
# 也被誤算進造訪次數）。
# 整段包在 try/except 裡，任何原因都不能讓這個附加功能影響到使用者
# 原本要造訪的頁面。
from database import log_page_visit, resolve_feature_key_from_path
from flask import session as _flask_session


@app.before_request
def _track_feature_page_visit():
    try:
        if request.method != 'GET':
            return
        user_id = _flask_session.get('user_id')
        if not user_id:
            return
        feature_key = resolve_feature_key_from_path(request.path)
        if feature_key:
            log_page_visit(user_id, feature_key)
    except Exception as _track_err:
        print(f"⚠️ 功能造訪紀錄失敗（不影響頁面本身）: {_track_err}")


# ==========================================
# 📦 註冊 Blueprint：站台核心 + AI 穿搭建議
# ==========================================
from ai_style_routes import ai_style_bp
app.register_blueprint(ai_style_bp)

import routes
app.register_blueprint(routes.routes_bp)


# ==========================================
# 👗 AI 試穿 (ComfyUI FASHN VTON + IPAdapter) + 商家登入
# ==========================================
# register_ai_tryon_routes / register_merchant_auth_routes 是核心必要
# 功能（整個 2D 試穿系統跟商家登入都靠它們），這兩個匯入失敗就應該讓
# 程式直接中止、把完整錯誤訊息印出來，不要吞掉。
from ai_tryon_routes import register_ai_tryon_routes, register_merchant_auth_routes

# 🔧 [追查啟動警告 2026/09/05：cannot import name 'run_comfyui_garment_txt2img']
# 查證後這不是「版本不同步」，是這整段本來就是一套注定連不上的舊 code：
# - run_comfyui_garment_txt2img 在 ai_tryon_routes.py 裡實際名字是
#   run_comfyui_garment_txt2img_new_item（重新命名過沒同步回這裡）。
# - _classify_new_garment_prompt 在 ai_tryon_routes.py 裡是定義在
#   register_ai_tryon_routes() 內部的巢狀函式，本來就不是模組層級的
#   名字，不管怎麼改名都無法從外部 import 到。
# 而且下面 _smart_assistant_impl() 這個 fallback 實際上「從來沒有被
# 註冊過」：ai_tryon_routes.py 的 register_ai_tryon_routes() 一定先跑，
# 已經把 /api/smart_assistant 註冊掉，下面 `if 'smart_assistant' not in
# app.view_functions` 恆為假，這段 fallback 永遠被跳過。也就是說「AI
# 造型師自動生成缺件商品」這個功能其實一直是活的、正常運作的——只是
# 活在 ai_tryon_routes.py 自己的 /api/smart_assistant 實作裡（該函式
# 內部就有呼叫 run_comfyui_garment_txt2img_new_item() 生成缺件商品），
# 不是靠這裡這份匯入。這裡這個 try/except 純粹是每次啟動都會失敗、
# 印出嚇人警告訊息的死路，不影響任何實際功能，直接停用即可。
_AUTO_GENERATE_GARMENT_AVAILABLE = False
run_comfyui_garment_txt2img = None
_comfyui_output_dir = None
_classify_new_garment_prompt = None
_CATEGORY_LABELS = {}

register_ai_tryon_routes(app, STATIC_FOLDER)
register_merchant_auth_routes(app, STATIC_FOLDER)


# ==========================================
# 💬 買家↔商家聊天室
# ==========================================
from chat_routes import register_chat_routes

register_chat_routes(app, STATIC_FOLDER)


# ==========================================
# 🗂️ 服飾目錄（SQLite 版，來自專案二原型 app.py，維持不變）
# ==========================================
def search_cc_catalog(keywords, category=None):
    """關鍵字比對服飾庫，回傳 dict 清單。

    🔧 [0-1 修復：Garment / clothing_items 資料整合] 原本這裡只查
    Garment 表（來源 catalog.json），跟商城/賣家中心讀的 clothing_items
    完全是兩套資料，導致「AI造型師」推薦出來的單品在商城裡永遠找不到、
    買不到。改成查 clothing_items，同時允許 status='on_sale'（真人賣家）
    跟 status='system_catalog'（見 migrate_garment_to_clothing_items.py
    匯入的系統衣庫商品，取代原本的 Garment 資料源），這樣不管是系統款
    還是賣家自己上架的商品，都會被搜尋到、也都真的能在商城買到。
    """
    keywords_lower = [str(k).lower().strip() for k in keywords if str(k).strip()]
    if not keywords_lower:
        return []

    from database import get_db_connection

    sql = """SELECT id, image_path, title, category, tags, price, seller_id
              FROM clothing_items
              WHERE status IN ('on_sale', 'system_catalog')"""
    params = []
    if category:
        sql += " AND category = ?"
        params.append(category)

    conn = get_db_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    matches = []
    for row in rows:
        try:
            kw_list = json.loads(row["tags"]) if row["tags"] else []
        except (TypeError, ValueError):
            kw_list = []

        name_text = str(row["title"] or "").lower()
        kw_text = " ".join(str(k) for k in kw_list).lower()
        haystack = f"{name_text} {kw_text}"

        hit = False
        for k in keywords_lower:
            if k in haystack:
                hit = True
                break
            if any(k in seg or seg in k for seg in (name_text.split() + kw_list) if seg):
                hit = True
                break
        if hit:
            image_path = row["image_path"] or ""
            # image 欄位過去存的是相對 static/ 的路徑（不含開頭的
            # "static/"），這裡去掉前綴以維持跟原本 Garment.to_dict()
            # 一致的格式，避免下游 catalog_items_to_image_options() 等
            # 組出重複的 "static/static/..." 路徑。
            if image_path.startswith("static/"):
                image_path = image_path[len("static/"):]
            matches.append({
                "id": row["id"],
                "image": image_path,
                "name": row["title"],
                "category": row["category"],
                "source": "merchant" if row["seller_id"] else "system_catalog",
                "price": row["price"],
                "store_id": str(row["seller_id"]) if row["seller_id"] is not None else None,
                "keywords": kw_list,
            })

    return matches[:5]


def catalog_items_to_image_options(items):
    """把服飾庫的項目轉成前端期待的格式：image_url 取代原本的 model_url。"""
    options = []
    for item in items:
        image_path = item.get('image', '')
        if not image_path:
            continue
        options.append({
            'name': item.get('name', image_path),
            'image_url': f"/static/{image_path}",
            'category': item.get('category', 'top'),
        })
    return options


_OUTFIT_CATEGORY_LABELS = {
    "top": "上裝", "long_sleeve": "長袖", "bottom": "下裝", "one_piece": "連衣裙",
    "outerwear": "外套", "shoes": "鞋子", "wig": "假髮", "nails": "美甲",
    "jewelry": "珠寶/戒指/項鍊", "bag": "包包", "accessories_unsorted": "其他配件",
}
_OUTFIT_VALID_CATEGORIES = set(_OUTFIT_CATEGORY_LABELS.keys())
_OUTFIT_UPPER_BODY_CATEGORIES = {"top", "long_sleeve", "one_piece"}
_UPPER_BODY_FALLBACK_ORDER = ["one_piece", "long_sleeve", "top"]


def _auto_generate_garment(prompt_text, category):
    """文字描述 + 分類 -> ComfyUI txt2img 生成 -> rembg 去背 -> 存進
    cc_library + 寫進 Garment 資料庫。"""
    if not _AUTO_GENERATE_GARMENT_AVAILABLE:
        # 見上方合併修正 6：ai_tryon_routes.py 缺少對應函式時，這個功能
        # 整個停用，直接回傳 None，呼叫端（/api/smart_assistant）本來
        # 就有「沒生成成功就跳過/提示使用者」的處理，不會壞。
        return None
    try:
        output_filename = run_comfyui_garment_txt2img(prompt_text, category)
    except Exception as e:
        print(f"⚠️ AI 造型師自動生成失敗（{category}／{prompt_text!r}），略過這個項目: {e}")
        return None

    output_path = os.path.join(_comfyui_output_dir(), output_filename)
    if not os.path.exists(output_path):
        print(f"⚠️ AI 造型師自動生成：ComfyUI 輸出檔案不存在 {output_path}")
        return None

    try:
        source_image = Image.open(output_path)
        final_image = remove(source_image).convert("RGBA")
    except Exception as rembg_err:
        print(f"⚠️ 自動生成去背失敗，改存不去背版本: {rembg_err}")
        final_image = Image.open(output_path).convert("RGBA")

    final_dir = os.path.join(CC_LIBRARY_FOLDER, category)
    os.makedirs(final_dir, exist_ok=True)
    final_filename = f"{uuid.uuid4().hex}.png"
    final_image.save(os.path.join(final_dir, final_filename), "PNG")

    rel_image = f"cc_library/{category}/{final_filename}"
    garment = Garment(
        image=rel_image,
        name=prompt_text[:40] or _CATEGORY_LABELS.get(category, category),
        category=category,
        source=None,
        price=None,
        store_id=None,
        keywords=json.dumps([prompt_text], ensure_ascii=False),
    )
    db.session.add(garment)
    db.session.commit()

    return garment.to_dict()


GLOBAL_CHAT_HISTORY = []


def get_context_prompts(history):
    context_text = ""
    for chat in history:
        role_name = "使用者" if chat["role"] == "user" else "AI造型師"
        context_text += f"{role_name}: {chat['content']}\n"
    return context_text


# 🔧 [合併修正 7] 原本這裡是直接 @app.route('/api/get_cc_library', ...)。
# 你本機的 ai_tryon_routes.py 顯示它自己已經有一支同名的
# /api/get_cc_library（見 register_ai_tryon_routes(app, STATIC_FOLDER)
# 執行時的行為，那個時間點比這裡早），兩支直接撞在一起會讓 Flask 在
# 啟動當下直接 AssertionError 中止整個程式。
#
# 改成「先檢查 app.view_functions 裡有沒有同名端點，沒有才註冊」：
# 如果 ai_tryon_routes.py 那份已經提供了這支 API（很可能是功能更完整、
# 跟你本機其他程式碼更同步的版本），這裡就自動讓路，不會二次註冊；
# 如果沒有（例如未來 ai_tryon_routes.py 拿掉了這支），這裡的版本照常
# 生效當備援，行為不會因為哪一份 ai_tryon_routes.py 在跑而不同。
def _get_cc_library_impl():
    """支援 ?category=&page=&limit= 分頁/分類篩選。"""
    try:
        query = Garment.query
        category = request.args.get('category', '').strip()
        if category:
            query = query.filter_by(category=category)

        owner = request.args.get('owner', '').strip().lower()
        if owner == 'store':
            query = query.filter_by(source='merchant')
        elif owner == 'mine':
            query = query.filter(db.or_(Garment.source.is_(None), Garment.source != 'merchant'))

        query = query.order_by(Garment.id)

        page_param = request.args.get('page')
        limit_param = request.args.get('limit')

        if page_param is None and limit_param is None:
            items = [g.to_dict() for g in query.all()]
            return jsonify({'success': True, 'count': len(items), 'items': items})

        try:
            page = max(int(page_param or 1), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            limit = min(max(int(limit_param or 20), 1), 100)
        except (TypeError, ValueError):
            limit = 20

        pagination = query.paginate(page=page, per_page=limit, error_out=False)
        items = [g.to_dict() for g in pagination.items]

        return jsonify({
            'success': True,
            'count': pagination.total,
            'items': items,
            'page': page,
            'limit': limit,
            'has_more': pagination.has_next,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'items': []}), 500


if 'get_cc_library' not in app.view_functions:
    app.add_url_rule('/api/get_cc_library', 'get_cc_library', _get_cc_library_impl, methods=['GET'])
else:
    print("ℹ️ /api/get_cc_library 已經由 ai_tryon_routes.py 註冊過，"
          "app.py 內建的備用版本自動略過，不會造成衝突。")


def _smart_assistant_impl():
    """Gemini 語意搜尋衣櫃，回傳 image_url 選項清單。"""
    global GLOBAL_CHAT_HISTORY

    # 🔧 [合併修正 1 的延伸] Client 沒初始化成功時，回傳正常的 JSON 錯誤，
    #     不會讓整支路由丟出 NameError（原本 client 是模組層級變數，
    #     沒有 API Key 時模組根本載入不起來，這個問題不會發生；
    #     但現在允許沒有 Key 也能開站，這裡要補上對應的防呆）。
    if _smart_assistant_client is None:
        return jsonify({
            'status': 'error',
            'message': 'AI 造型師暫時無法使用（未設定 GENAI_API_KEY）。',
        }), 503

    try:
        data = request.json
        user_prompt = data.get('prompt', '').strip()

        if not user_prompt:
            return jsonify({'status': 'error', 'message': '請輸入指令'})

        if user_prompt in ["重置", "reset", "清除"]:
            GLOBAL_CHAT_HISTORY = []
            return jsonify({'status': 'success', 'reply_message': '🔄 已為您重置對話歷史紀錄。',
                            'actions': {"update_options": False, "image_options": []}})

        history_context = get_context_prompts(GLOBAL_CHAT_HISTORY)

        sys_prompt = f"""
        你是一個 2D AI 試穿間的造型顧問。

        分類清單(只能使用這些英文值，前面中文是給你參考對應意思用)：
        top(上裝) / long_sleeve(長袖) / bottom(下裝) / one_piece(連衣裙) /
        outerwear(外套) / shoes(鞋子) / wig(假髮) / nails(美甲) /
        jewelry(珠寶/戒指/項鍊) / bag(包包) / accessories_unsorted(其他配件)

        請先判斷使用者是「單一單品搜尋」還是「整套穿搭需求」：
        - 單一單品搜尋：明確指定一件特定的東西，例如「黑色皮衣」「牛仔外套」「高跟鞋」。
        - 整套穿搭需求：描述一個場合/風格，希望你幫忙配一整套，例如
          「約會穿搭」「上班穿搭」「幫我配一套」「龐克風全身造型」。

        請以 JSON 格式輸出：
        - "reply": (字串) 用親切的繁體中文回覆。如果是整套穿搭，順便用一兩句話
          說明你選的配色/風格邏輯(例如「用米白+咖啡色營造溫柔約會感，鞋子選
          裸色跟鞋拉長比例」)，讓使用者知道這幾件為什麼搭在一起。
        - "is_outfit_request": (布林值) 是否為整套穿搭需求。
        - "outfit_plan": (陣列，只有 is_outfit_request=true 時才需要填，
          否則給空陣列) 每個元素是 {{"category": 分類清單裡的其中一個英文值,
          "keywords": ["繁體中文關鍵字1","關鍵字2","關鍵字3"]}}。規則：
            1. top / long_sleeve / one_piece 這三個屬於同一個身體部位(上半身)，
               三選一就好，不要同時挑兩個，不然套用到模特兒身上會變成
               同時穿兩件上身衣物的矛盾組合。
            2. 如果選了 one_piece(連衣裙)，就不要再另外選 bottom(下裝)，
               連衣裙本身已經涵蓋下半身。
            3. 預設至少要包含「上半身(three選一) + bottom(除非選了 one_piece)
               + shoes」，讓使用者一次拿到一套完整（不只上半身）的穿搭；
               視場合可以再加 outerwear / bag / jewelry，但不用每次都塞滿
               全部 11 個分類——不合場合硬湊的配件(例如日常通勤穿搭硬加美甲)
               只會讓整套看起來不協調。
            4. 每個 category 的 keywords 要反映「同一套穿搭」統一的配色/材質/
               風格語彙(例如都圍繞「米白、奶茶色、簡約」或都圍繞「黑色、
               皮革、龐克」)，不要每個分類各自天馬行空、互不相關。
        - "search_keywords": (陣列，is_outfit_request=false 時使用，
          is_outfit_request=true 時給空陣列即可) 3-6 個「繁體中文」關鍵字，
          用來比對資料庫的 keywords（資料庫內容全部是中文，不要用英文）。
        """

        full_input_payload = f"【過去對話歷史】:\n{history_context}\n【當前使用者最新指令】: {user_prompt}"

        gemini_result = None
        try:
            gemini_response = _smart_assistant_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"系統提示:\n{sys_prompt}\n\n{full_input_payload}",
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            gemini_result = json.loads(gemini_response.text)
        except Exception as e:
            print(f"⚠️ Gemini 呼叫失敗，退回關鍵字比對: {e}")
            gemini_result = {"reply": "為您搜尋合適的服裝！", "search_keywords": user_prompt.split(),
                              "is_outfit_request": False, "outfit_plan": []}

        GLOBAL_CHAT_HISTORY.append({"role": "user", "content": user_prompt})
        GLOBAL_CHAT_HISTORY.append({"role": "assistant", "content": gemini_result.get("reply", "")})
        if len(GLOBAL_CHAT_HISTORY) > 12:
            GLOBAL_CHAT_HISTORY = GLOBAL_CHAT_HISTORY[-12:]

        is_outfit_request = bool(gemini_result.get("is_outfit_request"))
        outfit_plan = gemini_result.get("outfit_plan") or []
        cleaned_plan = []
        seen_upper_body = False
        for slot in outfit_plan:
            if not isinstance(slot, dict):
                continue
            slot_category = str(slot.get("category", "")).strip()
            slot_keywords = slot.get("keywords") or []
            if slot_category not in _OUTFIT_VALID_CATEGORIES or not slot_keywords:
                continue
            if slot_category in _OUTFIT_UPPER_BODY_CATEGORIES:
                if seen_upper_body:
                    continue
                seen_upper_body = True
            cleaned_plan.append({"category": slot_category, "keywords": slot_keywords})

        outfit_mode = is_outfit_request and bool(cleaned_plan)

        missing_labels = []
        auto_generated_labels = []

        if outfit_mode:
            combined_items = []
            seen_images = set()
            for slot in cleaned_plan:
                slot_matches = search_cc_catalog(slot["keywords"], category=slot["category"])

                if not slot_matches and slot["category"] in _OUTFIT_UPPER_BODY_CATEGORIES:
                    for fallback_cat in _UPPER_BODY_FALLBACK_ORDER:
                        if fallback_cat == slot["category"]:
                            continue
                        slot_matches = search_cc_catalog(slot["keywords"], category=fallback_cat)
                        if slot_matches:
                            break

                if not slot_matches:
                    generated_item = _auto_generate_garment(
                        "、".join(slot["keywords"]), slot["category"]
                    )
                    if generated_item:
                        slot_matches = [generated_item]
                        auto_generated_labels.append(
                            _OUTFIT_CATEGORY_LABELS.get(slot["category"], slot["category"])
                        )

                if not slot_matches:
                    missing_labels.append(_OUTFIT_CATEGORY_LABELS.get(slot["category"], slot["category"]))
                    continue

                for m in slot_matches[:2]:
                    img = m.get("image")
                    if not img or img in seen_images:
                        continue
                    seen_images.add(img)
                    combined_items.append(m)
            matches = combined_items
        else:
            keywords = gemini_result.get("search_keywords", [])
            combined_keywords = list(dict.fromkeys(keywords + [user_prompt]))
            matches = search_cc_catalog(combined_keywords)

            if not matches and _AUTO_GENERATE_GARMENT_AVAILABLE:
                gen_category = _classify_new_garment_prompt(user_prompt)
                generated_item = _auto_generate_garment(user_prompt, gen_category)
                if generated_item:
                    matches = [generated_item]
                    auto_generated_labels.append(
                        _CATEGORY_LABELS.get(gen_category, gen_category)
                    )

        response_data = {
            "status": "success",
            "reply_message": gemini_result.get("reply", "好的，為您搜尋合適的服裝！"),
            "actions": {
                "update_options": bool(matches),
                "image_options": catalog_items_to_image_options(matches),
                "outfit_mode": outfit_mode,
                "auto_generated": bool(auto_generated_labels),
            }
        }
        if outfit_mode and missing_labels:
            response_data["reply_message"] += (
                f"\n(小提醒：目前服飾庫還沒有「{ '、'.join(missing_labels) }」的商品，"
                f"AI 現場生成也沒有成功，這幾類先幫您跳過了，其餘單品仍可以直接試穿。)"
            )
        if auto_generated_labels:
            response_data["reply_message"] += (
                f"\n(圖庫裡還沒有「{ '、'.join(auto_generated_labels) }」符合的商品，"
                f"已經幫您現場 AI 生成一件，可以直接試穿看看！)"
            )
        if not matches:
            response_data["reply_message"] += "\n(目前資料庫裡還沒有符合的服飾照片，AI 生成暫時也沒有成功，請稍後再試一次。)"

        return jsonify(response_data)

    except Exception as e:
        print(f"❌ 智能助理出錯: {str(e)}")
        return jsonify({'status': 'error', 'message': f"系統繁忙中: {str(e)}"}), 500


# 🔧 [合併修正 7 的延伸] 同樣的防呆：萬一你本機的 ai_tryon_routes.py
# 也已經有自己的 /api/smart_assistant，這裡一樣自動讓路，不會讓整個
# app.py 因為撞名而啟動失敗。
if 'smart_assistant' not in app.view_functions:
    app.add_url_rule('/api/smart_assistant', 'smart_assistant', _smart_assistant_impl, methods=['POST'])
else:
    print("ℹ️ /api/smart_assistant 已經由 ai_tryon_routes.py 註冊過，"
          "app.py 內建的備用版本自動略過，不會造成衝突。")


# 🔧 [合併修正 4] 原本專案二 app.py 這裡還有：
#
#     @app.route('/')
#     def index():
#         return render_template('ai_tryon.html')
#
#     @app.route('/static/<path:filename>')
#     def serve_static(filename):
#         return send_from_directory(STATIC_FOLDER, filename)
#
#   兩支都【刻意拿掉】，不是遺漏：
#   - routes.py 的 routes_bp 已經有自己的 `/`（render_template('index.html')，
#     真正的網站首頁），跟這裡的 `/` 是同一個 URL 路徑對兩支不同函式，
#     同時註冊在同一個 Flask app 上時行為不可預期（哪一個生效取決於
#     註冊順序，換一個 Flask/Werkzeug 版本就可能整個反過來）。
#     ai_tryon.html 依然可以正常訪問，只是要走 routes.py 既有的路徑
#     （例如 /try_on 或前端選單導過去的實際路徑），不是網站根目錄。
#   - `/static/<path:filename>` 這支自訂函式做的事情，跟 Flask 內建、
#     app 建立時就自動註冊好的靜態檔案路由完全一樣（Flask 預設就會用
#     `static_folder` 這個參數服務 /static/ 底下的檔案），多寫一支只是
#     重複定義同一條路由，一樣會撞名。
def _index_placeholder_removed():
    """（保留這個函式只是為了讓上面那段合併說明有個掛靠位置，
    不會被任何路由呼叫，可以直接刪除。）"""
    return None


if __name__ == '__main__':
    # [同一個修復] 資料庫初始化/遷移已經搬到檔案上方、跟著模組載入時
    # 就一定會執行一次（見上面「🗄️ sqlite3 專家知識庫」區塊），這裡不用
    # 再重複做一次，只保留印出啟動訊息、真正啟動伺服器的部分。
    print("------------------------------------------------")
    print("🚀 StyleAI System 正在啟動...")

    # 🔧 [合併修正 5] port 預設值沿用專案二原型的 8000（避免跟你既有
    #     前端/文件裡可能寫死的 8000 對不上），但改成可用 PORT 環境變數
    #     覆蓋，跟 web.py 原本的彈性一致。
    port = int(os.environ.get("PORT", 8000))
    debug_mode = os.environ.get("FLASK_DEBUG", "True").lower() == "true"

    print("------------------------------------------------")
    print(f"👉 請開啟瀏覽器訪問: http://127.0.0.1:{port}")
    print(f"👉 管理員後台入口:   http://127.0.0.1:{port}/admin")
    print("------------------------------------------------")
    # threaded=True：長時間的 ComfyUI 生成請求(AI 造型師自動生成、
    # /api/ai_generate_garment)不會擋住其他請求，見專案二原本的說明。
    app.run(host='0.0.0.0', port=port, debug=debug_mode, use_reloader=True, threaded=True)