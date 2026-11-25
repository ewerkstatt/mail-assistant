"""Text-to-speech via Windows SAPI (asynchronous, interruptible)."""

SVSF_ASYNC = 1
SVSF_PURGE = 2


class Speaker:
    def __init__(self, rate: int = 0):
        self.voice = None
        try:
            import win32com.client
            self.voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._pick_german_voice()
            self.set_rate(rate)
        except Exception:
            self.voice = None  # no speech on non-Windows or without pywin32

    @property
    def available(self) -> bool:
        return self.voice is not None

    def _pick_german_voice(self):
        voices = self.voice.GetVoices()
        for i in range(voices.Count):
            v = voices.Item(i)
            try:
                lang = v.GetAttribute("Language").lower()  # hex LCID: 407 de-DE, c07 de-AT
            except Exception:
                lang = ""
            desc = v.GetDescription().lower()
            if lang in ("407", "c07", "807") or "german" in desc or "deutsch" in desc:
                self.voice.Voice = v
                return

    def set_rate(self, rate: int):
        if self.voice:
            self.voice.Rate = max(-10, min(10, int(rate)))

    def say(self, text: str):
        if self.voice and text:
            self.voice.Speak(text, SVSF_ASYNC | SVSF_PURGE)  # interrupts previous output

    def stop(self):
        if self.voice:
            self.voice.Speak("", SVSF_ASYNC | SVSF_PURGE)
