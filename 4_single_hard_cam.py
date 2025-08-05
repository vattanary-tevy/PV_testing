import sys
import time
import signal
import cv2
from vmbpy import *
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QTextEdit, QSizePolicy
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt


class SoftwareTriggerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vimba Camera Software Trigger")
        self.cam = None
        self.vimba = None
        self.last_image = None

        self.init_button = QPushButton("Initialize Camera")
        self.trigger_button = QPushButton("Trigger Snapshot")
        self.trigger_button.setEnabled(False)

        self.image_label = QLabel("No Image")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        layout = QVBoxLayout()
        layout.addWidget(self.init_button)
        layout.addWidget(self.trigger_button)
        layout.addWidget(self.image_label)
        layout.addWidget(self.log_box)
        self.setLayout(layout)

        self.init_button.clicked.connect(self.init_camera)
        self.trigger_button.clicked.connect(self.software_trigger)

    def log(self, msg):
        print(msg)
        self.log_box.append(msg)

    def init_camera(self):
        try:
            self.vimba = VmbSystem.get_instance()
            self.vimba.__enter__()

            cams = self.vimba.get_all_cameras()
            if not cams:
                raise RuntimeError("No Vimba-compatible cameras found.")

            self.cam = cams[0]
            self.cam.__enter__()

            if PixelFormat.Mono8 in self.cam.get_pixel_formats():
                self.cam.set_pixel_format(PixelFormat.Mono8)

            self.cam.TriggerSelector.set("FrameStart")
            self.cam.TriggerSource.set("Software")
            self.cam.TriggerMode.set("On")
            self.cam.AcquisitionMode.set("Continuous")

            self.cam.start_streaming(self.frame_handler)
            self.trigger_button.setEnabled(True)
            self.log("Camera initialized and ready for software triggering.")

        except Exception as e:
            self.log(f"Initialization error: {e}")

    def software_trigger(self):
        try:
            self.log("Triggering camera via software...")
            self.cam.TriggerSoftware.run()
        except Exception as e:
            self.log(f"Software trigger failed: {e}")

    def frame_handler(self, cam, stream, frame):
        if frame.get_status() == FrameStatus.Complete:
            frame.convert_pixel_format(PixelFormat.Mono8)
            img = frame.as_opencv_image()
            self.last_image = img
            self.display_image(img)
            self.log("Frame acquired.")
        cam.queue_frame(frame)

    def display_image(self, img):
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
        )

    def closeEvent(self, event):
        try:
            if self.cam:
                self.cam.stop_streaming()
                self.cam.__exit__(None, None, None)
            if self.vimba:
                self.vimba.__exit__(None, None, None)
        except Exception as e:
            print(f"Cleanup error: {e}")
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Handle Ctrl+C properly
    win = SoftwareTriggerApp()
    win.show()
    sys.exit(app.exec_())
