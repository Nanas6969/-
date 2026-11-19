import json
import sys
import mysql.connector
from mysql.connector import Error

# ---- настройки ----
DB_CONFIG = {
    "host":     "localhost",
    "user":     "root",
    "password": "ваш_пароль",
    "database": "имя_базы",
}
JSON_FILE = "scans.json"

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    # 1. читаем файл
    try:
        rows = load_json(JSON_FILE)
    except FileNotFoundError:
        print(f"Файл не найден: {JSON_FILE}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Ошибка в JSON: {e}")
        sys.exit(1)

    if not rows:
        print("JSON пустой — нечего загружать.")
        return

    # 2. подключаемся
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor()
    except Error as e:
        print(f"Ошибка подключения: {e}")
        sys.exit(1)

    # 3. готовим данные
    data = [
        (
            r["tea_dm"],
            r["box_dm"],
            r["pallet_dm"],
            r.get("scanned_at"),   # None → COALESCE подставит NOW()
        )
        for r in rows
    ]

    # 4. вставляем пакетом
    sql = """
        INSERT INTO tea_scan (tea_dm, box_dm, pallet_dm, scanned_at)
        VALUES (%s, %s, %s, COALESCE(%s, NOW()))
        ON DUPLICATE KEY UPDATE
            box_dm     = VALUES(box_dm),
            pallet_dm  = VALUES(pallet_dm),
            scanned_at = VALUES(scanned_at)
    """
    try:
        cur.executemany(sql, data)
        conn.commit()
        print(f"Загружено строк: {cur.rowcount}")
    except Error as e:
        conn.rollback()
        print(f"Ошибка вставки: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
