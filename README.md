# WorkforceIQ

An Enterprise Employee Analytics &amp; Data Warehouse System

### Project Flow

BM HR Analytics data **→** synthesizer (100k+ employees) **→** Staging **→** OLTP (3NF) **→** Star-schema Data Warehouse with SCD Type 2 **→** OOP Python backend **→** Streamlit dashboards.

### Project Structure

```text
WorkforceIQ/
    ├── data/
    |   ├── raw/
    |   ├── synthesized/
    ├── docs/
    ├── scripts/
    ├── sql/
    ├── views/
    ├── workforceiq/
    |   ├── dal/
    |   ├── synthesis/
    |   ├── ui/
    ├── .gitignore
    ├── README.md
    ├── requirements.txt
```

### Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### WorkforceIQ: setup steps

You need: Python 3.10+, MySQL 8.0+ running on your PC (MySQL Workbench is fine).

Unzip the file and open a terminal in the WorkforceIQ folder (the one containing manage.py).
Create a virtual environment and install packagesWindows (PowerShell):

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

Mac/Linux:

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Create your own MySQL user. In MySQL Workbench, connected as root, run (pick your own password):

sql
CREATE USER ‘wfiq_app’@‘localhost’ IDENTIFIED BY ‘YourOwnPassword’;
GRANT ALL PRIVILEGES ON wfiq\_%.\* TO ‘wfiq_app’@‘localhost’;
GRANT CREATE ROUTINE, ALTER ROUTINE, EXECUTE ON . TO ‘wfiq_app’@‘localhost’;
FLUSH PRIVILEGES;

(Or just use your existing root user’s details.)

Create .env. Rename .env.example to .env (exactly that name, not .env.txt) and fill in:

WFIQ_DB_HOST=127.0.0.1
WFIQ_DB_PORT=3306
WFIQ_DB_USER=wfiq_app
WFIQ_DB_PASSWORD=YourOwnPassword

No quotes, no spaces around =. Never share or upload this file.

Build the database (about 2–4 minutes, creates 100,000 employees):

python manage.py pipeline --rows 100000

Success looks like: row counts, then four checks showing PASS. (Two WARN lines in the log are normal.)
For a quick 10-second trial first, use --rows 5000. If you change the number later, run python manage.py reset-db --yes first (this deletes the project’s three schemas, wfiq_staging, wfiq_oltp and wfiq_dw).

Start the app:

streamlit run app.py

It opens at http://localhost:8501.
