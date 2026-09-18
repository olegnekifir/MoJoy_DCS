import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys
import threading
import time
import atexit
import traceback

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r")

try:
    import webview
    import vgamepad as vg
    import keyboard
except Exception:
    ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), "MoJoy DCS — ошибка запуска", 0x10)
    sys.exit(1)


# Windows API
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIDEV_INPUTSINK = 0x00000100
HWND_MESSAGE = -3

PTR_MASK = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint32),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_ushort),
        ("usUsage", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint32),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_uint32),
        ("dwSize", ctypes.c_uint32),
        ("hDevice", ctypes.c_void_p),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", ctypes.c_ushort),
        ("usButtonData", ctypes.c_ushort),
    ]


class _RAWMOUSE_UNION(ctypes.Union):
    _fields_ = [
        ("ulButtons", ctypes.c_uint32),
        ("buttons", _RAWMOUSE_BUTTONS),
    ]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", ctypes.c_ushort),
        ("u", _RAWMOUSE_UNION),
        ("ulRawButtons", ctypes.c_uint32),
        ("lLastX", ctypes.c_long),
        ("lLastY", ctypes.c_long),
        ("ulExtraInformation", ctypes.c_uint32),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("mouse", RAWMOUSE),
    ]


ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SCAN_LEFT_ALT = 0x38
SCAN_C = 0x2E


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class SENDINPUT_STRUCT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("u", INPUT_UNION),
    ]


WNDPROCTYPE = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, ctypes.c_uint32, wintypes.WPARAM, wintypes.LPARAM)

user32.RegisterClassW.restype = ctypes.c_ushort
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
]

user32.DefWindowProcW.restype = wintypes.LPARAM
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint32, wintypes.WPARAM, wintypes.LPARAM]

user32.RegisterRawInputDevices.restype = ctypes.c_int
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), ctypes.c_uint32, ctypes.c_uint32]

user32.GetRawInputData.restype = ctypes.c_uint32
user32.GetRawInputData.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]

user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint32, ctypes.c_uint32]

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

user32.SetCursorPos.restype = ctypes.c_int
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

user32.SendInput.restype = ctypes.c_uint32
user32.SendInput.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]

kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]


# Конфиги и глобальные переменные
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(os.environ["APPDATA"], "MoJoyDCS_config.json")

LOCK = threading.Lock()

STATE = {
    "enabled": False,
    "sensitivity": 0.0,
    "bind_toggle": "kbd:caps lock",
    "bind_reset": "ms:3",
}

STICK_X = 0.0
STICK_Y = 0.0

WINDOW = None
TOGGLE_HOTKEY_HANDLE = None
RESET_HOTKEY_HANDLE = None
CAPTURING_BIND = None
CAPTURED_KEY = None

SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)
CENTER_X = SCREEN_W // 2
CENTER_Y = SCREEN_H // 2

gamepad = None


# Вспомогательные функции
def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def set_cursor_pos(x, y):
    user32.SetCursorPos(int(x), int(y))


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            with LOCK:
                STATE["sensitivity"] = float(data.get("sensitivity", STATE["sensitivity"]))
                if "toggle_key" in data and "bind_toggle" not in data:
                    STATE["bind_toggle"] = "kbd:" + str(data["toggle_key"])
                else:
                    STATE["bind_toggle"] = str(data.get("bind_toggle", STATE["bind_toggle"]))
                STATE["bind_reset"] = str(data.get("bind_reset", STATE["bind_reset"]))
        except Exception:
            pass


def save_config():
    with LOCK:
        data = {
            "sensitivity": STATE["sensitivity"],
            "bind_toggle": STATE["bind_toggle"],
            "bind_reset": STATE["bind_reset"],
        }
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def notify_js(code):
    if WINDOW is not None:
        try:
            WINDOW.evaluate_js(code)
        except Exception:
            pass


# Эмуляция клавиш
def send_scan(scan_code, key_up):
    inp = SENDINPUT_STRUCT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = 0
    inp.ki.wScan = scan_code
    inp.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if key_up else 0)
    inp.ki.time = 0
    inp.ki.dwExtraInfo = 0
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(SENDINPUT_STRUCT))


def send_alt_c():
    disable_hotkeys()
    send_scan(SCAN_LEFT_ALT, False)
    time.sleep(0.02)
    send_scan(SCAN_C, False)
    time.sleep(0.02)
    send_scan(SCAN_C, True)
    time.sleep(0.02)
    send_scan(SCAN_LEFT_ALT, True)
    apply_hotkeys()


# Управление горячими клавишами
def set_enabled(value):
    with LOCK:
        STATE["enabled"] = value
    threading.Thread(target=send_alt_c, daemon=True).start()
    threading.Thread(target=notify_js, args=("setEnabledUI(" + json.dumps(value) + ")",), daemon=True).start()


def on_toggle_pressed():
    with LOCK:
        new_value = not STATE["enabled"]
    set_enabled(new_value)


def disable_hotkeys():
    global TOGGLE_HOTKEY_HANDLE, RESET_HOTKEY_HANDLE
    if TOGGLE_HOTKEY_HANDLE:
        try:
            keyboard.remove_hotkey(TOGGLE_HOTKEY_HANDLE)
        except Exception:
            pass
        TOGGLE_HOTKEY_HANDLE = None
    if RESET_HOTKEY_HANDLE:
        try:
            keyboard.remove_hotkey(RESET_HOTKEY_HANDLE)
        except Exception:
            pass
        RESET_HOTKEY_HANDLE = None


def apply_hotkeys():
    global TOGGLE_HOTKEY_HANDLE, RESET_HOTKEY_HANDLE
    disable_hotkeys()
    
    tb = STATE.get("bind_toggle", "")
    if tb.startswith("kbd:"):
        try:
            TOGGLE_HOTKEY_HANDLE = keyboard.add_hotkey(tb[4:], on_toggle_pressed, suppress=True)
        except Exception:
            pass

    rb = STATE.get("bind_reset", "")
    if rb.startswith("kbd:"):
        try:
            RESET_HOTKEY_HANDLE = keyboard.add_hotkey(rb[4:], recenter_stick, suppress=True)
        except Exception:
            pass


def capture_key_thread(bind_name):
    global CAPTURING_BIND, CAPTURED_KEY
    if CAPTURING_BIND is not None:
        with LOCK:
            current = STATE.get(bind_name)
        notify_js(f"updateBindUI({json.dumps(bind_name)}, {json.dumps(current)})")
        return

    disable_hotkeys()
    time.sleep(0.3)

    CAPTURED_KEY = None
    CAPTURING_BIND = bind_name

    def kb_hook(e):
        global CAPTURED_KEY, CAPTURING_BIND
        if CAPTURING_BIND and e.event_type == 'down' and e.name is not None:
            CAPTURED_KEY = f"kbd:{e.name}"
            CAPTURING_BIND = None

    hook_handler = None
    captured_ok = False
    try:
        hook_handler = keyboard.hook(kb_hook)

        while CAPTURING_BIND is not None:
            time.sleep(0.01)
        captured_ok = True
    finally:
        if hook_handler is not None:
            try:
                keyboard.unhook(hook_handler)
            except Exception:
                pass
        CAPTURING_BIND = None

    time.sleep(0.2)

    with LOCK:
        if captured_ok and CAPTURED_KEY is not None:
            STATE[bind_name] = CAPTURED_KEY
        result_key = STATE.get(bind_name)

    apply_hotkeys()
    save_config()
    notify_js(f"updateBindUI({json.dumps(bind_name)}, {json.dumps(result_key)})")


# Raw Input
def on_raw_mouse(dx, dy):
    global STICK_X, STICK_Y
    with LOCK:
        if not STATE["enabled"]:
            return
        sens = STATE["sensitivity"]
        if sens >= 0:
            factor = (sens + 2.0) / 1000.0
        else:
            factor = (sens / 10.0 + 2.0) / 1000.0
        STICK_X = clamp(STICK_X + dx * factor, -1.0, 1.0)
        STICK_Y = clamp(STICK_Y - dy * factor, -1.0, 1.0)


def recenter_stick():
    global STICK_X, STICK_Y
    with LOCK:
        STICK_X = 0.0
        STICK_Y = 0.0


def check_mouse_down(flags, btn):
    if btn == 1 and (flags & 0x0001): return True
    if btn == 2 and (flags & 0x0004): return True
    if btn == 3 and (flags & 0x0010): return True
    if btn == 4 and (flags & 0x0040): return True
    if btn == 5 and (flags & 0x0100): return True
    return False


def handle_raw_input(lparam):
    global CAPTURING_BIND, CAPTURED_KEY
    hraw = ctypes.c_void_p(lparam & PTR_MASK)
    size = ctypes.c_uint32(0)
    user32.GetRawInputData(hraw, RID_INPUT, None, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
    if size.value == 0:
        return
    buf = ctypes.create_string_buffer(size.value)
    if user32.GetRawInputData(hraw, RID_INPUT, buf, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER)) != size.value:
        return
    raw = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
    
    if raw.header.dwType == RIM_TYPEMOUSE:
        flags = raw.mouse.buttons.usButtonFlags
        
        if CAPTURING_BIND:
            if flags & 0x0001:
                CAPTURED_KEY = "ms:1"
                CAPTURING_BIND = None
            elif flags & 0x0004:
                CAPTURED_KEY = "ms:2"
                CAPTURING_BIND = None
            elif flags & 0x0010:
                CAPTURED_KEY = "ms:3"
                CAPTURING_BIND = None
            elif flags & 0x0040:
                CAPTURED_KEY = "ms:4"
                CAPTURING_BIND = None
            elif flags & 0x0100:
                CAPTURED_KEY = "ms:5"
                CAPTURING_BIND = None
        else:
            tb = STATE.get("bind_toggle", "")
            if tb.startswith("ms:"):
                if check_mouse_down(flags, int(tb[3:])):
                    on_toggle_pressed()
            
            rb = STATE.get("bind_reset", "")
            if rb.startswith("ms:"):
                if check_mouse_down(flags, int(rb[3:])):
                    recenter_stick()
                    
        on_raw_mouse(raw.mouse.lLastX, raw.mouse.lLastY)


def raw_input_thread():
    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "MoJoyRawInputClass"

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            handle_raw_input(lparam)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wndproc_c = WNDPROCTYPE(wnd_proc)

    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = ctypes.cast(wndproc_c, ctypes.c_void_p)
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = hinstance
    wc.hIcon = None
    wc.hCursor = None
    wc.hbrBackground = None
    wc.lpszMenuName = None
    wc.lpszClassName = class_name

    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        return

    hwnd = user32.CreateWindowExW(
        0, class_name, "MoJoyRawInput", 0,
        0, 0, 0, 0,
        HWND_MESSAGE, None, hinstance, None,
    )
    
    if not hwnd:
        return

    rid = RAWINPUTDEVICE()
    rid.usUsagePage = 0x01
    rid.usUsage = 0x02
    rid.dwFlags = RIDEV_INPUTSINK
    rid.hwndTarget = hwnd

    ok = user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))
    if not ok:
        return

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# Обновление геймпада
def input_loop():
    while True:
        with LOCK:
            sx = STICK_X
            sy = STICK_Y
            enabled = STATE["enabled"]
        if enabled:
            set_cursor_pos(CENTER_X, CENTER_Y)
        gamepad.left_joystick_float(x_value_float=sx, y_value_float=sy)
        gamepad.update()
        time.sleep(0.008)


# Очистка при выходе
@atexit.register
def cleanup():
    try:
        keyboard.unhook_all()
    except Exception:
        pass
    try:
        gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        gamepad.update()
    except Exception:
        pass


# API для UI
class Api:
    def get_state(self):
        with LOCK:
            return {
                "enabled": STATE["enabled"],
                "sensitivity": STATE["sensitivity"],
                "bind_toggle": STATE["bind_toggle"],
                "bind_reset": STATE["bind_reset"],
            }

    def set_sensitivity(self, value):
        with LOCK:
            STATE["sensitivity"] = float(value)
        save_config()
        return True

    def toggle_enabled(self):
        with LOCK:
            new_value = not STATE["enabled"]
        set_enabled(new_value)
        return new_value

    def start_key_capture(self, bind_name):
        threading.Thread(target=capture_key_thread, args=(bind_name,), daemon=True).start()
        return True

    def reset_config(self):
        with LOCK:
            STATE["sensitivity"] = 0.0
            STATE["bind_toggle"] = "kbd:caps lock"
            STATE["bind_reset"] = "ms:3"
            state = dict(STATE)
        apply_hotkeys()
        save_config()
        return state


# UI
HTML_CONTENT = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>MoJoy DCS</title>
<style>
  :root {
    --bg-color: #ffffff;
    --card-bg: #ffffff;
    --text-main: #0f172a;
    --text-muted: #64748b;
    --accent: #2563eb;
    --accent-hover: #1d4ed8;
    --control-bg: #f8fafc;
    --border-color: #e2e8f0;
    --focus-ring: rgba(37, 99, 235, 0.2);
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }

  body {
    background: var(--bg-color);
    color: var(--text-main);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, Helvetica, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    user-select: none;
    -webkit-font-smoothing: antialiased;
  }

  .app-container {
    width: 420px;
    background: var(--card-bg);
    padding: 32px;
  }

  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
  }

  .title-group h1 {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.5px;
    margin-bottom: 4px;
  }

  .title-group p {
    font-size: 14px;
    color: var(--text-muted);
    font-weight: 500;
  }

  .switch {
    position: relative;
    display: inline-block;
    width: 56px;
    height: 32px;
  }

  .switch input {
    opacity: 0;
    width: 0;
    height: 0;
  }

  .slider {
    position: absolute;
    cursor: pointer;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: #cbd5e1;
    transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
    border-radius: 32px;
  }

  .slider:before {
    position: absolute;
    content: "";
    height: 24px;
    width: 24px;
    left: 4px;
    bottom: 4px;
    background-color: white;
    transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
    border-radius: 50%;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  }

  input:checked + .slider {
    background-color: var(--accent);
  }

  input:focus + .slider {
    box-shadow: 0 0 0 4px var(--focus-ring);
  }

  input:checked + .slider:before {
    transform: translateX(24px);
  }

  .section {
    background: var(--control-bg);
    border: 1px solid var(--border-color);
    border-radius: 16px;
    padding: 16px;
    margin-bottom: 12px;
    transition: border-color 0.2s;
  }

  .section:hover {
    border-color: #cbd5e1;
  }

  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }

  .section-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .val-badge {
    background: var(--accent);
    color: white;
    font-size: 13px;
    font-weight: 700;
    padding: 2px 10px;
    border-radius: 20px;
    min-width: 44px;
    text-align: center;
  }

  input[type=range] {
    -webkit-appearance: none;
    width: 100%;
    background: transparent;
  }

  input[type=range]:focus {
    outline: none;
  }

  input[type=range]::-webkit-slider-runnable-track {
    width: 100%;
    height: 6px;
    cursor: pointer;
    background: #e2e8f0;
    border-radius: 6px;
  }

  input[type=range]::-webkit-slider-thumb {
    height: 20px;
    width: 20px;
    border-radius: 50%;
    background: var(--accent);
    cursor: pointer;
    -webkit-appearance: none;
    margin-top: -7px;
    box-shadow: 0 2px 6px rgba(37, 99, 235, 0.3);
    transition: transform 0.1s;
  }

  input[type=range]::-webkit-slider-thumb:hover {
    transform: scale(1.15);
  }

  .keybind-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .key-display {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .key-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    background: white;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    color: var(--text-muted);
  }

  .key-value {
    font-weight: 600;
    font-size: 14px;
    color: var(--text-main);
  }

  .btn-rebind {
    background: white;
    border: 1px solid var(--border-color);
    color: var(--text-main);
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  }

  .btn-rebind:hover {
    border-color: var(--accent);
    color: var(--accent);
    box-shadow: 0 2px 8px var(--focus-ring);
  }

  .btn-rebind:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .btn-reset-config {
    width: 100%;
    margin-top: 4px;
    padding: 12px;
    border: 1px solid #ef4444;
    border-radius: 12px;
    background: #ef4444;
    color: #ffffff;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }

  .btn-reset-config:hover {
    background: transparent;
    color: #ef4444;
  }
</style>
</head>
<body>

  <div class="app-container" id="mainView">
    <div class="header">
      <div class="title-group">
        <h1>MoJoy DCS</h1>
        <p>Мышь в Xbox 360 Controller</p>
      </div>
      <label class="switch">
        <input type="checkbox" id="mainToggle" onclick="onToggleClick()">
        <span class="slider"></span>
      </label>
    </div>

    <div class="section">
      <div class="section-header">
        <span class="section-title">Чувствительность</span>
        <span class="val-badge" id="sensVal">0.0</span>
      </div>
      <input type="range" id="sens" min="-10" max="10" step="0.1" value="0" oninput="onSensChange(this.value)">
    </div>

    <div class="section">
      <div class="section-header">
        <span class="section-title">Включение / Отключение</span>
      </div>
      <div class="keybind-row">
        <div class="key-display">
          <div class="key-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14h16"/><path d="M4 10h16"/><path d="M10 20h4"/><path d="M10 4h4"/></svg>
          </div>
          <span class="key-value" id="bind_toggle_name">Caps Lock</span>
        </div>
        <button class="btn-rebind" id="bind_toggle_btn" onclick="onRebindClick('bind_toggle')">Изменить</button>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <span class="section-title">Сброс осей в центр</span>
      </div>
      <div class="keybind-row">
        <div class="key-display">
          <div class="key-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
          </div>
          <span class="key-value" id="bind_reset_name">СКМ</span>
        </div>
        <button class="btn-rebind" id="bind_reset_btn" onclick="onRebindClick('bind_reset')">Изменить</button>
      </div>
    </div>

    <button class="btn-reset-config" onclick="onResetConfigClick()">Сбросить конфигурацию</button>
  </div>

<script>
function formatKey(keyStr) {
  if (!keyStr) return "-";
  if (keyStr.startsWith("kbd:")) {
    let k = keyStr.substring(4);
    return k.split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  } else if (keyStr.startsWith("ms:")) {
    let b = keyStr.substring(3);
    if (b === "1") return "ЛКМ (Mouse 1)";
    if (b === "2") return "ПКМ (Mouse 2)";
    if (b === "3") return "СКМ (Mouse 3)";
    if (b === "4") return "Mouse 4";
    if (b === "5") return "Mouse 5";
    return "Mouse " + b;
  }
  return keyStr;
}

function setEnabledUI(enabled) {
  document.getElementById("mainToggle").checked = enabled;
}

function updateBindUI(bindName, keyStr) {
  const nameEl = document.getElementById(bindName + "_name");
  const btnEl = document.getElementById(bindName + "_btn");
  if (nameEl) nameEl.textContent = formatKey(keyStr);
  if (btnEl) {
    btnEl.disabled = false;
    btnEl.textContent = "Изменить";
  }
}

async function onSensChange(value) {
  document.getElementById("sensVal").textContent = Number(value).toFixed(1);
  await window.pywebview.api.set_sensitivity(value);
}

async function onToggleClick() {
  const enabled = await window.pywebview.api.toggle_enabled();
  setEnabledUI(enabled);
}

async function onRebindClick(bindName) {
  document.getElementById(bindName + "_name").textContent = "...";
  const btn = document.getElementById(bindName + "_btn");
  btn.disabled = true;
  btn.textContent = "Ждём...";
  await window.pywebview.api.start_key_capture(bindName);
}

async function onResetConfigClick() {
  const state = await window.pywebview.api.reset_config();
  document.getElementById("sens").value = state.sensitivity;
  document.getElementById("sensVal").textContent = Number(state.sensitivity).toFixed(1);
  updateBindUI('bind_toggle', state.bind_toggle);
  updateBindUI('bind_reset', state.bind_reset);
}

async function init() {
  const state = await window.pywebview.api.get_state();

  const sensInput = document.getElementById("sens");
  sensInput.value = state.sensitivity;
  document.getElementById("sensVal").textContent = Number(state.sensitivity).toFixed(1);

  setEnabledUI(state.enabled);
  updateBindUI('bind_toggle', state.bind_toggle);
  updateBindUI('bind_reset', state.bind_reset);
}

let _mojoyInited = false;
function initOnce() {
  if (_mojoyInited) return;
  _mojoyInited = true;
  init();
}

window.addEventListener("pywebviewready", initOnce);
if (window.pywebview) {
  initOnce();
}
</script>
</body>
</html>"""

# Создание окна
def main():
    global WINDOW, gamepad
    gamepad = vg.VX360Gamepad()
    load_config()
    apply_hotkeys()
    threading.Thread(target=raw_input_thread, daemon=True).start()
    threading.Thread(target=input_loop, daemon=True).start()
    
    WINDOW = webview.create_window(
        "MoJoy DCS",
        html=HTML_CONTENT,
        js_api=Api(),
        width=440,
        height=540,
        resizable=False,
    )
    webview.start()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), "MoJoy DCS — ошибка запуска", 0x10)
        sys.exit(1)