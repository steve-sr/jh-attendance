import os
import sys
import subprocess

def run(cmd: list[str]) -> int:
    print(f"▶ Running: {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd)

def ensure_root_user():
    """
    Crea/actualiza el usuario ROOT usando variables de entorno.
    """
    from app import app
    from models import db, User

    username = (os.getenv("ROOT_ADMIN_USER") or "root").strip()
    password = (os.getenv("ROOT_ADMIN_PASS") or "").strip()
    role = (os.getenv("ROOT_ADMIN_ROLE") or "ROOT").strip().upper()

    if not password:
        print("❌ Falta ROOT_ADMIN_PASS en variables de entorno.", flush=True)
        return 1

    if role not in ("ROOT", "ADMIN", "OPERATIVE"):
        print("❌ ROOT_ADMIN_ROLE debe ser ROOT, ADMIN u OPERATIVE (recomendado ROOT).", flush=True)
        return 1

    with app.app_context():
        u = User.query.filter_by(username=username).first()
        if not u:
            u = User(username=username, role=role, is_active=True)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            print(f"✅ Root creado: {username} ({role})", flush=True)
        else:
            u.role = role
            u.is_active = True
            u.set_password(password)
            db.session.commit()
            print(f"✅ Root actualizado: {username} ({role})", flush=True)

    return 0

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

    # 2) Crear/asegurar ROOT (por ENV)
    try:
        code = ensure_root_user()
        if code != 0:
            return code
    except Exception as e:
        print("❌ Error creating ROOT user:", e, flush=True)
        return 1

    # 3) (Opcional) si querés mantener seed_barrios SOLO temporalmente:
    #    Podés borrar este bloque si ya vas a administrar barrios desde /admin/barrios
    if os.path.exists("seed_barrios.py"):
        code = run([sys.executable, "seed_barrios.py"])
        if code != 0:
            print("❌ seed_barrios.py failed.", flush=True)
            return code

    print("✅ Bootstrap finished.", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
