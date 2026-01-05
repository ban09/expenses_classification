# expenses_classification

A small Streamlit app to upload bank statements, classify expenses with custom categories, and view spending stats.

## Features
- Upload CSV bank statements with auto-detected `date`, `description`, and `amount` columns.
- Persisted SQLite storage (created under `data/expenses.db`).
- Default **Unclassified** and **Unknown** categories, plus custom categories you add in the UI.
- Similarity-based category suggestions from previously labeled descriptions.
- One-by-one classification workflow with suggestions and quick save.
- Stats page with totals by category and top descriptions.

## Running the app
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start Streamlit:
   ```bash
   streamlit run app.py
   ```
3. Use the sidebar to upload a CSV, classify expenses, and review stats. Re-run the app later to continue where you left off—the database keeps your classifications.

## Expected CSV format
- Required columns: `description`, `amount` (case-insensitive; `details`, `narration`, `value`, `debit`, or `credit` also work for detection).
- Optional column: `date` (parsed to `YYYY-MM-DD` when possible).
- Duplicates (same date, description, and amount) are ignored on import.
