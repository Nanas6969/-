import csv
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import sys
from datetime import datetime
from pathlib import Path
import mysql.connector
from mysql.connector import Error

BASE_DIR = Path(__file__).resolve().parent
DB_CONFIG_PATH = BASE_DIR / "db_config.json"
FILES_CONFIG_PATH = BASE_DIR / "files_config.json"
STATE_FILE_PATH = BASE_DIR / "processed_hashes.json"
LOG_FILE_PATH = BASE_DIR / "csv_import.log"

MAX_LEN_BOX = 150
MAX_LEN_BIGBOX = 100
MAX_LEN_PALLET = 100


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("csv_importer")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        file_handler = RotatingFileHandler(
            LOG_FILE_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


logger = setup_logger()


def load_json(path, default=None):
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(str(path))
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def get_file_hash(filepath: Path) -> str:
    """Вычисляет контрольную сумму (SHA-256) файла."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_date_from_filename(filename: str):
    """Ищет реальную дату формата ГГГГ_ММ_ДД в имени файла."""
    for m in re.finditer(r"(\d{4})[-._](\d{2})[-._](\d{2})", filename):
        y, m_str, d = m.groups()
        try:
            dt = datetime(int(y), int(m_str), int(d))
            return dt.strftime("%Y-%m-%d 00:00:00")
        except ValueError:
            continue
    return None


def main():
    logger.info("=== Запуск импорта CSV-файлов ===")

    try:
        db_cfg = load_json(DB_CONFIG_PATH)
        files_cfg = load_json(FILES_CONFIG_PATH)
    except FileNotFoundError as e:
        logger.error("Не найден файл конфигурации: %s", e.filename)
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error("Ошибка синтаксиса в файле конфигурации: %s", e)
        sys.exit(1)

    try:
        processed_hashes = load_json(STATE_FILE_PATH, default={})
    except json.JSONDecodeError:
        logger.warning("Файл контрольных сумм поврежден, начинаем с чистого листа.")
        processed_hashes = {}

    db_params = db_cfg["db"]
    table_name = db_cfg["table_name"]
    scans_dir = Path(files_cfg["scans_folder"])

    if not scans_dir.exists() or not scans_dir.is_dir():
        logger.error("Указанная папка не существует: %s", scans_dir)
        sys.exit(1)

    csv_files = sorted(scans_dir.glob("*.csv"))

    if not csv_files:
        logger.info("В папке %s нет CSV-файлов.", scans_dir)
        return

    files_to_process = []
    for file_path in csv_files:
        current_hash = get_file_hash(file_path)
        saved_hash = processed_hashes.get(file_path.name)

        if saved_hash == current_hash:
            logger.info("[Пропуск] %s — файл не изменялся.", file_path.name)
        else:
            status = "обновлён" if saved_hash else "новый"
            files_to_process.append((file_path, current_hash, status))

    if not files_to_process:
        logger.info("Все файлы актуальны, загрузка в БД не требуется.")
        return

    try:
        conn = mysql.connector.connect(**db_params)
        cur = conn.cursor()
    except Error as e:
        logger.error("Ошибка подключения к БД: %s", e)
        sys.exit(1)

    sql = f"""
        INSERT INTO {table_name} (box, bigbox, pallet, time_stamp)
        VALUES (%s, %s, %s, COALESCE(%s, NOW()))
        ON DUPLICATE KEY UPDATE
            bigbox     = IF(VALUES(bigbox) = 'not define', bigbox, VALUES(bigbox)),
            pallet     = IF(VALUES(pallet) = 'not define', pallet, VALUES(pallet)),
            time_stamp = VALUES(time_stamp)
    """

    try:
        for file_path, current_hash, status in files_to_process:
            logger.info("--- Обработка файла (%s): %s ---", status, file_path.name)

            file_date = extract_date_from_filename(file_path.stem)
            logger.info(
                "Дата из названия файла: %s",
                file_date or "не найдена (будет использовано текущее время)"
            )

            data = []

            try:
                with open(file_path, mode="r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.reader(f, delimiter=";")

                    for line_num, row in enumerate(reader, start=1):
                        if not row:
                            continue

                        dm_code = row[0].strip()
                        if not dm_code or dm_code.lower() in ("datamatrix", "box", "code"):
                            continue

                        bigbox_code = row[1].strip() if len(row) > 1 and row[1].strip() else "not define"
                        pallet_code = row[2].strip() if len(row) > 2 and row[2].strip() else "not define"

                        if len(dm_code) > MAX_LEN_BOX or len(bigbox_code) > MAX_LEN_BIGBOX or len(pallet_code) > MAX_LEN_PALLET:
                            logger.warning(
                                "[%s] Строка %d пропущена — превышена длина поля | box (%d): %s... | bigbox (%d): %s...",
                                file_path.name, line_num, len(dm_code), dm_code[:40], len(bigbox_code), bigbox_code[:40]
                            )
                            continue

                        data.append((dm_code, bigbox_code, pallet_code, file_date))

            except Exception:
                logger.exception("Ошибка чтения CSV-файла %s", file_path.name)
                continue

            if not data:
                logger.warning("В файле %s не найдено корректных кодов box — пропускаем.", file_path.name)
                processed_hashes[file_path.name] = current_hash
                save_json(STATE_FILE_PATH, processed_hashes)
                continue

            try:
                cur.executemany(sql, data)
                conn.commit()
                logger.info("[%s] Успешно загружено/обновлено строк: %d", file_path.name, cur.rowcount)

                processed_hashes[file_path.name] = current_hash
                save_json(STATE_FILE_PATH, processed_hashes)

            except Error:
                conn.rollback()
                logger.exception("Ошибка вставки данных из %s", file_path.name)

    finally:
        cur.close()
        conn.close()
        logger.info("Работа завершена, соединение с БД закрыто.")


if __name__ == "__main__":
    main()
