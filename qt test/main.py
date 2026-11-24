import sys
import json
import logging
import traceback
import mysql.connector
from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QTableWidgetItem

class QTextEditLogger(logging.Handler):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def emit(self, record):
        msg = self.format(record)
        self.widget.appendPlainText(msg)
        scrollbar = self.widget.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

class DataViewerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("design.ui", self)
        self.setup_logging()
        self.db_config = self.load_config()
        self.tablesComboBox.currentTextChanged.connect(self.load_table_data)
        self.refreshButton.clicked.connect(self.load_table_data)
        self.clearLogsButton.clicked.connect(self.logTextEdit.clear)
        self.searchLineEdit.textChanged.connect(self.filter_table)
        if self.db_config:
            logging.info("Приложение запущено. Попытка подключения к БД...")
            self.load_tables_list()

    def setup_logging(self):
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
        ui_handler = QTextEditLogger(self.logTextEdit)
        ui_handler.setFormatter(formatter)
        logger.addHandler(ui_handler)
        file_handler = logging.FileHandler("app.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    def load_config(self):
        try:
            with open("config.json", "r", encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            logging.error("Файл config.json не найден!")
        except json.JSONDecodeError as err:
            logging.error(f"Ошибка синтаксиса в config.json: {err}")
        except Exception as e:
            logging.error(f"Не удалось загрузить config.json: {e}")
        return None

    def load_tables_list(self):
        if not self.db_config:
            return
        try:
            connection = mysql.connector.connect(**self.db_config)
            cursor = connection.cursor()
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]
            self.tablesComboBox.clear()
            if tables:
                logging.info(f"Подключение успешно. Найдено таблиц: {len(tables)}")
                self.tablesComboBox.addItems(tables)
            else:
                logging.warning("Подключение успешно, но в базе данных нет таблиц.")
        except mysql.connector.Error as err:
            logging.error(f"Ошибка MySQL при получении списка таблиц: {err}")
        except Exception as e:
            logging.error(f"Непредвиденная ошибка: {e}\n{traceback.format_exc()}")
        finally:
            if 'connection' in locals() and connection.is_connected():
                cursor.close()
                connection.close()

    def load_table_data(self):
        if not self.db_config:
            return
        table_name = self.tablesComboBox.currentText()
        if not table_name:
            return
        logging.info(f"Запрос данных из таблицы '{table_name}'...")
        try:
            connection = mysql.connector.connect(**self.db_config)
            cursor = connection.cursor()
            cursor.execute(f"SELECT * FROM `{table_name}`")
            rows = cursor.fetchall()
            column_names = [desc[0] for desc in cursor.description]
            self.tableWidget.clear()
            self.tableWidget.setRowCount(len(rows))
            self.tableWidget.setColumnCount(len(column_names))
            self.tableWidget.setHorizontalHeaderLabels(column_names)
            for row_idx, row_data in enumerate(rows):
                for col_idx, cell_value in enumerate(row_data):
                    text = str(cell_value) if cell_value is not None else ""
                    self.tableWidget.setItem(row_idx, col_idx, QTableWidgetItem(text))
            self.tableWidget.resizeColumnsToContents()
            self.filter_table(self.searchLineEdit.text())
            logging.info(f"Таблица '{table_name}' успешно загружена (строк: {len(rows)}).")
        except mysql.connector.Error as err:
            logging.error(f"Ошибка MySQL при чтении '{table_name}': {err}")
        except Exception as e:
            logging.error(f"Системная ошибка при загрузке '{table_name}': {e}\n{traceback.format_exc()}")
        finally:
            if 'connection' in locals() and connection.is_connected():
                cursor.close()
                connection.close()

    def filter_table(self, text):
        search_text = text.lower()
        for row in range(self.tableWidget.rowCount()):
            match = False
            for col in range(self.tableWidget.columnCount()):
                item = self.tableWidget.item(row, col)
                if item and search_text in item.text().lower():
                    match = True
                    break
            self.tableWidget.setRowHidden(row, not match)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DataViewerApp()
    window.show()
    sys.exit(app.exec())
