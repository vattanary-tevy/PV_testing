from PIL import Image, ImageTk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
from vmbpy import *
from datetime import datetime
# from avaspec import *
import sys, time, signal
import cv2
import matplotlib.ticker as ticker
import pyvisa
import os
import pandas as pd
from labjack import ljm
import math
import socket
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
Allied Vision Camera Functions
'''

# def handler_EL(cam: Camera, stream: Stream, frame: Frame):
#     print('Frame acquired: {}'.format(frame), flush=True)
#     frame.convert_pixel_format(PixelFormat.Mono8)
#     cv2.imwrite(folder_timestamp+'_EL_image_'+str(EL_image_counter)+'.png', frame.as_opencv_image())
#     time.sleep(0.3)
#     cam.queue_frame(frame)

# def handler_stan(cam: Camera, stream: Stream, frame: Frame):
#     print('Frame acquired: {}'.format(frame), flush=True)
#     frame.convert_pixel_format(PixelFormat.Mono8)
#     cv2.imwrite(folder_timestamp+'_standard_image_'+str(stan_image_counter)+'.png', frame.as_opencv_image())
#     time.sleep(0.3)
#     cam.queue_frame(frame)

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
            self.vimba.__enter__()  # enter context manually
            cams = self.vimba.get_all_cameras()
            if not cams:
                self.image_label.setText("No cameras found!")
                return
            self.cam = cams[0]
            self.cam.__enter__()  # open camera context manually

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

                # Plot histogram
                self.hist_canvas.plot_histogram(image_rgb)

                self.image_label.setText("Snapshot taken 100μs after trigger.")

                # Safely stop streaming after this callback exits
                QTimer.singleShot(0, lambda: cam.stop_streaming())

            # === Start Streaming and Trigger ===
            self.cam.start_streaming(handler)

            send_trigger()                   # Fire LabJack 5V trigger
            print("5V Trigger sent.")
            time.sleep(0.0001)               # 100 µs delay
            self.cam.TriggerSoftware.run()   # Software trigger to camera

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


'''
Avantas spectrometer
'''

# def avantes_init(int_time, int_delay, num_ave, trig_mode):
#     #trig_mode of 1 is hardware trigger, trig_mode of 0 is software trigger
#     AVS_Init(0)
#     device_list = AVS_GetList()[0]
#     handle = AVS_Activate(device_list)
#     info = AVS_GetParameter(handle)
#     pixels = info.m_Detector_m_NrPixels
#     wavelength_calibration = AVS_GetLambda(handle)
#     measconfig = MeasConfigType()

#     measconfig.m_StartPixel = 0
#     measconfig.m_StopPixel = pixels - 1
#     measconfig.m_IntegrationTime = int_time
#     measconfig.m_IntegrationDelay = int_delay
#     measconfig.m_NrAverages = num_ave
#     measconfig.m_CorDynDark_m_Enable = 0
#     measconfig.m_CorDynDark_m_ForgetPercentage = 100
#     measconfig.m_Smoothing_m_SmoothPix = 0
#     measconfig.m_Smoothing_m_SmoothModel = 0
#     measconfig.m_SaturationDetection = 0
#     measconfig.m_Trigger_m_Mode = trig_mode
#     measconfig.m_Trigger_m_Source = 0
#     measconfig.m_Trigger_m_SourceType = 0
#     measconfig.m_Control_m_StrobeControl = 0
#     measconfig.m_Control_m_LaserDelay = 0
#     measconfig.m_Control_m_LaserWidth = 0
#     measconfig.m_Control_m_LaserWaveLength = 0.0
#     measconfig.m_Control_m_StoreToRam = 0

#     return wavelength_calibration, handle, pixels, measconfig


# def avantes_measure(handle, measconfig, num_scans):
#     AVS_PrepareMeasure(handle, measconfig)
#     AVS_Measure(handle, -2, num_scans)

# def avantes_readout(pixels, wavelength_calibration, spec_num_scans, handle, spec_int_time):
#     wavelengths = []
#     for pixel in range(pixels):
#         wavelengths.append(wavelength_calibration[pixel])

#     ret_arr = []
#     timestamp_arr = []
#     spectra_data_arr = []

#     for i in range(spec_num_scans):
#         # check if the data is collected
#         dataready = False
#         m = 0
#         while not dataready:
#             # check if data is ready
#             dataready = AVS_PollScan(handle)
#             #print(dataready)
#             # sleep and then check again
#             time.sleep(spec_int_time / 1000)

#         # get the scope data
#         ret_arr.append(AVS_GetScopeData(handle))
#         #print("ret_arr", ret_arr)
#     for i in range(len(ret_arr)):
#         timestamp_arr.append(ret_arr[i][0])
#         spectra_data = []
#         for j, pix in enumerate(wavelengths):
#             spectra_data.append(ret_arr[i][1][j])
#         spectra_data_arr.append(spectra_data)
#         #print(len(spectra_data))
#         #print(spectra_data)
#     return ret_arr, timestamp_arr, spectra_data_arr, wavelengths

# '''
# Measurement (change to PL measurement)
# '''

# def EL(inst, current, voltage_limit, num_points_1_dir, nplc, delay,num_sweeps, spec_int_time, spec_int_delay, spec_num_ave, spec_num_scans, cam_expose_time):
#     print("EL Measurement Started")
#     spec_trig_mode = 1 #hardware trigger
#     wavelength_calibration, handle, pixels, measconfig = avantes_init(spec_int_time, spec_int_delay, spec_num_ave, spec_trig_mode)
#     time.sleep(0.1)
#     avantes_measure(handle, measconfig, spec_num_scans)
#     time.sleep(0.1)

#     voltage_initial = voltage_limit
#     voltage_final = voltage_limit
#     current_limit = current
#     SMU2651A_source_V_EL(inst, voltage_initial, voltage_final, current_limit, num_points_1_dir, nplc, delay, num_sweeps)
#     time.sleep(.5)

#     with VmbSystem.get_instance() as vmb:
#         cam = vmb.get_all_cameras()[0]

#         with cam:
#             cam.ExposureTime.set(cam_expose_time)
#             #cam.TriggerSource.set('Software')
#             cam.TriggerSource.set('Line1')
#             cam.TriggerSelector.set('FrameStart')
#             cam.TriggerMode.set('On')
#             cam.AcquisitionMode.set('Continuous')

#             try:
#                 cam.start_streaming(handler_EL)
#                 time.sleep(1)
#                 time_start = time.time_ns()//1000000
#                 inst.write("SourceVEL()")
#                 time.sleep(2)
                
#             finally:
#                 cam.stop_streaming()

#     timestamps, currents, voltages = SMU2651A_time_curr_volt_readout(inst)

#     ret_arr, timestamp_arr, spectra_data_arr, wavelengths = avantes_readout(pixels, wavelength_calibration, spec_num_scans, handle, spec_int_time)
#     time.sleep(0.2)

#     spec_trig_mode = 0 #hardware trigger
#     wavelength_calibration, handle, pixels, measconfig = avantes_init(spec_int_time, spec_int_delay, spec_num_ave, 0)
#     time.sleep(0.2)
#     avantes_measure(handle, measconfig, spec_num_scans)
#     time.sleep(0.2)

#     ret_arr_ref, timestamp_arr_ref, spectra_data_arr_ref, wavelengths_ref = avantes_readout(pixels, wavelength_calibration, spec_num_scans, handle, spec_int_time)
#     time.sleep(0.5)

#     print("EL Measurement Complete")

#     return timestamps, currents, voltages, ret_arr, timestamp_arr, spectra_data_arr, wavelengths, ret_arr_ref, timestamp_arr_ref, spectra_data_arr_ref, wavelengths_ref, time_start


# def stan_image(cam_expose_time):
#     print("Image Recording Started")
#     with VmbSystem.get_instance() as vmb:
#         cam = vmb.get_all_cameras()[0]

#         with cam:
#             cam.ExposureTime.set(cam_expose_time)
#             cam.TriggerSource.set('Software')
#             cam.TriggerSelector.set('FrameStart')
#             cam.TriggerMode.set('On')
#             cam.AcquisitionMode.set('Continuous')

#             try:
#                 cam.start_streaming(handler_stan)
#                 time.sleep(0.2)
#                 time_start = time.time_ns()//1000000
#                 cam.TriggerSoftware.run()
#                 time.sleep(0.5)
                
#             finally:
#                 cam.stop_streaming()
#     print("Image Recording Complete")
#     return time_start


'''
Record data
'''



'''
Settings
'''



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
        layout.addWidget(self.trigger_btn)          # Add trigger button

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
