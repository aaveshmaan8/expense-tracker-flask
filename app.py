from flask import Flask, render_template, request, redirect, session, flash, url_for, Response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import psycopg2
import psycopg2.extras
import sqlite3
import csv, io, os

# ================= APP SETUP =================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "expense-secret")

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = DATABASE_URL is not None


# ================= DATABASE =================
def get_db():
    if USE_POSTGRES:
        return psycopg2.connect(
            DATABASE_URL,
            cursor_factory=psycopg2.extras.DictCursor,
            sslmode="require"
        )
    else:
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # ✅ PREFIXED TABLES (NO CONFLICT)
    if USE_POSTGRES:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expense_users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expense_expenses (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES expense_users(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                amount NUMERIC NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expense_budgets (
                user_id INTEGER REFERENCES expense_users(id) ON DELETE CASCADE,
                month TEXT NOT NULL,
                year TEXT NOT NULL,
                amount NUMERIC NOT NULL,
                PRIMARY KEY (user_id, month, year)
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expense_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expense_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                amount REAL NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expense_budgets (
                user_id INTEGER,
                month TEXT,
                year TEXT,
                amount REAL,
                PRIMARY KEY (user_id, month, year)
            )
        """)

    conn.commit()
    conn.close()


# ================= DECORATOR =================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ================= AUTH =================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute("SELECT id FROM expense_users WHERE username=%s", (username,))
        else:
            cur.execute("SELECT id FROM expense_users WHERE username=?", (username,))

        if cur.fetchone():
            flash("Username already exists")
            return render_template("register.html")

        hashed = generate_password_hash(password)

        if USE_POSTGRES:
            cur.execute("INSERT INTO expense_users (username, password) VALUES (%s,%s)", (username, hashed))
        else:
            cur.execute("INSERT INTO expense_users (username, password) VALUES (?,?)", (username, hashed))

        conn.commit()
        conn.close()

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute("SELECT * FROM expense_users WHERE username=%s", (username,))
        else:
            cur.execute("SELECT * FROM expense_users WHERE username=?", (username,))

        user = cur.fetchone()
        conn.close()

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid login")
            return render_template("login.html")

        session["user_id"] = user["id"]
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
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("SELECT * FROM expense_expenses WHERE user_id=%s", (session["user_id"],))
    else:
        cur.execute("SELECT * FROM expense_expenses WHERE user_id=?", (session["user_id"],))
    
    category_summary = {}
    monthly_summary = {}
    expenses = cur.fetchall()
    total = sum(float(e["amount"]) for e in expenses)

    conn.close()

    return render_template(
        "index.html",
        expenses=expenses or [],
        total=float(total or 0),
        category_summary=dict(category_summary or {}),
        monthly_summary =dict(monthly_summary or {}),
    )


# ================= ADD =================
@app.route("/add", methods=["POST"])
@login_required
def add_expense():
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("""
            INSERT INTO expense_expenses (user_id,date,category,description,amount)
            VALUES (%s,%s,%s,%s,%s)
        """, (
            session["user_id"],
            request.form["date"],
            request.form["category"],
            request.form["description"],
            request.form["amount"]
        ))
    else:
        cur.execute("""
            INSERT INTO expense_expenses (user_id,date,category,description,amount)
            VALUES (?,?,?,?,?)
        """, (
            session["user_id"],
            request.form["date"],
            request.form["category"],
            request.form["description"],
            request.form["amount"]
        ))

    conn.commit()
    conn.close()
    return redirect(url_for("index"))


# ================= DELETE =================
@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("DELETE FROM expense_expenses WHERE id=%s", (id,))
    else:
        cur.execute("DELETE FROM expense_expenses WHERE id=?", (id,))

    conn.commit()
    conn.close()
    return redirect(url_for("index"))


# ================= EXPORT =================
@app.route("/export/csv")
@login_required
def export_csv():
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("SELECT * FROM expense_expenses WHERE user_id=%s", (session["user_id"],))
    else:
        cur.execute("SELECT * FROM expense_expenses WHERE user_id=?", (session["user_id"],))

    data = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Date", "Category", "Description", "Amount"])

    for row in data:
        writer.writerow([row["date"], row["category"], row["description"], row["amount"]])

    return Response(output.getvalue(), mimetype="text/csv")


# ================= INIT DB =================
with app.app_context():
    init_db()


# ================= MAIN =================
if __name__ == "__main__":
    app.run(debug=True)