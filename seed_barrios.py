from app import app
from models import db, Barrio

BARRIOS = [
    "Alaska",
    "Ángeles",
    "Buenos Aires",
    "Capulín",
    "Cerros",
    "Condega",
    "Corazón de Jesús",
    "Curime",
    "Choricera",
    "Chorotega",
    "Gallera",
    "Guaria",
    "Jícaro",
    "La Carreta",
    "Llano La Cruz",
    "Mocho (Santa Lucía)",
    "Moracia",
    "Nazareth",
    "Pueblo Nuevo",
    "Sabanero",
    "San Miguel",
    "San Roque",
    "Sitio",
    "Veinticinco de Julio",
    "Victoria",
    "Villanueva",
    "Martina Bustos",
    "El Regalito",
    "San Rafael",
    "Gallo",
    "Bagaces",
    "Felipe Pérez",
]

with app.app_context():
    created = 0
    for name in BARRIOS:
        exists = Barrio.query.filter_by(name=name).first()
        if not exists:
            db.session.add(Barrio(name=name, is_active=True))
            created += 1
    db.session.commit()
    print(f"✅ Barrios insertados: {created}")
