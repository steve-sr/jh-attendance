from datetime import datetime
import csv
from io import StringIO

from flask import Flask, render_template, request, redirect, url_for, flash, Response, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from sqlalchemy import text

from config import Config
from models import db, User, Youth, Barrio, Service, Attendance
from helpers import role_required


app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# --- Login Manager ---
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# --- Helpers: servicios activos y servicio seleccionado ---
def get_active_services():
    return Service.query.filter_by(is_active=True).order_by(Service.starts_at.desc()).all()

def get_selected_service():
    service_id = session.get("selected_service_id")
    if not service_id:
        return None
    return Service.query.filter_by(id=service_id, is_active=True).first()


# --- CLI: init-db ---
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
    session.pop("selected_service_id", None)  # opcional: limpiar servicio seleccionado
    return redirect(url_for("login"))


# -------------------------
# YOUTH: LIST / CREATE / EDIT
# -------------------------
@app.get("/youth")
@login_required
@role_required("OPERATIVE", "ADMIN")
def youth_list():
    youth = Youth.query.order_by(Youth.full_name.asc()).limit(500).all()
    return render_template("youth_list.html", youth=youth)

@app.route("/youth/new", methods=["GET", "POST"])
@login_required
@role_required("OPERATIVE", "ADMIN")
def youth_new():
    barrios = Barrio.query.filter_by(is_active=True).order_by(Barrio.name.asc()).all()

    if request.method == "POST":
        cedula = request.form["cedula"].strip()
        full_name = request.form["full_name"].strip()
        phone = request.form["phone"].strip()
        barrio_id = int(request.form["barrio_id"])

        if Youth.query.get(cedula):
            flash("Ya existe un joven con esa cédula.", "warning")
            return redirect(url_for("youth_new"))

        y = Youth(cedula=cedula, full_name=full_name, phone=phone, barrio_id=barrio_id)
        db.session.add(y)
        db.session.commit()
        flash("Joven registrado ✅", "success")
        return redirect(url_for("youth_list"))

    return render_template("youth_form.html", barrios=barrios)

@app.route("/youth/<cedula>/edit", methods=["GET", "POST"])
@login_required
@role_required("OPERATIVE", "ADMIN")
def youth_edit(cedula):
    y = Youth.query.get_or_404(cedula)
    barrios = Barrio.query.filter_by(is_active=True).order_by(Barrio.name.asc()).all()

    if request.method == "POST":
        y.full_name = request.form["full_name"].strip()
        y.phone = request.form["phone"].strip()
        y.barrio_id = int(request.form["barrio_id"])
        db.session.commit()
        flash("Joven actualizado ✅", "success")
        return redirect(url_for("youth_list"))

    return render_template("youth_edit.html", y=y, barrios=barrios)


# -------------------------
# ADMIN: SERVICES (CREATE + TOGGLE) + ATTENDANCE VIEW/EXPORT
# -------------------------
@app.route("/admin/services", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def admin_services():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            title = request.form.get("title", "").strip()
            date_str = request.form.get("service_date", "").strip()   # YYYY-MM-DD
            time_str = request.form.get("start_time", "").strip()     # HH:MM

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
                created_by=current_user.id
            )
            db.session.add(s)
            db.session.commit()
            flash("Servicio creado ✅ (puedes activarlo)", "success")

        elif action == "toggle":
            service_id = int(request.form.get("service_id"))
            s = Service.query.get_or_404(service_id)
            s.is_active = not s.is_active
            s.ends_at = None if s.is_active else datetime.now()
            db.session.commit()
            flash("Estado actualizado ✅", "success")

    services = Service.query.order_by(Service.starts_at.desc()).limit(200).all()
    return render_template("admin_services.html", services=services)

@app.get("/admin/attendance/<int:service_id>")
@login_required
@role_required("ADMIN")
def admin_attendance(service_id):
    s = Service.query.get_or_404(service_id)
    rows = (db.session.query(Attendance, Youth, User)
            .join(Youth, Youth.cedula == Attendance.youth_cedula)
            .join(User, User.id == Attendance.registered_by)
            .filter(Attendance.service_id == s.id)
            .order_by(Attendance.registered_at.asc())
            .all())
    return render_template("admin_attendance.html", service=s, rows=rows)

@app.get("/admin/attendance/<int:service_id>/export.csv")
@login_required
@role_required("ADMIN")
def admin_attendance_export(service_id):
    s = Service.query.get_or_404(service_id)
    rows = (db.session.query(Attendance, Youth, User)
            .join(Youth, Youth.cedula == Attendance.youth_cedula)
            .join(User, User.id == Attendance.registered_by)
            .filter(Attendance.service_id == s.id)
            .order_by(Attendance.registered_at.asc())
            .all())

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "service_date", "service_title", "cedula", "full_name", "phone",
        "barrio", "registered_at", "registered_by"
    ])

    for att, y, u in rows:
        writer.writerow([
            str(s.service_date),
            s.title,
            y.cedula,
            y.full_name,
            y.phone,
            y.barrio.name if y.barrio else "",
            att.registered_at.strftime("%Y-%m-%d %H:%M:%S") if att.registered_at else "",
            u.username
        ])

    csv_data = output.getvalue()
    output.close()

    filename = f"asistencia_{s.service_date}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
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
        rows = (db.session.query(Attendance, Youth)
                .join(Youth, Youth.cedula == Attendance.youth_cedula)
                .filter(Attendance.service_id == active.id)
                .order_by(Attendance.registered_at.desc())
                .all())

        if q:
            candidates = (Youth.query
                          .filter((Youth.cedula.like(f"%{q}%")) | (Youth.full_name.like(f"%{q}%")))
                          .order_by(Youth.full_name.asc())
                          .limit(20)
                          .all())

    return render_template("attendance_active.html", active=active, rows=rows, q=q, candidates=candidates)

@app.post("/attendance/active/register")
@login_required
@role_required("OPERATIVE", "ADMIN")
def attendance_register_active():
    active = get_selected_service()
    if not active:
        flash("Primero seleccioná un servicio activo.", "warning")
        return redirect(url_for("select_service"))

    cedula = request.form.get("cedula", "").strip()
    y = Youth.query.get(cedula)
    if not y:
        flash("No existe un joven con esa cédula. Regístralo primero.", "warning")
        return redirect(url_for("attendance_active"))

    try:
        att = Attendance(service_id=active.id, youth_cedula=cedula, registered_by=current_user.id)
        db.session.add(att)
        db.session.commit()
        flash("Asistencia registrada ✅", "success")
    except Exception:
        db.session.rollback()
        flash("Ya estaba registrado en este servicio.", "info")

    return redirect(url_for("attendance_active"))


# -------------------------
# DB TEST
# -------------------------
@app.get("/db-test")
def db_test():
    db.session.execute(text("SELECT 1"))
    return "DB OK ✅"


if __name__ == "__main__":
    app.run(debug=True)
