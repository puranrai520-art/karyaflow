# WorkPulse (KaryaFlow HR)

Enterprise Human Resource Management System — manage employees, leaves, tasks & attendance.

## 🏗️ Architecture

| Component | Service | URL |
|-----------|---------|-----|
| Frontend  | **Vercel** (static) | `https://your-app.vercel.app` |
| Backend   | **Render** (Python) | `https://your-api.onrender.com` |
| Database  | **Render PostgreSQL** | auto-configured |

## 🚀 Quick Start (Local Development)

```bash
cd karyaflow_final/backend
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` — default login: **admin / admin123**

## ☁️ Deployment

### 1. Backend → Render

1. Go to [render.com](https://render.com) → New → **Web Service**
2. Connect this GitHub repo
3. Settings:
   - **Root Directory**: `karyaflow_final/backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
4. Create a **PostgreSQL** database and link it
5. Add environment variables:
   - `DATABASE_URL` — auto-linked from PostgreSQL
   - `JWT_SECRET` — a strong random string
   - `FRONTEND_URL` — your Vercel URL (e.g. `https://workpulse.vercel.app`)

### 2. Frontend → Vercel

1. Go to [vercel.com](https://vercel.com) → Import Project
2. Connect this GitHub repo
3. Settings:
   - **Root Directory**: `karyaflow_final/frontend`
   - **Framework Preset**: Other
4. After deploy, update `API_BASE` in all frontend HTML files with your Render URL
5. Push changes → Vercel auto-redeploys

### 3. Update API_BASE

In each frontend HTML file, find this line:
```javascript
const API_BASE = '';
```
Change it to:
```javascript
const API_BASE = 'https://your-render-url.onrender.com';
```

## 📁 Structure

```
karyaflow_final/
├── backend/
│   ├── app.py              ← Flask API (PostgreSQL + SQLite)
│   ├── requirements.txt    ← Python dependencies
│   ├── Procfile            ← Render start command
│   └── .env.example        ← Environment config template
└── frontend/
    ├── vercel.json         ← Vercel URL rewrites
    ├── index.html          ← Landing page
    ├── admin/
    │   ├── index.html      ← Admin login
    │   └── dashboard.html  ← Admin panel
    └── employee/
        ├── index.html      ← Employee login
        └── dashboard.html  ← Employee portal
```

## 🔐 Default Credentials

- **Admin**: `admin` / `admin123`
- **Employees**: Created by admin, credentials shown on screen

## 📧 Email (Optional)

Copy `.env.example` → `.env` and configure Gmail SMTP with App Password.
