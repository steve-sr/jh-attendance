from datetime import datetime, date
import csv
from io import StringIO
import re
import traceback

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    Response,
    session,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from sqlalchemy import text

from config import Config
from models import db, User, Youth, Barrio, Service, Attendance
from helpers import role_required


# -------------------------
# Utils
# -------------------------
def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def validate_cedula(value: str) -> bool:
    d = digits_only(value)
    return d.isdigit() and len(d) in (8, 9)  # ajustable


def validate_phone(value: str) -> bool:
    d = digits_only(value)
    return d.isdigit() and len(d) == 8


def parse_birth_date(value: str):
    """
    Retorna:
      - date() si es válida
      - None si viene vacía
      - "invalid" si no parsea
      - "future" si es futura
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        bd = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return "invalid"

    if bd > date.today():
        return "future"

    return bd


# -------------------------
# App + DB
# -------------------------
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# -------------------------
# Login Manager
# -------------------------
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# -------------------------
# Helpers: servicios activos y seleccionado
# -------------------------
def get_active_services():
    return Service.query.filter_by(is_active=True).order_by(Service.starts_at.desc()).all()


def get_selected_service():
    service_id = session.get("selected_service_id")
    if not service_id:
        return None
    return Service.query.filter_by(id=service_id, is_active=True).first()


# -------------------------
# Template filters (format + WhatsApp)
# -------------------------
@app.template_filter("fmt_phone")
def fmt_phone(value):
    d = digits_only(value)
    if len(d) == 8:
        return f"{d[:4]}-{d[4:]}"
    return value or ""


@app.template_filter("fmt_cedula")
def fmt_cedula(value):
    d = digits_only(value)
    # 9 dígitos: 5-0448-0768
    if len(d) == 9:
        return f"{d[0]}-{d[1:5]}-{d[5:]}"
    # 8 dígitos: 0448-0768
    if len(d) == 8:
        return f"{d[:4]}-{d[4:]}"
    return value or ""


@app.template_filter("wa_link")
def wa_link(phone):
    d = digits_only(phone)
    if len(d) == 8:
        return f"https://wa.me/506{d}"  # Costa Rica
    if len(d) in (11, 12, 13):  # ej: 506xxxxxxxx
        return f"https://wa.me/{d}"
    return ""

@app.template_filter("age")
def age_filter(birth_date):
    if not birth_date:
        return ""
    today = date.today()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


# -------------------------
# CLI: init-db
# -------------------------
@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all()
    print("✅ Tablas creadas.")


# -------------------------
# AUTH / DASHBOARD
# -------------------------
@app.get("/")
@login_required
def dashboard():
    selected = get_selected_service()
    active_count = Service.query.filter_by(is_active=True).count()
    return render_template("dashboard.html", selected=selected, active_count=active_count)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username, is_active=True).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Credenciales inválidas.", "danger")

    return render_template("login.html")


@app.get("/logout")
@login_required
def logout():
    logout_user()
    session.pop("selected_service_id", None)
    return redirect(url_for("login"))


# -------------------------
# YOUTH: LIST / CREATE / EDIT
# -------------------------
@app.get("/youth")
@login_required
@role_required("OPERATIVE", "ADMIN")
def youth_list():
    youth_rows = (
        db.session.query(Youth, Barrio)
        .outerjoin(Barrio, Barrio.id == Youth.barrio_id)
        .order_by(Youth.full_name.asc())
        .limit(500)
        .all()
    )
    return render_template("youth_list.html", youth_rows=youth_rows)


@app.route("/youth/new", methods=["GET", "POST"])
@login_required
@role_required("OPERATIVE", "ADMIN")
def youth_new():
    barrios = Barrio.query.filter_by(is_active=True).order_by(Barrio.name.asc()).all()

    if request.method == "POST":
        cedula_raw = request.form.get("cedula", "").strip()
        full_name = request.form.get("full_name", "").strip()
        phone_raw = request.form.get("phone", "").strip()
        barrio_id = int(request.form.get("barrio_id"))
        birth_date_str = request.form.get("birth_date", "").strip()

        cedula = digits_only(cedula_raw)
        phone = digits_only(phone_raw)

        if not full_name:
            flash("El nombre es obligatorio.", "warning")
            return redirect(url_for("youth_new"))

        if not validate_cedula(cedula):
            flash("Cédula inválida. Debe tener 8 o 9 dígitos (sin letras).", "warning")
            return redirect(url_for("youth_new"))

        if not validate_phone(phone):
            flash("Teléfono inválido. Debe tener 8 dígitos.", "warning")
            return redirect(url_for("youth_new"))

        # Birth date (obligatoria en el form + backend)
        bd = parse_birth_date(birth_date_str)
        if bd is None:
            flash("La fecha de nacimiento es obligatoria.", "warning")
            return redirect(url_for("youth_new"))
        if bd == "invalid":
            flash("Fecha de nacimiento inválida.", "warning")
            return redirect(url_for("youth_new"))
        if bd == "future":
            flash("La fecha de nacimiento no puede ser futura.", "warning")
            return redirect(url_for("youth_new"))

        if Youth.query.get(cedula):
            flash("Ya existe un joven con esa cédula.", "warning")
            return redirect(url_for("youth_new"))

        y = Youth(
            cedula=cedula,
            full_name=full_name,
            phone=phone,
            barrio_id=barrio_id,
            birth_date=bd,
        )
        db.session.add(y)
        db.session.commit()

        flash("Joven registrado", "success")
        return redirect(url_for("youth_list"))

    return render_template("youth_form.html", barrios=barrios)


@app.route("/youth/<cedula>/edit", methods=["GET", "POST"])
@login_required
@role_required("OPERATIVE", "ADMIN")
def youth_edit(cedula):
    y = Youth.query.get_or_404(cedula)
    barrios = Barrio.query.filter_by(is_active=True).order_by(Barrio.name.asc()).all()

    if request.method == "POST":
        y.full_name = request.form.get("full_name", "").strip()
        if not y.full_name:
            flash("El nombre es obligatorio.", "warning")
            return redirect(url_for("youth_edit", cedula=cedula))

        phone = digits_only(request.form.get("phone", "").strip())
        if not validate_phone(phone):
            flash("Teléfono inválido. Debe tener 8 dígitos.", "warning")
            return redirect(url_for("youth_edit", cedula=cedula))

        y.phone = phone
        y.barrio_id = int(request.form.get("barrio_id"))

        # Birth date (obligatoria)
        birth_date_str = request.form.get("birth_date", "").strip()
        bd = parse_birth_date(birth_date_str)
        if bd is None:
            flash("La fecha de nacimiento es obligatoria.", "warning")
            return redirect(url_for("youth_edit", cedula=cedula))
        if bd == "invalid":
            flash("Fecha de nacimiento inválida.", "warning")
            return redirect(url_for("youth_edit", cedula=cedula))
        if bd == "future":
            flash("La fecha de nacimiento no puede ser futura.", "warning")
            return redirect(url_for("youth_edit", cedula=cedula))

        y.birth_date = bd

        db.session.commit()
        flash("Joven actualizado", "success")
        return redirect(url_for("youth_list"))

    return render_template("youth_edit.html", y=y, barrios=barrios)


# -------------------------
# ADMIN: SERVICES + ATTENDANCE VIEW/EXPORT
# -------------------------
@app.route("/admin/services", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def admin_services():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            title = request.form.get("title", "").strip()
            date_str = request.form.get("service_date", "").strip()  # YYYY-MM-DD
            time_str = request.form.get("start_time", "").strip()  # HH:MM

            if not title or not date_str or not time_str:
                flash("Complete título, fecha y hora.", "warning")
                return redirect(url_for("admin_services"))

            starts_at = datetime.fromisoformat(f"{date_str} {time_str}:00")

            s = Service(
                title=title,
                service_date=starts_at.date(),
                starts_at=starts_at,
                ends_at=None,
                is_active=False,
                created_by=current_user.id,
            )
            db.session.add(s)
            db.session.commit()
            flash("Servicio creado (puedes activarlo)", "success")

        elif action == "toggle":
            service_id = int(request.form.get("service_id"))
            s = Service.query.get_or_404(service_id)

            s.is_active = not s.is_active
            s.ends_at = None if s.is_active else datetime.now()

            db.session.commit()
            flash("Estado actualizado", "success")

    services = Service.query.order_by(Service.starts_at.desc()).limit(200).all()
    return render_template("admin_services.html", services=services)


@app.get("/admin/attendance/<int:service_id>")
@login_required
@role_required("ADMIN")
def admin_attendance(service_id):
    s = Service.query.get_or_404(service_id)

    rows = (
        db.session.query(Attendance, Youth, User)
        .join(Youth, Youth.cedula == Attendance.youth_cedula)
        .join(User, User.id == Attendance.registered_by)
        .filter(Attendance.service_id == s.id)
        .order_by(Attendance.registered_at.asc())
        .all()
    )

    return render_template("admin_attendance.html", service=s, rows=rows)


@app.get("/admin/attendance/<int:service_id>/export.csv")
@login_required
@role_required("ADMIN")
def admin_attendance_export(service_id):
    s = Service.query.get_or_404(service_id)

    rows = (
        db.session.query(Attendance, Youth, User)
        .join(Youth, Youth.cedula == Attendance.youth_cedula)
        .join(User, User.id == Attendance.registered_by)
        .filter(Attendance.service_id == s.id)
        .order_by(Attendance.registered_at.asc())
        .all()
    )

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["cedula", "nombre", "contacto", "barrio", "whatsapp"])

    for att, y, u in rows:
        phone_digits = digits_only(y.phone)
        wa = f"https://wa.me/506{phone_digits}" if len(phone_digits) == 8 else ""

        phone_fmt = (
            f"{phone_digits[:4]}-{phone_digits[4:]}"
            if len(phone_digits) == 8
            else (y.phone or "")
        )

        ced = digits_only(y.cedula)
        if len(ced) == 9:
            ced_fmt = f"{ced[0]}-{ced[1:5]}-{ced[5:]}"
        elif len(ced) == 8:
            ced_fmt = f"{ced[:4]}-{ced[4:]}"
        else:
            ced_fmt = y.cedula or ""

        barrio_name = y.barrio.name if getattr(y, "barrio", None) else ""

        writer.writerow([ced_fmt, y.full_name, phone_fmt, barrio_name, wa])

    csv_data = output.getvalue()
    output.close()

    filename = f"asistencia_{s.service_date}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# -------------------------
# OPERATIVE: SELECT SERVICE + ATTENDANCE
# -------------------------
@app.get("/services/select")
@login_required
@role_required("OPERATIVE", "ADMIN")
def select_service():
    services = get_active_services()
    selected = get_selected_service()
    return render_template("select_service.html", services=services, selected=selected)


@app.post("/services/select/<int:service_id>")
@login_required
@role_required("OPERATIVE", "ADMIN")
def set_service(service_id):
    s = Service.query.filter_by(id=service_id, is_active=True).first()
    if not s:
        flash("Servicio no disponible o no está activo.", "warning")
        return redirect(url_for("select_service"))

    session["selected_service_id"] = s.id
    flash(f"Servicio seleccionado: {s.title}", "success")
    return redirect(url_for("attendance_active"))


@app.get("/attendance/active")
@login_required
@role_required("OPERATIVE", "ADMIN")
def attendance_active():
    active = get_selected_service()
    q = request.args.get("q", "").strip()

    rows = []
    candidates = []

    if active:
        rows = (
            db.session.query(Attendance, Youth)
            .join(Youth, Youth.cedula == Attendance.youth_cedula)
            .filter(Attendance.service_id == active.id)
            .order_by(Attendance.registered_at.desc())
            .all()
        )

        if q:
            candidates = (
                Youth.query.filter(
                    (Youth.cedula.like(f"%{q}%")) | (Youth.full_name.like(f"%{q}%"))
                )
                .order_by(Youth.full_name.asc())
                .limit(20)
                .all()
            )

    return render_template(
        "attendance_active.html",
        active=active,
        rows=rows,
        q=q,
        candidates=candidates,
    )


@app.post("/attendance/active/register")
@login_required
@role_required("OPERATIVE", "ADMIN")
def attendance_register_active():
    active = get_selected_service()
    if not active:
        flash("Primero seleccioná un servicio activo.", "warning")
        return redirect(url_for("select_service"))

    cedula = digits_only(request.form.get("cedula", "").strip())

    if not validate_cedula(cedula):
        flash("Cédula inválida. Debe tener 8 o 9 dígitos.", "warning")
        return redirect(url_for("attendance_active"))

    y = Youth.query.get(cedula)
    if not y:
        flash("No existe un joven con esa cédula. Regístralo primero.", "warning")
        return redirect(url_for("attendance_active"))

    try:
        att = Attendance(
            service_id=active.id,
            youth_cedula=cedula,
            registered_by=current_user.id,
        )
        db.session.add(att)
        db.session.commit()
        flash("Asistencia registrada", "success")
    except Exception:
        db.session.rollback()
        flash("Ya estaba registrado en este servicio.", "info")

    return redirect(url_for("attendance_active"))


# -------------------------
# Error handler
# -------------------------
@app.errorhandler(Exception)
def handle_exception(e):
    print("🔥 ERROR:", repr(e))
    traceback.print_exc()
    return "Error interno. Revisa logs.", 500


# -------------------------
# DB TEST
# -------------------------
@app.get("/db-test")
def db_test():
    db.session.execute(text("SELECT 1"))
    return "DB OK"


if __name__ == "__main__":
    app.run(debug=True)
