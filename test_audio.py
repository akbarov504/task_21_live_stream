#!/usr/bin/env python3
"""
Audio qurilmalarni diagnostika qilish uchun test script.
MiniPC da ishlatish: python3 test_audio.py
"""
import subprocess
import sys
import time
import os

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg): print(f"  {RED}❌ {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠️  {msg}{RESET}")
def info(msg): print(f"  {CYAN}ℹ️  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{CYAN}{'='*55}\n  {msg}\n{'='*55}{RESET}")


# ─────────────────────────────────────────────────────────
# 1. arecord -L — barcha ALSA PCM qurilmalar
# ─────────────────────────────────────────────────────────
header("1. ALSA PCM qurilmalar ro'yxati (arecord -L)")
try:
    out = subprocess.check_output(["arecord", "-L"], stderr=subprocess.DEVNULL, timeout=5)
    lines = out.decode(errors="replace").splitlines()
    devs = [l.strip() for l in lines if l and not l.startswith(" ")]
    print(f"  Topilgan qurilmalar ({len(devs)} ta):")
    for d in devs:
        marker = GREEN + " ←" + RESET if d in ("IN_VMIC", "OUT_VMIC", "vmic") else ""
        print(f"    {d}{marker}")
    if "IN_VMIC" in devs:
        ok("IN_VMIC ALSA da ro'yxatda bor")
    else:
        fail("IN_VMIC ALSA da topilmadi!")
except Exception as e:
    fail(f"arecord -L: {e}")


# ─────────────────────────────────────────────────────────
# 2. /proc/asound/cards — karta raqamlari
# ─────────────────────────────────────────────────────────
header("2. ALSA karta raqamlari (/proc/asound/cards)")
try:
    with open("/proc/asound/cards") as f:
        cards = f.read()
    print(cards)
except Exception as e:
    fail(f"/proc/asound/cards: {e}")


# ─────────────────────────────────────────────────────────
# 3. IN_VMIC ni arecord bilan 2 soniya test yozuv
# ─────────────────────────────────────────────────────────
header("3. IN_VMIC → arecord test (2 soniya raw PCM)")

devices_to_test = ["IN_VMIC", "OUT_VMIC", "vmic"]
rates_to_test   = [48000, 44100, 16000]

for dev in devices_to_test:
    print(f"\n  [{dev}]")
    opened = False
    for rate in rates_to_test:
        cmd = ["arecord", "-D", dev, "-f", "S16_LE", "-r", str(rate), "-c", "1", "-t", "raw", "-q"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1.0)
            if proc.poll() is not None:
                err = proc.stderr.read(300).decode(errors="replace").strip()
                warn(f"rate={rate}Hz → jarayon tugadi. Xato: {err or '(yo`q)'}")
                continue

            # 1 sekundlik ma'lumot o'qi
            data = b""
            deadline = time.time() + 1.0
            while time.time() < deadline:
                try:
                    chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                    if chunk:
                        data += chunk
                    else:
                        break
                except Exception:
                    break
            proc.terminate()
            proc.wait(timeout=2)

            if len(data) >= 1920:  # kamida 20ms @ 48kHz
                rms = 0
                try:
                    import struct
                    samples = struct.unpack(f"<{len(data)//2}h", data[:len(data)//2*2])
                    rms = (sum(s*s for s in samples) / len(samples)) ** 0.5
                except Exception:
                    pass
                ok(f"rate={rate}Hz → {len(data)} bayt olindi, RMS≈{rms:.0f} {'(jim/tovushsiz)' if rms < 10 else '(audio bor!)'}")
                opened = True
                break
            else:
                warn(f"rate={rate}Hz → juda kam ma'lumot: {len(data)} bayt")

        except FileNotFoundError:
            fail("arecord topilmadi!")
            break
        except Exception as e:
            fail(f"rate={rate}Hz → {e}")

    if not opened:
        fail(f"{dev}: hech qanday rate bilan ochilmadi")


# ─────────────────────────────────────────────────────────
# 4. ffmpeg bilan IN_VMIC test
# ─────────────────────────────────────────────────────────
header("4. IN_VMIC → ffmpeg test")
for dev in ["IN_VMIC", "OUT_VMIC"]:
    print(f"\n  [{dev}]")
    cmd = ["ffmpeg", "-loglevel", "error", "-f", "alsa", "-i", dev,
           "-ar", "48000", "-ac", "1", "-f", "s16le", "pipe:1"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.8)
        if proc.poll() is not None:
            err = proc.stderr.read(500).decode(errors="replace").strip()
            fail(f"ffmpeg exited: {err or '(xato yo`q)'}")
        else:
            data = b""
            deadline = time.time() + 0.5
            while time.time() < deadline:
                try:
                    chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                    if chunk:
                        data += chunk
                except Exception:
                    break
            proc.terminate()
            proc.wait(timeout=2)
            if len(data) > 0:
                ok(f"ffmpeg → {len(data)} bayt olindi")
            else:
                warn("ffmpeg ochildi lekin ma'lumot yo'q")
    except FileNotFoundError:
        fail("ffmpeg topilmadi!")
    except Exception as e:
        fail(f"ffmpeg: {e}")


# ─────────────────────────────────────────────────────────
# 5. Yozuvchi dastur ishlayaptimi?
# ─────────────────────────────────────────────────────────
header("5. IN_VMIC ga yozayotgan jarayon bormi?")
try:
    out = subprocess.check_output(
        ["bash", "-c", "lsof /dev/snd/* 2>/dev/null || fuser -v /dev/snd/* 2>&1 || echo 'topilmadi'"],
        timeout=5
    ).decode(errors="replace")
    print(out)
except Exception as e:
    warn(f"jarayon tekshiruvi: {e}")

try:
    # aplay -l → playback qurilmalarni ko'rish
    out = subprocess.check_output(["aplay", "-l"], stderr=subprocess.DEVNULL, timeout=3)
    lines = [l for l in out.decode(errors="replace").splitlines() if "vmic" in l.lower() or "VMIC" in l]
    if lines:
        info("aplay -l da VMIC qurilmalar:")
        for l in lines:
            print(f"    {l}")
    else:
        info("aplay -l da VMIC qurilmalar ko'rinmaydi (playback side)")
except Exception as e:
    warn(f"aplay -l: {e}")


# ─────────────────────────────────────────────────────────
# 6. Xulosa
# ─────────────────────────────────────────────────────────
header("6. Xulosa va tavsiyalar")
print(f"""
  {BOLD}Agar 3-bo'limda 'jarayon tugadi' / 'Input/output error' bo'lsa:{RESET}
  → IN_VMIC qurilmasi mavjud lekin hozir hech kim unga yozmayapti.
  → Avval yozuvchi dasturni (boshqa dasturingizni) ishga tushiring,
    keyin ushbu servisni ishga tushiring.

  {BOLD}Agar RMS ≈ 0 (jim) bo'lsa:{RESET}
  → Qurilma ochildi lekin audio kelmayapti.
  → Yozuvchi dastur IN_VMIC ga haqiqiy audio yuboryaptimi?

  {BOLD}Agar ffmpeg muvaffaqiyatli bo'lsa lekin arecord bo'lmasa:{RESET}
  → config.py dagi IN_AUDIO_DEVICE ni ffmpeg bilan ishlaydigan
    formatga o'zgartiring.

  {BOLD}IN_VMIC haqiqiy audio kartami?{RESET}
  → aplay -l va arecord -l buyrug'larini ishlatib karta raqamini toping.
""")
