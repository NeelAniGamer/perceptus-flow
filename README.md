# Perceptus Flow

A Wispr-Flow-style background app for Windows, powered by the hosted Perceptus brain.
It lives in the system tray with a small floating pill that shows exactly what is happening:
**Ready → Listening → Thinking → Done**.

- Hold **Ctrl+Space** to talk — it types what you say into the app you're using, or carries out the action on your PC.
- Optional hand-sign control through the webcam (open palm, fist, point, peace, thumb).
- Plain-language settings window; anything risky (delete, format, shutdown…) is skipped automatically.

## Install & run

```
pip install pyautogui pillow pystray pygetwindow requests SpeechRecognition pyaudio keyboard opencv-python mediapipe pyttsx3
python perceptus_flow.py
```

## Hotkeys

| Hotkey | Action |
| --- | --- |
| Ctrl+Space | Talk |
| Ctrl+Alt+G | Toggle hand control |
| Ctrl+Alt+Q | Quit |

Settings are saved to `~/.perceptus_flow.json`. Brain endpoint: https://window-mind-whisper.lovable.app/api/public/brain (online) or your local server.
