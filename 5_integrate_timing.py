from vmbpy import *
from avaspec import *
import sys, time, signal
import cv2
from labjack import ljm
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QSizePolicy, QHBoxLayout, QTextEdit, QVBoxLayout
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import csv
from datetime import datetime

'''
LabJack (send 5V instead of acquiring temp.) 
'''

handle = ljm.openS("ANY", "USB", "ANY") #Connect to LabJack
SPEC_TRIG_LINE = "FIO4"
CAM_TRIG_LINE = "FIO5"


# start in input mode
ljm.eWriteName(handle, SPEC_TRIG_LINE, 0)   # 0 = input/high-Z
ljm.eWriteName(handle, CAM_TRIG_LINE, 0)

'''
Avantas spectrometer
'''

def avantes_init(int_time, int_delay, num_ave, trig_mode):
    AVS_Init(0)
    device_list = AVS_GetList()[0]
    handle = AVS_Activate(device_list)
    info = AVS_GetParameter(handle)
    pixels = info.m_Detector_m_NrPixels
    wavelength_calibration = AVS_GetLambda(handle)
    measconfig = MeasConfigType()

    measconfig.m_StartPixel = 0
    measconfig.m_StopPixel = pixels - 1
    measconfig.m_IntegrationTime = int_time
    measconfig.m_IntegrationDelay = int_delay
    measconfig.m_NrAverages = num_ave
    measconfig.m_CorDynDark_m_Enable = 0
    measconfig.m_CorDynDark_m_ForgetPercentage = 100
    measconfig.m_Smoothing_m_SmoothPix = 0
    measconfig.m_Smoothing_m_SmoothModel = 0
    measconfig.m_SaturationDetection = 0
    measconfig.m_Trigger_m_Mode = trig_mode
    measconfig.m_Trigger_m_Source = 0
    measconfig.m_Trigger_m_SourceType = 0
    measconfig.m_Control_m_StrobeControl = 0
    measconfig.m_Control_m_LaserDelay = 0
    measconfig.m_Control_m_LaserWidth = 0
    measconfig.m_Control_m_LaserWaveLength = 0.0
    measconfig.m_Control_m_StoreToRam = 0

    return wavelength_calibration, handle, pixels, measconfig

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
    def __init__(self, camera_controller, spectrometer_controller,
                 snapshot_handler, spectral_handler, data_saver, trigger_controller=None):
        super().__init__()

        # === Injected back-end components ===
        self.camera_controller = camera_controller
        self.spectrometer_controller = spectrometer_controller
        self.snapshot_handler = snapshot_handler
        self.spectral_handler = spectral_handler
        self.data_saver = data_saver
        self.trigger_controller = trigger_controller

        # === UI Elements ===
        self.init_camera_button = QPushButton("Initialize Camera")
        self.init_spec_button = QPushButton("Initialize Spectrometer")
        self.snap_button = QPushButton("Take Snapshot")
        self.snap_button.setEnabled(False)

        self.btn_measure = QPushButton("Single Trigger Measure")
        self.snapshot_label = QLabel("No Image")
        self.snapshot_label.setAlignment(Qt.AlignCenter)
        self.snapshot_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.hist_canvas = HistogramCanvas(self, width=4, height=2)
        self.spectrum_canvas = FigureCanvas(Figure(figsize=(4, 2)))
        self.spectrum_axes = self.spectrum_canvas.figure.add_subplot(111)

        self.trigger_all_button = QPushButton("Trigger All")

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(100)

        self.setup_layout()
        self.setup_connections()

    def setup_layout(self):
        layout = QVBoxLayout()

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.init_camera_button)
        btn_layout.addWidget(self.init_spec_button)
        btn_layout.addWidget(self.snap_button)

        img_layout = QHBoxLayout()
        img_layout.addWidget(self.snapshot_label)
        img_layout.addWidget(self.hist_canvas)

        layout.addLayout(btn_layout)
        layout.addLayout(img_layout)
        layout.addWidget(self.btn_measure)
        layout.addWidget(self.spectrum_canvas)
        layout.addWidget(self.log_output)
        layout.addWidget(self.trigger_all_button)

        self.setLayout(layout)

    def setup_connections(self):
        self.init_camera_button.clicked.connect(self.initialize_camera)
        self.init_spec_button.clicked.connect(self.initialize_spectrometer)
        self.snap_button.clicked.connect(self.take_snapshot)
        self.btn_measure.clicked.connect(self.run_spectrometer_measurement)
        self.trigger_all_button.clicked.connect(self.run_full_trigger)

    def log(self, message):
        print(message)
        self.log_output.append(message)

    def initialize_camera(self):
        try:
            self.camera_controller.initialize_camera()
            self.log("Camera initialized.")
            self.snap_button.setEnabled(True)
        except Exception as e:
            self.log(f"Failed to initialize camera: {e}")

    def initialize_spectrometer(self):
        try:
            wavelengths = self.spectrometer_controller.initialize(trig_mode=0)
            self.wavelengths = wavelengths
            self.log("Spectrometer initialized.")
        except Exception as e:
            self.log(f"Failed to initialize spectrometer: {e}")

    def take_snapshot(self):
        try:
            self.log("Taking snapshot...")
            image = self.snapshot_handler.take_snapshot()
            self.display_image(image)
            self.hist_canvas.plot_histogram(cv2.cvtColor(image, cv2.COLOR_GRAY2RGB))
            self.log("Snapshot and histogram updated.")
        except Exception as e:
            self.log(f"Error taking snapshot: {e}")

    def run_spectrometer_measurement(self):
        try:
            self.log("Running single trigger spectrometer measurement...")
            timestamp, spectrum = self.spectral_handler.measure()
            self.plot_spectrum(self.wavelengths, spectrum)
            saved_file = self.data_saver.save_spectrum(self.wavelengths, spectrum)
            self.log(f"Spectral data saved to {saved_file}")
        except Exception as e:
            self.log(f"Spectrometer error: {e}")

    def display_image(self, image):
        image_rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        h, w, ch = image_rgb.shape
        bytes_per_line = 3 * w
        q_img = QImage(image_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)
        self.snapshot_label.setPixmap(
            pixmap.scaled(
                self.snapshot_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
        )

    def plot_spectrum(self, wavelengths, spectrum):
        self.spectrum_axes.clear()
        self.spectrum_axes.plot(wavelengths, spectrum, label="Spectrum")
        self.spectrum_axes.set_xlabel("Wavelength (nm)")
        self.spectrum_axes.set_ylabel("Intensity")
        self.spectrum_axes.set_title("Single Software Trigger Spectrum")
        self.spectrum_axes.grid(True)
        self.spectrum_axes.legend()
        self.spectrum_canvas.draw()

    def run_full_trigger(self):
        if not self.trigger_controller:
            self.log("Trigger controller not set.")
            return

        try:
            self.log("Sending full trigger sequence...")
            self.trigger_controller.run(wavelengths=getattr(self, "wavelengths", None))
            self.log("Full trigger sequence complete.")
        except Exception as e:
            self.log(f"Trigger failed: {e}")

class CameraController:
    def __init__(self):
        self.vimba = VmbSystem.get_instance()
        self.cam = None

    def initialize_camera(self):
        self.vimba.__enter__()
        cams = self.vimba.get_all_cameras()
        if not cams:
            raise RuntimeError("No cameras found.")
        self.cam = cams[0]
        self.cam.__enter__()

        if PixelFormat.Mono8 in self.cam.get_pixel_formats():
            self.cam.set_pixel_format(PixelFormat.Mono8)
        else:
            raise RuntimeError("Mono8 format not supported.")

    def close(self):
        if self.cam:
            self.cam.__exit__(None, None, None)
        if self.vimba:
            self.vimba.__exit__(None, None, None)

class SpectrometerController:
    def __init__(self, int_time=10.0, delay=0, num_ave=1):
        self.int_time = int_time
        self.delay = delay
        self.num_ave = num_ave
        self.handle = None
        self.measconfig = None

    def initialize(self, trig_mode=0):
        wavelengths, handle, pixels, measconfig = avantes_init(
            self.int_time, self.delay, self.num_ave, trig_mode)
        self.handle = handle
        self.measconfig = measconfig
        return wavelengths

class SnapshotHandler:
    def __init__(self, cam):
        self.cam = cam

    def take_snapshot(self, output_path="frame.jpg"):
        self.cam.TriggerSource.set("Line1")
        self.cam.TriggerSelector.set("FrameStart")
        self.cam.TriggerMode.set("Off")
        self.cam.AcquisitionMode.set("SingleFrame")

        frame = self.cam.get_frame()
        frame.convert_pixel_format(PixelFormat.Mono8)
        image = frame.as_opencv_image()

        cv2.imwrite(output_path, image)
        return image

class SpectralMeasurementHandler:
    def __init__(self, spec_ctrl):
        self.ctrl = spec_ctrl

    def measure(self):
        AVS_PrepareMeasure(self.ctrl.handle, self.ctrl.measconfig)
        AVS_Measure(self.ctrl.handle, 0, 1)

        while not AVS_PollScan(self.ctrl.handle):
            time.sleep(0.01)

        timestamp, spectrum = AVS_GetScopeData(self.ctrl.handle)
        return timestamp, spectrum
    
class Trigger:
    def __init__(self, snapshot_handler, spectral_handler, data_saver, handle=None, spec_trig_line="FIO4"):
        self.snapshot_handler = snapshot_handler
        self.spectral_handler = spectral_handler
        self.data_saver = data_saver
        self.spec_trig_line = spec_trig_line

        # LabJack handle: use existing or create a new one
        self.handle = handle or ljm.openS("ANY", "USB", "ANY")

        # Set line to input/high-Z initially
        ljm.eWriteName(self.handle, self.spec_trig_line, 0)

    def send_trigger(self, pulse_us=100):
        """Send a short digital pulse on TRIG_LINE to trigger external hardware."""
        print("Triggering LabJack output...")

        # Set pin to output-high
        ljm.eWriteName(self.handle, self.spec_trig_line, 1)
        time.sleep(pulse_us / 1_000_000.0)  # e.g., 100 µs
        # Return to high-Z input (simulates open circuit)
        ljm.eWriteName(self.handle, self.spec_trig_line, 0)

        print(f"LabJack trigger pulse sent on {self.spec_trig_line} for {pulse_us}µs")

    def run(self, wavelengths=None):
        """Perform the full trigger routine: trigger → snapshot → spectrum"""
        try:
            self.send_trigger()

            print("Running snapshot handler...")
            image = self.snapshot_handler.take_snapshot()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = f"snapshot_{timestamp}.jpg"
            print(f"Image saved to {image_path}")

            print("Running spectrometer measurement...")
            timestamp, spectrum = self.spectral_handler.measure()

            if wavelengths:
                csv_path = self.data_saver.save_spectrum(wavelengths, spectrum)
                print(f"Spectrum saved to {csv_path}")
            else:
                print("Wavelengths not provided, skipping spectrum save.")

        except Exception as e:
            print(f"Error during trigger routine: {e}")

    def close(self):
        try:
            ljm.close(self.handle)
            print("LabJack closed.")
        except Exception as e:
            print(f"Error closing LabJack: {e}")

class DataSaver:
    @staticmethod
    def save_spectrum(wavelengths, intensities, prefix="spectrum"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{timestamp}.csv"
        with open(filename, mode="w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Wavelength (nm)", "Intensity"])
            for wl, intensity in zip(wavelengths, intensities):
                writer.writerow([wl, intensity])
        return filename


'''
Window config
'''

class MainApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Trigger Controller")

        # === Initialize back-end logic modules ===
        self.camera_controller = CameraController()
        self.spectrometer_controller = SpectrometerController()
        self.snapshot_handler = SnapshotHandler(self.camera_controller.cam)  # You may delay this until camera is initialized
        self.spectral_handler = SpectralMeasurementHandler(self.spectrometer_controller)

        # === Pass controllers to GUI ===
        self.camera_app = CameraApp(
            camera_controller=self.camera_controller,
            spectrometer_controller=self.spectrometer_controller,
            snapshot_handler=self.snapshot_handler,
            spectral_handler=self.spectral_handler,
            data_saver=DataSaver
        )

        self.trigger = Trigger(
            snapshot_handler=self.snapshot_handler,
            spectral_handler=self.spectral_handler,
            data_saver=DataSaver,
            # handle=ljm_handle,
            spec_trig_line="FIO4"
        )

        layout = QVBoxLayout()
        layout.addWidget(self.camera_app)
        self.setLayout(layout)

    def closeEvent(self, event):
        try:
            self.camera_controller.close()
        except Exception as e:
            print(f"Error closing camera: {e}")

        try:
            pass  # Optional: handle spectrometer shutdown
        except Exception as e:
            print(f"Error closing spectrometer: {e}")

        try:
            ljm.close(handle)  # Optional
        except Exception as e:
            print(f"Error closing LabJack: {e}")

        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C handling
    win = MainApp()
    win.show()
    sys.exit(app.exec_())