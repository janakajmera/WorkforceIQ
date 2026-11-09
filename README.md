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