import os
import sqlite3
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash

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
WHATSAPP_NUMBER = os.getenv('ARAS_WHATSAPP_NUMBER', '')
PHONE_NUMBER = os.getenv('ARAS_PHONE_NUMBER', '')


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript('''
    CREATE TABLE IF NOT EXISTS quotes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      public_id TEXT UNIQUE NOT NULL,
      created_at TEXT NOT NULL,
      name TEXT NOT NULL,
      phone TEXT NOT NULL,
      whatsapp TEXT,
      from_district TEXT NOT NULL,
      from_neighborhood TEXT,
      from_floor TEXT,
      from_elevator TEXT,
      to_district TEXT NOT NULL,
      to_neighborhood TEXT,
      to_floor TEXT,
      to_elevator TEXT,
      home_type TEXT,
      moving_date TEXT,
      notes TEXT,
      status TEXT NOT NULL DEFAULT 'Yeni Talep',
      offered_price TEXT,
      admin_note TEXT
    );
    CREATE TABLE IF NOT EXISTS quote_files (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      quote_id INTEGER NOT NULL,
      filename TEXT NOT NULL,
      original_name TEXT,
      kind TEXT,
      FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
    );
    ''')
    con.commit(); con.close()


init_db()


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return fn(*args, **kwargs)
    return wrapper


def valid_file(name):
    return '.' in name and name.rsplit('.',1)[1].lower() in ALLOWED_EXT


def save_files(files, quote_id, kind):
    con = db()
    for f in files:
        if not f or not f.filename or not valid_file(f.filename):
            continue
        ext = f.filename.rsplit('.',1)[1].lower()
        filename = f"{quote_id}_{uuid.uuid4().hex[:12]}.{ext}"
        f.save(UPLOAD_DIR / filename)
        con.execute('INSERT INTO quote_files(quote_id,filename,original_name,kind) VALUES(?,?,?,?)',
                    (quote_id, filename, secure_filename(f.filename), kind))
    con.commit(); con.close()


@app.get('/')
def home():
    return render_template('index.html', whatsapp=WHATSAPP_NUMBER, phone=PHONE_NUMBER)


@app.post('/teklif')
def create_quote():
    name = request.form.get('name','').strip()
    phone = request.form.get('phone','').strip()
    from_district = request.form.get('from_district','').strip()
    to_district = request.form.get('to_district','').strip()
    if not all([name, phone, from_district, to_district]):
        flash('Lütfen zorunlu alanları doldurun.', 'error')
        return redirect(url_for('home') + '#fiyat-al')

    public_id = 'ARAS-' + datetime.now().strftime('%y%m%d') + '-' + uuid.uuid4().hex[:6].upper()
    con = db()
    cur = con.execute('''INSERT INTO quotes(
      public_id,created_at,name,phone,whatsapp,from_district,from_neighborhood,from_floor,from_elevator,
      to_district,to_neighborhood,to_floor,to_elevator,home_type,moving_date,notes
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
      public_id, datetime.now().strftime('%Y-%m-%d %H:%M'), name, phone,
      request.form.get('whatsapp','').strip(), from_district,
      request.form.get('from_neighborhood','').strip(), request.form.get('from_floor','').strip(), request.form.get('from_elevator','').strip(),
      to_district, request.form.get('to_neighborhood','').strip(), request.form.get('to_floor','').strip(), request.form.get('to_elevator','').strip(),
      request.form.get('home_type','').strip(), request.form.get('moving_date','').strip(), request.form.get('notes','').strip()
    ))
    quote_id = cur.lastrowid
    con.commit(); con.close()

    save_files(request.files.getlist('building_photos'), quote_id, 'Bina')
    save_files(request.files.getlist('item_photos'), quote_id, 'Eşya')

    return redirect(url_for('success', ref=public_id))


@app.get('/talep-alindi')
def success():
    return render_template('success.html', ref=request.args.get('ref',''), whatsapp=WHATSAPP_NUMBER, phone=PHONE_NUMBER)


@app.get('/uploads/<path:name>')
@admin_required
def uploaded_file(name):
    return send_from_directory(UPLOAD_DIR, name)


@app.route('/admin/giris', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        flash('Kullanıcı adı veya şifre yanlış.', 'error')
    return render_template('admin_login.html')


@app.get('/admin/cikis')
def admin_logout():
    session.clear(); return redirect(url_for('admin_login'))


@app.get('/admin')
@admin_required
def admin_dashboard():
    status = request.args.get('status','').strip()
    con = db()
    if status:
        rows = con.execute('SELECT * FROM quotes WHERE status=? ORDER BY id DESC', (status,)).fetchall()
    else:
        rows = con.execute('SELECT * FROM quotes ORDER BY id DESC').fetchall()
    counts = {r['status']: r['n'] for r in con.execute('SELECT status,COUNT(*) n FROM quotes GROUP BY status').fetchall()}
    con.close()
    return render_template('admin_dashboard.html', quotes=rows, counts=counts, current_status=status)


@app.route('/admin/talep/<int:qid>', methods=['GET','POST'])
@admin_required
def admin_quote(qid):
    con = db()
    if request.method == 'POST':
        con.execute('UPDATE quotes SET status=?,offered_price=?,admin_note=? WHERE id=?', (
            request.form.get('status','Yeni Talep'), request.form.get('offered_price','').strip(), request.form.get('admin_note','').strip(), qid))
        con.commit(); flash('Talep güncellendi.', 'ok')
    q = con.execute('SELECT * FROM quotes WHERE id=?',(qid,)).fetchone()
    files = con.execute('SELECT * FROM quote_files WHERE quote_id=? ORDER BY id',(qid,)).fetchall()
    con.close()
    if not q: return 'Talep bulunamadı',404
    return render_template('admin_quote.html', q=q, files=files, whatsapp=WHATSAPP_NUMBER)


@app.post('/admin/talep/<int:qid>/sil')
@admin_required
def delete_quote(qid):
    con=db(); fs=con.execute('SELECT filename FROM quote_files WHERE quote_id=?',(qid,)).fetchall()
    con.execute('DELETE FROM quote_files WHERE quote_id=?',(qid,)); con.execute('DELETE FROM quotes WHERE id=?',(qid,)); con.commit(); con.close()
    for r in fs:
        try:(UPLOAD_DIR/r['filename']).unlink(missing_ok=True)
        except:pass
    flash('Talep silindi.','ok'); return redirect(url_for('admin_dashboard'))


@app.get('/api/health')
def health():
    return jsonify(ok=True, service='Aras Asansör')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT','5000')), debug=True)
