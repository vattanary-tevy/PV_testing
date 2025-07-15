from vmbpy import *
from avaspec import *
import sys, time, signal
import cv2
from labjack import ljm
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QTimer, QObject, QThread
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QSizePolicy, QHBoxLayout, QTextEdit
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import csv
from datetime import datetime


'''
LabJack (send 5V instead of acquiring temp.) 
'''

handle = ljm.openS("ANY", "USB", "ANY") #Connect to LabJack
TRIG_LINE = "FIO4"

# start in input mode
ljm.eWriteName(handle, TRIG_LINE, 0)   # 0 = input/high-Z

def send_trigger(pulse_us=100):
    """
    drive input low for pulse_us microseconds, then release it so the 5V pull-up resistor 
    returns the line to logic-high
    """
    ljm.eWriteName(handle, TRIG_LINE, 1)   # output mode
    ljm.eWriteName(handle, TRIG_LINE, 0)    # drive low
    time.sleep(pulse_us / 1_000_000)
    ljm.eWriteName(handle, TRIG_LINE, 0)   # back to input
    print("5V trigger sent")


'''
Avantas spectrometer
'''

class SpectrometerWorker(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(object, object, object, object)
    error = pyqtSignal(str)

    def __init__(self, int_time, int_delay, num_ave, num_scans):
        super().__init__()
        self.int_time = int_time
        self.int_delay = int_delay
        self.num_ave = num_ave
        self.num_scans = num_scans

    def run(self):
        try:
            # Setup with hardware trigger
            trig_mode = 1
            wavelength_calibration, handle, pixels, measconfig = avantes_init(
                self.int_time, self.int_delay, self.num_ave, trig_mode
            )

            AVS_PrepareMeasure(handle, measconfig)

            send_trigger(pulse_us=100)

            AVS_Measure(handle, -1, self.num_scans)

            for i in range(self.num_scans):
                while not AVS_PollScan(handle):
                    time.sleep(0.001)

            ret_arr, timestamp_arr, spectra_data_arr, wavelengths = avantes_readout(
                pixels, wavelength_calibration, self.num_scans, handle, self.int_time
            )

            self.result.emit(ret_arr, timestamp_arr, spectra_data_arr, wavelengths)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()

def avantes_init(int_time, int_delay, num_ave, trig_mode):
    #trig_mode of 1 is hardware trigger, trig_mode of 0 is software trigger
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


def avantes_measure(handle, measconfig, num_scans):
    AVS_PrepareMeasure(handle, measconfig)
    AVS_Measure(handle, -2, num_scans)

def avantes_readout(pixels, wavelength_calibration, spec_num_scans, handle, spec_int_time):
    wavelengths = []
    for pixel in range(pixels):
        wavelengths.append(wavelength_calibration[pixel])

    ret_arr = []
    timestamp_arr = []
    spectra_data_arr = []

    for i in range(spec_num_scans):
        # check if the data is collected
        dataready = False
        m = 0
        while not dataready:
            # check if data is ready
            dataready = AVS_PollScan(handle)
            #print(dataready)
            # sleep and then check again
            time.sleep(spec_int_time / 1000)

        # get the scope data
        ret_arr.append(AVS_GetScopeData(handle))
        #print("ret_arr", ret_arr)
    for i in range(len(ret_arr)):
        timestamp_arr.append(ret_arr[i][0])
        spectra_data = []
        for j, pix in enumerate(wavelengths):
            spectra_data.append(ret_arr[i][1][j])
        spectra_data_arr.append(spectra_data)
        #print(len(spectra_data))
        #print(spectra_data)
    return ret_arr, timestamp_arr, spectra_data_arr, wavelengths

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
    frame_ready = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vimba Camera Snapshot GUI")

        self.vimba = None
        self.cam = None
        self.spec_initialized = False

        self.spec_int_time = 10.0         # milliseconds
        self.spec_int_delay = 0           # milliseconds
        self.spec_num_ave = 1
        self.spec_num_scans = 1

        # UI Elements
        self.init_camera_button = QPushButton("Initialize Camera")
        self.init_spec_button = QPushButton("Initialize spectrometer")
        self.snap_button = QPushButton("Take Snapshot")
        self.snap_button.setEnabled(False)

        self.snapshot_label = QLabel("No Image")
        self.snapshot_label.setAlignment(Qt.AlignCenter)
        self.snapshot_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.hist_canvas = HistogramCanvas(self, width=4, height=2)
        self.spectrum_canvas = FigureCanvas(Figure(figsize=(4, 2)))
        self.spectrum_axes = self.spectrum_canvas.figure.add_subplot(111)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(100)

        # Layout
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
        layout.addWidget(self.spectrum_canvas)
        layout.addWidget(self.log_output)
        self.setLayout(layout)

        # Connect buttons
        self.init_camera_button.clicked.connect(self.initialize_camera)
        self.init_spec_button.clicked.connect(self.initialize_spectrometer)
        self.snap_button.clicked.connect(self.take_snapshot)

        self.frame_ready.connect(self._post_frame_actions)

    def log(self, message):
        print(message)
        self.log_output.append(message)

    def save_spectral_data(self, wavelengths, intensities, filename_prefix="spectrum"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{filename_prefix}_{timestamp}.csv"
        try:
            with open(filename, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Wavelength (nm)", "Intensity"])
                for wl, intensity in zip(wavelengths, intensities):
                    writer.writerow([wl, intensity])
            self.log(f"Spectral data saved to {filename}")
        except Exception as e:
            self.log(f"Error saving spectral data: {e}")

    def initialize_spectrometer(self):
        try:
            _, self.avs_handle, _, self.measconfig = avantes_init(
                self.spec_int_time, self.spec_int_delay, self.spec_num_ave, 1
            )
            self.spec_initialized = True
            self.log("Spectrometer initialized.")
        except Exception as e:
            self.log(f"Failed to initialize spectrometer: {e}")

    def _post_frame_actions(self):
        try:
            self.cam.stop_streaming()
            self.log("Camera streaming stopped.")
        except Exception as e:
            self.log(f"Error stopping streaming: {e}")
        QTimer.singleShot(200, self.run_spectrometer)


    def initialize_camera(self):
        try:
            self.vimba = VmbSystem.get_instance()
            self.vimba.__enter__()  # enter context manually
            cams = self.vimba.get_all_cameras()
            if not cams:
                self.log("No cameras found!")
                return
            self.cam = cams[0]
            self.cam.__enter__()  # open camera context manually

            # Set pixel format BEFORE grabbing images
            supported_formats = self.cam.get_pixel_formats()
            if PixelFormat.Mono8 in supported_formats:
                self.cam.set_pixel_format(PixelFormat.Mono8)
            else:
                self.log("No supported pixel format (BGR8/Mono8) found.")
                return

            self.log("Camera initialized!")
            self.snap_button.setEnabled(True)

        except Exception as e:
            self.log(f"Error initializing camera:\n{e}")

    def take_snapshot(self):
        if not self.cam:
            self.log("Camera not initialized!")
            return
        if not self.spec_initialized:
            self.log("Spectrometer not initialized.")
            return

        self.log("Sending 5V trigger.")
        send_trigger(pulse_us=100)
        time.sleep(0.0001)

        # Set trigger config
        self.cam.TriggerSource.set("Line1")
        self.cam.TriggerSelector.set("FrameStart")
        # self.cam.TriggerMode.set("On")
        self.cam.TriggerMode.set("Off")
        # self.cam.AcquisitionMode.set("Continuous")
        self.cam.AcquisitionMode.set("SingleFrame")

        self.log("Triggering camera capture.")
        self.cam.start_streaming(self._on_frame_acquired)


    def run_spectrometer(self):
        self.log("Starting spectrometer thread.")
        self.thread = QThread()
        self.worker = SpectrometerWorker(
            self.spec_int_time,
            self.spec_int_delay,
            self.spec_num_ave,
            self.spec_num_scans
        )
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.worker.result.connect(self.handle_spectrometer_result)
        self.worker.error.connect(self.handle_spectrometer_error)

        self.thread.start()

    def _on_frame_acquired(self, cam, stream, frame):
        self.log("Frame acquired from camera.")
        frame.convert_pixel_format(PixelFormat.Mono8)
        image = frame.as_opencv_image()

        # Save image
        cv2.imwrite('frame.jpg', image)

        # Display image
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

        # Plot histogram
        self.log("Plotted histogram.")
        self.hist_canvas.plot_histogram(image_rgb)

        # Exit frame callback before stopping streaming
        QTimer.singleShot(10, self._post_frame_actions)  # allow callback to fully exit


    def handle_spectrometer_result(self, ret_arr, timestamp_arr, spectra_data_arr, wavelengths):
        self.log("Spectrometer capture completed.")
        if spectra_data_arr:
            self.spectrum_axes.clear()
            self.spectrum_axes.plot(wavelengths, spectra_data_arr[0])
            self.spectrum_axes.set_title("Spectrum (Intensity vs Wavelength)")
            self.spectrum_axes.set_xlabel("Wavelength (nm)")
            self.spectrum_axes.set_ylabel("Intensity")
            self.spectrum_canvas.draw()
            self.save_spectral_data(wavelengths, spectra_data_arr[0])
        else:
            self.log("No spectral data received.")

    def handle_spectrometer_error(self, error_msg):
        self.log(f"Spectrometer error: {error_msg}")

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


'''
Window config
'''

class MainApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Camera and Trigger Controller")

        # Create CameraApp widget
        self.camera_app = CameraApp()

        # Trigger button
        # self.trigger_btn = QPushButton("Fire 5V Pulse")
        # self.trigger_btn.clicked.connect(lambda: send_trigger(100))

        # Layout setup
        layout = QVBoxLayout()
        layout.addWidget(self.camera_app)           # Embed camera app
        # layout.addWidget(self.trigger_btn)          # Add trigger button

        self.setLayout(layout)

    def closeEvent(self, event):
        # Close LabJack
        try:
            ljm.close(handle)
        except Exception as e:
            print(f"Error closing LabJack: {e}")
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C handling
    win = MainApp()
    win.show()
    sys.exit(app.exec_())
