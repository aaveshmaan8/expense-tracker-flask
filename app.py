from flask import Flask, render_template, request, redirect, session, flash, url_for, Response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import psycopg2
import psycopg2.extras
import csv, io, os

# ================= APP SETUP =================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "expense-secret")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# ================= DATABASE =================
def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.DictCursor
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            amount NUMERIC NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            month TEXT NOT NULL,
            year TEXT NOT NULL,
            amount NUMERIC NOT NULL,
            PRIMARY KEY (user_id, month, year)
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

# ================= DECORATORS =================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# ================= AUTH =================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("register.html")

        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cur.fetchone():
            flash("Username already exists.", "error")
            cur.close()
            conn.close()
            return render_template("register.html")

        cur.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s)",
            (username, generate_password_hash(password))
        )

        conn.commit()
        cur.close()
        conn.close()

        flash("Account created successfully. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["is_admin"] = user["is_admin"]

        return redirect(url_for("index"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ================= DASHBOARD =================
@app.route("/")
@login_required
def index():
    month = request.args.get("month")
    year = request.args.get("year")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    query = "SELECT * FROM expenses WHERE user_id=%s"
    params = [session["user_id"]]

    if month:
        query += " AND TO_CHAR(date,'MM')=%s"
        params.append(month)

    if year:
        query += " AND TO_CHAR(date,'YYYY')=%s"
        params.append(year)

    if start_date:
        query += " AND date >= %s"
        params.append(start_date)

    if end_date:
        query += " AND date <= %s"
        params.append(end_date)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, params)
    expenses = cur.fetchall()

    total = sum(float(e["amount"]) for e in expenses)

    category_summary = {}
    monthly_summary = {}

    for e in expenses:
        category_summary[e["category"]] = category_summary.get(e["category"], 0) + float(e["amount"])
        key = e["date"].strftime("%Y-%m")
        monthly_summary[key] = monthly_summary.get(key, 0) + float(e["amount"])

    budget = None
    if month and year:
        cur.execute(
            "SELECT amount FROM budgets WHERE user_id=%s AND month=%s AND year=%s",
            (session["user_id"], month, year)
        )
        row = cur.fetchone()
        if row:
            budget = float(row["amount"])

    cur.close()
    conn.close()

    return render_template(
        "index.html",
        expenses=expenses,
        total=total,
        category_summary=category_summary,
        monthly_summary=monthly_summary,
        selected_month=month,
        selected_year=year,
        start_date=start_date,
        end_date=end_date,
        budget=budget
    )

# ================= EXPENSES =================
@app.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO expenses (user_id, date, category, description, amount)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            session["user_id"],
            request.form["date"],
            request.form["category"],
            request.form["description"],
            float(request.form["amount"])
        ))

        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for("index"))

    return render_template("add_expense.html")

@app.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_expense(id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM expenses WHERE id=%s AND user_id=%s",
        (id, session["user_id"])
    )
    expense = cur.fetchone()

    if not expense:
        cur.close()
        conn.close()
        return "Unauthorized", 403

    if request.method == "POST":
        cur.execute("""
            UPDATE expenses
            SET category=%s, description=%s, amount=%s
            WHERE id=%s AND user_id=%s
        """, (
            request.form["category"],
            request.form["description"],
            float(request.form["amount"]),
            id,
            session["user_id"]
        ))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for("index"))

    cur.close()
    conn.close()
    return render_template("edit_expense.html", expense=expense)

@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM expenses WHERE id=%s AND user_id=%s",
        (id, session["user_id"])
    )

    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("index"))

# ================= EXPORT =================
@app.route("/export/csv")
@login_required
def export_csv():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT date, category, description, amount FROM expenses WHERE user_id=%s",
        (session["user_id"],)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Category", "Description", "Amount"])
    for r in rows:
        writer.writerow([r["date"], r["category"], r["description"], r["amount"]])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=expenses.csv"}
    )

# ================= MAIN =================
if __name__ == "__main__":
    init_db()
    app.run(debug=True)
