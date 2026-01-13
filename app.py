from flask import (
    Flask, render_template, request, redirect,
    session, flash, url_for, Response
)
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import csv, io

app = Flask(__name__)
app.secret_key = "expense-secret"


# ================= DATABASE =================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # ✅ enable FK
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            amount REAL NOT NULL CHECK(amount >= 0),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            user_id INTEGER NOT NULL,
            month TEXT NOT NULL,
            year TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount >= 0),
            PRIMARY KEY (user_id, month, year),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
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


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("is_admin") != 1:
            return "Access Denied", 403
        return f(*args, **kwargs)
    return wrapper


# ================= AUTH =================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        if not username:
            flash("Username cannot be empty.", "error")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("register.html")

        conn = get_db()
        if conn.execute(
            "SELECT id FROM users WHERE username=?",
            (username,)
        ).fetchone():
            conn.close()
            flash("Username already exists.", "error")
            return render_template("register.html")

        conn.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, generate_password_hash(password))
        )
        conn.commit()
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
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()
        conn.close()

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["is_admin"] = user["is_admin"]

        flash("Welcome back!", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


# ================= DASHBOARD =================
@app.route("/")
@login_required
def index():
    month = request.args.get("month")
    year = request.args.get("year")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    conn = get_db()
    query = "SELECT * FROM expenses WHERE user_id=?"
    params = [session["user_id"]]

    if month:
        query += " AND substr(date,6,2)=?"
        params.append(month)

    if year:
        query += " AND substr(date,1,4)=?"
        params.append(year)

    if start_date:
        query += " AND date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND date <= ?"
        params.append(end_date)

    expenses = conn.execute(query, params).fetchall()
    total = sum(e["amount"] for e in expenses)

    category_summary = {}
    monthly_summary = {}

    for e in expenses:
        category_summary[e["category"]] = category_summary.get(e["category"], 0) + e["amount"]
        key = e["date"][:7]
        monthly_summary[key] = monthly_summary.get(key, 0) + e["amount"]

    budget = None
    if month and year:
        row = conn.execute(
            "SELECT amount FROM budgets WHERE user_id=? AND month=? AND year=?",
            (session["user_id"], month, year)
        ).fetchone()
        if row:
            budget = row["amount"]

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
        amount = float(request.form["amount"])
        if amount < 0:
            flash("Amount cannot be negative.", "error")
            return redirect(url_for("add_expense"))

        conn = get_db()
        conn.execute("""
            INSERT INTO expenses (user_id, date, category, description, amount)
            VALUES (?, ?, ?, ?, ?)
        """, (
            session["user_id"],
            request.form["date"],
            request.form["category"],
            request.form["description"],
            amount
        ))
        conn.commit()
        conn.close()

        flash("Expense added successfully.", "success")
        return redirect(url_for("index"))

    return render_template("add_expense.html")


@app.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_expense(id):
    conn = get_db()
    expense = conn.execute(
        "SELECT * FROM expenses WHERE id=? AND user_id=?",
        (id, session["user_id"])
    ).fetchone()

    if not expense:
        conn.close()
        return "Unauthorized", 403

    if request.method == "POST":
        amount = float(request.form["amount"])
        if amount < 0:
            flash("Amount cannot be negative.", "error")
            return redirect(url_for("edit_expense", id=id))

        conn.execute("""
            UPDATE expenses
            SET category=?, description=?, amount=?
            WHERE id=? AND user_id=?
        """, (
            request.form["category"],
            request.form["description"],
            amount,
            id,
            session["user_id"]
        ))
        conn.commit()
        conn.close()

        flash("Expense updated successfully.", "success")
        return redirect(url_for("index"))

    conn.close()
    return render_template("edit_expense.html", expense=expense)


@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = get_db()
    conn.execute(
        "DELETE FROM expenses WHERE id=? AND user_id=?",
        (id, session["user_id"])
    )
    conn.commit()
    conn.close()

    flash("Expense deleted.", "success")
    return redirect(url_for("index"))


# ================= BUDGET =================
@app.route("/budget", methods=["POST"])
@login_required
def budget():
    month = request.form.get("month")
    year = request.form.get("year")
    amount = float(request.form.get("amount"))

    if not month or not year:
        flash("Select month & year to set budget.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    conn.execute(
        "DELETE FROM budgets WHERE user_id=? AND month=? AND year=?",
        (session["user_id"], month, year)
    )
    conn.execute(
        "INSERT INTO budgets VALUES (?, ?, ?, ?)",
        (session["user_id"], month, year, amount)
    )
    conn.commit()
    conn.close()

    flash("Budget saved.", "success")
    return redirect(url_for("index", month=month, year=year))


# ================= EXPORT =================
@app.route("/export/csv")
@login_required
def export_csv():
    conn = get_db()
    rows = conn.execute(
        "SELECT date, category, description, amount FROM expenses WHERE user_id=?",
        (session["user_id"],)
    ).fetchall()
    conn.close()

    if not rows:
        flash("No expenses to export.", "error")
        return redirect(url_for("index"))

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


# ================= ADMIN =================
@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_expenses = conn.execute("SELECT SUM(amount) FROM expenses").fetchone()[0] or 0

    expenses = conn.execute("""
        SELECT expenses.*, users.username
        FROM expenses JOIN users ON users.id = expenses.user_id
    """).fetchall()

    conn.close()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_expenses=total_expenses,
        expenses=expenses
    )


# ================= MAIN =================
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
