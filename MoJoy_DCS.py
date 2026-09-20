import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys
import threading
import time
import atexit
import traceback

#Если программа запущена без консоли, подставляем пустые потоки ввода-вывода, чтобы библиотеки не падали
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r")

#Подключение библиотек интерфейса и клавиатуры; при ошибке показываем окно с текстом ошибки и выходим
#vgamepad подключается позже: при импорте он сразу обращается к драйверу и без него падает
try:
    import webview
    import keyboard
except Exception:
    ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), "MoJoy DCS — ошибка запуска", 0x10)
    sys.exit(1)


#Windows API
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

#Константы Raw Input (ввод с мыши) и скрытого окна
WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIDEV_INPUTSINK = 0x00000100
HWND_MESSAGE = -3

#Маска указателя: превращает знаковое значение lparam в корректный адрес (для 32 и 64 бит)
PTR_MASK = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1


#Класс окна: параметры скрытого окна, которое принимает сообщения Raw Input
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


#Устройство Raw Input: указываем, ввод какого устройства (мышь) хотим получать
class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_ushort),
        ("usUsage", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint32),
        ("hwndTarget", wintypes.HWND),
    ]


#Заголовок пакета Raw Input: тип устройства и размер данных
class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_uint32),
        ("dwSize", ctypes.c_uint32),
        ("hDevice", ctypes.c_void_p),
        ("wParam", wintypes.WPARAM),
    ]


#Флаги и данные кнопок мыши
class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", ctypes.c_ushort),
        ("usButtonData", ctypes.c_ushort),
    ]


#Кнопки мыши можно прочитать целиком (ulButtons) или по частям (buttons)
class _RAWMOUSE_UNION(ctypes.Union):
    _fields_ = [
        ("ulButtons", ctypes.c_uint32),
        ("buttons", _RAWMOUSE_BUTTONS),
    ]


#Данные мыши из Raw Input: смещение по X и Y (lLastX, lLastY) и состояние кнопок
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


#Пакет Raw Input целиком: заголовок и данные мыши
class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("mouse", RAWMOUSE),
    ]


#Константы и тип для эмуляции клавиш через SendInput (скан-коды: левый Alt и клавиша C)
ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SCAN_LEFT_ALT = 0x38
SCAN_C = 0x2E


#Событие клавиатуры для SendInput
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ULONG_PTR),
    ]


#Событие мыши для SendInput (не используется, но нужно, чтобы размер структуры совпал с системным)
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ULONG_PTR),
    ]


#Событие оборудования для SendInput (то же самое: нужно для правильного размера структуры)
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


#Объединение: SendInput принимает событие клавиатуры, мыши или оборудования
class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


#Структура INPUT для SendInput: тип события и его данные
class SENDINPUT_STRUCT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("u", INPUT_UNION),
    ]


#Описание функций WinAPI для ctypes: restype - что функция возвращает,
#argtypes - какие аргументы принимает
#Без этого ctypes неправильно передаёт указатели и дескрипторы на 64-битной системе

#Тип функции-обработчика сообщений окна (оконная процедура)
WNDPROCTYPE = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, ctypes.c_uint32, wintypes.WPARAM, wintypes.LPARAM)

#Регистрация класса окна
user32.RegisterClassW.restype = ctypes.c_ushort
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

#Создание окна (у нас - скрытое окно-приёмник сообщений)
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
]

#Обработка сообщений окна по умолчанию
user32.DefWindowProcW.restype = wintypes.LPARAM
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint32, wintypes.WPARAM, wintypes.LPARAM]

#Подписка на Raw Input (получаем движение и кнопки мыши, даже когда окно не в фокусе)
user32.RegisterRawInputDevices.restype = ctypes.c_int
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), ctypes.c_uint32, ctypes.c_uint32]

#Чтение данных Raw Input из сообщения WM_INPUT
user32.GetRawInputData.restype = ctypes.c_uint32
user32.GetRawInputData.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]

#Получение сообщения из очереди (основа цикла сообщений)
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint32, ctypes.c_uint32]

#Обработка сообщения и отправка его в оконную процедуру
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

#Размеры экрана
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

#Установка позиции курсора (возвращаем его в центр экрана)
user32.SetCursorPos.restype = ctypes.c_int
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

#Отправка эмулированных нажатий клавиш
user32.SendInput.restype = ctypes.c_uint32
user32.SendInput.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]

#Дескриптор текущего модуля (нужен при регистрации окна)
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

#Запуск установщика драйвера: shell32 позволяет запустить программу от имени администратора
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

#Константы для запуска установщика (получить дескриптор процесса, код отмены UAC) и для окна с ошибкой
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF
ERROR_CANCELLED = 1223
MB_ICONERROR = 0x00000010
MB_TOPMOST = 0x00040000


#Параметры запуска программы через ShellExecuteEx: что запускать, от чьего имени и как показать окно
class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


#Запуск программы через оболочку Windows (режим runas - с запросом прав администратора)
shell32.ShellExecuteExW.restype = wintypes.BOOL
shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]

#Ожидание завершения процесса (ждём, пока закроется установщик)
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]

#Закрытие дескриптора процесса после ожидания
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


#Конфиги и глобальные переменные

#Папки: BASE_DIR - где лежит код, APP_DIR - где лежит exe (после сборки они могут отличаться)
#Именно от них ищется установщик драйвера bin/driver.exe
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR

#Файл настроек (лежит в папке AppData)
CONFIG_PATH = os.path.join(os.environ["APPDATA"], "MoJoyDCS_config.json")

#Блокировка для безопасного доступа к настройкам и положению стика из разных потоков
LOCK = threading.Lock()

#Состояние программы: включена ли эмуляция, чувствительность и назначенные клавиши
#Формат клавиш: kbd:название - клавиатура, ms:номер - кнопка мыши
STATE = {
    "enabled": False,
    "sensitivity": 0.0,
    "bind_toggle": "kbd:caps lock",
    "bind_reset": "ms:3",
}

#Текущее положение левого стика геймпада (от -1.0 до 1.0)
STICK_X = 0.0
STICK_Y = 0.0

#Окно интерфейса, ссылки на горячие клавиши и данные для назначения новой клавиши
WINDOW = None
TOGGLE_HOTKEY_HANDLE = None
RESET_HOTKEY_HANDLE = None
CAPTURING_BIND = None
CAPTURED_KEY = None

#Размер экрана и его центр (курсор возвращается в центр на каждом шаге цикла)
SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)
CENTER_X = SCREEN_W // 2
CENTER_Y = SCREEN_H // 2

#Виртуальный геймпад и модуль vgamepad: создаются только после того, как драйвер найден
gamepad = None
vg = None

#Сколько секунд показывать крутилку поиска драйвера (сама проверка проходит мгновенно)
SEARCH_DELAY_SECONDS = 1.5
#Ответ пользователя на вопрос об установке драйвера и флаг "запуск уже начат"
ANSWER_EVENT = threading.Event()
ANSWER_VALUE = False
BOOT_STARTED = False


#Вспомогательные функции

#Ограничивает значение диапазоном от min_value до max_value
def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


#Перемещает курсор в указанную точку экрана
def set_cursor_pos(x, y):
    user32.SetCursorPos(int(x), int(y))


#Загружает настройки из файла (понимает и старый формат с toggle_key)
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


#Сохраняет настройки в файл
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


#Выполняет JS-код в окне интерфейса (ошибки игнорируются)
def notify_js(code):
    if WINDOW is not None:
        try:
            WINDOW.evaluate_js(code)
        except Exception:
            pass


#Показывает окно с ошибкой поверх остальных окон
def show_error(text):
    user32.MessageBoxW(None, text, "MoJoy DCS — ошибка запуска", MB_ICONERROR | MB_TOPMOST)


#Эмуляция клавиш

#Нажимает или отпускает клавишу по скан-коду
def send_scan(scan_code, key_up):
    inp = SENDINPUT_STRUCT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = 0
    inp.ki.wScan = scan_code
    inp.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if key_up else 0)
    inp.ki.time = 0
    inp.ki.dwExtraInfo = 0
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(SENDINPUT_STRUCT))


#Эмулирует нажатие Alt+C (вызывается при каждом включении и отключении)
#На время отправки горячие клавиши снимаются, потом включаются снова
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


#Управление горячими клавишами

#Включает или отключает эмуляцию, отправляет Alt+C и обновляет переключатель в интерфейсе
def set_enabled(value):
    with LOCK:
        STATE["enabled"] = value
    threading.Thread(target=send_alt_c, daemon=True).start()
    threading.Thread(target=notify_js, args=("setEnabledUI(" + json.dumps(value) + ")",), daemon=True).start()


#Срабатывает по кнопке включения: переключает состояние на противоположное
def on_toggle_pressed():
    with LOCK:
        new_value = not STATE["enabled"]
    set_enabled(new_value)


#Снимает все зарегистрированные горячие клавиши
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


#Регистрирует горячие клавиши из настроек (старые сначала снимаются)
#Здесь только клавиатурные бинды, кнопки мыши ловятся через Raw Input
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


#Ждёт, пока пользователь нажмёт новую клавишу или кнопку мыши, назначает её и сохраняет настройки
#Работает в отдельном потоке
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

    #Хук клавиатуры: запоминает первую нажатую клавишу
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


#Raw Input

#Двигает стик на величину смещения мыши (ось Y инвертирована)
#Коэффициент зависит от чувствительности и считается по-разному для положительных и отрицательных значений
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


#Возвращает стик в центр
def recenter_stick():
    global STICK_X, STICK_Y
    with LOCK:
        STICK_X = 0.0
        STICK_Y = 0.0


#Проверяет по флагам Raw Input, нажата ли кнопка мыши (1 - ЛКМ, 2 - ПКМ, 3 - СКМ, 4 и 5 - боковые)
def check_mouse_down(flags, btn):
    if btn == 1 and (flags & 0x0001): return True
    if btn == 2 and (flags & 0x0004): return True
    if btn == 3 and (flags & 0x0010): return True
    if btn == 4 and (flags & 0x0040): return True
    if btn == 5 and (flags & 0x0100): return True
    return False


#Разбирает сообщение WM_INPUT от мыши: при назначении клавиши запоминает нажатую кнопку,
#иначе проверяет бинды включения и сброса, затем передаёт смещение мыши в on_raw_mouse
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


#Поток со скрытым окном и собственным циклом сообщений
#Получает Raw Input от мыши, даже когда окно программы не в фокусе
def raw_input_thread():
    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "MoJoyRawInputClass"

    #Оконная процедура: на сообщение WM_INPUT передаёт данные в handle_raw_input
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


#Обновление геймпада

#Главный цикл (каждые 8 мс): пока эмуляция включена, держит курсор в центре
#и отправляет положение стика в виртуальный геймпад
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


#Очистка при выходе

#При выходе снимает хуки клавиатуры и обнуляет стик (если программа успела запуститься)
@atexit.register
def cleanup():
    if gamepad is None:
        return
    try:
        keyboard.unhook_all()
    except Exception:
        pass
    try:
        gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        gamepad.update()
    except Exception:
        pass


#Проверка и установка драйвера ViGEmBus

#Ищет bin/driver.exe рядом с программой или рядом со скриптом; возвращает путь или None
def find_installer():
    for base in (APP_DIR, BASE_DIR):
        path = os.path.join(base, "bin", "driver.exe")
        if os.path.isfile(path):
            return path
    return None


#Запускает bin/driver.exe от имени администратора (появится запрос UAC) и ждёт, пока установщик закроется
#Если файла нет или запуск не удался, показывает ошибку (отмена в UAC ошибкой не считается)
def run_installer():
    path = find_installer()
    if path is None:
        show_error(
            "Не найден установщик драйвера:\n"
            + os.path.join(APP_DIR, "bin", "driver.exe")
            + "\n\nПоложите driver.exe в папку bin рядом с программой."
        )
        return
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = path
    info.lpDirectory = os.path.dirname(path)
    info.nShow = SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.get_last_error() != ERROR_CANCELLED:
            show_error("Не удалось запустить установщик драйвера:\n" + path)
        return
    if info.hProcess:
        kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        kernel32.CloseHandle(info.hProcess)


#Ищет драйвер: ждёт SEARCH_DELAY_SECONDS (чтобы была видна крутилка) и пробует подключить vgamepad
#vgamepad при импорте подключается к драйверу: ошибка VIGEM_ERROR - драйвера нет (False),
#успех - драйвер есть (True); любая другая ошибка не про драйвер, её показываем как есть
def search_driver():
    global vg
    time.sleep(SEARCH_DELAY_SECONDS)
    try:
        import vgamepad
    except Exception as e:
        if "VIGEM_ERROR" in str(e):
            return False
        raise
    vg = vgamepad
    return True


#Показывает в интерфейсе крутилку с указанной надписью
def show_search(text):
    notify_js("showSearch(" + json.dumps(text) + ")")


#Показывает вопрос об установке драйвера и ждёт ответа: True - "Да", False - "Нет"
def ask_install():
    ANSWER_EVENT.clear()
    notify_js("showPrompt()")
    ANSWER_EVENT.wait()
    return ANSWER_VALUE


#Запускает саму программу: создаёт геймпад, загружает настройки, включает горячие клавиши и потоки ввода
def start_app():
    global gamepad
    gamepad = vg.VX360Gamepad()
    load_config()
    apply_hotkeys()
    threading.Thread(target=raw_input_thread, daemon=True).start()
    threading.Thread(target=input_loop, daemon=True).start()


#Сценарий запуска: поиск драйвера -> если его нет, вопрос об установке -> установка -> повторный поиск
#Когда драйвер найден, запускается сама программа
#Ответ "Нет" закрывает программу; любая ошибка показывается в окне и тоже закрывает программу
def boot_sequence():
    try:
        while True:
            show_search("Поиск драйвера ViGEmBus...")
            if search_driver():
                break
            if not ask_install():
                WINDOW.destroy()
                return
            show_search("Установка драйвера...")
            run_installer()
        start_app()
        notify_js("showMain()")
    except Exception:
        show_error(traceback.format_exc())
        WINDOW.destroy()


#API для UI

#Методы этого класса интерфейс вызывает из JavaScript через window.pywebview.api
class Api:
    #Текущее состояние программы для заполнения интерфейса
    def get_state(self):
        with LOCK:
            return {
                "enabled": STATE["enabled"],
                "sensitivity": STATE["sensitivity"],
                "bind_toggle": STATE["bind_toggle"],
                "bind_reset": STATE["bind_reset"],
            }

    #Сохраняет новое значение чувствительности
    def set_sensitivity(self, value):
        with LOCK:
            STATE["sensitivity"] = float(value)
        save_config()
        return True

    #Переключает эмуляцию (вкл/выкл) и возвращает новое состояние
    def toggle_enabled(self):
        with LOCK:
            new_value = not STATE["enabled"]
        set_enabled(new_value)
        return new_value

    #Запускает назначение новой клавиши в отдельном потоке
    def start_key_capture(self, bind_name):
        threading.Thread(target=capture_key_thread, args=(bind_name,), daemon=True).start()
        return True

    #Сбрасывает настройки к значениям по умолчанию и возвращает их интерфейсу
    def reset_config(self):
        with LOCK:
            STATE["sensitivity"] = 0.0
            STATE["bind_toggle"] = "kbd:caps lock"
            STATE["bind_reset"] = "ms:3"
            state = dict(STATE)
        apply_hotkeys()
        save_config()
        return state

    #Вызывается интерфейсом, когда страница готова: запускает сценарий запуска (один раз)
    def boot(self):
        global BOOT_STARTED
        with LOCK:
            if BOOT_STARTED:
                return False
            BOOT_STARTED = True
        threading.Thread(target=boot_sequence, daemon=True).start()
        return True

    #Принимает ответ Да/Нет на вопрос об установке драйвера и будит сценарий запуска
    def driver_answer(self, value):
        global ANSWER_VALUE
        ANSWER_VALUE = bool(value)
        ANSWER_EVENT.set()
        return True


#Интерфейс
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

  /*Переключение экранов: скрытый экран не показывается*/
  .hidden {
    display: none !important;
  }

  /*Экран поиска драйвера: общая раскладка экранов, крутилка и надпись под ней*/
  .center-view {
    width: 420px;
    padding: 32px;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
  }

  .spinner {
    width: 56px;
    height: 56px;
    border: 5px solid var(--border-color);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /*Если в системе отключены анимации, крутилка вращается медленнее, но не останавливается*/
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation-duration: 2.4s;
    }
  }

  .status-text {
    margin-top: 24px;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-muted);
  }

  /*Вопрос об установке драйвера: текст и кнопки Да/Нет*/
  .prompt-text {
    font-size: 16px;
    font-weight: 600;
    line-height: 1.5;
    color: var(--text-main);
  }

  .prompt-buttons {
    display: flex;
    gap: 12px;
    width: 100%;
    margin-top: 28px;
  }

  .btn-choice {
    flex: 1;
    padding: 12px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }

  .btn-choice:focus-visible {
    outline: none;
    box-shadow: 0 0 0 4px var(--focus-ring);
  }

  .btn-yes {
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #ffffff;
  }

  .btn-yes:hover {
    background: var(--accent-hover);
    border-color: var(--accent-hover);
  }

  /*Кнопка Нет: красная, стиль как у кнопки Сбросить конфигурацию*/
  .btn-no {
    border: 1px solid #ef4444;
    background: #ef4444;
    color: #ffffff;
  }

  .btn-no:hover {
    background: transparent;
    color: #ef4444;
  }

  .btn-no:focus-visible {
    box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.2);
  }
</style>
</head>
<body>

  <!--Экран поиска драйвера: крутилка и надпись под ней-->
  <div class="center-view" id="searchView">
    <div class="spinner"></div>
    <p class="status-text" id="searchText">Поиск драйвера ViGEmBus...</p>
  </div>

  <!--Вопрос об установке драйвера: кнопки Да и Нет-->
  <div class="center-view hidden" id="promptView">
    <p class="prompt-text">Необходимого драйвера для работы программы не найдено.<br><br>Установить сейчас?</p>
    <div class="prompt-buttons">
      <button class="btn-choice btn-yes" onclick="onDriverAnswer(true)">Да</button>
      <button class="btn-choice btn-no" onclick="onDriverAnswer(false)">Нет</button>
    </div>
  </div>

  <!--Основной экран программы-->
  <div class="app-container hidden" id="mainView">
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
//Превращает код клавиши в читаемое название (kbd:caps lock -> Caps Lock, ms:3 -> СКМ (Mouse 3))
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

//Ставит переключатель в положение включено или выключено
function setEnabledUI(enabled) {
  document.getElementById("mainToggle").checked = enabled;
}

//Показывает назначенную клавишу и возвращает кнопку Изменить в обычное состояние
function updateBindUI(bindName, keyStr) {
  const nameEl = document.getElementById(bindName + "_name");
  const btnEl = document.getElementById(bindName + "_btn");
  if (nameEl) nameEl.textContent = formatKey(keyStr);
  if (btnEl) {
    btnEl.disabled = false;
    btnEl.textContent = "Изменить";
  }
}

//Ползунок чувствительности: обновляет число рядом с ним и передаёт значение в Python
async function onSensChange(value) {
  document.getElementById("sensVal").textContent = Number(value).toFixed(1);
  await window.pywebview.api.set_sensitivity(value);
}

//Клик по переключателю: Python включает или отключает эмуляцию, интерфейс показывает итоговое состояние
async function onToggleClick() {
  const enabled = await window.pywebview.api.toggle_enabled();
  setEnabledUI(enabled);
}

//Кнопка Изменить: показывает ожидание и просит Python запомнить
//следующую нажатую клавишу или кнопку мыши
async function onRebindClick(bindName) {
  document.getElementById(bindName + "_name").textContent = "...";
  const btn = document.getElementById(bindName + "_btn");
  btn.disabled = true;
  btn.textContent = "Ждём...";
  await window.pywebview.api.start_key_capture(bindName);
}

//Кнопка Сбросить конфигурацию: Python возвращает настройки по умолчанию, интерфейс их показывает
async function onResetConfigClick() {
  const state = await window.pywebview.api.reset_config();
  document.getElementById("sens").value = state.sensitivity;
  document.getElementById("sensVal").textContent = Number(state.sensitivity).toFixed(1);
  updateBindUI('bind_toggle', state.bind_toggle);
  updateBindUI('bind_reset', state.bind_reset);
}

//Заполняет основной экран сохранёнными настройками (вызывается из showMain)
async function init() {
  const state = await window.pywebview.api.get_state();

  const sensInput = document.getElementById("sens");
  sensInput.value = state.sensitivity;
  document.getElementById("sensVal").textContent = Number(state.sensitivity).toFixed(1);

  setEnabledUI(state.enabled);
  updateBindUI('bind_toggle', state.bind_toggle);
  updateBindUI('bind_reset', state.bind_reset);
}

//Экраны интерфейса: поиск драйвера, вопрос об установке и основной экран
const VIEWS = ["searchView", "promptView", "mainView"];

//Показывает один экран по имени и прячет остальные
function showView(name) {
  VIEWS.forEach(id => {
    document.getElementById(id).classList.toggle("hidden", id !== name);
  });
}

//Экран с крутилкой и надписью (вызывается из Python)
function showSearch(text) {
  document.getElementById("searchText").textContent = text;
  showView("searchView");
}

//Экран с вопросом об установке драйвера (вызывается из Python)
function showPrompt() {
  showView("promptView");
}

//Основной экран (вызывается из Python, когда драйвер найден и программа запущена)
function showMain() {
  showView("mainView");
  init();
}

//Кнопки Да и Нет: при Да сразу показывает крутилку установки, затем передаёт ответ в Python
async function onDriverAnswer(answer) {
  if (answer) showSearch("Установка драйвера...");
  await window.pywebview.api.driver_answer(answer);
}

//Флаг: интерфейс уже сообщил Python о готовности (защита от двойного вызова)
let _mojoyInited = false;
//Один раз сообщает Python, что интерфейс готов: после этого Python начинает поиск драйвера
function initOnce() {
  if (_mojoyInited) return;
  if (!(window.pywebview && window.pywebview.api)) return;
  _mojoyInited = true;
  window.pywebview.api.boot();
}

//Запуск: ждём события pywebviewready или стартуем сразу, если pywebview уже готов
window.addEventListener("pywebviewready", initOnce);
initOnce();
</script>
</body>
</html>"""


#Создание окна

#Создаёт окно с интерфейсом. Дальше всё запускается из самого интерфейса (Api.boot),
#когда страница загрузится
def main():
    global WINDOW
    WINDOW = webview.create_window(
        "MoJoy DCS",
        html=HTML_CONTENT,
        js_api=Api(),
        width=440,
        height=540,
        resizable=False,
    )
    webview.start()


#Точка входа: при любой ошибке показываем окно с её текстом и выходим
if __name__ == "__main__":
    try:
        main()
    except Exception:
        ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), "MoJoy DCS — ошибка запуска", 0x10)
        sys.exit(1)