from app import app
from models import db, User

ADMINISTRADORES = [
    ("john_espinoza", "john_espinoza!"),
    ("steve_rios", "steve_rios!"),
    ("jafet_alvarez", "jafet_alvarez!"),
]

with app.app_context():
    
    for username, password in ADMINISTRADORES:
        exists = User.query.filter_by(username=username).first()
        if not exists:
            u = User(username=username, role="ADMIN", is_active=True)
            u.set_password(password)
            db.session.add(u)
            print(f"Creado: {username} / {password}")
        else:
            print(f"Ya existe: {username}")
    db.session.commit()
