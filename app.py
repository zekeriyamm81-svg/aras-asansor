import os
import sqlite3
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify, Response
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('ARAS_DB_PATH', BASE_DIR / 'aras_asansor.db'))
UPLOAD_DIR = Path(os.getenv('ARAS_UPLOAD_DIR', BASE_DIR / 'uploads'))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
ALLOWED_EXT = {'jpg','jpeg','png','webp','heic','heif','mp4','mov','m4v','webm'}
MAX_UPLOAD_MB = 120
app = Flask(__name__)
app.secret_key = os.getenv('ARAS_SECRET_KEY', 'change-this-secret-in-production')
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_MB * 1024 * 1024
ADMIN_USER = os.getenv('ARAS_ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ARAS_ADMIN_PASSWORD', '123456')
PHONE_NUMBER = os.getenv('ARAS_PHONE_NUMBER', '0541 870 50 01')
WHATSAPP_NUMBER = os.getenv('ARAS_WHATSAPP_NUMBER', '905418705001')

def db():
    con=sqlite3.connect(DB_PATH); con.row_factory=sqlite3.Row; con.execute('PRAGMA foreign_keys=ON'); return con

def ensure_column(con, table, col, ddl):
    cols={r['name'] for r in con.execute(f'PRAGMA table_info({table})').fetchall()}
    if col not in cols: con.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')

def init_db():
    con=db(); con.executescript('''
    CREATE TABLE IF NOT EXISTS quotes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
      name TEXT NOT NULL, phone TEXT NOT NULL, whatsapp TEXT, from_district TEXT NOT NULL, from_neighborhood TEXT,
      from_floor TEXT, from_elevator TEXT, to_district TEXT NOT NULL, to_neighborhood TEXT, to_floor TEXT,
      to_elevator TEXT, home_type TEXT, moving_date TEXT, notes TEXT, status TEXT NOT NULL DEFAULT 'Yeni Talep',
      offered_price TEXT, admin_note TEXT
    );
    CREATE TABLE IF NOT EXISTS quote_files (
      id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id INTEGER NOT NULL, filename TEXT NOT NULL,
      original_name TEXT, kind TEXT, FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS reviews (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL, rating INTEGER NOT NULL,
      comment TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0
    );''')
    ensure_column(con,'quotes','customer_token','TEXT')
    # existing quotes get private share tokens
    for r in con.execute("SELECT id FROM quotes WHERE customer_token IS NULL OR customer_token='' ").fetchall():
        con.execute('UPDATE quotes SET customer_token=? WHERE id=?',(uuid.uuid4().hex,r['id']))
    con.commit(); con.close()
init_db()

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not session.get('admin'): return redirect(url_for('admin_login'))
        return fn(*a,**kw)
    return wrapper

def valid_file(name): return '.' in name and name.rsplit('.',1)[1].lower() in ALLOWED_EXT

def save_files(files,qid,kind):
    con=db()
    for f in files:
        if not f or not f.filename or not valid_file(f.filename): continue
        ext=f.filename.rsplit('.',1)[1].lower(); filename=f'{qid}_{uuid.uuid4().hex[:12]}.{ext}'
        f.save(UPLOAD_DIR/filename)
        con.execute('INSERT INTO quote_files(quote_id,filename,original_name,kind) VALUES(?,?,?,?)',(qid,filename,secure_filename(f.filename),kind))
    con.commit(); con.close()

def normalize_wa(v):
    n=''.join(ch for ch in (v or '') if ch.isdigit())
    if n.startswith('0'): n='90'+n[1:]
    elif len(n)==10: n='90'+n
    return n

@app.get('/')
def home():
    con=db(); reviews=con.execute('SELECT * FROM reviews WHERE approved=1 ORDER BY id DESC LIMIT 8').fetchall(); con.close()
    return render_template('index.html', whatsapp=WHATSAPP_NUMBER, phone=PHONE_NUMBER, reviews=reviews)

@app.post('/teklif')
def create_quote():
    name=request.form.get('name','').strip(); phone=request.form.get('phone','').strip()
    fr=request.form.get('from_district','').strip(); to=request.form.get('to_district','').strip()
    if not all([name,phone,fr,to]):
        flash('Lütfen ad, telefon, nereden ve nereye alanlarını doldurun.','error'); return redirect(url_for('home')+'#fiyat-al')
    public_id='ARAS-'+datetime.now().strftime('%y%m%d')+'-'+uuid.uuid4().hex[:6].upper(); token=uuid.uuid4().hex
    con=db(); cur=con.execute('''INSERT INTO quotes(public_id,customer_token,created_at,name,phone,whatsapp,from_district,from_neighborhood,from_floor,from_elevator,to_district,to_neighborhood,to_floor,to_elevator,home_type,moving_date,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
      public_id,token,datetime.now().strftime('%Y-%m-%d %H:%M'),name,phone,request.form.get('whatsapp','').strip(),fr,
      request.form.get('from_neighborhood','').strip(),request.form.get('from_floor','').strip(),request.form.get('from_elevator','').strip(),to,
      request.form.get('to_neighborhood','').strip(),request.form.get('to_floor','').strip(),request.form.get('to_elevator','').strip(),request.form.get('home_type','').strip(),request.form.get('moving_date','').strip(),request.form.get('notes','').strip()))
    qid=cur.lastrowid; con.commit(); con.close()
    save_files(request.files.getlist('building_photos'),qid,'Bina'); save_files(request.files.getlist('item_photos'),qid,'Eşya')
    return redirect(url_for('success',ref=public_id))

@app.post('/yorum')
def add_review():
    name=request.form.get('name','').strip(); comment=request.form.get('comment','').strip()
    try: rating=max(1,min(5,int(request.form.get('rating','5'))))
    except: rating=5
    if len(name)<2 or len(comment)<8:
        flash('Yorum için adınızı ve görüşünüzü yazın.','error'); return redirect(url_for('home')+'#yorumlar')
    con=db(); con.execute('INSERT INTO reviews(created_at,name,rating,comment,approved) VALUES(?,?,?,?,0)',(datetime.now().strftime('%Y-%m-%d %H:%M'),name,rating,comment)); con.commit(); con.close()
    flash('Teşekkürler. Yorumunuz onaylandıktan sonra yayınlanacak.','ok'); return redirect(url_for('home')+'#yorumlar')

@app.get('/talep-alindi')
def success(): return render_template('success.html',ref=request.args.get('ref',''),whatsapp=WHATSAPP_NUMBER,phone=PHONE_NUMBER)

@app.get('/teklifim/<public_id>/<token>')
def customer_offer(public_id,token):
    con=db(); q=con.execute('SELECT * FROM quotes WHERE public_id=? AND customer_token=?',(public_id,token)).fetchone(); con.close()
    if not q: return 'Teklif bulunamadı',404
    return render_template('customer_offer.html',q=q,phone=PHONE_NUMBER,whatsapp=WHATSAPP_NUMBER)

@app.get('/uploads/<path:name>')
@admin_required
def uploaded_file(name): return send_from_directory(UPLOAD_DIR,name)

@app.route('/admin/giris',methods=['GET','POST'])
def admin_login():
    if request.method=='POST':
        if request.form.get('username')==ADMIN_USER and request.form.get('password')==ADMIN_PASSWORD:
            session['admin']=True; return redirect(url_for('admin_dashboard'))
        flash('Kullanıcı adı veya şifre yanlış.','error')
    return render_template('admin_login.html')
@app.get('/admin/cikis')
def admin_logout(): session.clear(); return redirect(url_for('admin_login'))

@app.get('/admin')
@admin_required
def admin_dashboard():
    status=request.args.get('status','').strip(); con=db()
    rows=con.execute('SELECT * FROM quotes '+('WHERE status=? ' if status else '')+'ORDER BY id DESC',((status,) if status else ())).fetchall()
    counts={r['status']:r['n'] for r in con.execute('SELECT status,COUNT(*) n FROM quotes GROUP BY status').fetchall()}
    reviews=con.execute('SELECT * FROM reviews ORDER BY approved ASC,id DESC LIMIT 30').fetchall(); con.close()
    return render_template('admin_dashboard.html',quotes=rows,counts=counts,current_status=status,reviews=reviews)

@app.route('/admin/talep/<int:qid>',methods=['GET','POST'])
@admin_required
def admin_quote(qid):
    con=db()
    if request.method=='POST':
        con.execute('UPDATE quotes SET status=?,offered_price=?,admin_note=? WHERE id=?',(request.form.get('status','Yeni Talep'),request.form.get('offered_price','').strip(),request.form.get('admin_note','').strip(),qid)); con.commit(); flash('Teklif kaydedildi.','ok')
    q=con.execute('SELECT * FROM quotes WHERE id=?',(qid,)).fetchone(); files=con.execute('SELECT * FROM quote_files WHERE quote_id=? ORDER BY id',(qid,)).fetchall(); con.close()
    if not q:return 'Talep bulunamadı',404
    share_url=request.url_root.rstrip('/')+url_for('customer_offer',public_id=q['public_id'],token=q['customer_token'])
    wa=normalize_wa(q['whatsapp'] or q['phone'])
    price=q['offered_price'] or ''
    msg=f"Merhaba {q['name']}, Aras Asansör taşıma teklifiniz hazır."
    if price: msg+=f" Toplam teklifimiz: {price} TL."
    msg+=f" Teklif detayınız: {share_url}"
    from urllib.parse import quote
    wa_url=f'https://wa.me/{wa}?text={quote(msg)}' if wa else '#'
    return render_template('admin_quote.html',q=q,files=files,share_url=share_url,wa_url=wa_url,whatsapp=WHATSAPP_NUMBER)

@app.post('/admin/talep/<int:qid>/sil')
@admin_required
def delete_quote(qid):
    con=db(); fs=con.execute('SELECT filename FROM quote_files WHERE quote_id=?',(qid,)).fetchall(); con.execute('DELETE FROM quote_files WHERE quote_id=?',(qid,)); con.execute('DELETE FROM quotes WHERE id=?',(qid,)); con.commit(); con.close()
    for r in fs:
        try:(UPLOAD_DIR/r['filename']).unlink(missing_ok=True)
        except:pass
    flash('Talep silindi.','ok'); return redirect(url_for('admin_dashboard'))

@app.post('/admin/yorum/<int:rid>/onay')
@admin_required
def approve_review(rid):
    con=db(); con.execute('UPDATE reviews SET approved=1 WHERE id=?',(rid,)); con.commit(); con.close(); return redirect(url_for('admin_dashboard')+'#yorum-yonetimi')
@app.post('/admin/yorum/<int:rid>/sil')
@admin_required
def delete_review(rid):
    con=db(); con.execute('DELETE FROM reviews WHERE id=?',(rid,)); con.commit(); con.close(); return redirect(url_for('admin_dashboard')+'#yorum-yonetimi')

@app.get('/api/health')
def health(): return jsonify(ok=True,service='Aras Asansör')

# --- SEO / Google ---
def public_site_url():
    configured = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    try:
        return request.url_root.rstrip("/")
    except Exception:
        return ""

@app.get("/robots.txt")
def robots_txt():
    base = public_site_url()
    body = "User-agent: *\nAllow: /\nDisallow: /admin/\n"
    if base:
        body += f"Sitemap: {base}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")

@app.get("/sitemap.xml")
def sitemap_xml():
    base = public_site_url()
    urls = [("/", "1.0")]
    items = []
    for path, priority in urls:
        loc = f"{base}{path}" if base else path
        items.append(
            f"<url><loc>{loc}</loc><changefreq>weekly</changefreq><priority>{priority}</priority></url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(items)
        + "</urlset>"
    )
    return Response(xml, mimetype="application/xml")


if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=False)
