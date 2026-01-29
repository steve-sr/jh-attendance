from app import app
from models import db, User

OPERATIVOS = [
    ("sara_salazar", "sara_salazar!"),
    ("sofia_alvarez", "sofia_alvarez!"),
    ("christopher_ordonoñez", "christopher_ordonoñez!"),
    ("valery_alvarez", "valery_alvarez!"),
    ("abi_vargas", "abi_vargas!"),
    ("angelica_quintero", "angelica_quintero!"),
    ("fernanda_fonseca", "fernanda_fonseca!"),
]

with app.app_context():
    for username, password in OPERATIVOS:
        exists = User.query.filter_by(username=username).first()
        if not exists:
            u = User(username=username, role="OPERATIVE", is_active=True)
            u.set_password(password)
            db.session.add(u)
            print(f"Creado: {username} / {password}")
        else:
            print(f"Ya existe: {username}")
    db.session.commit()
