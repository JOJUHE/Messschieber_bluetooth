

SSID = "JOJU_Net"
PASSWORD = "7297597020208224"




from camera import Camera, FrameSize, PixelFormat
import time

# Wir versuchen einen "Kaltstart" mit minimalen Anforderungen
print("Starte Initialisierung...")

try:
    # Versuche es erst mal ganz ohne PixelFormat im Konstruktor, 
    # da manche Treiber JPEG als Standard haben, sobald FrameSize > CIF ist.
    # cam = Camera(frame_size=FrameSize.VGA)
    cam = Camera(frame_size=FrameSize.VGA, xclk_freq=10000000) # 10 MHz statt 20 MHz

    # Falls das fehlschlägt, probier stattdessen:
    # cam = Camera(frame_size=FrameSize.VGA, pixel_format=PixelFormat.JPEG)
    
    if cam.init():
        print("Initialisierung erfolgreich!")
        time.sleep(2) # Wichtig: Geb dem OV5640 Zeit zum "Aufwachen"
        
        # Jetzt versuchen wir die Qualität zu erhöhen
        cam.set_quality(15) 
        cam.set_brightness(2) # Helligkeit hoch für dein dunkles Zimmer
        
        # Test-Foto
        img = cam.capture()
        print(f"Bild aufgenommen! Größe: {len(img)} Bytes")
    else:
        print("init() hat False zurückgegeben.")
        
except Exception as e:
    print(f"Fehler beim Erstellen des Objekts: {e}")

# cam = Camera()
# cam = Camera(frame_size=FrameSize.SVGA)
# cam.init()
time.sleep(2) 
print("dir(cam):")
print(dir(cam))

print("\nTestbild:")
print(f"Kamera auflösung: {cam.get_pixel_width()}x{cam.get_pixel_height()}")
test = cam.capture()
print(f"Bytes: {len(test)}")
print(f"Erste Bytes: {test[0]:02X} {test[1]:02X} {test[2]:02X} {test[3]:02X}")

import network, socket, time
from camera import Camera, FrameSize

HTML = """<!DOCTYPE html>
<html>
<head><title>ESP32-S3-EYE</title></head>
<body style="background:#111;text-align:center;font-family:sans-serif">
<h2 style="color:white">ESP32-S3-EYE Kamera</h2>
<canvas id="c" width="640" height="480"
  style="width:640px;height:480px;margin-top:20px;display:block;margin-inline:auto">
</canvas>
<p style="color:#aaa">Live • alle 2000ms</p>
<script>
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const W = 640, H = 480;

async function update() {
  try {
    const r = await fetch('/image?' + Date.now());
    const buf = await r.arrayBuffer();
    const data = new Uint8Array(buf);
    const img = ctx.createImageData(W, H);
    for (let i = 0; i < W * H; i++) {
      const b0 = data[i*2], b1 = data[i*2+1];
      const px = (b0 << 8) | b1;
      img.data[i*4]   = (px >> 8) & 0xF8;
      img.data[i*4+1] = (px >> 3) & 0xFC;
      img.data[i*4+2] = (px << 3) & 0xF8;
      img.data[i*4+3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  } catch(e) { console.log(e); }
  setTimeout(update, 2000);
}
update();
</script>
</body>
</html>"""

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)
print("Verbinde mit WLAN", end="")
while not wlan.isconnected():
    print(".", end="")
    time.sleep(0.3)
print("\nIP:", wlan.ifconfig()[0])

#(FrameSize.QVGA,  "QVGA  320x240")
#(FrameSize.HQVGA, "HQVGA 240x176")
#(FrameSize.CIF,   "CIF   400x296")
#(FrameSize.VGA,   "VGA   640x480")
# FrameSize.UXGA  1600x1200
# FrameSize.SVGA (800x600)
# Maximale Auflösung für OV5640 (ca. 5MP)
# cam = Camera(frame_size=FrameSize.QSXGA)
cam = Camera(frame_size=FrameSize.SVGA)
cam.init()
time.sleep(2) 
cam.set_vflip(True)
# cam.set_hmirror(True)

# Qualität erhöhen (niedrigerer Wert = bessere Qualität, Bereich 10-63)
# Achtung: Braucht viel PSRAM! Wenn es abstürzt, nimm 15 oder 20.
# cam.set_quality(20)

# Werte jeweils von -2 bis +2
cam.set_brightness(1)   # Helligkeit  (-2 dunkel bis +2 hell)
cam.set_contrast(1)     # Kontrast    (-2 bis +2)
cam.set_saturation(1)   # Sättigung   (-2 bis +2)
cam.set_sharpness(1)    # Schärfe     (-2 bis +2)

cam.set_brightness(2)   # Maximum für dunkle Räume
cam.set_contrast(0)     # Bei Dunkelheit Kontrast eher neutral (0) lassen, sonst säuft Schwarz ab
cam.set_sharpness(2)    # Maximum, um Kanten zu retten

# Aktuelle Werte lesen
print(f"Helligkeit:  {cam.get_brightness()}")
print(f"Kontrast:    {cam.get_contrast()}")
print(f"Sättigung:   {cam.get_saturation()}")
print(f"Schärfe:     {cam.get_sharpness()}")
print(f"Kamera1: {cam.get_pixel_width()}x{cam.get_pixel_height()}")
print(f"Kamera2: {cam.get_frame_size()}")
print(f"Kamera3: {cam.get_max_frame_size()}")
print(f"Kamera4: {cam.get_quality()}")
print(f"Kamera5: {cam.get_sensor_name()}")

# Mache ggf. ein "Wegwerf-Bild", damit der Puffer frisch ist
dummy = cam.capture() 
time.sleep(2) 
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('', 80))
s.listen(5)
print("Bereit! Browser: http://" + wlan.ifconfig()[0])

while True:
    try:
        conn, addr = s.accept()
        req = conn.recv(1024).decode()
        if 'GET /image' in req:
            img = cam.capture()
            conn.send(b'HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n')
            conn.sendall(img)
        else:
            conn.send(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n')
            conn.send(HTML.encode())
        conn.close()
    except Exception as e:
        print("Fehler:", e)
        try: conn.close()
        except: pass66+