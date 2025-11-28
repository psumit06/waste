import oracledb
import pandas as pd

# ---------- CONFIG SECTION ----------
# Update these values as per your DB
DB_HOST = "your-db-hostname"       # e.g. "10.10.10.5" or "db.mycompany.com"
DB_PORT = 1521
DB_SERVICE_NAME = "your_service"   # e.g. "ORCL", "ORCLPDB1", etc.

DB_USER = "your_username"
DB_PASSWORD = "your_password"

# Your SQL query
SQL_QUERY = """
SELECT *
FROM your_table
WHERE ROWNUM <= 100
"""

# Output file config
OUTPUT_FILE = "oracle_query_result.csv"  # change to .xlsx for Excel
WRITE_EXCEL = False  # set to True to write Excel instead of CSV
# ------------------------------------


def get_connection():
    """
    Create and return an Oracle DB connection using python-oracledb in thin mode.
    """
    dsn = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE_NAME)
    conn = oracledb.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        dsn=dsn
    )
    return conn


def run_query_to_file():
    conn = None
    try:
        conn = get_connection()

        # Use pandas to execute the query and fetch into a DataFrame
        df = pd.read_sql(SQL_QUERY, conn)

        # Save to CSV or Excel
        if WRITE_EXCEL or OUTPUT_FILE.lower().endswith(".xlsx"):
            output_path = OUTPUT_FILE if OUTPUT_FILE.lower().endswith(".xlsx") else "oracle_query_result.xlsx"
            df.to_excel(output_path, index=False)
            print(f"Data written to Excel file: {output_path}")
        else:
            output_path = OUTPUT_FILE if OUTPUT_FILE.lower().endswith(".csv") else "oracle_query_result.csv"
            df.to_csv(output_path, index=False)
            print(f"Data written to CSV file: {output_path}")

    except oracledb.DatabaseError as e:
        error_obj, = e.args
        print("Oracle-Error-Message:", error_obj.message)
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run_query_to_file()
