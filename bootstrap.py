import os
import sys
import subprocess

def run(cmd: list[str]) -> int:
    print(f"▶ Running: {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd)

def main():
    # 1) Crear tablas
    try:
        from app import app
        from models import db
        with app.app_context():
            db.create_all()
        print("✅ DB tables ensured (create_all).", flush=True)
    except Exception as e:
        print("❌ Error creating tables:", e, flush=True)
        return 1

    # 2) Seeds (idempotentes)
    if os.path.exists("seed_admin.py"):
        code = run([sys.executable, "seed_admin.py"])
        if code != 0:
            print("❌ seed_admin.py failed.", flush=True)
            return code

    if os.path.exists("seed_barrios.py"):
        code = run([sys.executable, "seed_barrios.py"])
        if code != 0:
            print("❌ seed_barrios.py failed.", flush=True)
            return code

    print("✅ Bootstrap finished.", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
