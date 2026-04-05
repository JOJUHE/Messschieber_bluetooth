#!/usr/bin/env python3
"""
Tkinter-basierte GUI für Messschieber B-00014265 (BLE)

Start:
  python messschieber_tk.py

Hinweis: `bleak` und `tkinter` müssen installiert sein.

Reichweite BLE: Die Funkleistung ist im Gerät und gesetzlich begrenzt – per App nicht „verstärkbar“.
Metall (z. B. Stahlplatte) dämpft stark; ggf. Mac/Adapter näher an den Messbereich oder Messschieber
nicht direkt auf Metall legen.
"""

import asyncio
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import os
import traceback

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    from bleak import BleakScanner, BleakClient
    try:
        from bleak.exc import BleakDeviceNotFoundError
    except ImportError:
        class BleakDeviceNotFoundError(Exception):
            """Fallback für ältere bleak-Versionen."""
            pass
except ImportError as e:
    raise SystemExit("Fehler: bleak nicht installiert. Bitte: pip install bleak") from e

# Notify kann beim ruhigen Liegen lange ausbleiben – zu kurz = Fehlalarm / AttributeError beim Read
_NOTIFY_SILENCE_BEFORE_HEALTH_S = 18.0
_HEALTH_READ_INTERVAL_S = 10.0

TARGET_NAME = "B-00014265"
TARGET_UUID = "7710AC70-264A-25CF-CC4C-CEB90B1E0984"
CHARACTERISTIC_CANDIDATES = [
    "0000ffff-0000-1000-8000-00805f9b34fb",
    "00000001-0000-1000-8000-00805f9b34fb",
    "0001",
]

state = {
    "connected": False,
    "connecting": False,
    "value": "-- mm",
    "raw": "",
    "logs": [],
    "client": None,
    "reading": False,
    "disconnecting": False,
    "characteristic": None,
    "target_address": None,
    "auto_reconnect": True,
    "connected_once": False,
    "send_to_clipboard": True,
    "autopaste": True,
    "enter_after_paste": True,
    "last_notification_time": 0,
    "status_msg": "Initializing...",
    "decimal_sep": ".",  # "." oder "," für Anzeige und Clipboard
}

state_lock = threading.Lock()

root = None  # Global für Clipboard-Zugriff


def format_measurement_value(num_str: str, sep: str) -> str:
    """Wert für Anzeige/Clipboard: Punkt oder Komma als Dezimaltrennzeichen."""
    if not num_str or sep not in (".", ","):
        return num_str
    if sep == ",":
        return num_str.replace(".", ",", 1)
    return num_str.replace(",", ".", 1)


def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with state_lock:
        state["logs"].append(f"[{ts}] {msg}")
        if len(state["logs"]) > 200:
            state["logs"] = state["logs"][-200:]
    print(msg)


def normalize_uuid(uuid: str) -> str:
    u = (uuid or "").strip().lower()
    if len(u) == 4:
        return f"0000{u}-0000-1000-8000-00805f9b34fb"
    if len(u) == 36:
        return u
    return u


def parse_value(data: bytes):
    if not data:
        return None, ""

    try:
        # Mitutoyo-Format (8 Byte): Wert in Big-Endian an Position 5..6, Vorzeichen in Position 7
        # ZUERST versuchen, weil zufällige bytes UTF-8 ähnlich sein können!
        if len(data) >= 8:
            raw_bytes = data[5:7]
            raw = int.from_bytes(raw_bytes, "big", signed=False)
            sign = -1 if data[7] == 0x01 else 1
            return f"{sign * raw / 100.0:.2f}", data.hex()

        # Fallback Alt: Position 0..1
        if len(data) >= 2:
            raw = int.from_bytes(data[0:2], "little", signed=False)
            return f"{raw / 100.0:.2f}", data.hex()
    except Exception:
        pass

    # Fallback: UTF-8 Dekodierung (nur wenn Mitutoyo nicht passte)
    try:
        text = data.decode("utf-8", errors="ignore").strip()
        if any(ch.isdigit() for ch in text):
            return text, data.hex()
    except Exception:
        pass

    return f"0x{data[:4].hex()}", data.hex()


def choose_characteristic(services):
    all_uuids = []
    for svc in services:
        for ch in svc.characteristics:
            all_uuids.append(ch.uuid.lower())

    # Versuche Kandidaten nacheinander
    for candidate in CHARACTERISTIC_CANDIDATES:
        candidate_norm = normalize_uuid(candidate)
        if candidate_norm in all_uuids:
            return candidate_norm

    # Fallback: erstes lesbares/notifybares
    for svc in services:
        for ch in svc.characteristics:
            props = (ch.properties or [])
            if "read" in props or "notify" in props or "indicate" in props:
                return ch.uuid

    return None


def _ble_device_matches_target(d, ad):
    """True wenn Gerät dem Messschieber entspricht (Name / UUID im Advertisement)."""
    name = ""
    address = ""
    if hasattr(d, "name"):
        name = d.name or ""
    elif isinstance(d, str):
        name = d
    if hasattr(d, "address"):
        address = d.address or ""
    elif isinstance(d, str):
        address = d
    local_name = ""
    service_uuids = []
    if ad is not None:
        local_name = getattr(ad, "local_name", None) or ""
        su = getattr(ad, "service_uuids", None) or []
        service_uuids = list(su) if su else []
    if TARGET_NAME in name or TARGET_NAME in local_name:
        return True, address, name or local_name
    ulow = TARGET_UUID.lower()
    for u in service_uuids:
        uu = (u or "").lower() if isinstance(u, str) else str(u).lower()
        if ulow in uu:
            return True, address, name or local_name
    if address and ulow in address.lower():
        return True, address, name or address
    if name and ulow in name.lower():
        return True, address, name
    return False, address, ""


async def find_target_address():
    with state_lock:
        state["status_msg"] = "Scanning..."
    add_log("Suche nach BLE-Geräten...")

    with state_lock:
        known = state.get("target_address")
    if known:
        add_log(f"Verwende bekannte Adresse {known} für Reconnect")
        with state_lock:
            state["status_msg"] = "Reconnecting..."
        return known

    # --- Schneller Pfad: laufender Scan mit Callback (sobald Werbung passt -> sofort fertig)
    found_box = [None]  # [address] oder [None]

    def _on_detect(device, advertisement_data):
        if found_box[0] is not None:
            return
        ok, addr, label = _ble_device_matches_target(device, advertisement_data)
        if ok and addr:
            found_box[0] = addr
            add_log(f"Schnellsuche: {label or '?'} / {addr}")

    try:
        scanner = BleakScanner(_on_detect)
        # async with stellt sicher, dass Scan sauber beendet wird
        async with scanner:
            max_wait_s = 18.0
            step = 0.05
            elapsed = 0.0
            while elapsed < max_wait_s and found_box[0] is None:
                await asyncio.sleep(step)
                elapsed += step
    except Exception as e:
        add_log(f"Schnellsuche (Callback) nicht nutzbar: {e}")

    if found_box[0]:
        with state_lock:
            state["status_msg"] = "Device detected"
        add_log(f"Gefunden (Schnellsuche): {found_box[0]}")
        return found_box[0]

    # --- Fallback: klassisches discover (kurz), falls Callback nichts lieferte
    add_log("Schnellsuche ohne Treffer – Fallback-Scan …")
    try:
        devices = await BleakScanner.discover(timeout=6.0, return_adv=True)
    except TypeError:
        devices = await BleakScanner.discover(timeout=6.0)
    except Exception as e:
        add_log(f"Scan-Fehler: {e}")
        with state_lock:
            state["status_msg"] = "Scan failed"
        return None

    if not devices:
        add_log("Kein Gerät gefunden (Scan leer)")
        with state_lock:
            state["status_msg"] = "No devices found"
        return None

    add_log(f"Fallback: {len(devices)} Geräte")
    normalized = []
    if isinstance(devices, dict):
        for key, value in devices.items():
            if isinstance(value, tuple) and len(value) == 2:
                normalized.append((value[0], value[1]))
            else:
                add_log(f"- RAW dict entry: key={key} value={repr(value)}")
    else:
        for entry in devices:
            if isinstance(entry, tuple) and len(entry) == 2:
                normalized.append((entry[0], entry[1]))
            else:
                d = entry
                if hasattr(d, "address") or hasattr(d, "name"):
                    normalized.append((d, None))

    for d, ad in normalized:
        ok, address, label = _ble_device_matches_target(d, ad)
        if ok and address:
            add_log(f"Gefunden (Fallback): {label} / {address}")
            with state_lock:
                state["status_msg"] = f"Device detected: {label}"
            return address

    add_log("Zielgerät nicht gefunden (Scan abgeschlossen)")
    with state_lock:
        state["status_msg"] = "Device not found"
    return None


async def connect_to_address(addr: str):
    """
    macOS/CoreBluetooth: Nach Scan-Stopp ist der Connect oft erst nach kurzer Pause + ggf. discover möglich.
    """
    await asyncio.sleep(0.35)
    last_err = None
    for attempt in range(2):
        c = BleakClient(addr)
        try:
            await c.connect(timeout=20.0)
            return c
        except BleakDeviceNotFoundError as e:
            last_err = e
            if attempt == 0:
                add_log("Bluetooth-Cache leer – Auffrisch-Scan 2s …")
                try:
                    await BleakScanner.discover(timeout=2.0)
                except Exception:
                    pass
                await asyncio.sleep(0.2)
                continue
    with state_lock:
        state["target_address"] = None
    if last_err:
        raise last_err
    raise BleakDeviceNotFoundError("connect failed")


def notification_callback(sender, data):
    try:
        value, raw = parse_value(data)
        if value:
            with state_lock:
                sep = state["decimal_sep"]
                state["value"] = f"{format_measurement_value(value, sep)} mm"
                state["raw"] = raw
                state["last_notification_time"] = time.time()
                send_to_clipboard = state["send_to_clipboard"]
                autopaste = state["autopaste"]
            add_log(f"✓ Messwert empfangen: {value} mm (raw={raw})")

            if send_to_clipboard:
                root.after(0, lambda: handle_clipboard(value, autopaste))
    except Exception as e:
        add_log(f"Fehler in notification_callback: {e}")


def handle_clipboard(value, autopaste):
    try:
        with state_lock:
            sep = state["decimal_sep"]
        value_out = format_measurement_value(value, sep)
        root.clipboard_clear()
        root.clipboard_append(value_out)
        add_log(f"✓ Wert in Zwischenablage kopiert: {value_out}")
    except Exception as e:
        add_log(f"Fehler beim Kopieren in Zwischenablage: {type(e).__name__}: {e}")
        return

    if autopaste:
        # Kleine Verzögerung damit Fenster bereit ist
        root.after(100, lambda: execute_autopaste())


def execute_autopaste():
    try:
        if PYAUTOGUI_AVAILABLE:
            pyautogui.hotkey('command', 'v')
            add_log("✓ Autopaste ausgeführt (pyautogui)")
        else:
            result = os.system('osascript -e "tell application \\"System Events\\" to keystroke \\"v\\" using command down" 2>/dev/null')
            if result == 0:
                add_log("✓ Autopaste ausgeführt")

        # Simulate Enter key if enabled
        if state.get("enter_after_paste"):
            root.after(50, lambda: simulate_enter_key())
    except Exception as e:
        add_log(f"Fehler beim Autopaste: {type(e).__name__}: {e}")


def simulate_enter_key():
    try:
        if PYAUTOGUI_AVAILABLE:
            pyautogui.press('return')
            add_log("✓ Enter-Taste simuliert")
        else:
            # key code 36 ist der macOS Keycode für Return-Taste
            result = os.system('osascript -e "tell application \\"System Events\\" to key code 36" 2>/dev/null')
            if result == 0:
                add_log("✓ Enter-Taste simuliert")
    except Exception as e:
        add_log(f"Fehler beim Enter-Taste simulieren: {type(e).__name__}: {e}")


def disconnected_callback(client):
    add_log("⚠ Verbindung zum Messschieber verloren")
    with state_lock:
        state["connected"] = False
        state["reading"] = False
        state["client"] = None
        state["status_msg"] = "Connection lost"

    if state.get("auto_reconnect"):
        add_log("Starte automatisches Reconnect in 2s...")
        time.sleep(2)
        if state.get("auto_reconnect"):
            # Vermeide Rekursion, Thread neu starten
            t = threading.Thread(target=connect_worker, daemon=True)
            t.start()


async def read_loop(client: BleakClient):
    while True:
        with state_lock:
            if not state["reading"] or state["disconnecting"]:
                break
            characteristic = state.get("characteristic")

        if not characteristic:
            add_log("Keine Charakteristik konfiguriert, warte...")
            await asyncio.sleep(1)
            continue

        try:
            raw = await client.read_gatt_char(characteristic)
            value, hex_str = parse_value(raw)
            if value:
                with state_lock:
                    sep = state["decimal_sep"]
                    state["value"] = f"{format_measurement_value(value, sep)} mm"
                    state["raw"] = hex_str
                add_log(f"✓ Messwert: {value} mm")
        except Exception as e:
            add_log(f"Lese-Fehler: {e}")

        await asyncio.sleep(0.6)


def connect_worker():
    async def _connect():
        with state_lock:
            state["connecting"] = True
            state["auto_reconnect"] = True

        while True:
            with state_lock:
                if not state["auto_reconnect"]:
                    break

            addr = await find_target_address()
            if not addr:
                add_log("Messschieber nicht gefunden. Erneut scannen...")
                with state_lock:
                    state["status_msg"] = "Scanning..."
                await asyncio.sleep(0.5)
                continue

            add_log(f"Verbinde zu {addr}...")
            with state_lock:
                state["status_msg"] = "Connecting..."
            client = None
            try:
                try:
                    client = await connect_to_address(addr)
                except BleakDeviceNotFoundError:
                    add_log("Gerät nicht gefunden – nächster Versuch mit neuer Suche")
                    await asyncio.sleep(0.35)
                    continue

                if not client.is_connected:
                    add_log("Verbindung fehlgeschlagen, nochmal versuchen...")
                    with state_lock:
                        state["status_msg"] = "Connection failed"
                    await asyncio.sleep(0.5)
                    continue

                with state_lock:
                    state["client"] = client
                    state["connected"] = True
                    state["connecting"] = False
                    state["reading"] = True
                    state["target_address"] = addr
                    state["connected_once"] = True

                services = client.services
                if services is None:
                    services = await client.get_services()
                else:
                    first_service = next(iter(services), None)
                    if first_service is None:
                        services = await client.get_services()

                char = choose_characteristic(services)
                if char is None:
                    add_log("Keine passende Charakteristik gefunden, nochmal versuchen...")
                    with state_lock:
                        state["status_msg"] = "No characteristic found"
                    await client.disconnect()
                    with state_lock:
                        state["connected"] = False
                        state["reading"] = False
                        state["client"] = None
                    await asyncio.sleep(0.5)
                    continue

                with state_lock:
                    state["characteristic"] = char

                add_log("✓ Verbunden")
                with state_lock:
                    state["status_msg"] = "Connected"
                add_log(f"Verwende Charakteristik {char}")
                add_log("Starte Notify (wenn verfügbar)")

                notify_ok = False
                try:
                    await client.start_notify(char, notification_callback)
                    add_log("Notify aktiv")
                    notify_ok = True
                except Exception as e:
                    add_log(f"Notify nicht verfügbar: {e} (Polling-Fallback)")

                last_check = time.time()
                while True:
                    with state_lock:
                        if not state["auto_reconnect"] or not state["connected"]:
                            break
                        last_notif = state.get("last_notification_time", 0)
                        characteristic_uuid = state.get("characteristic")

                    # Health-Check: lange keine Notify (ruhender Messschieber) ist normal – erst nach längerer Pause prüfen
                    now = time.time()
                    if notify_ok and (now - last_notif) > _NOTIFY_SILENCE_BEFORE_HEALTH_S and characteristic_uuid:
                        if (now - last_check) > _HEALTH_READ_INTERVAL_S:
                            try:
                                if not getattr(client, "is_connected", True):
                                    raise ConnectionError("not connected")
                                await client.read_gatt_char(characteristic_uuid)
                                last_check = now
                            except AttributeError:
                                # macOS/bleak: read_gatt_char manchmal nicht nutzbar – kein Abbruch
                                last_check = now
                            except Exception as e:
                                add_log(f"⚠ Verbindung verloren (Health-Check): {type(e).__name__}: {e}")
                                with state_lock:
                                    state["connected"] = False
                                    state["status_msg"] = "Connection lost (Health-Check)"
                                break

                    if notify_ok:
                        await asyncio.sleep(0.5)
                    else:
                        await read_loop(client)
                        break

            except BleakDeviceNotFoundError as e:
                try:
                    if client is not None and getattr(client, "is_connected", False):
                        await client.disconnect()
                except Exception:
                    pass
                with state_lock:
                    state["client"] = None
                    state["connected"] = False
                    state["target_address"] = None
                    state["status_msg"] = "Device not found"
                add_log(f"Gerät nicht gefunden: {e}")
            except Exception as e:
                # Stelle sicher, dass Client cleaned up ist
                try:
                    if client is not None and getattr(client, "is_connected", False):
                        await client.disconnect()
                except Exception:
                    pass
                with state_lock:
                    state["client"] = None
                    state["connected"] = False
                    state["status_msg"] = "Connection error"
                add_log(f"Verbindungsfehler: {type(e).__name__}: {str(e)[:100]}")

            finally:
                try:
                    if client is not None and getattr(client, "is_connected", False):
                        if state.get("characteristic"):
                            await client.stop_notify(state["characteristic"])
                        await client.disconnect()
                except Exception as e:
                    add_log(f"Fehler beim Disconnect: {e}")

                with state_lock:
                    state["connected"] = False
                    state["client"] = None
                    state["reading"] = False
                    state["characteristic"] = None
                    state["last_notification_time"] = 0
                    state["status_msg"] = "Disconnected"

            if not state.get("auto_reconnect"):
                break

            add_log("Erneut verbinden...")
            with state_lock:
                state["status_msg"] = "Rescanning..."
            await asyncio.sleep(0.1)

        with state_lock:
            state["connecting"] = False

    threading.Thread(target=lambda: asyncio.run(_connect()), daemon=True).start()


def disconnect_worker():
    async def _disconnect():
        with state_lock:
            state["disconnecting"] = True
            state["reading"] = False
            state["auto_reconnect"] = False

        client = None
        with state_lock:
            client = state.get("client")

        if client is not None and client.is_connected:
            try:
                try:
                    if state.get("characteristic"):
                        await client.stop_notify(state["characteristic"])
                except Exception:
                    pass
                await client.disconnect()
            except Exception as e:
                add_log(f"Beenden-Fehler: {e}")

        with state_lock:
            state["connected"] = False
            state["client"] = None
            state["reading"] = False
            state["characteristic"] = None
            state["connecting"] = False
            state["disconnecting"] = False

        add_log("✓ Getrennt")

    threading.Thread(target=lambda: asyncio.run(_disconnect()), daemon=True).start()


def start_connect():
    # add_log darf nicht innerhalb state_lock laufen (add_log braucht selbst den Lock -> Deadlock)
    with state_lock:
        busy = state["connected"] or state["connecting"]
    if busy:
        add_log("Bereits verbunden bzw. Verbindung im Gange")
        return

    th = threading.Thread(target=connect_worker, daemon=True)
    th.start()


def start_disconnect():
    with state_lock:
        connected = state["connected"]
        connecting = state["connecting"]
    if not connected and not connecting:
        add_log("Nicht verbunden")
        return
    # Auch bei laufendem Verbindungsaufbau: abbrechen (auto_reconnect aus etc.)
    disconnect_worker()


def update_clipboard(value):
    with state_lock:
        state["send_to_clipboard"] = value


def update_autopaste(value):
    with state_lock:
        state["autopaste"] = value


def update_enter_after_paste(value):
    with state_lock:
        state["enter_after_paste"] = value


def update_decimal_sep(sep: str):
    with state_lock:
        state["decimal_sep"] = sep


def build_gui():
    global root
    root = tk.Tk()
    root.title("Messschieber B-00014265 - v01.09R004")
    root.geometry("780x620")

    style = ttk.Style()
    style.configure("TButton", font=(None, 12), padding=8)
    style.configure("TLabel", font=(None, 12))

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    title = ttk.Label(frame, text="Messschieber B-00014265", font=(None, 20, "bold"))
    title.pack(pady=8)

    version_label = ttk.Label(frame, text="Version 01.10R000 from 05.04.2026", font=(None, 10))
    version_label.pack(pady=2)

    status_frame = ttk.Frame(frame)
    status_frame.pack(fill="x", pady=6)

    status_label = ttk.Label(status_frame, text="Status: Getrennt", font=(None, 44))
    status_label.pack(side="top", padx=4)

    value_frame = ttk.Frame(frame)
    value_frame.pack(fill="x", pady=8)

    value_text = ttk.Label(value_frame, text="-- mm", font=("Courier", 42, "bold"), foreground="white")
    value_text.pack(pady=2)

    raw_text = ttk.Label(value_frame, text="", font=("Courier", 12), foreground="#424242")
    raw_text.pack(pady=1)

    # Dezimaltrennzeichen: Punkt oder Komma

    options_frame = ttk.Frame(frame)
    options_frame.pack(fill="x", pady=8)
    
    decimal_sep_var = tk.StringVar(value=".")
    with state_lock:
        decimal_sep_var.set(state["decimal_sep"])

    def on_decimal_sep_change():
        update_decimal_sep(decimal_sep_var.get())

    rb_dot = ttk.Radiobutton(
        options_frame,
        text="Punkt (.)",
        variable=decimal_sep_var,
        value=".",
        command=on_decimal_sep_change,
    )
    rb_dot.pack(side="left", padx=25, pady=6)

    rb_comma = ttk.Radiobutton(
        options_frame,
        text="Komma (,)",
        variable=decimal_sep_var,
        value=",",
        command=on_decimal_sep_change,
    )
    rb_comma.pack(side="left", padx=25, pady=6)



    clipboard_var = tk.BooleanVar(value=True)
    clipboard_cb = ttk.Checkbutton(options_frame, text="Send to Clipboard", variable=clipboard_var, command=lambda: update_clipboard(clipboard_var.get()))
    clipboard_cb.pack(side="left", padx=25)

    autopaste_var = tk.BooleanVar(value=True)
    autopaste_cb = ttk.Checkbutton(options_frame, text="Autopaste", variable=autopaste_var, command=lambda: update_autopaste(autopaste_var.get()))
    autopaste_cb.pack(side="left", padx=25)

    enter_after_paste_var = tk.BooleanVar(value=True)
    enter_after_paste_cb = ttk.Checkbutton(options_frame, text="Enter after Paste", variable=enter_after_paste_var, command=lambda: update_enter_after_paste(enter_after_paste_var.get()))
    enter_after_paste_cb.pack(side="left", padx=25)

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(fill="x", pady=10)

    btn_connect = ttk.Button(btn_frame, text="Verbinden", command=start_connect)
    btn_connect.pack(side="left", expand=True, padx=8)

    btn_disconnect = ttk.Button(btn_frame, text="Trennen", command=start_disconnect)
    btn_disconnect.pack(side="left", expand=True, padx=8)

    log_frame = ttk.LabelFrame(frame, text="Log")
    log_frame.pack(fill="both", expand=True, pady=6)

    log_area = scrolledtext.ScrolledText(log_frame, state="disabled", wrap="word", font=("Courier", 10))
    log_area.pack(fill="both", expand=True, padx=4, pady=4)

    def refresh_ui():
        try:
            with state_lock:
                connected = state["connected"]
                connecting = state["connecting"]
                value = state["value"]
                raw = state["raw"]
                logs = list(state["logs"])
                send_to_clipboard = state["send_to_clipboard"]
                autopaste = state["autopaste"]
                enter_after_paste = state["enter_after_paste"]
                status_msg = state["status_msg"]
                dec_sep = state["decimal_sep"]

            clipboard_var.set(send_to_clipboard)
            autopaste_var.set(autopaste)
            enter_after_paste_var.set(enter_after_paste)
            if decimal_sep_var.get() != dec_sep:
                decimal_sep_var.set(dec_sep)

            # Update status mit detaillierten Infos
            status_label.config(text=f"Status: {status_msg}")

            # Farbe basierend auf connected Status
            if connected:
                status_label.config(foreground="#32cd32")  # lightgreen - verbunden
            elif connecting or "Connecting" in status_msg or "Scanning" in status_msg or "Rescanning" in status_msg:
                status_label.config(foreground="#ff8f00")  # Orange - verbindungsaufbau
            else:
                status_label.config(foreground="#b71c1c")  # Rot - getrennt

            value_text.config(text=value)
            raw_text.config(text=f"Raw: {raw}" if raw else "")

            log_area.config(state="normal")
            log_area.delete("1.0", tk.END)
            for line in logs[-100:]:
                log_area.insert(tk.END, line + "\n")
            log_area.see(tk.END)
            log_area.config(state="disabled")

            root.after(400, refresh_ui)
        except Exception as e:
            print(f"UI-Refresh Fehler: {e}")
            root.after(400, refresh_ui)

    root.after(400, refresh_ui)

    def on_close():
        # if messagebox.askokcancel("Beenden", "Wirklich beenden? Verbindung wird getrennt."):
        with state_lock:
            state["reading"] = False
            state["disconnecting"] = True
        if state.get("client") is not None:
            try:
                asyncio.run(state["client"].disconnect())
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    return root


if __name__ == '__main__':
    add_log("Starte Messschieber Tkinter GUI")
    app = build_gui()
    start_connect() 
    app.mainloop()

