import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path
import mysql.connector
from mysql.connector import Error

BASE_DIR = Path(__file__).resolve().parent
DB_CONFIG_PATH = BASE_DIR / "db_config.json"
FILES_CONFIG_PATH = BASE_DIR / "files_config.json"
STATE_FILE_PATH = BASE_DIR / "processed_json_hashes.json"
LOG_FILE_PATH = BASE_DIR / "import.log"

MAX_LEN_BOX = 150
MAX_LEN_BIGBOX = 100
MAX_LEN_PALLET = 100


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("json_importer")
    logger.setLevel(logging.INFO)

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

def main():
    logger.info("Запуск процесса импорта JSON-файлов.")

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

    json_files = sorted(scans_dir.glob("*.json"))

    if not json_files:
        logger.info("В папке %s нет JSON-файлов.", scans_dir)
        return

    logger.info("Найдено JSON-файлов: %d", len(json_files))
    files_to_process = []
