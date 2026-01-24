from app import app
from models import db, User

OPERATIVOS = [
    ("oper1", "Oper1234!"),
    ("oper2", "Oper1234!"),
]

with app.app_context():
    for username, password in OPERATIVOS:
        exists = User.query.filter_by(username=username).first()
        if not exists:
            u = User(username=username, role="OPERATIVE", is_active=True)
            u.set_password(password)
            db.session.add(u)
            print(f"✅ Creado: {username} / {password}")
        else:
            print(f"ℹ️ Ya existe: {username}")
    db.session.commit()
