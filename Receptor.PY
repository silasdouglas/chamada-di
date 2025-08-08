import sys
import json
import asyncio
import csv
from datetime import datetime
import tempfile

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QPushButton, QHBoxLayout,
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QSystemTrayIcon, QMenu, QStyle,
    QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, 
    QPropertyAnimation, QEasingCurve, pyqtProperty, QPoint # QPoint foi adicionado aqui
)
from PyQt6.QtGui import QFont, QIcon, QAction
import websockets

# --- CONFIGURAÇÕES ---
HOST = 'localhost'
PORT = 8081
TIMEOUT_MS = 300000 # 300 segundos = 5 minutos

# --- JANELA DE HISTÓRICO ---
class HistoryWindow(QDialog):
    def __init__(self, history_file_path, parent=None):
        super().__init__(parent)
        self.history_file_path = history_file_path
        self.setWindowTitle("Histórico de Chamadas")
        self.setFixedSize(450, 500)

        self.layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Horário", "Sala", "Número"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)

        self.layout.addWidget(self.table)
        self.load_history()

        self.setStyleSheet("""
            QDialog { background-color: #34495e; }
            QTableWidget { background-color: #2c3e50; color: white; gridline-color: #34495e; font-size: 11pt; }
            QHeaderView::section { background-color: #34495e; color: white; padding: 5px; border: 1px solid #2c3e50; font-weight: bold; }
        """)

    def load_history(self):
        try:
            with open(self.history_file_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader)
                history_data = list(reader)
                self.table.setRowCount(0)
                for row_data in reversed(history_data):
                    self.add_row_to_table(row_data)
        except (IOError, FileNotFoundError):
            pass
        except StopIteration:
            pass

    def add_row_to_table(self, row_data, at_top=False):
        timestamp_str, room, number = row_data
        
        try:
            dt_object = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            display_time = dt_object.strftime('%H:%M:%S')
        except ValueError:
            display_time = timestamp_str

        row_position = 0 if at_top else self.table.rowCount()
        self.table.insertRow(row_position)
        
        self.table.setItem(row_position, 0, QTableWidgetItem(display_time))
        self.table.setItem(row_position, 1, QTableWidgetItem(room))
        self.table.setItem(row_position, 2, QTableWidgetItem(number))

    def on_new_history_entry(self, timestamp, room, number):
        self.add_row_to_table([timestamp, room, number], at_top=True)

# --- WebSocketThread ---
class WebSocketThread(QThread):
    client_connected = pyqtSignal()
    client_disconnected = pyqtSignal()
    message_received = pyqtSignal(str)

    def __init__(self, host, port):
        super().__init__()
        self.host = host
        self.port = port

    async def handler(self, websocket, path=None):
        self.client_connected.emit()
        print("Cliente conectado.")
        try:
            async for message in websocket:
                self.message_received.emit(message)
        except websockets.exceptions.ConnectionClosed:
            print("Cliente desconectou.")
        finally:
            self.client_disconnected.emit()

    async def start_server(self):
        async with websockets.serve(self.handler, self.host, self.port):
            await asyncio.Future()

    def run(self):
        try:
            asyncio.run(self.start_server())
        except OSError as e:
            print(f"Erro ao iniciar o servidor: {e}")

class NotificationApp(QWidget):
    new_history_entry = pyqtSignal(str, str, str)

    def __init__(self):
        super().__init__()
        self.timer = None
        self.seconds_left = TIMEOUT_MS // 1000
        self.history_window = None

        self.history_file = tempfile.NamedTemporaryFile(
            mode='w', delete=False, newline='', encoding='utf-8', suffix='.csv'
        ).name
        with open(self.history_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'Room', 'Number'])

        self.setup_tray_icon()
        self.init_ui()
        self.start_websocket_server()
        self.hide()
    
    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "Painel de Chamadas",
            "O aplicativo continua executando em segundo plano.",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )

    def init_ui(self):
        self.setWindowTitle("Painel de Chamadas")
        self.setFixedSize(400, 320)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.layout)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container.setObjectName("container")

        top_bar = QHBoxLayout()
        self.history_button = QPushButton("📜")
        self.history_button.setFixedSize(32, 32)
        self.history_button.setObjectName("historyButton")
        self.history_button.setToolTip("Ver histórico de chamadas")
        self.history_button.clicked.connect(self.show_history)
        top_bar.addWidget(self.history_button)
        top_bar.addStretch()
        self.close_button = QPushButton("✖")
        self.close_button.setFixedSize(32, 32)
        self.close_button.setObjectName("closeButton")
        self.close_button.clicked.connect(self.close) 
        top_bar.addWidget(self.close_button)
        container_layout.addLayout(top_bar)

        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0,0,0,0)
        
        self.status_label = QLabel("AGUARDANDO CONEXÃO")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.room_text_label = QLabel("SALA")
        self.room_text_label.setObjectName("textLabel")
        self.room_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.room_label = QLabel("---")
        self.room_label.setObjectName("mainLabel")
        self.room_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.number_text_label = QLabel("NÚMERO")
        self.number_text_label.setObjectName("textLabel")
        self.number_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.number_label = QLabel("--")
        self.number_label.setObjectName("mainLabel")
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.timer_label = QLabel("")
        self.timer_label.setObjectName("timerLabel")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        content_layout.addStretch()
        content_layout.addWidget(self.room_text_label)
        content_layout.addWidget(self.room_label)
        content_layout.addStretch()
        content_layout.addWidget(self.number_text_label)
        content_layout.addWidget(self.number_label)
        content_layout.addStretch()

        container_layout.addWidget(self.status_label)
        container_layout.addWidget(self.timer_label)
        container_layout.addWidget(self.content_widget)

        self.layout.addWidget(container)

        self.apply_styles()
        self.on_client_disconnected()
        
    def setup_tray_icon(self):
        style = self.style()
        icon = QIcon(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Painel de Chamadas")
        tray_menu = QMenu()
        show_action = QAction("Exibir Painel", self)
        show_action.triggered.connect(self.show_latest_notification)
        history_action = QAction("Histórico", self)
        history_action.triggered.connect(self.show_history)
        quit_action = QAction("Sair", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        tray_menu.addAction(show_action)
        tray_menu.addAction(history_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
    
    def show_latest_notification(self):
        if not self.room_label.text() == "---":
            self.show()

    def apply_styles(self):
        self.setStyleSheet("""
            #container { background-color: #2c3e50; border-radius: 15px; }
            #statusLabel { color: white; font-size: 12pt; font-weight: bold; padding: 5px; border-radius: 8px; }
            #textLabel { color: #bdc3c7; font-size: 18pt; font-weight: normal; }
            #mainLabel { color: white; font-size: 36pt; font-weight: bold; }
            #timerLabel { color: #f1c40f; font-size: 16pt; font-weight: bold; padding: 5px; }
            #closeButton, #historyButton { background-color: transparent; border: none; font-size: 18pt; font-weight: bold; border-radius: 8px; }
            #closeButton { color: #e74c3c; }
            #historyButton { color: #3498db; }
            #closeButton:hover { background-color: #c0392b; color: white; }
            #historyButton:hover { background-color: #2980b9; color: white; }
        """)

    def showEvent(self, event):
        screen = QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        x = geometry.right() - self.width() - 20
        y = geometry.bottom() - self.height() - 20
        self.move(x, y)
        super().showEvent(event)

    def start_websocket_server(self):
        self.ws_thread = WebSocketThread(HOST, PORT)
        self.ws_thread.client_connected.connect(self.on_client_connected)
        self.ws_thread.client_disconnected.connect(self.on_client_disconnected)
        self.ws_thread.message_received.connect(self.on_message_received)
        self.ws_thread.start()

    def on_client_connected(self):
        self.status_label.setText("CONECTADO")
        self.status_label.setStyleSheet("background-color: #27ae60; color: white; border-radius: 8px; padding: 5px;")
        self.tray_icon.showMessage("Conexão", "Cliente conectado com sucesso.", QSystemTrayIcon.MessageIcon.Information, 2000)

    def on_client_disconnected(self):
        self.status_label.setText("AGUARDANDO CONEXÃO")
        self.status_label.setStyleSheet("background-color: #c0392b; color: white; border-radius: 8px; padding: 5px;")
        self.tray_icon.showMessage("Conexão", "Cliente desconectado.", QSystemTrayIcon.MessageIcon.Warning, 2000)

    def on_message_received(self, message):
        try:
            data = json.loads(message)
            room = str(data.get('room', 'N/A')).upper()
            number = f"{data.get('number', 0):02d}"
            
            is_already_visible = self.isVisible()

            timestamp = datetime.now()
            self.add_history_entry(timestamp.strftime('%Y-%m-%d %H:%M:%S'), room, number)
            
            self.new_history_entry.emit(timestamp.strftime('%H:%M:%S'), room, number)

            if is_already_visible:
                self.trigger_attention_animation(room, number)
            else:
                self.room_label.setText(room)
                self.number_label.setText(number)
                self.show()

            self.raise_()
            self.activateWindow()
            self.start_countdown()

            self.tray_icon.showMessage(f"Nova Chamada: Sala {room}", f"Atender o número {number}", QSystemTrayIcon.MessageIcon.Information, 5000)

        except json.JSONDecodeError:
            print(f"Erro ao decodificar a mensagem: {message}")

    # --- MÉTODO DE ANIMAÇÃO MODIFICADO ---
    def trigger_attention_animation(self, new_room, new_number):
        self.room_label.setText(new_room)
        self.number_label.setText(new_number)

        self.pos_animation = QPropertyAnimation(self.content_widget, b"pos")
        
        original_pos = self.content_widget.pos()

        self.pos_animation.setDuration(500)
        self.pos_animation.setKeyValueAt(0.0, original_pos)
        self.pos_animation.setKeyValueAt(0.1, original_pos + QPoint(10, 0))
        self.pos_animation.setKeyValueAt(0.2, original_pos + QPoint(-10, 0))
        self.pos_animation.setKeyValueAt(0.3, original_pos + QPoint(10, 0))
        self.pos_animation.setKeyValueAt(0.4, original_pos + QPoint(-10, 0))
        self.pos_animation.setKeyValueAt(0.5, original_pos + QPoint(10, 0))
        self.pos_animation.setKeyValueAt(0.6, original_pos + QPoint(-10, 0))
        self.pos_animation.setKeyValueAt(0.7, original_pos + QPoint(5, 0))
        self.pos_animation.setKeyValueAt(0.8, original_pos + QPoint(-5, 0))
        self.pos_animation.setKeyValueAt(1.0, original_pos)
        
        self.pos_animation.start()
    # ------------------------------------

    def show_history(self):
        if self.history_window is None or not self.history_window.isVisible():
            self.history_window = HistoryWindow(self.history_file, self)
            self.new_history_entry.connect(self.history_window.on_new_history_entry)
            self.history_window.show()
        else:
            self.history_window.raise_()
            self.history_window.activateWindow()

    def add_history_entry(self, timestamp, room, number):
        try:
            with open(self.history_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([timestamp, room, number])
        except IOError as e:
            print(f"Erro ao salvar no histórico: {e}")

    def start_countdown(self):
        self.seconds_left = TIMEOUT_MS // 1000
        self.update_timer_label()
        if hasattr(self, 'timer') and self.timer:
            self.timer.stop()
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_timer)
        self.timer.start(1000)

    def update_timer_label(self):
        self.timer_label.setText(f"Aviso some em {self.seconds_left} segundo(s)")

    def update_timer(self):
        self.seconds_left -= 1
        if self.seconds_left <= 0:
            self.timer.stop()
            self.hide()
        else:
            self.update_timer_label()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    font = QFont("Segoe UI")
    app.setFont(font)
    window = NotificationApp()
    sys.exit(app.exec())