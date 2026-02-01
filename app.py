# app.py
from __future__ import annotations

import csv
from datetime import datetime, date, timedelta
from io import StringIO
import os
import re
import secrets
import traceback
import click

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
from sqlalchemy import func, distinct, or_, text
from sqlalchemy.exc import IntegrityError

from config import Config
from models import db, User, Youth, Barrio, Service, Attendance
from helpers import role_required


# ============================================================
# CONFIG
# ============================================================
IDLE_MINUTES = int(os.getenv("IDLE_MINUTES", "15"))  # inactividad
MAX_SERVICES_FOR_STREAK = int(os.getenv("MAX_SERVICES_FOR_STREAK", "200"))
SYSTEM_USER = os.getenv("SYSTEM_USER", "system").strip()


# ============================================================
# UTILS
# ============================================================
def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def validate_cedula(value: str) -> bool:
    d = digits_only(value)
    return d.isdigit() and len(d) in (8, 9)


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


# ============================================================
# APP + DB
# ============================================================
app = Flask(__name__)
app.config.from_object(Config)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=IDLE_MINUTES)
db.init_app(app)


# ============================================================
# LOGIN
# ============================================================
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ============================================================
# ROOT HELPERS
# ============================================================
def is_root_user() -> bool:
    return current_user.is_authenticated and getattr(current_user, "role", None) == "ROOT"


def get_or_create_system_user() -> User:
    """
    Usuario técnico para reasignar asistencias cuando se elimina un usuario real.
    """
    u = User.query.filter_by(username=SYSTEM_USER).first()
    if u:
        return u

    # Crear usuario "system" (no se usa para login real)
    u = User(username=SYSTEM_USER, role="ROOT", is_active=False)
    u.set_password(secrets.token_urlsafe(16))  # password random
    if hasattr(u, "session_token"):
        u.session_token = None

    db.session.add(u)
    db.session.commit()
    return u


@app.context_processor
def inject_helpers():
    return {"is_root_user": is_root_user}


# ============================================================
# SERVICES HELPERS
# ============================================================
def get_active_services():
    return Service.query.filter_by(is_active=True).order_by(Service.starts_at.desc()).all()


def get_selected_service():
    service_id = session.get("selected_service_id")
    if not service_id:
        return None
    return Service.query.filter_by(id=service_id, is_active=True).first()


# ============================================================
# TEMPLATE FILTERS
# ============================================================
@app.template_filter("fmt_phone")
def fmt_phone(value):
    d = digits_only(value)
    if len(d) == 8:
        return f"{d[:4]}-{d[4:]}"
    return value or ""


@app.template_filter("fmt_cedula")
def fmt_cedula(value):
    d = digits_only(value)
    if len(d) == 9:
        return f"{d[0]}-{d[1:5]}-{d[5:]}"
    if len(d) == 8:
        return f"{d[:4]}-{d[4:]}"
    return value or ""


@app.template_filter("wa_link")
def wa_link(phone):
    d = digits_only(phone)
    if len(d) == 8:
        return f"https://wa.me/506{d}"
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


# ============================================================
# SESSION POLICIES (inactividad + 1 sesión por usuario)
# ============================================================
@app.before_request
def enforce_session_policies():
    if not current_user.is_authenticated:
        return

    now = datetime.utcnow()

    # 1) Inactividad
    last = session.get("last_activity")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt) > timedelta(minutes=IDLE_MINUTES):
                logout_user()
                session.clear()
                flash(f"Sesión cerrada por inactividad ({IDLE_MINUTES} minutos).", "warning")
                return redirect(url_for("login"))
        except Exception:
            logout_user()
            session.clear()
            return redirect(url_for("login"))

    # 2) Solo 1 sesión activa por usuario
    current_token = session.get("session_token")
    user_token = getattr(current_user, "session_token", None)
    if not current_token or not user_token or user_token != current_token:
        logout_user()
        session.clear()
        flash("Tu sesión fue iniciada en otro dispositivo. Se cerró esta sesión.", "warning")
        return redirect(url_for("login"))

    session["last_activity"] = now.isoformat()
    session.permanent = True


# ============================================================
# CLI COMMANDS
# ============================================================
@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all()
    print("Tablas creadas.")


@app.cli.command("bootstrap-root")
def bootstrap_root():
    """
    Crea/actualiza un ROOT desde variables de entorno:
      ROOT_ADMIN_USER (default root)
      ROOT_ADMIN_PASS (obligatorio)
      ROOT_ADMIN_ROLE (default ROOT)
    """
    username = os.getenv("ROOT_ADMIN_USER", "root").strip()
    password = os.getenv("ROOT_ADMIN_PASS", "").strip()
    role = os.getenv("ROOT_ADMIN_ROLE", "ROOT").strip().upper()

    if not password:
        raise click.ClickException("Falta ROOT_ADMIN_PASS en variables de entorno.")

    if role not in ("ROOT", "ADMIN", "OPERATIVE"):
        raise click.ClickException("ROOT_ADMIN_ROLE debe ser ROOT, ADMIN u OPERATIVE (recomendado ROOT).")

    with app.app_context():
        u = User.query.filter_by(username=username).first()
        if not u:
            u = User(username=username, role=role, is_active=True)
            u.set_password(password)
            db.session.add(u)
        else:
            u.role = role
            u.is_active = True
            u.set_password(password)

        if hasattr(u, "session_token"):
            u.session_token = None  # invalida sesiones

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise click.ClickException("No se pudo crear/actualizar root (revisa si el username ya existe).")

        print(f"Root listo: {username} ({role})")


# ============================================================
# AUTH / DASHBOARD
# ============================================================
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
            token = secrets.token_urlsafe(32)
            if hasattr(user, "session_token"):
                user.session_token = token
                db.session.commit()

            login_user(user)
            session["session_token"] = token
            session["last_activity"] = datetime.utcnow().isoformat()
            session.permanent = True
            return redirect(url_for("dashboard"))

        flash("Credenciales inválidas.", "danger")

    return render_template("login.html")


@app.get("/logout")
@login_required
def logout():
    try:
        if hasattr(current_user, "session_token"):
            current_user.session_token = None
            db.session.commit()
    except Exception:
        db.session.rollback()

    logout_user()
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# YOUTH: LIST / CREATE / EDIT / DELETE (delete solo ROOT)
# ============================================================
@app.get("/youth")
@login_required
@role_required("OPERATIVE", "ADMIN", "ROOT")
def youth_list():
    q = (request.args.get("q") or "").strip()
    q_digits = digits_only(q)

    def apply_search(query):
        if not q:
            return query
        conditions = [Youth.full_name.ilike(f"%{q}%")]
        if q_digits:
            conditions.append(Youth.cedula.like(f"%{q_digits}%"))
            conditions.append(Youth.phone.like(f"%{q_digits}%"))
        return query.filter(or_(*conditions))

    # ADMIN/ROOT: total + racha real
    if current_user.role in ("ADMIN", "ROOT"):
        subq = (
            db.session.query(
                Attendance.youth_cedula.label("cedula"),
                func.count(distinct(Attendance.service_id)).label("att_count"),
            )
            .group_by(Attendance.youth_cedula)
            .subquery()
        )

        query = (
            db.session.query(
                Youth,
                Barrio,
                func.coalesce(subq.c.att_count, 0).label("att_count"),
            )
            .outerjoin(Barrio, Barrio.id == Youth.barrio_id)
            .outerjoin(subq, subq.c.cedula == Youth.cedula)
        )

        query = apply_search(query)
        youth_rows = query.order_by(Youth.full_name.asc()).limit(500).all()

        # racha real (consecutivos desde el servicio más reciente hacia atrás)
        streaks: dict[str, int] = {}

        recent_services = (
            Service.query.filter(Service.starts_at <= datetime.now())
            .order_by(Service.starts_at.desc())
            .limit(MAX_SERVICES_FOR_STREAK)
            .all()
        )
        service_ids = [s.id for s in recent_services]  # newest -> oldest

        if service_ids and youth_rows:
            cedulas = [row[0].cedula for row in youth_rows]

            pairs = (
                db.session.query(Attendance.youth_cedula, Attendance.service_id)
                .filter(Attendance.youth_cedula.in_(cedulas))
                .filter(Attendance.service_id.in_(service_ids))
                .all()
            )

            attended: dict[str, set[int]] = {}
            for c, sid in pairs:
                attended.setdefault(c, set()).add(sid)

            for c in cedulas:
                s = 0
                aset = attended.get(c, set())
                for sid in service_ids:
                    if sid in aset:
                        s += 1
                    else:
                        break
                streaks[c] = s
        else:
            for row in youth_rows:
                streaks[row[0].cedula] = 0

        total = query.count() if q else Youth.query.count()
        return render_template("youth_list.html", youth_rows=youth_rows, q=q, total=total, streaks=streaks)

    # OPERATIVE: listado simple
    query = db.session.query(Youth, Barrio).outerjoin(Barrio, Barrio.id == Youth.barrio_id)
    query = apply_search(query)

    youth_rows = query.order_by(Youth.full_name.asc()).limit(500).all()
    total = query.count() if q else Youth.query.count()
    return render_template("youth_list.html", youth_rows=youth_rows, q=q, total=total)


@app.route("/youth/new", methods=["GET", "POST"])
@login_required
@role_required("OPERATIVE", "ADMIN", "ROOT")
def youth_new():
    barrios = Barrio.query.filter_by(is_active=True).order_by(Barrio.name.asc()).all()

    if request.method == "POST":
        cedula = digits_only(request.form.get("cedula", "").strip())
        full_name = request.form.get("full_name", "").strip()
        phone = digits_only(request.form.get("phone", "").strip())
        barrio_id = int(request.form.get("barrio_id"))
        birth_date_str = request.form.get("birth_date", "").strip()

        if not full_name:
            flash("El nombre es obligatorio.", "warning")
            return redirect(url_for("youth_new"))

        if not validate_cedula(cedula):
            flash("Cédula inválida. Debe tener 8 o 9 dígitos (sin letras).", "warning")
            return redirect(url_for("youth_new"))

        if not validate_phone(phone):
            flash("Teléfono inválido. Debe tener 8 dígitos.", "warning")
            return redirect(url_for("youth_new"))

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

        y = Youth(cedula=cedula, full_name=full_name, phone=phone, barrio_id=barrio_id, birth_date=bd)
        db.session.add(y)
        db.session.commit()

        flash("Joven registrado", "success")
        return redirect(url_for("youth_list"))

    return render_template("youth_form.html", barrios=barrios)


@app.route("/youth/<cedula>/edit", methods=["GET", "POST"])
@login_required
@role_required("OPERATIVE", "ADMIN", "ROOT")
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


@app.post("/youth/<cedula>/delete")
@login_required
@role_required("ROOT")
def youth_delete(cedula):
    y = Youth.query.get_or_404(cedula)
    try:
        Attendance.query.filter_by(youth_cedula=cedula).delete(synchronize_session=False)
        db.session.delete(y)
        db.session.commit()
        flash("Joven eliminado (y asistencias asociadas)", "success")
    except Exception:
        db.session.rollback()
        flash("No se pudo eliminar. Revisá logs.", "danger")
    return redirect(url_for("youth_list"))


# ============================================================
# ADMIN/ROOT: SERVICES
# ============================================================
@app.route("/admin/services", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "ROOT")
def admin_services():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            title = request.form.get("title", "").strip()
            date_str = request.form.get("service_date", "").strip()
            time_str = request.form.get("start_time", "").strip()

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


# ============================================================
# ROOT: USERS
# ============================================================
@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@role_required("ROOT")
def admin_users():
    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "create":
            username = request.form.get("username", "").strip()
            role = request.form.get("role", "").strip().upper()
            password = request.form.get("password", "")

            if not username or not password:
                flash("Complete usuario y contraseña.", "warning")
                return redirect(url_for("admin_users"))

            if role not in ("ROOT", "ADMIN", "OPERATIVE"):
                flash("Rol inválido.", "warning")
                return redirect(url_for("admin_users"))

            if User.query.filter_by(username=username).first():
                flash("Ese usuario ya existe.", "warning")
                return redirect(url_for("admin_users"))

            u = User(username=username, role=role, is_active=True)
            u.set_password(password)
            if hasattr(u, "session_token"):
                u.session_token = None

            db.session.add(u)
            db.session.commit()
            flash("Usuario creado", "success")
            return redirect(url_for("admin_users"))

        if action == "toggle":
            user_id = int(request.form.get("user_id"))
            u = User.query.get_or_404(user_id)

            if u.id == current_user.id:
                flash("No podés desactivarte a vos mismo.", "warning")
                return redirect(url_for("admin_users"))

            u.is_active = not u.is_active
            db.session.commit()
            flash("Estado actualizado", "success")
            return redirect(url_for("admin_users"))

        if action == "reset_password":
            user_id = int(request.form.get("user_id"))
            new_password = request.form.get("new_password", "")

            if not new_password:
                flash("Ingrese la nueva contraseña.", "warning")
                return redirect(url_for("admin_users"))

            u = User.query.get_or_404(user_id)
            u.set_password(new_password)
            if hasattr(u, "session_token"):
                u.session_token = None  # invalida sesiones existentes

            db.session.commit()
            flash("Contraseña actualizada", "success")
            return redirect(url_for("admin_users"))

    users = User.query.order_by(User.role.desc(), User.username.asc()).all()
    return render_template("admin_users.html", users=users)


@app.post("/admin/users/<int:user_id>/delete")
@login_required
@role_required("ROOT")
def admin_user_delete(user_id):
    if user_id == current_user.id:
        flash("No podés eliminar tu propio usuario.", "warning")
        return redirect(url_for("admin_users"))

    u = User.query.get_or_404(user_id)

    if u.username == SYSTEM_USER:
        flash("No se puede eliminar el usuario del sistema.", "warning")
        return redirect(url_for("admin_users"))

    try:
        system_user = get_or_create_system_user()

        Attendance.query.filter_by(registered_by=u.id).update(
            {"registered_by": system_user.id},
            synchronize_session=False,
        )

        db.session.delete(u)
        db.session.commit()
        flash("Usuario eliminado. Sus asistencias se reasignaron a SYSTEM", "success")
    except Exception:
        db.session.rollback()
        flash("No se pudo eliminar el usuario. Revisá logs.", "danger")

    return redirect(url_for("admin_users"))


# ============================================================
# ROOT: BARRIOS
# ============================================================
@app.route("/admin/barrios", methods=["GET", "POST"])
@login_required
@role_required("ROOT")
def admin_barrios():
    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "create":
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Ingrese el nombre del barrio.", "warning")
                return redirect(url_for("admin_barrios"))

            name_clean = " ".join(name.split())

            if Barrio.query.filter(func.lower(Barrio.name) == name_clean.lower()).first():
                flash("Ese barrio ya existe.", "warning")
                return redirect(url_for("admin_barrios"))

            b = Barrio(name=name_clean, is_active=True)
            db.session.add(b)
            db.session.commit()
            flash("Barrio creado", "success")
            return redirect(url_for("admin_barrios"))

        if action == "rename":
            barrio_id = int(request.form.get("barrio_id"))
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Ingrese el nuevo nombre.", "warning")
                return redirect(url_for("admin_barrios"))

            name_clean = " ".join(name.split())

            exists = Barrio.query.filter(
                func.lower(Barrio.name) == name_clean.lower(),
                Barrio.id != barrio_id,
            ).first()
            if exists:
                flash("Ya existe otro barrio con ese nombre.", "warning")
                return redirect(url_for("admin_barrios"))

            b = Barrio.query.get_or_404(barrio_id)
            b.name = name_clean
            db.session.commit()
            flash("Barrio actualizado", "success")
            return redirect(url_for("admin_barrios"))

        if action == "toggle":
            barrio_id = int(request.form.get("barrio_id"))
            b = Barrio.query.get_or_404(barrio_id)
            b.is_active = not b.is_active
            db.session.commit()
            flash("Estado actualizado", "success")
            return redirect(url_for("admin_barrios"))

    barrios = Barrio.query.order_by(Barrio.is_active.desc(), Barrio.name.asc()).all()
    return render_template("admin_barrios.html", barrios=barrios)


# ============================================================
# ADMIN/ROOT: ATTENDANCE VIEW + EXPORT
# ============================================================
@app.get("/admin/attendance/<int:service_id>")
@login_required
@role_required("ADMIN", "ROOT")
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
@role_required("ADMIN", "ROOT")
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


# ============================================================
# OPERATIVE/ADMIN/ROOT: SELECT SERVICE + ATTENDANCE
# ============================================================
@app.get("/services/select")
@login_required
@role_required("OPERATIVE", "ADMIN", "ROOT")
def select_service():
    services = get_active_services()
    selected = get_selected_service()
    return render_template("select_service.html", services=services, selected=selected)


@app.post("/services/select/<int:service_id>")
@login_required
@role_required("OPERATIVE", "ADMIN", "ROOT")
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
@role_required("OPERATIVE", "ADMIN", "ROOT")
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
            q_digits = digits_only(q)
            conditions = [Youth.full_name.ilike(f"%{q}%")]
            if q_digits:
                conditions.append(Youth.cedula.like(f"%{q_digits}%"))
                conditions.append(Youth.phone.like(f"%{q_digits}%"))

            candidates = (
                Youth.query.filter(or_(*conditions))
                .order_by(Youth.full_name.asc())
                .limit(20)
                .all()
            )

    return render_template("attendance_active.html", active=active, rows=rows, q=q, candidates=candidates)


@app.post("/attendance/active/register")
@login_required
@role_required("OPERATIVE", "ADMIN", "ROOT")
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
        att = Attendance(service_id=active.id, youth_cedula=cedula, registered_by=current_user.id)
        db.session.add(att)
        db.session.commit()
        flash("Asistencia registrada", "success")
    except Exception:
        db.session.rollback()
        flash("Ya estaba registrado en este servicio.", "info")

    return redirect(url_for("attendance_active"))


# ============================================================
# ERROR HANDLER
# ============================================================
@app.errorhandler(Exception)
def handle_exception(e):
    print("🔥 ERROR:", repr(e))
    traceback.print_exc()
    return "Error interno. Revisa logs.", 500


# ============================================================
# DB TEST
# ============================================================
@app.get("/db-test")
def db_test():
    db.session.execute(text("SELECT 1"))
    return "DB OK"


if __name__ == "__main__":
    app.run(debug=True)
