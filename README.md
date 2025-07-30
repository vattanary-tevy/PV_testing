# PV_testing

Integrates an Allied Vision camera, an Avantes spectrometer, and a LabJack U3-HV to perform synchronized image and spectral data acquisition using a PyQt5 GUI.

## Key Libraries

- PyQt5
- OpenCV (cv2)
- Matplotlib
- NumPy
- LabJackM (via labjack-ljm)
- Avantes AvaSpec SDK
- Allied Vision Vimba SDK

## Getting Started
1. Setup Hardware
2. Connect Allied Vision camera via USB.
3. Connect LabJack U3 and ensure FIO4 is free for triggering.
4. Connect the spectrometer.

## GUI Controls
1. Initialize Camera: Opens and configures the AV camera.
2. Initialize Spectrometer: Connects and configures the spectrometer.
3. Take Snapshot: Sends 5V trigger → captures image → plots histogram.
4. Single Trigger Measure: Measures spectrum using software trigger → plots spectrum.

## Output
- Captured frames: frame.jpg
- Spectra: data/spectrum_<timestamp>.csv