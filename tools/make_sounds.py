"""Generate LectureWeave+'s interface sounds as small WAV files.

The sounds are synthesised here (sine and triangle tones, filtered noise), so
they are original and carry no licence terms. Run from the repository root:

    python tools/make_sounds.py

Timings for startup.wav follow the opening animation in splash.py.
"""

from __future__ import annotations

from pathlib import Path
import wave

import numpy as np

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "sounds"


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds))


def envelope(length: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(length) / RATE
    rise = np.clip(t / max(attack, 1e-4), 0, 1)
    return rise * np.exp(-t / max(decay, 1e-4))


def tone(freq: float, seconds: float, gain: float, kind: str = "sine", decay: float = 0.3) -> np.ndarray:
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    phase = 2 * np.pi * freq * t
    if kind == "triangle":
        wave_ = 2 / np.pi * np.arcsin(np.sin(phase))
    else:
        wave_ = np.sin(phase)
    return gain * wave_ * envelope(n, 0.006, decay)


def chime(freq: float, gain: float = 0.32) -> np.ndarray:
    sound = tone(freq, 1.2, gain, decay=0.42)
    place(sound, tone(freq * 2, 0.7, gain * 0.22, decay=0.2), 0.0)
    place(sound, tone(freq * 3, 0.4, gain * 0.08, decay=0.1), 0.0)
    return sound


def thump(gain: float = 0.28) -> np.ndarray:
    n = int(RATE * 0.22)
    t = np.arange(n) / RATE
    freq = 160 * (90 / 160) ** (t / 0.18)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    return gain * np.sin(phase) * envelope(n, 0.008, 0.06)


def swish(seconds: float = 0.25, gain: float = 0.16) -> np.ndarray:
    rng = np.random.default_rng(7)
    n = int(RATE * seconds)
    noise = rng.uniform(-1, 1, n)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    band = np.exp(-(((freqs - 2600) / 1400) ** 2))
    shaped = np.fft.irfft(spectrum * band, n)
    shaped /= np.max(np.abs(shaped)) or 1
    t = np.arange(n) / RATE
    shape = np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 1.5
    return gain * shaped * shape


def slide_in(seconds: float = 0.42, gain: float = 0.2) -> np.ndarray:
    """A soft, low whoomp for the slide rising in behind the page."""
    rng = np.random.default_rng(11)
    n = int(RATE * seconds)
    noise = rng.uniform(-1, 1, n)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    shaped = np.fft.irfft(spectrum * np.exp(-((freqs / 700) ** 2)), n)
    shaped /= np.max(np.abs(shaped)) or 1
    t = np.arange(n) / RATE
    swell = np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 2
    freq = 150 * (220 / 150) ** (t / seconds)
    body = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * swell
    return gain * (0.7 * shaped * swell + 0.6 * body)


def place(track: np.ndarray, sound: np.ndarray, at: float) -> None:
    start = int(RATE * at)
    end = min(len(track), start + len(sound))
    track[start:end] += sound[: end - start]


def write(name: str, track: np.ndarray) -> None:
    peak = np.max(np.abs(track)) or 1
    track = track / max(peak, 1.0) * 0.9 if peak > 0.9 else track
    data = (np.clip(track, -1, 1) * 32767).astype("<i2")
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(data.tobytes())
    print(f"{name}: {len(track) / RATE:.2f}s")


def main() -> None:
    startup = silence(2.6)
    place(startup, thump(0.24), 0.08)
    place(startup, thump(0.2), 0.24)
    place(startup, swish(), 0.36)
    place(startup, slide_in(), 0.77)
    place(startup, chime(659.25), 1.21)
    place(startup, chime(987.77, 0.28), 1.31)
    write("startup.wav", startup)

    done = silence(1.4)
    for offset, freq in ((0.0, 783.99), (0.11, 987.77), (0.22, 1318.51)):
        place(done, tone(freq, 1.0, 0.26, decay=0.32), offset)
        place(done, tone(freq * 2, 0.5, 0.05, decay=0.15), offset)
    write("done.wav", done)

    failed = silence(1.2)
    place(failed, tone(392.0, 0.6, 0.26, "triangle", decay=0.22), 0.0)
    place(failed, tone(311.13, 0.9, 0.26, "triangle", decay=0.3), 0.22)
    write("failed.wav", failed)


if __name__ == "__main__":
    main()
