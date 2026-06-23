"""
KaryaFlow HR — Backend (Python + Flask + PostgreSQL)
Production-ready backend with PostgreSQL for cloud deployment.
Falls back to SQLite for local development.
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import bcrypt, jwt, uuid, random, string, smtplib, os
from functools import wraps
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta

# Load .env if present (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Point Flask at the frontend directory for static file serving
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend')
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')

# ── CORS — allow frontend origins ─────────────────────────────────────────────
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5000')
CORS(app, origins=[
    FRONTEND_URL,
    'http://localhost:5000',
    'http://localhost:3000',
    'http://127.0.0.1:5000',
])

# ── CONFIG ────────────────────────────────────────────────────────────────────
SECRET       = os.getenv('JWT_SECRET', 'karyaflow_super_secret_2024')
DATABASE_URL = os.getenv('DATABASE_URL', '')
SMTP_HOST    = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT    = int(os.getenv('SMTP_PORT', 587))
SMTP_USER    = os.getenv('SMTP_USER', '')
SMTP_PASS    = os.getenv('SMTP_PASS', '')
SMTP_FROM    = os.getenv('SMTP_FROM', 'noreply@karyaflow.com')

# ── DATABASE LAYER ────────────────────────────────────────────────────────────
# Detect if we should use PostgreSQL or SQLite
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

    def db():
        # Render provides DATABASE_URL starting with postgres:// but psycopg2
        # needs postgresql://
        url = DATABASE_URL
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(url)
        conn.autocommit = False
        return conn

    def dict_row(cursor):
        """Convert psycopg2 cursor results to list of dicts."""
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def dict_one(cursor):
        """Fetch one row as dict."""
        columns = [desc[0] for desc in cursor.description]
        row = cursor.fetchone()
        return dict(zip(columns, row)) if row else None

    def execute_query(conn, sql, params=None):
        cur = conn.cursor()
        cur.execute(sql, params or ())
        return cur

    def execute_fetchall(conn, sql, params=None):
        cur = execute_query(conn, sql, params)
        return dict_row(cur)

    def execute_fetchone(conn, sql, params=None):
        cur = execute_query(conn, sql, params)
        return dict_one(cur)

    def execute_scalar(conn, sql, params=None):
        cur = execute_query(conn, sql, params)
        row = cur.fetchone()
        return row[0] if row else None

else:
    import sqlite3
    DB_PATH = os.path.join(os.path.dirname(__file__), 'karyaflow.db')

    def db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def execute_query(conn, sql, params=None):
        return conn.execute(sql, params or ())

    def execute_fetchall(conn, sql, params=None):
        rows = conn.execute(sql, params or ()).fetchall()
        return [dict(r) for r in rows]

    def execute_fetchone(conn, sql, params=None):
        row = conn.execute(sql, params or ()).fetchone()
        return dict(row) if row else None

    def execute_scalar(conn, sql, params=None):
        row = conn.execute(sql, params or ()).fetchone()
        return row[0] if row else None


# ── SCHEMA INIT ───────────────────────────────────────────────────────────────
def init_db():
    c = db()
    if USE_POSTGRES:
        cur = c.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS employees (
                id TEXT PRIMARY KEY,
                employee_id TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                department TEXT NOT NULL,
                designation TEXT NOT NULL,
                phone TEXT DEFAULT '',
                join_date TEXT NOT NULL,
                monthly_leaves INTEGER DEFAULT 10,
                leaves_remaining INTEGER DEFAULT 10,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS leave_requests (
                id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL REFERENCES employees(id),
                from_date TEXT NOT NULL,
                to_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                leave_type TEXT DEFAULT 'casual',
                status TEXT DEFAULT 'pending',
                approved_by TEXT,
                approved_at TEXT,
                total_days INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS attendance (
                id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL REFERENCES employees(id),
                login_time TEXT,
                logout_time TEXT,
                date TEXT NOT NULL,
                total_hours REAL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                assigned_to TEXT NOT NULL REFERENCES employees(id),
                assigned_by TEXT NOT NULL,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'pending',
                due_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS task_updates (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                employee_id TEXT NOT NULL,
                update_text TEXT NOT NULL,
                status_changed_to TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.commit()
        # Seed default admin
        existing = execute_fetchone(c, "SELECT id FROM admins WHERE username='admin'")
        if not existing:
            pw = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
            cur.execute("INSERT INTO admins (id,username,full_name,email,password_hash) VALUES (%s,%s,%s,%s,%s)",
                        (str(uuid.uuid4()), 'admin', 'System Administrator', 'admin@karyaflow.com', pw))
            c.commit()
    else:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS admins (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS employees (
                id TEXT PRIMARY KEY,
                employee_id TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                department TEXT NOT NULL,
                designation TEXT NOT NULL,
                phone TEXT DEFAULT '',
                join_date TEXT NOT NULL,
                monthly_leaves INTEGER DEFAULT 10,
                leaves_remaining INTEGER DEFAULT 10,
                password_hash TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS leave_requests (
                id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL,
                from_date TEXT NOT NULL,
                to_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                leave_type TEXT DEFAULT 'casual',
                status TEXT DEFAULT 'pending',
                approved_by TEXT,
                approved_at TEXT,
                total_days INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees(id)
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL,
                login_time TEXT,
                logout_time TEXT,
                date TEXT NOT NULL,
                total_hours REAL,
                FOREIGN KEY (employee_id) REFERENCES employees(id)
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                assigned_to TEXT NOT NULL,
                assigned_by TEXT NOT NULL,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'pending',
                due_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assigned_to) REFERENCES employees(id)
            );
            CREATE TABLE IF NOT EXISTS task_updates (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                employee_id TEXT NOT NULL,
                update_text TEXT NOT NULL,
                status_changed_to TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            );
        ''')
        existing = execute_fetchone(c, "SELECT id FROM admins WHERE username='admin'")
        if not existing:
            pw = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
            c.execute("INSERT INTO admins (id,username,full_name,email,password_hash) VALUES (?,?,?,?,?)",
                      (str(uuid.uuid4()), 'admin', 'System Administrator', 'admin@karyaflow.com', pw))
        c.commit()
    c.close()
    print("✅ KaryaFlow database ready" + (" (PostgreSQL)" if USE_POSTGRES else " (SQLite)"))

# ── HELPERS ───────────────────────────────────────────────────────────────────
# SQL placeholder: %s for Postgres, ? for SQLite
PH = '%s' if USE_POSTGRES else '?'

def Q(sql):
    """Convert ? placeholders to %s for PostgreSQL."""
    if USE_POSTGRES:
        return sql.replace('?', '%s')
    return sql

def next_emp_id():
    c = db()
    n = execute_scalar(c, "SELECT COUNT(*) FROM employees")
    c.close()
    return f"EMP{str(n + 1001).zfill(4)}"

def gen_password(n=9):
    return ''.join(random.choices(string.ascii_letters + string.digits + '!@#', k=n))

def decode_token(token):
    try:
        return jwt.decode(token, SECRET, algorithms=['HS256'])
    except:
        return None

def auth(role=None):
    """Decorator: checks JWT and optionally enforces role."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            tok = request.headers.get('Authorization','').replace('Bearer ','')
            payload = decode_token(tok)
            if not payload:
                return jsonify(error='Unauthorized'), 401
            if role and payload.get('role') != role:
                return jsonify(error='Forbidden'), 403
            request.user = payload
            return fn(*a, **kw)
        return wrapper
    return deco

def days_between(d1, d2):
    return (datetime.strptime(d2,'%Y-%m-%d') - datetime.strptime(d1,'%Y-%m-%d')).days + 1

def send_email(to_email, subject, html_body):
    """Send email via SMTP. Returns True on success, False on failure."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[EMAIL SKIPPED] To: {to_email} | Subject: {subject}")
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = SMTP_FROM
        msg['To']   = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

def welcome_email_html(name, emp_id, password, department, designation):
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #F5F3EF; margin: 0; padding: 32px 16px; }}
  .card {{ background: #fff; max-width: 520px; margin: 0 auto; border-radius: 10px;
           border: 1px solid #E0DBD3; overflow: hidden; }}
  .header {{ background: #17243D; padding: 28px 32px; }}
  .header h1 {{ color: #fff; font-size: 20px; margin: 0; }}
  .header p {{ color: rgba(255,255,255,0.45); font-size: 13px; margin: 4px 0 0; }}
  .body {{ padding: 28px 32px; }}
  .body p {{ color: #3A4560; font-size: 14px; line-height: 1.7; }}
  .cred-box {{ background: #17243D; border-radius: 8px; padding: 18px 20px; margin: 20px 0; }}
  .cred-row {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
  .cred-label {{ color: rgba(255,255,255,0.4); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .cred-val {{ color: #D69E2E; font-family: monospace; font-size: 15px; font-weight: 700; }}
  .warn {{ background: #FDF6E3; border-left: 3px solid #D69E2E; padding: 10px 14px;
           border-radius: 0 6px 6px 0; font-size: 13px; color: #7A5C0A; margin-top: 16px; }}
  .footer {{ padding: 20px 32px; border-top: 1px solid #E0DBD3;
             font-size: 12px; color: #9BA5B5; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>Welcome to KaryaFlow HR</h1>
    <p>Your employee account is ready</p>
  </div>
  <div class="body">
    <p>Hi <strong>{name}</strong>,</p>
    <p>Your employee account has been created. You have been added as
       <strong>{designation}</strong> in the <strong>{department}</strong> department.
       Below are your login credentials. Please keep them safe.</p>
    <div class="cred-box">
      <div class="cred-row"><span class="cred-label">Employee ID</span><span class="cred-val">{emp_id}</span></div>
      <div class="cred-row"><span class="cred-label">Password</span><span class="cred-val">{password}</span></div>
    </div>
    <div class="warn">⚠ This is your one-time password. Please change it after your first login
       (or contact your admin to reset it).</div>
    <p style="margin-top:20px;">Login at: <strong>{FRONTEND_URL}/employee/</strong></p>
  </div>
  <div class="footer">KaryaFlow HR &bull; This is an automated message. Do not reply.</div>
</div>
</body></html>"""

def leave_status_email_html(name, status, from_date, to_date, total_days, reason):
    color = '#1A6640' if status == 'approved' else '#8B1A1A'
    bg    = '#EAF6F0' if status == 'approved' else '#FBEAEA'
    label = 'APPROVED' if status == 'approved' else 'REJECTED'
    return f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"/>
<style>
  body {{ font-family:'Segoe UI',Arial,sans-serif; background:#F5F3EF; margin:0; padding:32px 16px; }}
  .card {{ background:#fff; max-width:520px; margin:0 auto; border-radius:10px;
           border:1px solid #E0DBD3; overflow:hidden; }}
  .header {{ background:#17243D; padding:24px 32px; }}
  .header h1 {{ color:#fff; font-size:18px; margin:0; }}
  .badge {{ display:inline-block; background:{bg}; color:{color};
            border:1px solid; padding:4px 14px; border-radius:20px;
            font-size:12px; font-weight:600; margin-top:8px; }}
  .body {{ padding:28px 32px; color:#3A4560; font-size:14px; line-height:1.7; }}
  .detail-row {{ display:flex; gap:16px; margin:6px 0; }}
  .detail-label {{ color:#9BA5B5; min-width:90px; font-size:12px; }}
  .footer {{ padding:16px 32px; border-top:1px solid #E0DBD3; font-size:12px; color:#9BA5B5; }}
</style>
</head><body>
<div class="card">
  <div class="header">
    <h1>Leave Request Update</h1>
    <span class="badge">{label}</span>
  </div>
  <div class="body">
    <p>Hi <strong>{name}</strong>, your leave request has been <strong>{status}</strong>.</p>
    <div class="detail-row"><span class="detail-label">Period</span><span>{from_date} → {to_date}</span></div>
    <div class="detail-row"><span class="detail-label">Days</span><span>{total_days}</span></div>
    <div class="detail-row"><span class="detail-label">Reason</span><span>{reason}</span></div>
  </div>
  <div class="footer">KaryaFlow HR &bull; Automated notification</div>
</div>
</body></html>"""

def task_assign_email_html(emp_name, task_title, description, priority, due_date, assigned_by):
    return f"""
<!DOCTYPE html><html><head><meta charset="UTF-8"/>
<style>
  body {{ font-family:'Segoe UI',Arial,sans-serif; background:#F5F3EF; margin:0; padding:32px 16px; }}
  .card {{ background:#fff; max-width:520px; margin:0 auto; border-radius:10px; border:1px solid #E0DBD3; overflow:hidden; }}
  .header {{ background:#17243D; padding:24px 32px; }}
  .header h1 {{ color:#fff; font-size:18px; margin:0; }}
  .body {{ padding:28px 32px; color:#3A4560; font-size:14px; line-height:1.7; }}
  .task-box {{ background:#F5F3EF; border-radius:8px; padding:16px 18px; margin:16px 0;
               border-left:3px solid #D69E2E; }}
  .task-box h3 {{ margin:0 0 6px; color:#1A2740; font-size:16px; }}
  .task-box p {{ margin:0; color:#6B7A99; font-size:13px; }}
  .meta {{ display:flex; gap:24px; margin-top:12px; font-size:12px; color:#9BA5B5; }}
  .footer {{ padding:16px 32px; border-top:1px solid #E0DBD3; font-size:12px; color:#9BA5B5; }}
</style>
</head><body>
<div class="card">
  <div class="header"><h1>New Task Assigned</h1></div>
  <div class="body">
    <p>Hi <strong>{emp_name}</strong>, a new task has been assigned to you by <strong>{assigned_by}</strong>.</p>
    <div class="task-box">
      <h3>{task_title}</h3>
      <p>{description}</p>
      <div class="meta">
        <span>Priority: <strong>{priority.upper()}</strong></span>
        <span>Due: <strong>{due_date or 'No deadline'}</strong></span>
      </div>
    </div>
    <p>Log in to KaryaFlow to view and update this task.</p>
  </div>
  <div class="footer">KaryaFlow HR &bull; Automated notification</div>
</div>
</body></html>"""

# ── AUTH ─────────────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    d = request.json or {}
    username = d.get('username','').strip()
    password = d.get('password','').strip()
    if not username or not password:
        return jsonify(error='Username and password required'), 400

    c = db()
    # Check admin first
    admin = execute_fetchone(c, Q("SELECT * FROM admins WHERE username=?"), (username,))
    if admin and bcrypt.checkpw(password.encode(), admin['password_hash'].encode()):
        c.close()
        token = jwt.encode({
            'id': admin['id'], 'role': 'admin',
            'full_name': admin['full_name'],
            'exp': datetime.utcnow() + timedelta(hours=12)
        }, SECRET, algorithm='HS256')
        return jsonify(token=token, role='admin', full_name=admin['full_name'])

    # Check employee
    emp = execute_fetchone(c, Q("SELECT * FROM employees WHERE employee_id=? AND is_active=TRUE"), (username,))
    # SQLite uses 1 for true, so also check is_active=1
    if not emp:
        emp = execute_fetchone(c, Q("SELECT * FROM employees WHERE employee_id=? AND is_active=1"), (username,))
    if emp and bcrypt.checkpw(password.encode(), emp['password_hash'].encode()):
        today = datetime.now().strftime('%Y-%m-%d')
        existing = execute_fetchone(c, Q("SELECT id FROM attendance WHERE employee_id=? AND date=?"),
                                    (emp['id'], today))
        if not existing:
            execute_query(c, Q("INSERT INTO attendance (id,employee_id,login_time,date) VALUES (?,?,?,?)"),
                          (str(uuid.uuid4()), emp['id'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), today))
            c.commit()
        c.close()
        token = jwt.encode({
            'id': emp['id'], 'role': 'employee',
            'employee_id': emp['employee_id'],
            'full_name': emp['full_name'],
            'department': emp['department'],
            'exp': datetime.utcnow() + timedelta(hours=12)
        }, SECRET, algorithm='HS256')
        return jsonify(token=token, role='employee', full_name=emp['full_name'],
                       employee_id=emp['employee_id'], department=emp['department'])

    c.close()
    return jsonify(error='Invalid credentials. Check your Employee ID and password.'), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    tok = request.headers.get('Authorization','').replace('Bearer ','')
    payload = decode_token(tok)
    if payload and payload.get('role') == 'employee':
        c = db()
        today = datetime.now().strftime('%Y-%m-%d')
        now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log = execute_fetchone(c, Q("SELECT * FROM attendance WHERE employee_id=? AND date=? AND logout_time IS NULL"),
                               (payload['id'], today))
        if log:
            login_dt  = datetime.strptime(log['login_time'], '%Y-%m-%d %H:%M:%S')
            logout_dt = datetime.strptime(now, '%Y-%m-%d %H:%M:%S')
            hrs = round((logout_dt - login_dt).seconds / 3600, 2)
            execute_query(c, Q("UPDATE attendance SET logout_time=?, total_hours=? WHERE id=?"),
                          (now, hrs, log['id']))
            c.commit()
        c.close()
    return jsonify(message='Logged out')

# ── ADMIN — DASHBOARD ─────────────────────────────────────────────────────────
@app.route('/api/admin/dashboard', methods=['GET'])
@auth('admin')
def admin_dashboard():
    c = db()
    today = datetime.now().strftime('%Y-%m-%d')

    # For PostgreSQL, is_active=TRUE; for SQLite, is_active=1
    active_clause = "is_active=TRUE" if USE_POSTGRES else "is_active=1"

    data = {
        'total_employees': execute_scalar(c, f"SELECT COUNT(*) FROM employees WHERE {active_clause}"),
        'pending_leaves':  execute_scalar(c, "SELECT COUNT(*) FROM leave_requests WHERE status='pending'"),
        'open_tasks':      execute_scalar(c, "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed')"),
        'present_today':   execute_scalar(c, Q("SELECT COUNT(*) FROM attendance WHERE date=?"), (today,)),
        'pending_leaves_list': execute_fetchall(c, Q("""
            SELECT lr.*, e.full_name, e.employee_id, e.department
            FROM leave_requests lr JOIN employees e ON e.id=lr.employee_id
            WHERE lr.status='pending' ORDER BY lr.created_at DESC LIMIT 6""")),
        'recent_tasks': execute_fetchall(c, """
            SELECT t.*, e.full_name, e.employee_id
            FROM tasks t JOIN employees e ON e.id=t.assigned_to
            ORDER BY t.created_at DESC LIMIT 6"""),
    }
    c.close()
    return jsonify(data)

# ── ADMIN — EMPLOYEES ─────────────────────────────────────────────────────────
@app.route('/api/admin/employees', methods=['GET'])
@auth('admin')
def list_employees():
    c = db()
    rows = execute_fetchall(c, """SELECT id,employee_id,full_name,email,department,designation,
                        phone,join_date,monthly_leaves,leaves_remaining,is_active,created_at
                        FROM employees ORDER BY created_at DESC""")
    c.close()
    # Normalize is_active to int for frontend compatibility
    for r in rows:
        r['is_active'] = 1 if r['is_active'] else 0
    return jsonify(rows)

@app.route('/api/admin/employees', methods=['POST'])
@auth('admin')
def create_employee():
    d = request.json or {}
    required = ['full_name','email','department','designation','join_date']
    for f in required:
        if not d.get(f):
            return jsonify(error=f'{f} is required'), 400

    c = db()
    if execute_fetchone(c, Q("SELECT id FROM employees WHERE email=?"), (d['email'],)):
        c.close()
        return jsonify(error='An employee with this email already exists'), 400

    emp_id   = next_emp_id()
    password = gen_password()
    pw_hash  = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    uid      = str(uuid.uuid4())
    leaves   = int(d.get('monthly_leaves', 10))

    execute_query(c, Q("""INSERT INTO employees
                 (id,employee_id,full_name,email,department,designation,phone,join_date,
                  monthly_leaves,leaves_remaining,password_hash)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)"""),
              (uid, emp_id, d['full_name'], d['email'], d['department'],
               d['designation'], d.get('phone',''), d['join_date'], leaves, leaves, pw_hash))
    c.commit(); c.close()

    # Send welcome email
    email_sent = send_email(
        d['email'],
        f"Welcome to KaryaFlow HR — Your Login Credentials",
        welcome_email_html(d['full_name'], emp_id, password, d['department'], d['designation'])
    )

    return jsonify(
        message='Employee created',
        employee_id=emp_id,
        password=password,
        full_name=d['full_name'],
        email=d['email'],
        email_sent=email_sent
    ), 201

@app.route('/api/admin/employees/<eid>/toggle', methods=['PUT'])
@auth('admin')
def toggle_employee(eid):
    c = db()
    emp = execute_fetchone(c, Q("SELECT * FROM employees WHERE id=?"), (eid,))
    if not emp: c.close(); return jsonify(error='Not found'), 404
    new_val = not emp['is_active']
    if USE_POSTGRES:
        execute_query(c, "UPDATE employees SET is_active=%s WHERE id=%s", (new_val, eid))
    else:
        new_int = 0 if emp['is_active'] else 1
        execute_query(c, "UPDATE employees SET is_active=? WHERE id=?", (new_int, eid))
    c.commit(); c.close()
    return jsonify(is_active=1 if new_val else 0)

@app.route('/api/admin/employees/<eid>', methods=['GET'])
@auth('admin')
def get_employee(eid):
    c = db()
    emp = execute_fetchone(c, Q("SELECT id,employee_id,full_name,email,department,designation,phone,join_date,monthly_leaves,leaves_remaining,is_active FROM employees WHERE id=?"), (eid,))
    if not emp: c.close(); return jsonify(error='Not found'), 404
    tasks = execute_fetchall(c, Q("SELECT * FROM tasks WHERE assigned_to=? ORDER BY created_at DESC"), (eid,))
    leaves = execute_fetchall(c, Q("SELECT * FROM leave_requests WHERE employee_id=? ORDER BY created_at DESC LIMIT 5"), (eid,))
    c.close()
    emp['is_active'] = 1 if emp['is_active'] else 0
    return jsonify(employee=emp, tasks=tasks, leaves=leaves)

# ── ADMIN — LEAVES ────────────────────────────────────────────────────────────
@app.route('/api/admin/leaves', methods=['GET'])
@auth('admin')
def all_leaves():
    status = request.args.get('status', '')
    c = db()
    if status:
        rows = execute_fetchall(c, Q("""SELECT lr.*, e.full_name, e.employee_id, e.department, e.email
               FROM leave_requests lr JOIN employees e ON e.id=lr.employee_id
               WHERE lr.status=?"""), (status,))
    else:
        rows = execute_fetchall(c, """SELECT lr.*, e.full_name, e.employee_id, e.department, e.email
               FROM leave_requests lr JOIN employees e ON e.id=lr.employee_id
               ORDER BY lr.created_at DESC""")
    c.close()
    return jsonify(rows)

@app.route('/api/admin/leaves/<lid>/approve', methods=['PUT'])
@auth('admin')
def approve_leave(lid):
    c = db()
    leave = execute_fetchone(c, Q("SELECT * FROM leave_requests WHERE id=?"), (lid,))
    if not leave: c.close(); return jsonify(error='Not found'), 404
    if leave['status'] != 'pending': c.close(); return jsonify(error='Already processed'), 400
    emp = execute_fetchone(c, Q("SELECT * FROM employees WHERE id=?"), (leave['employee_id'],))
    if emp['leaves_remaining'] < leave['total_days']:
        c.close(); return jsonify(error=f'Insufficient balance ({emp["leaves_remaining"]} days left)'), 400
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute_query(c, Q("UPDATE leave_requests SET status='approved',approved_by=?,approved_at=? WHERE id=?"),
              (request.user['full_name'], now, lid))
    execute_query(c, Q("UPDATE employees SET leaves_remaining=leaves_remaining-? WHERE id=?"),
              (leave['total_days'], leave['employee_id']))
    c.commit()
    send_email(emp['email'], 'Your leave request has been approved',
               leave_status_email_html(emp['full_name'], 'approved',
                                       leave['from_date'], leave['to_date'],
                                       leave['total_days'], leave['reason']))
    c.close()
    return jsonify(message='Leave approved')

@app.route('/api/admin/leaves/<lid>/reject', methods=['PUT'])
@auth('admin')
def reject_leave(lid):
    c = db()
    leave = execute_fetchone(c, Q("SELECT * FROM leave_requests WHERE id=?"), (lid,))
    if not leave: c.close(); return jsonify(error='Not found'), 404
    if leave['status'] != 'pending': c.close(); return jsonify(error='Already processed'), 400
    emp = execute_fetchone(c, Q("SELECT * FROM employees WHERE id=?"), (leave['employee_id'],))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute_query(c, Q("UPDATE leave_requests SET status='rejected',approved_by=?,approved_at=? WHERE id=?"),
              (request.user['full_name'], now, lid))
    c.commit()
    send_email(emp['email'], 'Your leave request has been rejected',
               leave_status_email_html(emp['full_name'], 'rejected',
                                       leave['from_date'], leave['to_date'],
                                       leave['total_days'], leave['reason']))
    c.close()
    return jsonify(message='Leave rejected')

# ── ADMIN — TASKS ─────────────────────────────────────────────────────────────
@app.route('/api/admin/tasks', methods=['GET'])
@auth('admin')
def all_tasks():
    emp_filter = request.args.get('employee_id', '')
    c = db()
    if emp_filter:
        rows = execute_fetchall(c, Q("""SELECT t.*, e.full_name, e.employee_id as emp_code, e.department
               FROM tasks t JOIN employees e ON e.id=t.assigned_to
               WHERE t.assigned_to=? ORDER BY t.created_at DESC"""), (emp_filter,))
    else:
        rows = execute_fetchall(c, """SELECT t.*, e.full_name, e.employee_id as emp_code, e.department
               FROM tasks t JOIN employees e ON e.id=t.assigned_to
               ORDER BY t.created_at DESC""")
    c.close()
    return jsonify(rows)

@app.route('/api/admin/tasks', methods=['POST'])
@auth('admin')
def create_task():
    d = request.json or {}
    if not d.get('title') or not d.get('assigned_to'):
        return jsonify(error='Title and assigned_to required'), 400
    c = db()
    emp = execute_fetchone(c, Q("SELECT * FROM employees WHERE id=?"), (d['assigned_to'],))
    if not emp: c.close(); return jsonify(error='Employee not found'), 404
    tid = str(uuid.uuid4())
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute_query(c, Q("""INSERT INTO tasks (id,title,description,assigned_to,assigned_by,priority,due_date,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?)"""),
              (tid, d['title'], d.get('description',''), d['assigned_to'],
               request.user['full_name'], d.get('priority','medium'),
               d.get('due_date',''), now, now))
    c.commit()
    send_email(emp['email'], f'New Task Assigned: {d["title"]}',
               task_assign_email_html(emp['full_name'], d['title'],
                                      d.get('description',''), d.get('priority','medium'),
                                      d.get('due_date',''), request.user['full_name']))
    c.close()
    return jsonify(message='Task created', task_id=tid), 201

@app.route('/api/admin/tasks/<tid>', methods=['PUT'])
@auth('admin')
def update_task_admin(tid):
    d = request.json or {}
    c = db()
    task = execute_fetchone(c, Q("SELECT * FROM tasks WHERE id=?"), (tid,))
    if not task: c.close(); return jsonify(error='Not found'), 404
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute_query(c, Q("""UPDATE tasks SET title=?,description=?,priority=?,status=?,due_date=?,updated_at=?
                 WHERE id=?"""),
              (d.get('title', task['title']),
               d.get('description', task['description']),
               d.get('priority', task['priority']),
               d.get('status', task['status']),
               d.get('due_date', task['due_date']), now, tid))
    c.commit(); c.close()
    return jsonify(message='Task updated')

@app.route('/api/admin/tasks/<tid>/updates', methods=['GET'])
@auth('admin')
def task_updates_admin(tid):
    c = db()
    rows = execute_fetchall(c, Q("""SELECT tu.*, e.full_name FROM task_updates tu
                        JOIN employees e ON e.id=tu.employee_id
                        WHERE tu.task_id=? ORDER BY tu.created_at ASC"""), (tid,))
    c.close()
    return jsonify(rows)

# ── ADMIN — ATTENDANCE ────────────────────────────────────────────────────────
@app.route('/api/admin/attendance', methods=['GET'])
@auth('admin')
def admin_attendance():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    c = db()
    rows = execute_fetchall(c, Q("""SELECT a.*, e.full_name, e.employee_id, e.department
                        FROM attendance a JOIN employees e ON e.id=a.employee_id
                        WHERE a.date=? ORDER BY a.login_time"""), (date,))
    c.close()
    return jsonify(rows)

# ── EMPLOYEE — ROUTES ─────────────────────────────────────────────────────────
@app.route('/api/employee/dashboard', methods=['GET'])
@auth('employee')
def emp_dashboard():
    c = db()
    emp = execute_fetchone(c, Q("""SELECT id,employee_id,full_name,email,department,designation,
                       monthly_leaves,leaves_remaining,join_date FROM employees WHERE id=?"""),
                    (request.user['id'],))
    today = datetime.now().strftime('%Y-%m-%d')
    att   = execute_fetchone(c, Q("SELECT * FROM attendance WHERE employee_id=? AND date=?"),
                      (request.user['id'], today))
    pending_leaves = execute_scalar(c, Q("SELECT COUNT(*) FROM leave_requests WHERE employee_id=? AND status='pending'"),
                               (request.user['id'],))
    open_tasks     = execute_scalar(c, Q("SELECT COUNT(*) FROM tasks WHERE assigned_to=? AND status NOT IN ('completed')"),
                               (request.user['id'],))
    overdue_tasks  = execute_scalar(c, Q("SELECT COUNT(*) FROM tasks WHERE assigned_to=? AND status NOT IN ('completed') AND due_date < ?"),
                               (request.user['id'], today))
    recent_tasks   = execute_fetchall(c, Q("SELECT * FROM tasks WHERE assigned_to=? ORDER BY created_at DESC LIMIT 5"),
                               (request.user['id'],))
    c.close()
    return jsonify(
        employee=emp,
        today_attendance=att,
        pending_leaves=pending_leaves,
        open_tasks=open_tasks,
        overdue_tasks=overdue_tasks,
        recent_tasks=recent_tasks
    )

@app.route('/api/employee/profile', methods=['GET'])
@auth('employee')
def emp_profile():
    c = db()
    emp = execute_fetchone(c, Q("SELECT id,employee_id,full_name,email,department,designation,phone,join_date,monthly_leaves,leaves_remaining,is_active FROM employees WHERE id=?"),
                    (request.user['id'],))
    c.close()
    if emp:
        emp['is_active'] = 1 if emp['is_active'] else 0
    return jsonify(emp)

@app.route('/api/employee/leaves', methods=['GET'])
@auth('employee')
def emp_leaves():
    c = db()
    rows = execute_fetchall(c, Q("SELECT * FROM leave_requests WHERE employee_id=? ORDER BY created_at DESC"),
                     (request.user['id'],))
    c.close()
    return jsonify(rows)

@app.route('/api/employee/leaves', methods=['POST'])
@auth('employee')
def apply_leave():
    d = request.json or {}
    if not d.get('from_date') or not d.get('to_date') or not d.get('reason'):
        return jsonify(error='from_date, to_date and reason are required'), 400
    if d['from_date'] > d['to_date']:
        return jsonify(error='From date must be before to date'), 400
    total = days_between(d['from_date'], d['to_date'])
    c = db()
    emp = execute_fetchone(c, Q("SELECT * FROM employees WHERE id=?"), (request.user['id'],))
    if emp['leaves_remaining'] < total:
        c.close(); return jsonify(error=f'Only {emp["leaves_remaining"]} leave days remaining'), 400
    overlap = execute_fetchone(c, Q("""SELECT id FROM leave_requests WHERE employee_id=? AND status!='rejected'
                           AND from_date<=? AND to_date>=?"""),
                        (request.user['id'], d['to_date'], d['from_date']))
    if overlap:
        c.close(); return jsonify(error='Overlapping dates with existing leave request'), 400
    lid = str(uuid.uuid4())
    execute_query(c, Q("""INSERT INTO leave_requests (id,employee_id,from_date,to_date,reason,leave_type,total_days)
                 VALUES (?,?,?,?,?,?,?)"""),
              (lid, request.user['id'], d['from_date'], d['to_date'],
               d['reason'], d.get('leave_type','casual'), total))
    c.commit(); c.close()
    return jsonify(message='Leave request submitted', total_days=total), 201

@app.route('/api/employee/attendance', methods=['GET'])
@auth('employee')
def emp_attendance():
    c = db()
    rows = execute_fetchall(c, Q("SELECT * FROM attendance WHERE employee_id=? ORDER BY date DESC LIMIT 30"),
                     (request.user['id'],))
    c.close()
    return jsonify(rows)

@app.route('/api/employee/tasks', methods=['GET'])
@auth('employee')
def emp_tasks():
    c = db()
    rows = execute_fetchall(c, Q("SELECT * FROM tasks WHERE assigned_to=? ORDER BY created_at DESC"),
                     (request.user['id'],))
    c.close()
    return jsonify(rows)

@app.route('/api/employee/tasks/<tid>/update', methods=['POST'])
@auth('employee')
def add_task_update(tid):
    d = request.json or {}
    if not d.get('update_text'):
        return jsonify(error='update_text required'), 400
    c = db()
    task = execute_fetchone(c, Q("SELECT * FROM tasks WHERE id=? AND assigned_to=?"),
                     (tid, request.user['id']))
    if not task: c.close(); return jsonify(error='Task not found'), 404
    uid = str(uuid.uuid4())
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute_query(c, Q("INSERT INTO task_updates (id,task_id,employee_id,update_text,status_changed_to,created_at) VALUES (?,?,?,?,?,?)"),
              (uid, tid, request.user['id'], d['update_text'], d.get('status_changed_to'), now))
    if d.get('status_changed_to'):
        execute_query(c, Q("UPDATE tasks SET status=?,updated_at=? WHERE id=?"),
                  (d['status_changed_to'], now, tid))
    c.commit(); c.close()
    return jsonify(message='Update added')

@app.route('/api/employee/tasks/<tid>/updates', methods=['GET'])
@auth('employee')
def emp_task_updates(tid):
    c = db()
    rows = execute_fetchall(c, Q("SELECT * FROM task_updates WHERE task_id=? ORDER BY created_at ASC"), (tid,))
    c.close()
    return jsonify(rows)

# ── HEALTH CHECK ──────────────────────────────────────────────────────────────
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify(status='ok', database='postgres' if USE_POSTGRES else 'sqlite')

# ── FRONTEND SERVING ──────────────────────────────────────────────────────────
# Serve the separate Admin and Employee portals
@app.route('/')
def serve_landing():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/admin/')
@app.route('/admin')
def serve_admin_login():
    return send_from_directory(os.path.join(FRONTEND_DIR, 'admin'), 'index.html')

@app.route('/admin/dashboard.html')
@app.route('/admin/dashboard')
def serve_admin_dashboard():
    return send_from_directory(os.path.join(FRONTEND_DIR, 'admin'), 'dashboard.html')

@app.route('/employee/')
@app.route('/employee')
def serve_employee_login():
    return send_from_directory(os.path.join(FRONTEND_DIR, 'employee'), 'index.html')

@app.route('/employee/dashboard.html')
@app.route('/employee/dashboard')
def serve_employee_dashboard():
    return send_from_directory(os.path.join(FRONTEND_DIR, 'employee'), 'dashboard.html')

# ── INIT — always run init_db (works with gunicorn too) ──────────────────────
init_db()

if __name__ == '__main__':
    print("\n" + "="*50)
    print("  KaryaFlow HR — Running on http://localhost:5000")
    print("  Admin Portal:    http://localhost:5000/admin/")
    print("  Employee Portal: http://localhost:5000/employee/")
    print("  Default login:   admin / admin123")
    print("="*50 + "\n")
    app.run(debug=False, port=5000, use_reloader=False)
