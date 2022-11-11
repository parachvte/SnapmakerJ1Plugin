from PyQt6.QtCore import QTimer
from PyQt6.QtNetwork import QNetworkInterface, QUdpSocket, QAbstractSocket

from UM.Application import Application
from UM.Logger import Logger
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin
from UM.Platform import Platform

from .SnapmakerJ1OutputDevice import SnapmakerJ1OutputDevice
from ..config import MACHINE_NAME

DISCOVER_PORT = 20054


class SnapmakerOutputDevicePlugin(OutputDevicePlugin):
    """Output device plugin that detects Snapmaker machines."""

    def __init__(self) -> None:
        super().__init__()

        self._discover_timer = QTimer()
        self._discover_timer.setInterval(10000)  # 10 seconds
        self._discover_timer.setSingleShot(False)
        self._discover_timer.timeout.connect(self.__discover)

        self._discover_sockets = []  # type: List[QUdpSocket]

        Application.getInstance().globalContainerStackChanged.connect(self._onGlobalContainerStackChanged)
        Application.getInstance().applicationShuttingDown.connect(self.stop)

    def __prepare(self) -> None:
        self._discover_sockets = []
        available_port = 20054
        for interface in QNetworkInterface.allInterfaces():
            for address_entry in interface.addressEntries():
                address = address_entry.ip()
                if address.isLoopback():
                    continue
                if address.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                    continue

                Logger.info("Discovering printers on network interface: %s", address.toString())
                socket = QUdpSocket()
                if Platform.isWindows():
                    # Need to bind to a specified port in order to let QUdpSocket receive datagram
                    socket.bind(address, available_port)
                    available_port += 1
                else:
                    socket.bind(address)
                socket.readyRead.connect(lambda: self._readSocket(socket))
                self._discover_sockets.append((socket, address_entry))

    def __discover(self) -> None:
        if not self._discover_sockets:
            self.__prepare()

        for socket, address_entry in self._discover_sockets:
            socket.writeDatagram(b"discover", address_entry.broadcast(), DISCOVER_PORT)

    def __parseMessage(self, ip: str, msg: str) -> None:
        """Parse message.

        e.g. Snapmaker J1@172.18.0.2|model:J1|status:IDLE
        """
        parts = msg.split("|")
        if len(parts) < 1 or "@" not in parts[0]:
            # invalid message
            return

        device_id = parts[0]
        name, address = device_id.split("@")

        properties = {}
        for part in parts[1:]:
            if ":" not in part:
                continue

            key, value = part.split(":")
            properties[key] = value

        # only accept Snapmaker J1 series
        model = properties.get("model", None)
        if model != MACHINE_NAME:
            return

        device = self.getOutputDeviceManager().getOutputDevice(device_id)
        if not device:
            Logger.info("Discovered Snapmaker J1: %s@%s", name, address)
            device = SnapmakerJ1OutputDevice(device_id, address, properties)
            self.getOutputDeviceManager().addOutputDevice(device)

    def _readSocket(self, socket: QUdpSocket) -> None:
        while socket.hasPendingDatagrams():
            data = socket.receiveDatagram()
            if data.isValid() and not data.senderAddress().isNull():
                ip = data.senderAddress().toString()
                try:
                    msg = bytes(data.data()).decode("utf-8")
                    self.__parseMessage(ip, msg)
                except UnicodeDecodeError:
                    pass

    def start(self) -> None:
        if not self._discover_timer.isActive():
            self._discover_timer.start()
            Logger.info("Snapmaker J1 discovering started.")

    def stop(self) -> None:
        if self._discover_timer.isActive():
            self._discover_timer.stop()

        for socket in self._discover_sockets:
            socket.abort()

        Logger.info("Snapmaker J1 discovering stopped.")

    def startDiscovery(self) -> None:
        self.__discover()

    def _onGlobalContainerStackChanged(self) -> None:
        global_stack = Application.getInstance().getGlobalContainerStack()

        # Start timer when active machine is Snapmaker J1 only
        machine_name = global_stack.getProperty("machine_name", "value")
        if machine_name == MACHINE_NAME:
            self.start()
        else:
            self.stop()
