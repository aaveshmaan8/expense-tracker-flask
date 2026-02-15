from flask import Flask, render_template, request, redirect, session, flash, url_for, Response
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import psycopg2
import psycopg2.extras
import sqlite3
import csv, io, os
from datetime import datetime

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

    if USE_POSTGRES:
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
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                amount REAL NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                user_id INTEGER,
                month TEXT,
                year TEXT,
                amount REAL,
                PRIMARY KEY (user_id, month, year)
            )
        """)

    conn.commit()
    cur.close()
    conn.close()


# ================= DECORATOR =================
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

        if USE_POSTGRES:
            cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        else:
            cur.execute("SELECT id FROM users WHERE username=?", (username,))

        if cur.fetchone():
            flash("Username already exists.", "error")
            conn.close()
            return render_template("register.html")

        hashed = generate_password_hash(password)

        if USE_POSTGRES:
            cur.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed))
        else:
            cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed))

        conn.commit()
        conn.close()
        flash("Account created successfully!", "success")
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
            cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        else:
            cur.execute("SELECT * FROM users WHERE username=?", (username,))

        user = cur.fetchone()
        conn.close()

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid credentials", "error")
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

    if USE_POSTGRES:
        query = "SELECT * FROM expenses WHERE user_id=%s"
    else:
        query = "SELECT * FROM expenses WHERE user_id=?"

    params = [session["user_id"]]

    conn = get_db()
    cur = conn.cursor()

    # Filters
    if month:
        if USE_POSTGRES:
            query += " AND TO_CHAR(date,'MM')=%s"
        else:
            query += " AND substr(date,6,2)=?"
        params.append(month)

    if year:
        if USE_POSTGRES:
            query += " AND TO_CHAR(date,'YYYY')=%s"
        else:
            query += " AND substr(date,1,4)=?"
        params.append(year)

    if start_date:
        query += " AND date >= %s" if USE_POSTGRES else " AND date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND date <= %s" if USE_POSTGRES else " AND date <= ?"
        params.append(end_date)

    cur.execute(query, params)
    expenses = cur.fetchall()

    # ================= ANALYTICS =================
    total = sum(float(e["amount"]) for e in expenses)

    category_summary = {}
    monthly_summary = {}

    for e in expenses:
        category = e["category"]
        amount = float(e["amount"])
        date_key = str(e["date"])[:7]

        category_summary[category] = category_summary.get(category, 0) + amount
        monthly_summary[date_key] = monthly_summary.get(date_key, 0) + amount

    # Budget check
    budget = None
    if month and year:
        if USE_POSTGRES:
            cur.execute("SELECT amount FROM budgets WHERE user_id=%s AND month=%s AND year=%s",
                        (session["user_id"], month, year))
        else:
            cur.execute("SELECT amount FROM budgets WHERE user_id=? AND month=? AND year=?",
                        (session["user_id"], month, year))

        row = cur.fetchone()
        if row:
            budget = float(row["amount"])

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


# ================= ADD / EDIT / DELETE =================
@app.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()

        query = """
            INSERT INTO expenses (user_id, date, category, description, amount)
            VALUES (%s, %s, %s, %s, %s)
        """ if USE_POSTGRES else """
            INSERT INTO expenses (user_id, date, category, description, amount)
            VALUES (?, ?, ?, ?, ?)
        """

        cur.execute(query, (
            session["user_id"],
            request.form["date"],
            request.form["category"],
            request.form["description"],
            float(request.form["amount"])
        ))

        conn.commit()
        conn.close()
        return redirect(url_for("index"))

    return render_template("add_expense.html")


@app.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_expense(id):
    conn = get_db()
    cur = conn.cursor()

    query = "SELECT * FROM expenses WHERE id=%s AND user_id=%s" if USE_POSTGRES else \
            "SELECT * FROM expenses WHERE id=? AND user_id=?"

    cur.execute(query, (id, session["user_id"]))
    expense = cur.fetchone()

    if not expense:
        conn.close()
        return "Unauthorized", 403

    if request.method == "POST":
        update_query = """
            UPDATE expenses SET category=%s, description=%s, amount=%s
            WHERE id=%s AND user_id=%s
        """ if USE_POSTGRES else """
            UPDATE expenses SET category=?, description=?, amount=?
            WHERE id=? AND user_id=?
        """

        cur.execute(update_query, (
            request.form["category"],
            request.form["description"],
            float(request.form["amount"]),
            id,
            session["user_id"]
        ))

        conn.commit()
        conn.close()
        return redirect(url_for("index"))

    conn.close()
    return render_template("edit_expense.html", expense=expense)


@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = get_db()
    cur = conn.cursor()

    query = "DELETE FROM expenses WHERE id=%s AND user_id=%s" if USE_POSTGRES else \
            "DELETE FROM expenses WHERE id=? AND user_id=?"

    cur.execute(query, (id, session["user_id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


# ================= EXPORT =================
@app.route("/export/csv")
@login_required
def export_csv():
    conn = get_db()
    cur = conn.cursor()

    query = "SELECT date, category, description, amount FROM expenses WHERE user_id=%s" \
        if USE_POSTGRES else \
        "SELECT date, category, description, amount FROM expenses WHERE user_id=?"

    cur.execute(query, (session["user_id"],))
    rows = cur.fetchall()
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


# ================= INIT DB ON START =================
with app.app_context():
    try:
        init_db()
        print("Database initialized successfully.")
    except Exception as e:
        print("Database init error:", e)


# ================= MAIN =================
if __name__ == "__main__":
    app.run(debug=True)
