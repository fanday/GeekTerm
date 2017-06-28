from gui_qt5.ui_mainwindow import Ui_MainWindow
from PyQt5.QtWidgets import (QApplication, QMessageBox, QMainWindow, QFileDialog, QDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5 import QtCore, QtGui, QtWidgets
from enum_ports import enum_ports
import serial
import logging
import os
from pyqode.python.backend import server
from pyqode.python.widgets import PyCodeEdit
import faulthandler
import time
from res import resources_pyqt5

crash_log = open('crash.log', 'w')
faulthandler.enable(file=crash_log, all_threads=True)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class WriterThread(QThread):
    on_command_executed = pyqtSignal(name='on_command_executed')

    def __init__(self, parent=None):
        QThread.__init__(self, parent)
        self._isRunning = False
        self._serialPort = None
        self._data = None
        self._fileName = None
        self._cmd = None

    def set_serial_port(self, port):
        self._serialPort = port

    def send(self, data, file_name):
        self._fileName = file_name
        self._data = data
        self._cmd = 'Send'

    def execute_script_on_board(self, data):
        self._data = data
        self._cmd = 'Run'

    def save_file_on_board(self, data, file_name):
        if self._serialPort.isOpen():
            cmd = "f = open('{0}', 'wb')\r".format(file_name)
            self._serialPort.write(cmd.encode())

            size = len(data)
            for i in range(0, size, BUFFER_SIZE):
                chunk_size = min(BUFFER_SIZE, size-i)
                chunk = repr(data[i:i+chunk_size])
                # Make sure to send explicit byte strings (handles python 2 compatibility).
                if not chunk.startswith('b'):
                    chunk = 'b' + chunk

                cmd = "f.write({0})\r".format(chunk)
                self._serialPort.write(cmd.encode('utf-8'))
                en_data = cmd.encode('utf-8')
                time.sleep(0.05)
            cmd = "f.close()\r"
            self._serialPort.write(cmd.encode())
        else:
            logger.error('serial port is no open')

    def execute_code(self, data):
        #data  = data.encode('utf-8')
        #data  = data.decode('utf-8')
        if self._serialPort.isOpen():
            text = data.replace('\n', '\r')
            self._serialPort.write('\x05'.encode())
            time.sleep(0.05)
            size = len(text)
            for i in range(0, size, BUFFER_SIZE):
                    chunk_size = min(BUFFER_SIZE, size-i)
                    chunk = text[i:i+chunk_size]
                    self._serialPort.write(chunk.encode('utf-8'))
                    en_data = chunk.encode('utf-8')
                    time.sleep(0.05)
            self._serialPort.write('\x04'.encode())
            time.sleep(0.05)
        else:
            pass

    def run(self):
        if self._cmd is None:
            pass
        elif self._cmd == 'Send':
            self.save_file_on_board(self._data, self._fileName)
        elif self._cmd == 'Run':
            self.execute_code(self._data)
        else:
            pass
        self.on_command_executed.emit()
        self._cmd = None


class ReaderThread(QThread):
    # loop and copy serial->GUI
    read = pyqtSignal(str)
    exception = pyqtSignal(str)

    def __init__(self, parent=None):
        QThread.__init__(self, parent)
        self._alive = None
        self._serialPort = None
        self._viewMode = None
        self._tmpData = bytes()

    def set_port(self, port):
        self._serialPort = port
        self._tmpData = bytes()

    def set_view_mode(self, mode):
        self._viewMode = mode

    def start(self, priority=QThread.InheritPriority):
        self._alive = True
        self._tmpData = bytes()
        super(ReaderThread, self).start(priority)

    def __del__(self):
        if self._alive:
            self._alive = False
            if hasattr(self._serialPort, 'cancel_read'):
                self._serialPort.cancel_read()
        self.wait()

    def join(self):
        self.__del__()

    def run(self):
        self._alive = True
        try:
            while self._alive:
                # read all that is there or wait for one byte
                data = self._serialPort.read()
                rawData = bytes()
                if len(self._tmpData) == 0:
                    rawData = data
                else:
                    rawData = self._tmpData +data
                if rawData:
                    #print('recv data:', rawData)
                    try:
                        text = rawData.decode('utf-8', 'ignore')
                        #print('recv:', list(text))
                    except UnicodeDecodeError:
                        #self._tmpData = self._tmpData+data
                        #self.read.emit(rawData.decode('ascii'))
                        pass
                    else:
                        self.read.emit(text)
                        self._tmpData = bytes()
                #time.sleep(0.01)
        except serial.SerialException as e:
            self.exception.emit('{}'.format(e))

BUFFER_SIZE = 64
KEY_MAP = {
           Qt.Key_Backspace: chr(127),
           Qt.Key_Escape: chr(27),
           Qt.Key_AsciiTilde: "~~",
           Qt.Key_Up: '\x1B[A',
           Qt.Key_Down: '\x1B[B',
           Qt.Key_Left: '\x1B[D',
           Qt.Key_Right: '\x1B[C',
           Qt.Key_PageUp: "~1",
           Qt.Key_PageDown: "~2",
           Qt.Key_Home: "~H",
           Qt.Key_End: "~F",
           Qt.Key_Insert: "~3",
           Qt.Key_Delete: chr(8),
           Qt.Key_F1: "~a",
           Qt.Key_F2: "~b",
           Qt.Key_F3:  "~c",
           Qt.Key_F4:  "~d",
           Qt.Key_F5:  "~e",
           Qt.Key_F6:  "~f",
           Qt.Key_F7:  "~g",
           Qt.Key_F8:  "~h",
           Qt.Key_F9:  "~i",
           Qt.Key_F10:  "~j",
           Qt.Key_F11:  "~k",
           Qt.Key_F12:  "~l",
    }


class GeekTermMainWindow(QMainWindow):

    def __init__(self, parent=None):
        QMainWindow.__init__(self, parent)
        super(GeekTermMainWindow, self).__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.serialPortList = dict()
        icon = QtGui.QIcon(":/icon.ico")
        self.setWindowIcon(icon)
        self.on_enum_ports()
        self.serialPort = serial.Serial()
        self.receiver_thread = ReaderThread(self)
        self.receiver_thread.set_port(self.serialPort)
        self.param = []
        self.state = None
        self.text = None
        self.ui.txtEdtOutput.setAcceptRichText(False)
        self.ui.txtEdtOutput.setAcceptDrops(False)
        self.ui.tabWidget.setTabsClosable(True)
        self.ui.tabWidget.setContextMenuPolicy(Qt.CustomContextMenu)

        # bind action
        self.ui.cmbPort.setEditable(False)
        self.ui.btnEnumPorts.clicked.connect(self.on_enum_ports)
        self.ui.btnOpen.clicked.connect(self.on_open)
        self.receiver_thread.read.connect(self.receive)
        self.receiver_thread.exception.connect(self.reader_except)
        self.ui.txtEdtOutput.onKeyPressed.connect(self.handle_keypressed)
        self.ui.txtEdtOutput.onInsertFromMimeData.connect(self.on_paste_text)
        self.ui.txtEdtOutput.onInputMethodCommit.connect(self.on_input_commit)
        self.ui.btnNewFile.clicked.connect(self.on_new_tab)
        self.ui.btnOpenFile.clicked.connect(self.on_open_file)
        self.ui.tabWidget.tabCloseRequested.connect(self.on_tab_close)
        self.ui.btnSave.clicked.connect(self.on_save)
        self.ui.btnRun.clicked.connect(self.on_run)
        self.ui.btnSend.clicked.connect(self.on_send)
        self.ui.btnClear.clicked.connect(self.on_clear)
        self.ui.actionEditor_Panel.triggered.connect(self.on_editor_panel_trig)
        self.ui.actionPort_Config_Panel.triggered.connect(self.on_port_panel_trig)
        self.ui.actionEditor_Panel.setChecked(True)
        self.ui.actionPort_Config_Panel.setChecked(True)
        self.ui.cmbPort.currentIndexChanged.connect(self.on_port_change)
        self.ui.cmbBaudRate.currentIndexChanged.connect(self.on_baudrate_change)
        self.ui.cmbParity.currentIndexChanged.connect(self.on_parity_change)
        self.ui.cmbDataBits.currentIndexChanged.connect(self.on_data_bit_change)
        self.ui.cmbStopBits.currentIndexChanged.connect(self.on_stop_bit_change)
        self.ui.chkRTSCTS.stateChanged.connect(self.on_flow_control_change)
        self.ui.chkXonXoff.stateChanged.connect(self.on_flow_control_change)
        self.ui.About_GeekTerm.onClicked.connect(self.on_about_geek_term)

    def on_input_commit(self, data):
        # FIXME:input method is not work
        return
        print('main data:',data)
        if self.serialPort.isOpen():
            text = str(data.replace('\n', '\r').encode('utf-8'))
            send_text = text[2:len(text)-1]

            self.serialPort.write(send_text.encode('utf-8'))
            print('input data:', send_text.encode('utf-8'))

    def on_writer_thread_quit(self):
        self.ui.btnRun.setEnabled(True)
        self.ui.btnSend.setEnabled(True)

    def on_about_geek_term(self):
        MESSAGE = "<a href=\"https://github.com/fanday/GeekTerm\">Source Code at Github</a><p>Author:FandayDai</p><p>V0.1.0</p>"
        QMessageBox.information(self,"About GeekTerm", MESSAGE)

    def on_always_on_top_trig(self):
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint)

    def on_flow_control_change(self, state):
        if self.serialPort.isOpen():
            self.serialPort.rtscts = self.ui.chkRTSCTS.isChecked()
            self.serialPort.xonxoff = self.ui.chkXonXoff.isChecked()
        else:
            return

    def on_stop_bit_change(self, index):
        if self.serialPort.isOpen():
            self.serialPort.stopbits = self.get_stop_bits()
        else:
            return

    def on_data_bit_change(self, index):
        if self.serialPort.isOpen():
            self.serialPort.bytesize = self.get_data_bits()
        else:
            return

    def on_parity_change(self, index):
        if self.serialPort.isOpen():
            self.serialPort.parity = self.get_parity()
        else:
            return

    def on_port_change(self, index):
        if self.serialPort.isOpen():
            _port = self.get_port()
            if '' == _port:
                return
            self.serialPort.port = _port
        else:
            return

    def on_baudrate_change(self, index):
        if self.serialPort.isOpen():
            _baudrate = self.ui.cmbBaudRate.currentText()
            if '' == _baudrate:
                return
            try:
                baudrate = int(_baudrate)
            except Exception as e:
                QMessageBox.critical(self, "Invalid baudrate!", str(e),
                                 QMessageBox.Close)
                return
            try:
                self.serialPort.baudrate = _baudrate
            except Exception as e:
                QMessageBox.critical(self, "change baudrate failed!", str(e), QMessageBox.Close)
        else:
            return

    def on_editor_panel_trig(self):
        if self.ui.actionEditor_Panel.isChecked():
            self.ui.dockWidget_Editor.show()
        else:
            self.ui.dockWidget_Editor.hide()

    def on_port_panel_trig(self):
        if self.ui.actionPort_Config_Panel.isChecked():
            self.ui.dockWidget_PortConfig.show()
        else:
            self.ui.dockWidget_PortConfig.hide()

    def on_clear(self):
        self.ui.txtEdtOutput.clear()

    def on_paste_text(self, text):
        if len(text) == 0:
            return

        if self.serialPort.isOpen():
            writer_thread = WriterThread(self)
            writer_thread.set_serial_port(self.serialPort)
            writer_thread.execute_script_on_board(text)
            writer_thread.on_command_executed.connect(self.on_writer_thread_quit)
            self.ui.btnRun.setEnabled(False)
            self.ui.btnSend.setEnabled(False)
            writer_thread.start()
        else:
            msg_box = QMessageBox()
            msg_box.setText('please open serial port!')
            msg_box.exec_()

    def on_send(self):
        cur_tab = self.ui.tabWidget.currentWidget()
        if cur_tab is None:
            return
        cur_tab.file.save()
        file_path = cur_tab.file.path
        file_name = os.path.basename(file_path)

        with open(file_path, 'rb') as infile:
            data = infile.read()
            writer_thread = WriterThread(self)
            writer_thread.set_serial_port(self.serialPort)
            writer_thread.send(data, file_name)
            writer_thread.on_command_executed.connect(self.on_writer_thread_quit)
            self.ui.btnRun.setEnabled(False)
            self.ui.btnSend.setEnabled(False)
            writer_thread.start()

    def on_open_file(self):
        file_name, filetype = QFileDialog.getOpenFileName(self, "open file", "", "Python Script (*.py)")
        if len(file_name) == 0:
            return
        else:
            try:
                editor = PyCodeEdit(server_script=server.__file__)
                # show the PyCodeEdit module in the editor
                editor.file.open(file_name)
                editor.setObjectName(file_name)
                self.ui.tabWidget.addTab(editor,os.path.basename(file_name))
                self.ui.tabWidget.setCurrentWidget(editor)
            except Exception:
                logger.error(Exception)

    def on_tab_close(self, index):
        self.ui.tabWidget.widget(index).file.save()
        self.ui.tabWidget.removeTab(index)

    def on_new_tab(self):
        file_name,filetype = QFileDialog.getSaveFileName(self, "New file", "", " Python Script (*.py)")
        if len(file_name) == 0:
            return
        else:
            file = open(file_name,'w')
            file.close()
            editor = PyCodeEdit(server_script=server.__file__)
            # show the PyCodeEdit module in the editor
            editor.file.open(file_name)
            editor.setObjectName(file_name)
            self.ui.tabWidget.addTab(editor,os.path.basename(file_name))
            self.ui.tabWidget.setCurrentWidget(editor)

    def on_save(self):
        tab = self.ui.tabWidget.currentWidget()
        if tab is None:
            return
        tab.file.save()

    def on_run(self):
        tab = self.ui.tabWidget.currentWidget()
        if tab is None:
            return
        text = tab.toPlainText()
        if len(text) == 0:
            return

        if self.serialPort.isOpen():
            writer_thread = WriterThread(self)
            writer_thread.set_serial_port(self.serialPort)
            writer_thread.execute_script_on_board(text)
            writer_thread.on_command_executed.connect(self.on_writer_thread_quit)
            self.ui.btnRun.setEnabled(False)
            self.ui.btnSend.setEnabled(False)
            writer_thread.start()
        else:
            msg = QMessageBox()
            msg.setText('please open serial port!')
            msg.exec_()

    def handle_keypressed(self, key, text):
        s = KEY_MAP.get(key)
        if self.serialPort.isOpen():
            if s:
                self.serialPort.write(s.encode())
            else:
                if len(text) > 0:
                    text = text.replace('\n', '\r')
                    self.serialPort.write(text.encode())
                    #print('input data:', text.encode())
        else:
            msg = QMessageBox()
            msg.setText('please open serial port!')
            msg.exec_()

    def close_port(self):
        if self.serialPort.isOpen():
            self._stop_reader()
            self.serialPort.close()
            pal = self.ui.btnOpen.style().standardPalette()
            self.ui.btnOpen.setAutoFillBackground(True)
            self.ui.btnOpen.setPalette(pal)
            self.ui.btnOpen.setText('Open')
            self.ui.btnOpen.update()

    def reader_except(self, e):
        self.close_port()
        QMessageBox.critical(self, "Read failed", str(e), QMessageBox.Close)

    def flush_text(self):
        if self.text != None and len(self.text) != 0:
            self.ui.txtEdtOutput.append(self.text)
            self.text = None

    def cursor_start_of_line(self):
        cur = self.ui.txtEdtOutput.textCursor()
        cur.movePosition(QtGui.QTextCursor.StartOfBlock)
        self.ui.txtEdtOutput.setTextCursor(cur)

    def cursor_new_line(self, n):
        cur = self.ui.txtEdtOutput.textCursor()
        while n > 0:
            cur.movePosition(QtGui.QTextCursor.EndOfBlock)
            if cur.atEnd():
                cur.insertBlock()
                self.ui.txtEdtOutput.setTextCursor(cur)
            else:
                cur.movePosition(QtGui.QTextCursor.NextBlock)
                self.ui.txtEdtOutput.setTextCursor(cur)
            n = n - 1

    def cursor_left(self, n):
        cur = self.ui.txtEdtOutput.textCursor()
        cur.movePosition(QtGui.QTextCursor.Left,QtGui.QTextCursor.MoveAnchor, n)
        self.ui.txtEdtOutput.setTextCursor(cur)

    def parse_param(self, np, defval=None):
        pass

    def move_cursor(self, data):

        n = 1
        if len(self.param) == 0:
            n = 1
        else:
            length = len(self.param)
            if length == 1:
                n = int(self.param[0])
            else:
                n = int(self.param[0])*10+int(self.param[1])

        cur = self.ui.txtEdtOutput.textCursor()
        if data == 'A':
            cur.movePosition(QtGui.QTextCursor.Up, QtGui.QTextCursor.MoveAnchor, n)
        elif data =='B':
            cur.movePosition(QtGui.QTextCursor.Down, QtGui.QTextCursor.MoveAnchor, n)
        elif data =='C':
            cur.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.MoveAnchor, n)
        elif data =='D':
            cur.movePosition(QtGui.QTextCursor.Left, QtGui.QTextCursor.MoveAnchor, n)
        elif data =='H':
            pass
        else:
            pass

        self.ui.txtEdtOutput.setTextCursor(cur)

    def erase_text(self, data):
        n = 0
        if len(self.param) == 0:
            n = 0
        else:
            n = int(self.param[0])
        if data == 'K':
            cur = self.ui.txtEdtOutput.textCursor()
            if n == 0:
                cur.movePosition(QtGui.QTextCursor.EndOfBlock, QtGui.QTextCursor.KeepAnchor)
                cur.removeSelectedText()
            elif n == 1:
                cur.movePosition(QtGui.QTextCursor.StartOfBlock, QtGui.QTextCursor.KeepAnchor)
                cur.removeSelectedText()
            elif n == 2:
                cur.select(QtGui.QTextCursor.LineUnderCursor)
                cur.removeSelectedText()
            else:
                pass
            self.ui.txtEdtOutput.setTextCursor(cur)

        elif data == 'J':
            pass
        else:
            pass

    def setDisplay(self ):
        pass

    def handle_default(self, data):
        if data == '\x1B': #ESC
            self.state = 1
            self.param = []
            self.flush_text()
        elif data == '\x0D':
            self.flush_text()
            self.cursor_start_of_line()
        elif data == '\x0A':
            self.flush_text()
            self.cursor_new_line(1)
        elif data == '\x08':
            self.flush_text()
            self.cursor_left(1)
        elif data == '\x07':
            pass # Beep
        else:
            return data

    def handle_esc(self, data):
        if data =='[':
            self.state = 2
        else:
            self.state = 0

        return None

    def handle_escape(self, data):
        if data == 'A' or data == 'B' or data == 'C' or data == 'D' or data == 'H':
            self.state = None
            self.move_cursor(data)
        elif data == 'J' or data == 'K':
            self.state = None
            self.erase_text(data)
        elif data == 'm':
            self.state = None
            self.setDisplay()

        elif data == '0' or data == '1' or data == '2' or data == '3' or data == '4' or data == '5' or data == '6' or data == '7' or data == '8' or data == '9' or data == ';':
            self.param.append(data)
        elif data =='?':
            self.state = 3
        else:
            self.state = None
        return None

    def handle_state3(self, data):
        if data == '0' or data == '1':
            pass
        elif data == 'h':
            self.state = None
        else:
            pass



    def receive(self, data):
        color=Qt.black
        for d in data:
            if self.state == 2:
                res = self.handle_escape(d)
            elif self.state == 1:# ESC
                res = self.handle_esc(d)
            elif self.state == 3:
                res = self.handle_state3(d)
            else:
                res = self.handle_default(d)

            if res is None:
                continue

            # select right
            cur = self.ui.txtEdtOutput.textCursor()
            tcend = self.ui.txtEdtOutput.textCursor()
            tcend.movePosition(QtGui.QTextCursor.EndOfBlock)
            endpos = tcend.position()
            pos = cur.position()
            n = 0

            if pos < endpos:
                n = 1
            if n > 0:
                cur.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor, n)
                self.ui.txtEdtOutput.setTextCursor(cur)

            tc=self.ui.txtEdtOutput.textColor()
            self.ui.txtEdtOutput.setTextColor(QtGui.QColor(color))
            self.ui.txtEdtOutput.insertPlainText(res)
            cur.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor, n)
            self.ui.txtEdtOutput.setTextColor(tc)

    def on_open(self):
        if self.serialPort.isOpen():
            self.close_port()
        else:
            self.open_port()

    def open_port(self):
        if self.serialPort.isOpen():
            return

        _port = self.get_port()
        if '' == _port:
            QMessageBox.information(self, "Invalid parameters", "Port is empty.")
            return

        _baudrate = self.ui.cmbBaudRate.currentText()
        if '' == _baudrate:
            QMessageBox.information(self, "Invalid parameters", "Baudrate is empty.")
            return
        try:
            baudrate = int(_baudrate)
        except Exception as e:
            logger.error("Error %s"%e)
            return

        self.serialPort.port = _port
        self.serialPort.baudrate = _baudrate
        self.serialPort.bytesize = self.get_data_bits()
        self.serialPort.stopbits = self.get_stop_bits()
        self.serialPort.parity = self.get_parity()
        self.serialPort.rtscts = self.ui.chkRTSCTS.isChecked()
        self.serialPort.xonxoff = self.ui.chkXonXoff.isChecked()
        try:
            self.serialPort.open()
        except serial.SerialException as e:
            QMessageBox.critical(self, "Could not open serial port", str(e),
                                 QMessageBox.Close)
        else:
            self._start_reader()
            self.setWindowTitle("%s on %s [%s, %s%s%s%s%s]" % (
                'GeekTerm',
                self.serialPort.portstr,
                self.serialPort.baudrate,
                self.serialPort.bytesize,
                self.serialPort.parity,
                self.serialPort.stopbits,
                self.serialPort.rtscts and ' RTS/CTS' or '',
                self.serialPort.xonxoff and ' Xon/Xoff' or '',
            )
                                )
            pal = self.ui.btnOpen.palette()
            pal.setColor(QtGui.QPalette.Button, QtGui.QColor(0, 0xff, 0x7f))
            self.ui.btnOpen.setAutoFillBackground(True)
            self.ui.btnOpen.setPalette(pal)
            self.ui.btnOpen.setText('Close')
            self.ui.btnOpen.update()

    def on_enum_ports(self):
        self.serialPortList.clear()
        self.ui.cmbPort.clear()
        for p in enum_ports():
            self.serialPortList[p[1]] = p[0]
            self.ui.cmbPort.addItem(p[1])

    def get_port(self):
        text = self.ui.cmbPort.currentText()
        if text == '' or len(self.serialPortList) == 0:
            return ''
        try:
            self.serialPortList[text]
        except Exception as e:
            logger.error("COM port error %s"% e)
            return ''
        return self.serialPortList[text]

    def get_data_bits(self):
        s = self.ui.cmbDataBits.currentText()
        if s == '5':
            return serial.FIVEBITS
        elif s == '6':
            return serial.SIXBITS
        elif s == '7':
            return serial.SEVENBITS
        elif s == '8':
            return serial.EIGHTBITS

    def get_parity(self):
        s = self.ui.cmbParity.currentText()
        if s == 'None':
            return serial.PARITY_NONE
        elif s == 'Even':
            return serial.PARITY_EVEN
        elif s == 'Odd':
            return serial.PARITY_ODD
        elif s == 'Mark':
            return serial.PARITY_MARK
        elif s == 'Space':
            return serial.PARITY_SPACE

    def get_stop_bits(self):
        s = self.ui.cmbStopBits.currentText()
        if s == '1':
            return serial.STOPBITS_ONE
        elif s == '1.5':
            return serial.STOPBITS_ONE_POINT_FIVE
        elif s == '2':
            return serial.STOPBITS_TWO
    def _start_reader(self):
        self.receiver_thread.start()

    def _stop_reader(self):
        self.receiver_thread.join()

def check_exsit(process_name):
    import win32com.client
    WMI = win32com.client.GetObject('winmgmts:')
    processCodeCov = WMI.ExecQuery('select * from Win32_Process where Name="%s"' % process_name)
    if len(processCodeCov) > 2:
        return True
    else:
        return False


def exceptionHook(etype, value, trace):
    import traceback
    tmp = traceback.format_exception(etype, value, trace)
    for info in tmp:
        crash_log.write(info)
    crash_log.flush()
    print(tmp)


if __name__ == "__main__":
    import sys
    sys.excepthook = exceptionHook
    if check_exsit('GeekTerm.exe') == True:
        sys.exit()
    try:
        app = QApplication(sys.argv)
        window = GeekTermMainWindow()
        window.show()
        sys.exit(app.exec_())
    except Exception:
        logger.error('rase exception>>>>>>>>>>>>>>>>>>>>', Exception)
