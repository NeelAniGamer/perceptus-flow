"""
Perceptus Flow — an always-on Windows helper that works like Wispr Flow,
but for your whole computer: talk to it, or use hand gestures, and it types
text or controls Windows for you.

  pip install pyautogui pillow pystray pygetwindow requests SpeechRecognition
  pip install pyaudio keyboard opencv-python mediapipe pyttsx3
  python perceptus_flow.py

Everything lives in the tray. A small pill floats above your work and shows
exactly what is happening: Idle -> Listening -> Thinking -> Done.

Hotkeys (global):
  Ctrl + Space  hold to talk, release to send
  Ctrl + Alt + G  toggle hand-gesture control
  Ctrl + Alt + Q  quit
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

CONFIG_PATH = Path.home() / ".perceptus_flow.json"

CLOUD_URL = "https://window-mind-whisper.lovable.app/api/public/brain"
LOCAL_URL = "http://localhost:8080/api/public/brain"

DESTRUCTIVE = {"delete", "format", "shutdown", "uninstall", "drop"}


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
@dataclass
class Settings:
    mode: str = "cloud"          # cloud | local
    key: str = ""                # optional brain key
    speed: str = "fast"          # fast | deep
    dictation: bool = True       # insert spoken text at the cursor
    control: bool = True         # let the brain click / type / launch apps
    gestures: bool = False       # webcam hand control
    speak_back: bool = True      # read replies out loud
    camera: int = 0

    @property
    def url(self) -> str:
        return CLOUD_URL if self.mode == "cloud" else LOCAL_URL


def load_settings() -> Settings:
    if CONFIG_PATH.exists():
        try:
            return Settings(**{**asdict(Settings()), **json.loads(CONFIG_PATH.read_text())})
        except Exception:
            pass
    return Settings()


def save_settings(s: Settings) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(asdict(s), indent=2))
    except Exception as exc:
        print("could not save settings:", exc)


SET = load_settings()
EVENTS: "queue.Queue[tuple[str, str]]" = queue.Queue()


def status(state: str, text: str = "") -> None:
    EVENTS.put((state, text))


# --------------------------------------------------------------------------
# screen + window context
# --------------------------------------------------------------------------
def screenshot_b64(max_width: int = 1280) -> str:
    import pyautogui
    from PIL import Image

    img = pyautogui.screenshot()
    if img.width > max_width:
        h = int(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def active_window() -> dict:
    try:
        import pygetwindow as gw

        w = gw.getActiveWindow()
        title = getattr(w, "title", "") or ""
    except Exception:
        title = ""
    app = title.split(" - ")[-1] if " - " in title else title
    return {"os": "windows", "app": app, "window_title": title, "activity": "desktop"}


# --------------------------------------------------------------------------
# camera + hand gestures (optional, degrades to voice-only)
# --------------------------------------------------------------------------
class Hands:
    """Finds a webcam that actually opens, then reads simple hand signs."""

    BACKENDS = ("DSHOW", "MSMF", "ANY")

    def __init__(self) -> None:
        self.cap = None
        self.last_frame = None
        self.detector = None
        self.running = False

    @staticmethod
    def probe() -> list[int]:
        try:
            import cv2
        except Exception:
            return []
        found = []
        for idx in range(5):
            for name in Hands.BACKENDS:
                api = getattr(cv2, f"CAP_{name}", cv2.CAP_ANY)
                cap = cv2.VideoCapture(idx, api)
                ok = False
                for _ in range(4):  # warm up; first frames are often empty
                    ok, _f = cap.read()
                    if ok:
                        break
                    time.sleep(0.08)
                cap.release()
                if ok:
                    found.append(idx)
                    break
        return found

    def open(self, index: int) -> bool:
        import cv2

        for name in self.BACKENDS:
            api = getattr(cv2, f"CAP_{name}", cv2.CAP_ANY)
            cap = cv2.VideoCapture(index, api)
            for _ in range(5):
                ok, frame = cap.read()
                if ok:
                    self.cap = cap
                    self.last_frame = frame
                    return True
                time.sleep(0.08)
            cap.release()
        return False

    def start(self, index: int, on_gesture) -> None:
        if self.running:
            return
        try:
            import cv2
            import mediapipe as mp
        except Exception:
            status("idle", "gestures need opencv + mediapipe")
            return
        if not self.open(index):
            status("idle", "no camera found — voice still works")
            return
        self.detector = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=0.6)
        self.running = True

        def loop() -> None:
            misses, last_sign, held = 0, "", 0.0
            while self.running and self.cap is not None:
                ok, frame = self.cap.read()
                if not ok:
                    misses += 1
                    if misses > 5:
                        self.cap.release()
                        if not self.open(index):
                            break
                        misses = 0
                    time.sleep(0.05)
                    continue
                misses = 0
                self.last_frame = frame
                res = self.detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                sign = ""
                if res.multi_hand_landmarks:
                    sign = classify(res.multi_hand_landmarks[0].landmark)
                now = time.time()
                if sign and sign == last_sign and now - held > 1.2:
                    held = now
                    on_gesture(sign)
                elif sign != last_sign:
                    last_sign, held = sign, now
                time.sleep(0.03)
            self.running = False

        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self.running = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def frame_b64(self) -> str | None:
        if self.last_frame is None:
            return None
        try:
            import cv2

            ok, buf = cv2.imencode(".jpg", self.last_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ok:
                return None
            return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
        except Exception:
            return None


def classify(lm) -> str:
    """Very small, very readable hand-sign reader."""
    tips = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
    up = {n: lm[i].y < lm[i - 2].y for n, i in tips.items()}
    thumb_out = abs(lm[4].x - lm[3].x) > 0.04
    count = sum(up.values())
    if count == 0 and not thumb_out:
        return "fist"
    if count == 4:
        return "open_palm"
    if up["index"] and not up["middle"] and count == 1:
        return "point"
    if up["index"] and up["middle"] and count == 2:
        return "peace"
    if count == 0 and thumb_out:
        return "thumb"
    return ""


GESTURE_GOALS = {
    "open_palm": "stop whatever is running and stay idle",
    "fist": "confirm the highlighted action",
    "point": "click the thing I am pointing at on screen",
    "peace": "switch to the next window",
    "thumb": "scroll down a little",
}


# --------------------------------------------------------------------------
# speech
# --------------------------------------------------------------------------
class Voice:
    def __init__(self) -> None:
        self.engine = None
        if SET.speak_back:
            try:
                import pyttsx3

                self.engine = pyttsx3.init()
            except Exception:
                self.engine = None

    def say(self, text: str) -> None:
        if not (SET.speak_back and self.engine and text):
            return
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception:
            pass

    @staticmethod
    def listen(max_seconds: int = 12) -> str:
        try:
            import speech_recognition as sr
        except Exception:
            status("idle", "install SpeechRecognition + pyaudio for voice")
            return ""
        r = sr.Recognizer()
        try:
            with sr.Microphone() as src:
                r.adjust_for_ambient_noise(src, duration=0.3)
                status("listening", "listening…")
                audio = r.listen(src, timeout=6, phrase_time_limit=max_seconds)
            status("thinking", "transcribing…")
            return r.recognize_google(audio)
        except Exception:
            return ""


# --------------------------------------------------------------------------
# brain
# --------------------------------------------------------------------------
def ask_brain(utterance: str, camera_image: str | None) -> dict:
    payload = {
        "session": "flow-desktop",
        "mode": SET.speed,
        "utterance": utterance,
        "image": screenshot_b64(),
        "os_context": active_window(),
    }
    if camera_image:
        payload["camera_image"] = camera_image
    headers = {"Content-Type": "application/json"}
    if SET.key:
        headers["X-API-Key"] = SET.key
    res = requests.post(SET.url, json=payload, headers=headers, timeout=60)
    if res.status_code >= 400:
        raise RuntimeError(f"brain {res.status_code}: {res.text[:300]}")
    return res.json()


def looks_destructive(action: dict) -> bool:
    if action.get("needs_confirmation"):
        return True
    blob = json.dumps(action).lower()
    return any(word in blob for word in DESTRUCTIVE)


def run_actions(actions: list[dict]) -> int:
    import pyautogui

    pyautogui.FAILSAFE = True
    done = 0
    for a in actions:
        op = a.get("type")
        if looks_destructive(a):
            status("idle", f"skipped risky step: {op}")
            continue
        try:
            if op == "click":
                pyautogui.click(a["x"], a["y"], button=a.get("button", "left"))
            elif op == "double_click":
                pyautogui.doubleClick(a["x"], a["y"])
            elif op == "move":
                pyautogui.moveTo(a["x"], a["y"], duration=0.15)
            elif op == "drag":
                pyautogui.moveTo(a["x"], a["y"])
                pyautogui.dragTo(a["to_x"], a["to_y"], duration=0.3)
            elif op == "scroll":
                pyautogui.scroll(int(a.get("amount", 300)))
            elif op == "hotkey":
                pyautogui.hotkey(*[normalize(k) for k in a.get("keys", [])])
            elif op == "type":
                pyautogui.typewrite(a.get("text", ""), interval=0.01)
            elif op == "wait":
                time.sleep(max(0, int(a.get("amount", 200))) / 1000)
            elif op in ("launch", "focus"):
                os.startfile(a.get("text", ""))
            else:
                continue
            done += 1
            time.sleep(0.12)
        except Exception as exc:
            status("idle", f"{op} failed: {exc}")
    return done


def normalize(key: str) -> str:
    k = str(key).strip().lower()
    return {"win": "winleft", "cmd": "winleft", "control": "ctrl", "return": "enter", "esc": "escape"}.get(k, k)


def insert_text(text: str) -> None:
    import pyautogui

    pyautogui.typewrite(text, interval=0.005)


# --------------------------------------------------------------------------
# the floating pill
# --------------------------------------------------------------------------
COLORS = {
    "idle": ("#1b1d23", "#8b93a7"),
    "listening": ("#10261f", "#48e39b"),
    "thinking": ("#191f33", "#7aa2ff"),
    "done": ("#14231a", "#7ce7a8"),
    "error": ("#2a1618", "#ff8189"),
}


class Pill:
    def __init__(self, root: tk.Tk) -> None:
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.94)
        self.frame = tk.Frame(self.win, bg="#1b1d23", padx=16, pady=9)
        self.frame.pack()
        self.dot = tk.Canvas(self.frame, width=10, height=10, bg="#1b1d23", highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 10))
        self.blob = self.dot.create_oval(1, 1, 9, 9, fill="#8b93a7", outline="")
        self.label = tk.Label(self.frame, text="Ready — hold Ctrl+Space to talk",
                              bg="#1b1d23", fg="#e6e9f0", font=("Segoe UI", 10))
        self.label.pack(side="left")
        self.place()
        self.frame.bind("<Button-1>", self.grab)
        self.frame.bind("<B1-Motion>", self.drag)
        self.label.bind("<Button-1>", self.grab)
        self.label.bind("<B1-Motion>", self.drag)
        self._off = (0, 0)

    def place(self) -> None:
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        w = self.win.winfo_width()
        self.win.geometry(f"+{(sw - w) // 2}+{sh - 140}")

    def grab(self, e) -> None:
        self._off = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def drag(self, e) -> None:
        self.win.geometry(f"+{e.x_root - self._off[0]}+{e.y_root - self._off[1]}")

    def set(self, state: str, text: str) -> None:
        bg, fg = COLORS.get(state, COLORS["idle"])
        self.frame.configure(bg=bg)
        self.dot.configure(bg=bg)
        self.dot.itemconfigure(self.blob, fill=fg)
        self.label.configure(bg=bg, fg="#e6e9f0", text=text or state.title())


# --------------------------------------------------------------------------
# settings window
# --------------------------------------------------------------------------
def open_settings(root: tk.Tk, app: "Flow") -> None:
    win = tk.Toplevel(root)
    win.title("Perceptus Flow — Settings")
    win.configure(bg="#14161b")
    win.geometry("420x430")
    win.attributes("-topmost", True)

    def head(text: str) -> None:
        tk.Label(win, text=text, bg="#14161b", fg="#8b93a7",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(14, 4))

    head("BRAIN")
    mode = tk.StringVar(value=SET.mode)
    row = tk.Frame(win, bg="#14161b")
    row.pack(anchor="w", padx=18)
    for value, text in (("cloud", "Online (recommended)"), ("local", "This computer")):
        tk.Radiobutton(row, text=text, value=value, variable=mode, bg="#14161b", fg="#e6e9f0",
                       selectcolor="#14161b", activebackground="#14161b",
                       font=("Segoe UI", 10)).pack(side="left", padx=(0, 12))

    head("ACCESS KEY (optional)")
    key = tk.StringVar(value=SET.key)
    tk.Entry(win, textvariable=key, show="•", bg="#1b1d23", fg="#e6e9f0",
             insertbackground="#e6e9f0", relief="flat").pack(fill="x", padx=18, ipady=5)

    head("HOW IT BEHAVES")
    speed = tk.StringVar(value=SET.speed)
    srow = tk.Frame(win, bg="#14161b")
    srow.pack(anchor="w", padx=18)
    for value, text in (("fast", "Quick answers"), ("deep", "Careful thinking")):
        tk.Radiobutton(srow, text=text, value=value, variable=speed, bg="#14161b", fg="#e6e9f0",
                       selectcolor="#14161b", activebackground="#14161b",
                       font=("Segoe UI", 10)).pack(side="left", padx=(0, 12))

    flags = {
        "dictation": (tk.BooleanVar(value=SET.dictation), "Type what I say into the app I'm using"),
        "control": (tk.BooleanVar(value=SET.control), "Let it click and use my computer"),
        "gestures": (tk.BooleanVar(value=SET.gestures), "Watch my hands through the camera"),
        "speak_back": (tk.BooleanVar(value=SET.speak_back), "Read replies out loud"),
    }
    for var, text in flags.values():
        tk.Checkbutton(win, text=text, variable=var, bg="#14161b", fg="#e6e9f0",
                       selectcolor="#14161b", activebackground="#14161b", anchor="w",
                       font=("Segoe UI", 10)).pack(fill="x", padx=16, pady=1)

    note = tk.Label(win, text="", bg="#14161b", fg="#7ce7a8", font=("Segoe UI", 9))
    note.pack(anchor="w", padx=18, pady=(8, 0))

    def save() -> None:
        SET.mode = mode.get()
        SET.key = key.get().strip()
        SET.speed = speed.get()
        for name, (var, _t) in flags.items():
            setattr(SET, name, bool(var.get()))
        save_settings(SET)
        app.apply_settings()
        note.configure(text="Saved.")

    tk.Button(win, text="Save", command=save, bg="#7aa2ff", fg="#0d0f14", relief="flat",
              font=("Segoe UI", 10, "bold"), padx=18, pady=6).pack(anchor="w", padx=18, pady=14)


# --------------------------------------------------------------------------
# the app
# --------------------------------------------------------------------------
class Flow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.pill = Pill(self.root)
        self.voice = Voice()
        self.hands = Hands()
        self.busy = False
        self.root.after(80, self.pump)

    # ---- lifecycle -------------------------------------------------
    def apply_settings(self) -> None:
        if SET.gestures and not self.hands.running:
            cams = Hands.probe()
            if cams:
                SET.camera = cams[0]
                self.hands.start(SET.camera, self.on_gesture)
                status("idle", f"watching camera {SET.camera}")
            else:
                status("idle", "no camera found — voice still works")
        elif not SET.gestures and self.hands.running:
            self.hands.stop()

    def pump(self) -> None:
        try:
            while True:
                state, text = EVENTS.get_nowait()
                self.pill.set(state, text)
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    # ---- triggers --------------------------------------------------
    def on_gesture(self, sign: str) -> None:
        goal = GESTURE_GOALS.get(sign)
        if goal:
            self.handle(goal, source=f"hand: {sign.replace('_', ' ')}")

    def on_hotkey(self) -> None:
        if self.busy:
            return
        threading.Thread(target=self._voice_turn, daemon=True).start()

    def _voice_turn(self) -> None:
        said = Voice.listen()
        if not said:
            status("idle", "didn't catch that — try again")
            return
        self.handle(said, source="you said")

    # ---- the one path everything goes through ----------------------
    def handle(self, utterance: str, source: str = "you said") -> None:
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._work, args=(utterance, source), daemon=True).start()

    def _work(self, utterance: str, source: str) -> None:
        try:
            status("thinking", f"{source}: {utterance[:48]}")
            data = ask_brain(utterance, self.hands.frame_b64() if SET.gestures else None)
            intent = (data.get("intent") or {}).get("label", "")
            plan = data.get("plan") or {}
            actions = plan.get("actions") or []
            say = data.get("say") or ""
            text = data.get("text") or plan.get("text") or ""

            if SET.dictation and intent in ("dictate", "write", "compose") and text:
                insert_text(text)
                status("done", "text inserted")
            elif SET.control and actions:
                n = run_actions(actions)
                status("done", f"{n} step{'s' if n != 1 else ''} done")
            else:
                status("done", say[:60] or "nothing to do")

            if say:
                self.voice.say(say)
        except Exception as exc:
            status("error", str(exc)[:70])
        finally:
            time.sleep(1.6)
            self.busy = False
            status("idle", "Ready — hold Ctrl+Space to talk")

    def quit(self) -> None:
        self.hands.stop()
        try:
            self.root.quit()
        except Exception:
            pass
        os._exit(0)


# --------------------------------------------------------------------------
# tray + hotkeys
# --------------------------------------------------------------------------
def tray(app: Flow) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        print("tray needs pystray + pillow — running without a tray icon")
        return

    img = Image.new("RGB", (64, 64), "#0d0f14")
    d = ImageDraw.Draw(img)
    d.ellipse((14, 14, 50, 50), outline="#7aa2ff", width=5)
    d.ellipse((28, 28, 36, 36), fill="#7ce7a8")

    def toggle(name: str):
        def inner(icon, item):
            setattr(SET, name, not getattr(SET, name))
            save_settings(SET)
            app.apply_settings()
        return inner

    menu = pystray.Menu(
        pystray.MenuItem("Talk now (Ctrl+Space)", lambda i, it: app.on_hotkey()),
        pystray.MenuItem("Type what I say", toggle("dictation"), checked=lambda i: SET.dictation),
        pystray.MenuItem("Control my computer", toggle("control"), checked=lambda i: SET.control),
        pystray.MenuItem("Watch my hands", toggle("gestures"), checked=lambda i: SET.gestures),
        pystray.MenuItem("Read replies out loud", toggle("speak_back"), checked=lambda i: SET.speak_back),
        pystray.MenuItem("Settings…", lambda i, it: app.root.after(0, lambda: open_settings(app.root, app))),
        pystray.MenuItem("Quit", lambda i, it: (i.stop(), app.quit())),
    )
    icon = pystray.Icon("perceptus_flow", img, "Perceptus Flow", menu)
    threading.Thread(target=icon.run, daemon=True).start()


def hotkeys(app: Flow) -> None:
    try:
        import keyboard
    except Exception:
        print("global hotkeys need the 'keyboard' package — use the tray menu instead")
        return
    keyboard.add_hotkey("ctrl+space", app.on_hotkey, suppress=False)
    keyboard.add_hotkey("ctrl+alt+g", lambda: (setattr(SET, "gestures", not SET.gestures),
                                               save_settings(SET), app.apply_settings()))
    keyboard.add_hotkey("ctrl+alt+q", app.quit)


def main() -> None:
    if sys.platform != "win32":
        print("Perceptus Flow is built for Windows; some parts will not work here.")
    app = Flow()
    tray(app)
    hotkeys(app)
    app.apply_settings()
    status("idle", "Ready — hold Ctrl+Space to talk")
    app.root.mainloop()


if __name__ == "__main__":
    main()
