@echo off
adb shell dumpsys meminfo |find "vendor.samsung.hardware.camera.provider-service_64"
adb shell ps -e |find "camera"


pause
