import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import streamlit as st

DB_PATH = Path("data/expenses.db")
DEFAULT_CATEGORIES = ("Unclassified", "Unknown")


@dataclass
class Category:
    id: int
    name: str


@dataclass
class Transaction:
    id: int
    date: Optional[str]
    description: str
    amount: float
    category_id: Optional[int]
    suggested_category_id: Optional[int]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                category_id INTEGER REFERENCES categories(id),
                suggested_category_id INTEGER REFERENCES categories(id),
                UNIQUE(date, description, amount)
            );
            """
        )
        for category in DEFAULT_CATEGORIES:
            ensure_category(conn, category)


def ensure_category(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute("SELECT id FROM categories WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    return cur.lastrowid


def list_categories(conn: sqlite3.Connection) -> list[Category]:
    cur = conn.execute("SELECT id, name FROM categories ORDER BY name")
    return [Category(id=row[0], name=row[1]) for row in cur.fetchall()]


def add_category(name: str) -> None:
    with get_connection() as conn:
        ensure_category(conn, name.strip())


def normalized_columns(columns: Iterable[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in columns:
        normalized = col.strip().lower()
        if normalized in {"date", "transaction date"}:
            mapping[col] = "date"
        elif normalized in {"description", "details", "narration"}:
            mapping[col] = "description"
        elif normalized in {"amount", "value", "debit", "credit"}:
            mapping[col] = "amount"
    return mapping


def load_statement(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    column_map = normalized_columns(df.columns)
    df = df.rename(columns=column_map)
    required = {"description", "amount"}
    if not required.issubset(set(df.columns)):
        missing = required - set(df.columns)
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    df = df[[col for col in ["date", "description", "amount"] if col in df.columns]]
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["amount", "description"])
    df["description"] = df["description"].astype(str).str.strip()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return df


def store_transactions(df: pd.DataFrame) -> tuple[int, int]:
    added = 0
    skipped = 0
    with get_connection() as conn:
        unclassified_id = ensure_category(conn, "Unclassified")
        for _, row in df.iterrows():
            date_value = row["date"] if "date" in row else None
            description = row["description"]
            amount = float(row["amount"])
            try:
                conn.execute(
                    """
                    INSERT INTO transactions (date, description, amount, category_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (date_value, description, amount, unclassified_id),
                )
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1
        conn.commit()
    return added, skipped


def fetch_unclassified(limit: int = 1) -> list[Transaction]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT id, date, description, amount, category_id, suggested_category_id
            FROM transactions
            WHERE category_id = (SELECT id FROM categories WHERE name = 'Unclassified')
            ORDER BY date IS NULL, date, id
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [Transaction(*row) for row in rows]


def fetch_history(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    cur = conn.execute(
        """
        SELECT description, category_id
        FROM transactions
        WHERE category_id NOT IN (
            SELECT id FROM categories WHERE name IN ('Unclassified', 'Unknown')
        )
        """
    )
    return [(row[0], row[1]) for row in cur.fetchall()]


def suggest_category(description: str) -> Optional[int]:
    with get_connection() as conn:
        history = fetch_history(conn)
        best_score = 0.0
        best_category: Optional[int] = None
        for previous_description, category_id in history:
            score = SequenceMatcher(None, description.lower(), previous_description.lower()).ratio()
            if score > best_score:
                best_score = score
                best_category = category_id
        return best_category if best_score >= 0.35 else None


def update_transaction_category(transaction_id: int, category_id: int, suggestion_id: Optional[int]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE transactions
            SET category_id = ?, suggested_category_id = ?
            WHERE id = ?
            """,
            (category_id, suggestion_id, transaction_id),
        )
        conn.commit()


def classify_view() -> None:
    st.header("Classify expenses")
    st.markdown(
        "Upload statements first, then assign categories. Suggestions are based on previously tagged descriptions."
    )

    with st.expander("Add a new category"):
        new_category = st.text_input("Category name", key="new_category")
        if st.button("Create category"):
            if new_category.strip():
                add_category(new_category)
                st.success(f"Added category: {new_category.strip()}")
            else:
                st.warning("Please enter a category name.")

    unclassified = fetch_unclassified(limit=1)
    if not unclassified:
        st.success("All expenses are classified!")
        return

    tx = unclassified[0]
    categories = list_categories(get_connection())
    category_options = {c.name: c.id for c in categories}

    suggestion_id = suggest_category(tx.description)
    suggestion_name = next((c.name for c in categories if c.id == suggestion_id), None)

    st.subheader("Next expense")
    st.write(
        {
            "Date": tx.date or "—",
            "Description": tx.description,
            "Amount": f"{tx.amount:,.2f}",
        }
    )

    default_selection = suggestion_name or "Unclassified"
    selected = st.selectbox("Choose a category", options=list(category_options.keys()), index=list(category_options.keys()).index(default_selection))

    if suggestion_name:
        st.info(f"Suggested category: {suggestion_name}")

    if st.button("Save classification"):
        update_transaction_category(tx.id, category_options[selected], suggestion_id)
        st.success("Classification saved")
        st.experimental_rerun()


def upload_view() -> None:
    st.header("Upload bank statement")
    st.markdown(
        "Supported format: CSV with at least `description` and `amount` columns.\n"
        "Optional: `date`. Column names are detected automatically."
    )

    uploaded = st.file_uploader("Upload CSV", type=["csv"])
    if not uploaded:
        return

    try:
        df = load_statement(uploaded)
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))
        return

    st.subheader("Preview")
    st.dataframe(df.head())

    if st.button("Import statement"):
        added, skipped = store_transactions(df)
        st.success(f"Imported {added} transactions. Skipped {skipped} duplicates.")


def stats_view() -> None:
    st.header("Spending overview")
    with get_connection() as conn:
        df = pd.read_sql_query(
            """
            SELECT c.name AS category, SUM(t.amount) AS total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            GROUP BY c.name
            ORDER BY total DESC
            """,
            conn,
        )

        if df.empty:
            st.info("No data yet. Upload and classify expenses to see stats.")
            return

        st.subheader("Totals by category")
        st.table(df)
        st.bar_chart(df.set_index("category"))

        st.subheader("Top descriptions")
        top_desc = pd.read_sql_query(
            """
            SELECT description, SUM(amount) AS total, COUNT(*) AS occurrences
            FROM transactions
            GROUP BY description
            ORDER BY total DESC
            LIMIT 10
            """,
            conn,
        )
        st.table(top_desc)


def main() -> None:
    st.set_page_config(page_title="Expense classification", layout="wide")
    init_db()

    page = st.sidebar.radio("Navigate", ("Upload", "Classify", "Stats"))

    if page == "Upload":
        upload_view()
    elif page == "Classify":
        classify_view()
    elif page == "Stats":
        stats_view()


if __name__ == "__main__":
    main()
