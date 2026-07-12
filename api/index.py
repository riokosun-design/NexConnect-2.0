import os
import random
import string
import json
from datetime import datetime
from flask import Flask, request, jsonify, redirect, send_from_directory
from flask_cors import CORS
from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash

# Fix Vercel Path Issue
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

# Initialize Flask
app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=os.path.join(TEMPLATES_DIR, 'static'))
CORS(app)

# Supabase Init
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- HELPER FUNCTIONS ---
def get_current_user():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        return None
    try:
        res = supabase.auth.get_user(token)
        return res.user
    except:
        return None

def generate_ref_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def generate_short_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# --- FRONTEND SERVING ---
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    if path.startswith('api/') or path.startswith('go/'):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(TEMPLATES_DIR, 'index.html')

# --- CONFIG ENDPOINT ---
@app.route('/api/config')
def config():
    return jsonify({
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY")
    })

# --- AUTH ENDPOINTS ---
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    email = user.email
    ref_code = data.get('referral_code')
    
    # Check Admin Lock
    lock_res = supabase.table('admin_lock').select('*').eq('id', 1).execute()
    lock = lock_res.data[0] if lock_res.data else None
    
    if not lock:
        supabase.table('admin_lock').insert({"id": 1, "admin_count": 0}).execute()
        lock = {"admin_count": 0}
    
    role = 'normal_user'
    if lock['admin_count'] < 2:
        role = 'admin'
        new_count = lock['admin_count'] + 1
        update_data = {"admin_count": new_count}
        if new_count == 2:
            update_data["locked_at"] = datetime.utcnow().isoformat()
        supabase.table('admin_lock').update(update_data).eq('id', 1).execute()
    
    # Referral mapping
    referred_by = None
    if ref_code:
        ref_res = supabase.table('users').select('id').eq('referral_code', ref_code).execute()
        if ref_res.data:
            referred_by = ref_res.data[0]['id']
    
    # Insert user
    new_user = {
        "id": user.id,
        "email": email,
        "role": role,
        "referral_code": generate_ref_code(),
        "referred_by": referred_by
    }
    supabase.table('users').insert(new_user).execute()
    
    return jsonify({"message": "User registered", "user": new_user}), 201

@app.route('/api/auth/sync', methods=['POST'])
def sync_user():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    res = supabase.table('users').select('*').eq('id', user.id).execute()
    if res.data:
        return jsonify(res.data[0]), 200
    
    # OAuth user bypassed /register endpoint, create profile now
    lock_res = supabase.table('admin_lock').select('*').eq('id', 1).execute()
    lock = lock_res.data[0] if lock_res.data else None
    role = 'normal_user'
    
    if lock and lock['admin_count'] < 2:
        role = 'admin'
        new_count = lock['admin_count'] + 1
        update_data = {"admin_count": new_count}
        if new_count == 2:
            update_data["locked_at"] = datetime.utcnow().isoformat()
        supabase.table('admin_lock').update(update_data).eq('id', 1).execute()
    
    new_user = {
        "id": user.id,
        "email": user.email,
        "role": role,
        "referral_code": generate_ref_code()
    }
    supabase.table('users').insert(new_user).execute()
    return jsonify(new_user), 201

# --- USER ROUTES ---
@app.route('/api/users/profile', methods=['GET', 'PUT'])
def user_profile():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    if request.method == 'GET':
        res = supabase.table('users').select('*').eq('id', user.id).execute()
        if not res.data:
            return jsonify({"error": "Profile not found"}), 404
        return jsonify(res.data[0])
    
    elif request.method == 'PUT':
        data = request.json
        allowed_fields = ['full_name', 'role', 'youtube_link', 'instagram_link', 'tiktok_link', 'follower_count']
        update_data = {k: v for k, v in data.items() if k in allowed_fields}
        
        res = supabase.table('users').update(update_data).eq('id', user.id).execute()
        return jsonify(res.data[0])

@app.route('/api/users/leaderboard')
def leaderboard():
    res = supabase.table('users').select('id, email, total_earnings, tier').order('total_earnings', desc=True).limit(10).execute()
    return jsonify(res.data)

# --- PRODUCT ROUTES ---
@app.route('/api/products')
def get_products():
    query = supabase.table('products').select('*').eq('stock_status', 'active')
    
    search = request.args.get('search')
    category = request.args.get('category')
    
    if search:
        query = query.ilike('title', f'%{search}%')
    if category:
        query = query.eq('category', category)
        
    res = query.execute()
    return jsonify(res.data)

@app.route('/api/products/<product_id>')
def get_product(product_id):
    res = supabase.table('products').select('*').eq('id', product_id).execute()
    if not res.data:
        return jsonify({"error": "Product not found"}), 404
    return jsonify(res.data[0])

@app.route('/api/products/<product_id>/similar')
def get_similar(product_id):
    p_res = supabase.table('products').select('category, tags').eq('id', product_id).execute()
    if not p_res.data:
        return jsonify([])
    p = p_res.data[0]
    
    res = supabase.table('products').select('*').eq('category', p['category']).neq('id', product_id).limit(4).execute()
    return jsonify(res.data)

# --- LINK ROUTES ---
@app.route('/api/links/generate', methods=['POST'])
def generate_link():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    product_id = data.get('product_id')
    
    # Check existing link
    existing = supabase.table('short_links').select('*').eq('user_id', user.id).eq('product_id', product_id).execute()
    if existing.data:
        return jsonify({"short_code": existing.data[0]['short_code']})
    
    short_code = f"{generate_short_code()}-{user.id[:4]}"
    new_link = {
        "user_id": user.id,
        "product_id": product_id,
        "short_code": short_code
    }
    supabase.table('short_links').insert(new_link).execute()
    return jsonify({"short_code": short_code}), 201

@app.route('/go/<short_code>')
def redirect_link(short_code):
    res = supabase.table('short_links').select('*, products(*)').eq('short_code', short_code).execute()
    if not res.data:
        return jsonify({"error": "Invalid link"}), 404
    
    link_data = res.data[0]
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
    
    # Log click
    supabase.table('clicks').insert({
        "user_id": link_data['user_id'],
        "product_id": link_data['product_id'],
        "short_code": short_code,
        "ip_address": ip_address
    }).execute()
    
    return redirect(link_data['products']['super_link'])

# --- CLICK ROUTES ---
@app.route('/api/clicks')
def get_clicks():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    res = supabase.table('clicks').select('*, products(title)').eq('user_id', user.id).order('created_at', desc=True).execute()
    return jsonify(res.data)

@app.route('/api/clicks/stats')
def click_stats():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    clicks_res = supabase.table('clicks').select('id, created_at, product_id').eq('user_id', user.id).execute()
    clicks = clicks_res.data
    
    total_clicks = len(clicks)
    
    # Mock weekly data for chart
    weekly_data = [
        {"date": "Mon", "count": random.randint(0, 10)},
        {"date": "Tue", "count": random.randint(0, 10)},
        {"date": "Wed", "count": random.randint(0, 10)},
        {"date": "Thu", "count": random.randint(0, 10)},
        {"date": "Fri", "count": random.randint(0, 10)},
        {"date": "Sat", "count": random.randint(0, 10)},
        {"date": "Sun", "count": random.randint(0, 10)},
    ]
    
    # Top product
    top_product = None
    if clicks:
        product_counts = {}
        for c in clicks:
            pid = c['product_id']
            product_counts[pid] = product_counts.get(pid, 0) + 1
        top_pid = max(product_counts, key=product_counts.get)
        p_res = supabase.table('products').select('title, image_url').eq('id', top_pid).execute()
        if p_res.data:
            top_product = p_res.data[0]
            top_product['clicks'] = product_counts[top_pid]
    
    return jsonify({
        "total_clicks": total_clicks,
        "conversion_rate": 2.5, # Mocked
        "weekly_data": weekly_data,
        "top_product": top_product
    })

# --- WITHDRAWAL ROUTES ---
@app.route('/api/withdrawals', methods=['GET', 'POST'])
def withdrawals():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    if request.method == 'GET':
        res = supabase.table('withdrawals').select('*').eq('user_id', user.id).order('created_at', desc=True).execute()
        return jsonify(res.data)
    
    elif request.method == 'POST':
        data = request.json
        amount = float(data.get('amount'))
        
        profile = supabase.table('users').select('balance').eq('id', user.id).execute().data[0]
        settings = supabase.table('settings').select('min_withdrawal').eq('id', 1).execute().data
        min_w = settings[0]['min_withdrawal'] if settings else 100.00
        
        if amount > profile['balance']:
            return jsonify({"error": "Insufficient balance"}), 400
        if amount < min_w:
            return jsonify({"error": f"Minimum withdrawal is {min_w}"}), 400
        
        # Deduct balance
        supabase.table('users').update({"balance": profile['balance'] - amount}).eq('id', user.id).execute()
        
        new_w = {
            "user_id": user.id,
            "amount": amount,
            "method": data.get('method'),
            "bank_ifsc": data.get('bank_ifsc'),
            "bank_account": data.get('bank_account'),
            "bank_holder": data.get('bank_holder'),
            "upi_id": data.get('upi_id'),
            "status": "pending"
        }
        res = supabase.table('withdrawals').insert(new_w).execute()
        return jsonify(res.data[0]), 201

# --- REFERRAL ROUTES ---
@app.route('/api/referrals')
def referrals():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    users_res = supabase.table('users').select('email, created_at').eq('referred_by', user.id).execute()
    earn_res = supabase.table('referral_earnings').select('amount').eq('referrer_id', user.id).execute()
    
    total_earned = sum(e['amount'] for e in earn_res.data)
    
    return jsonify({
        "users": users_res.data,
        "stats": {
            "total_referred": len(users_res.data),
            "total_earned": total_earned
        }
    })

# --- BOOKMARK ROUTES ---
@app.route('/api/bookmarks', methods=['GET', 'POST', 'DELETE'])
def bookmarks():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    if request.method == 'GET':
        res = supabase.table('bookmarks').select('*, products(*)').eq('user_id', user.id).execute()
        return jsonify(res.data)
    
    elif request.method == 'POST':
        data = request.json
        new_b = {"user_id": user.id, "product_id": data.get('product_id')}
        try:
            res = supabase.table('bookmarks').insert(new_b).execute()
            return jsonify(res.data[0]), 201
        except:
            return jsonify({"error": "Already bookmarked"}), 400
            
    elif request.method == 'DELETE':
        bm_id = request.args.get('id')
        supabase.table('bookmarks').delete().eq('id', bm_id).eq('user_id', user.id).execute()
        return jsonify({"message": "Deleted"})

# --- SUPPORT ROUTES ---
@app.route('/api/support', methods=['GET', 'POST'])
def support():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    if request.method == 'GET':
        res = supabase.table('support_messages').select('*').eq('user_id', user.id).order('created_at', desc=True).execute()
        return jsonify(res.data)
    
    elif request.method == 'POST':
        data = request.json
        new_m = {"user_id": user.id, "message": data.get('message')}
        res = supabase.table('support_messages').insert(new_m).execute()
        return jsonify(res.data[0]), 201

# --- NOTIFICATION ROUTES ---
@app.route('/api/notifications')
def notifications():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    res = supabase.table('notifications').select('*').eq('user_id', user.id).order('created_at', desc=True).execute()
    return jsonify(res.data)

@app.route('/api/notifications/<notif_id>/read', methods=['PUT'])
def mark_read(notif_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    
    supabase.table('notifications').update({"is_read": True}).eq('id', notif_id).eq('user_id', user.id).execute()
    return jsonify({"message": "Marked as read"})

# --- SETTINGS ROUTES ---
@app.route('/api/settings')
def settings():
    res = supabase.table('settings').select('*').eq('id', 1).execute()
    if not res.data:
        return jsonify({
            "hero_banner_url": "",
            "hero_title": "Connect. Create. Earn.",
            "hero_subtitle": "Join NexConnect today",
            "min_withdrawal": 100.00,
            "support_email": "support@nexconnect.com"
        })
    return jsonify(res.data[0])

# Vercel Handler
if __name__ == "__main__":
    app.run(debug=True)
