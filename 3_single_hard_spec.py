import sys
import time
import signal
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from avaspec import *
from labjack import ljm

# === LabJack Constants ===
SPEC_TRIG_LINE = "FIO4"

# === Global Variables ===
spec_handle = None
meas_config = None
wavelengths = None
pixels = None

# === LabJack Setup ===
lj_handle = ljm.openS("ANY", "USB", "ANY")
ljm.eWriteName(lj_handle, SPEC_TRIG_LINE, 0)  # Set as input/high-Z

# === Avantes Spectrometer Init ===
def initialize_spectrometer(int_time=10.0, delay=0, num_ave=1, trig_mode=1):
    global spec_handle, meas_config, wavelengths, pixels

    AVS_Init(0)
    device_list = AVS_GetList()[0]
    spec_handle = AVS_Activate(device_list)
    info = AVS_GetParameter(spec_handle)
    pixels = info.m_Detector_m_NrPixels
    wavelengths = AVS_GetLambda(spec_handle)

    meas_config = MeasConfigType()
    meas_config.m_StartPixel = 0
    meas_config.m_StopPixel = pixels - 1
    meas_config.m_IntegrationTime = int_time
    meas_config.m_IntegrationDelay = delay
    meas_config.m_NrAverages = num_ave
    meas_config.m_CorDynDark_m_Enable = 0
    meas_config.m_CorDynDark_m_ForgetPercentage = 100
    meas_config.m_Smoothing_m_SmoothPix = 0
    meas_config.m_Smoothing_m_SmoothModel = 0
    meas_config.m_SaturationDetection = 0
    meas_config.m_Trigger_m_Mode = trig_mode  # 1 = HW trigger
    meas_config.m_Trigger_m_Source = 0
    meas_config.m_Trigger_m_SourceType = 0
    meas_config.m_Control_m_StrobeControl = 0

# === Start Spectrometer Measurement and Trigger via LabJack ===
def trigger_measurement():
    if spec_handle is None or meas_config is None:
        raise RuntimeError("Spectrometer not initialized.")

    # Start spectrometer in HW trigger mode
    AVS_PrepareMeasure(spec_handle, meas_config)
    AVS_Measure(spec_handle, -2, 1)  # -2 = HW trigger

    # Send trigger pulse via LabJack
    ljm.eWriteName(lj_handle, SPEC_TRIG_LINE, 1)
    time.sleep(0.0001)  # 100 microseconds
    ljm.eWriteName(lj_handle, SPEC_TRIG_LINE, 0)

    # Wait for spectrometer to acquire
    while not AVS_PollScan(spec_handle):
        time.sleep(0.01)

    timestamp, spectrum = AVS_GetScopeData(spec_handle)
    return wavelengths, spectrum

# === PyQt5 GUI ===
class SpectrometerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spectrometer Trigger with Display")

        # Buttons and output
        self.init_btn = QPushButton("Initialize Spectrometer")
        self.trigger_btn = QPushButton("Trigger Spectrometer")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        # Matplotlib plot
        self.figure = Figure(figsize=(5, 3))
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.init_btn)
        layout.addWidget(self.trigger_btn)
        layout.addWidget(self.canvas)
        layout.addWidget(self.log_output)
        self.setLayout(layout)

        # Connect buttons
        self.init_btn.clicked.connect(self.init_spectrometer)
        self.trigger_btn.clicked.connect(self.trigger_spectrometer)

    def log(self, text):
        print(text)
        self.log_output.append(text)

    def init_spectrometer(self):
        try:
            initialize_spectrometer()
            self.log("Spectrometer initialized.")
        except Exception as e:
            self.log(f"Error initializing spectrometer: {e}")

    def trigger_spectrometer(self):
        try:
            self.log("Triggering measurement...")
            wls, intensities = trigger_measurement()
            self.log("Measurement complete.")
            self.plot_spectrum(wls, intensities)
        except Exception as e:
            self.log(f"Error during trigger: {e}")

    def plot_spectrum(self, wls, intensities):
        self.ax.clear()
        self.ax.plot(wls, intensities, color='blue')
        self.ax.set_title("Captured Spectrum")
        self.ax.set_xlabel("Wavelength (nm)")
        self.ax.set_ylabel("Intensity")
        self.ax.grid(True)
        self.canvas.draw()

    def closeEvent(self, event):
        try:
            ljm.close(lj_handle)
        except Exception as e:
            print(f"Error closing LabJack: {e}")
        event.accept()

# === Main Entry ===
if __name__ == "__main__":
    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Handle Ctrl+C
    window = SpectrometerApp()
    window.show()
    sys.exit(app.exec_())
