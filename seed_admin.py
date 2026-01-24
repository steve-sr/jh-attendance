from app import app
from models import db, User

with app.app_context():
    username = "admin"
    password = "Admin1234!"  # luego la cambiamos

    existing = User.query.filter_by(username=username).first()
    if existing:
        print("✅ Admin ya existe.")
    else:
        u = User(username=username, role="ADMIN", is_active=True)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        print(f"✅ Admin creado: {username} / {password}")
