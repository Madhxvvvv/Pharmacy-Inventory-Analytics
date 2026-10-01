# Pharma Inventory Analytics

## Project Structure

```
├── data_generation/   # Scripts to generate synthetic data
├── data/              # Generated CSVs
├── sql/               # SQL scripts
├── powerbi/           # Power BI reports
├── docs/              # Documentation
└── README.md
```

## Quick start

```bash
pip install pandas numpy faker mysql-connector-python
python data_generation/generate.py    # writes CSVs to data/ (seed 42)
python data_generation/validate.py    # integrity checks + planted-scenario summary
MYSQL_PWD=... python data_generation/load.py   # creates DB pharma_inventory and loads CSVs
```

See [docs/data_dictionary.md](docs/data_dictionary.md) for every table and column.
