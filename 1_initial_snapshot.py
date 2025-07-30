from vmbpy import *
# from avaspec import *
import sys, time, signal
import cv2
from labjack import ljm
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QSizePolicy
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


'''
LabJack (send 5V instead of acquiring temp.) 
'''

handle = ljm.openS("ANY", "USB", "ANY") #Connect to LabJack
TRIG_LINE = "FIO4"

# start in input mode
ljm.eWriteName(handle, TRIG_LINE, 0)   # 0 = input/high-Z


'''
Allied Vision Camera Functions
'''

class HistogramCanvas(FigureCanvas):
    def __init__(self, parent=None, width=4, height=3, dpi=100):
        fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = fig.add_subplot(111)
        super().__init__(fig)
        self.setParent(parent)

    def plot_histogram(self, img):
        self.axes.clear()
        color_labels = ('b', 'g', 'r')
        for i, color in enumerate(color_labels):
            hist = cv2.calcHist([img], [i], None, [256], [0, 256])
            self.axes.plot(hist, color=color)
        self.axes.set_xlim([0, 256])
        self.axes.set_title("RGB Histogram")
        self.draw()


class CameraApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vimba Camera Snapshot GUI")

        self.vimba = None
        self.cam = None

        # UI Elements
        self.init_button = QPushButton("Initialize Camera")
        self.snap_button = QPushButton("Take Snapshot")
        self.snap_button.setEnabled(False)

        self.image_label = QLabel("No Image")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.hist_canvas = HistogramCanvas(self, width=5, height=3)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.init_button)
        layout.addWidget(self.snap_button)
        layout.addWidget(self.image_label)
        layout.addWidget(self.hist_canvas)
        self.setLayout(layout)

        # Connect buttons
        self.init_button.clicked.connect(self.initialize_camera)
        self.snap_button.clicked.connect(self.take_snapshot)

    def initialize_camera(self):
        try:
            self.vimba = VmbSystem.get_instance()
            self.vimba.__enter__()
            cams = self.vimba.get_all_cameras()
            if not cams:
                self.image_label.setText("No cameras found!")
                return
            self.cam = cams[0]
            self.cam.__enter__()

            # Set pixel format BEFORE grabbing images
            supported_formats = self.cam.get_pixel_formats()
            if PixelFormat.Mono8 in supported_formats:
                self.cam.set_pixel_format(PixelFormat.Mono8)
            else:
                self.image_label.setText("No supported pixel format (BGR8/Mono8) found.")
                return

            self.image_label.setText("Camera initialized!")
            self.snap_button.setEnabled(True)

        except Exception as e:
            self.image_label.setText(f"Error initializing camera:\n{e}")

    def take_snapshot(self):
        if not self.cam:
            self.image_label.setText("Camera not initialized!")
            return

        try:
            # === Configure Camera for Trigger Mode ===
            self.cam.TriggerSource.set('Software')
            self.cam.TriggerSelector.set('FrameStart')
            self.cam.TriggerMode.set('On')
            self.cam.AcquisitionMode.set('Continuous')
            
            def handler(cam: Camera, stream: Stream, frame: Frame):
                print("Frame acquired.")
                frame.convert_pixel_format(PixelFormat.Mono8)
                image = frame.as_opencv_image()

                # Save image
                cv2.imwrite('frame.jpg', image)

                # Convert to RGB and display
                image_rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
                h, w, ch = image_rgb.shape
                bytes_per_line = 3 * w
                q_img = QImage(image_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(q_img)
                self.image_label.setPixmap(pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio))

                self.hist_canvas.plot_histogram(image_rgb)

                # Safely stop streaming after this callback exits
                QTimer.singleShot(0, lambda: cam.stop_streaming())

            # === Start Streaming and Trigger ===
            self.cam.start_streaming(handler)

            time.sleep(0.0001)               # 100 µs delay
            self.cam.TriggerSoftware.run()   # Software trigger to camera
            self.image_label.setText("Snapshot taken 100μs after trigger.")


        except Exception as e:
            self.image_label.setText(f"Error capturing snapshot:\n{e}")

    def closeEvent(self, event):
        try:
            if self.cam:
                try:
                    self.cam.__exit__(None, None, None)
                    self.cam = None
                except Exception as cam_err:
                    print(f"Error closing camera: {cam_err}")

            if self.vimba:
                try:
                    self.vimba.__exit__(None, None, None)
                    self.vimba = None
                except Exception as vimba_err:
                    print(f"Error closing Vimba system: {vimba_err}")

        except Exception as e:
            print(f"Unhandled error in closeEvent: {e}")

        event.accept()

class MainApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Camera and Trigger Controller")

        # Create CameraApp widget
        self.camera_app = CameraApp()

        # Layout setup
        layout = QVBoxLayout()
        layout.addWidget(self.camera_app)           # Embed camera app
        # layout.addWidget(self.trigger_btn)          # Add trigger button

        self.setLayout(layout)

    def closeEvent(self, event):
        # Close LabJack
        self.camera_app.close()
        ljm.close(handle)
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C handling
    win = MainApp()
    win.show()
    sys.exit(app.exec_())
