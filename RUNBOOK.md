# RFQ Runbook (clean setup)

## 1) Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Security env (recommended)
Create `.env` from `.env.example` and set strong values:
- `DJANGO_DEBUG=0`
- `DJANGO_SECRET_KEY=<long-random>`
- `DJANGO_ALLOWED_HOSTS=<your-domain,127.0.0.1,localhost>`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://<your-domain>`

## 3) DB
```bash
python manage.py migrate
python manage.py createsuperuser
```

## 4) Run
```bash
python manage.py runserver 0.0.0.0:8780
```

## 5) Quick smoke check
- Open `/` and verify app loads
- `GET /api/health` => `{ok:true}`
- With `DJANGO_DEBUG=0`, buyer API without auth should return `401`

## 6) Pre-release preflight (recommended)
```bash
./scripts/preflight.sh
```
This runs:
- `manage.py check`
- RFQ API/auth/supplier tests
- a production-mode auth guard sanity test (`DJANGO_DEBUG=0`)

## 7) Notes
- Large artifacts are moved to `../_artifacts/` and should not be versioned.
- Do not commit `.venv`, `db.sqlite3`, `media/`, `staticfiles/`.
