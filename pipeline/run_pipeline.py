"""Run the whole pipeline: build the database, check the KPIs, export the Tableau files.

Run from the repository root: .venv/bin/python pipeline/run_pipeline.py [--skip-transactions]
"""
import sys

import build_database
import check_kpis
import export_tableau

if __name__ == '__main__':
    print('1/3 Build database')
    build_database.main()
    print('2/3 Check KPIs')
    check_kpis.main()
    print('3/3 Export Tableau files')
    export_tableau.main()
    print('Reports in output/reports, Tableau files in output/tableau' + (
        ' (transactions.csv skipped)' if '--skip-transactions' in sys.argv else ''))
