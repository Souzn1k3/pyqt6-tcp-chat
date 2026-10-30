import json
import socket
import struct
import sys
from queue import Queue

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


HOST = "127.0.0.1"
PORT = 50505


class NetCom:
    def __init__(self, sock):
        self.sock = sock
        self._closed = False

    @staticmethod
    def _recv_exact(sock, size):
        chunks = []
        received = 0
        while received < size:
            chunk = sock.recv(size - received)
            if not chunk:
                return None
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    def recv(self):
        header = self._recv_exact(self.sock, 4)
        if header is None:
            return None

        payload_size = struct.unpack("!I", header)[0]
        payload = self._recv_exact(self.sock, payload_size)
        if payload is None:
            return None

        return json.loads(payload.decode("utf-8"))

    def send(self, message):
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = struct.pack("!I", len(payload))
        self.sock.sendall(header + payload)

    def close(self):
        if self._closed:
            return
        self._closed = True

        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            self.sock.close()
        except OSError:
            pass


class ReceiveThread(QThread):
    message_received = pyqtSignal(dict)
    connection_lost = pyqtSignal(str)

    def __init__(self, net):
        super().__init__()
        self.net = net
        self._stopping = False

    def stop(self):
        self._stopping = True

    def run(self):
        reason = "Соединение с сервером закрыто"

        try:
            while True:
                message = self.net.recv()
                if message is None:
                    break
                self.message_received.emit(message)
        except (OSError, json.JSONDecodeError) as exc:
            if not self._stopping:
                print(f"Receive error: {exc}")
            reason = "Соединение с сервером потеряно"

        if not self._stopping:
            self.connection_lost.emit(reason)


class SendThread(QThread):
    connection_lost = pyqtSignal(str)

    def __init__(self, net):
        super().__init__()
        self.net = net
        self.queue = Queue()
        self._stopping = False

    def send(self, message):
        self.queue.put(message)

    def stop(self):
        self._stopping = True
        self.queue.put(None)

    def run(self):
        while True:
            message = self.queue.get()
            if message is None:
                break

            try:
                self.net.send(message)
            except OSError as exc:
                if not self._stopping:
                    print(f"Send error: {exc}")
                    self.connection_lost.emit("Соединение с сервером потеряно")
                break


class MainWindow(QMainWindow):
    def __init__(self, net):
        super().__init__()
        self.net = net

        self.setWindowTitle("PyQt6 network chat")
        self.resize(560, 420)

        self.history = QTextEdit()
        self.history.setReadOnly(True)

        self.username_label = QLabel("Ваше имя:")
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Аноним")

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a message…")

        self.send_button = QPushButton("Send")
        self.clear_button = QPushButton("Clear history")
        self.status_label = QLabel("Connected")

        name_row = QHBoxLayout()
        name_row.addWidget(self.username_label)
        name_row.addWidget(self.username_input)

        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_row.addWidget(self.clear_button)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input)
        input_row.addWidget(self.send_button)

        layout = QVBoxLayout()
        layout.addLayout(name_row)
        layout.addLayout(status_row)
        layout.addWidget(self.history)
        layout.addLayout(input_row)

        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.send_button.clicked.connect(self.on_send_clicked)
        self.clear_button.clicked.connect(self.on_clear_history_clicked)
        self.input.returnPressed.connect(self.on_send_clicked)

        self._set_connection_state(True)

        self.receive_thread = ReceiveThread(self.net)
        self.receive_thread.message_received.connect(self.handle_message)
        self.receive_thread.connection_lost.connect(self.on_connection_lost)
        self.receive_thread.start()

        self.send_thread = SendThread(self.net)
        self.send_thread.connection_lost.connect(self.on_connection_lost)
        self.send_thread.start()

    def _set_connection_state(self, connected):
        self._connected = connected

        if connected:
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        else:
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet("color: #c62828; font-weight: bold;")

        self.input.setEnabled(connected)
        self.send_button.setEnabled(connected)

    def on_connection_lost(self, reason):
        if not self._connected:
            return

        self._set_connection_state(False)
        self.history.append(f"[Система] {reason}")
        self.net.close()

    def closeEvent(self, event):
        self.receive_thread.stop()
        self.send_thread.stop()
        self.net.close()
        self.receive_thread.wait(2000)
        self.send_thread.wait(2000)
        super().closeEvent(event)

    def on_send_clicked(self):
        text = self.input.text().strip()
        if not text:
            return

        username = self.username_input.text().strip() or "Аноним"

        self.send_thread.send({
            "type": "chat",
            "username": username,
            "text": text,
        })

        self.input.clear()

    def on_clear_history_clicked(self):
        self.history.clear()

    def handle_message(self, message):
        message_type = message.get("type")

        if message_type == "chat":
            username = message.get("username") or "Аноним"
            text = message.get("text", "")
            self.history.append(f"{username}: {text}")
        elif message_type == "system":
            text = message.get("text", "")
            self.history.append(f"[Система] {text}")


def main():
    app = QApplication(sys.argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((HOST, PORT))
    except OSError as exc:
        print(f"Could not connect to server at {HOST}:{PORT}: {exc}")
        print("Run server.py first, then start main.py.")
        return

    net = NetCom(sock)
    window = MainWindow(net)
    window.show()

    exit_code = app.exec()

    net.close()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
