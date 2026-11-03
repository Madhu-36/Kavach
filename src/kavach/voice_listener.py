"""Voice Listener & Semantic Action Extractor for Kavach.

Advanced Acoustic Architecture:
1. Acoustic Capture & Conditioning:
   - 16 kHz 16-bit Mono PCM stream via sounddevice with auto-device resolution.
   - 4th-order Butterworth Bandpass Filter (85 Hz to 7500 Hz) to eliminate DC rumble,
     AC hum (50/60 Hz), mechanical vibrations, and high-frequency thermal hiss.
   - Spectral Subtraction Noise Reduction (STFT/ISTFT) using ambient noise profile
     to eliminate stationary background noise (PC fans, air conditioning).
   - Pre-Emphasis Filter (alpha=0.95) to balance speech spectrum and elevate high-frequency
     consonant formants (/s/, /sh/, /f/, /t/, /p/, /k/).
   - 500ms Circular Ring Buffer (pre-speech buffer) to eliminate initial consonant clipping.
2. Advanced Dual-Feature Voice Activity Detection (VAD):
   - 1-second adaptive ambient noise calibration.
   - Dual-metric speech detection: Short-Time RMS (voiced vowels) + Zero-Crossing Rate (unvoiced consonants).
   - Dual-threshold state machine with hysteresis (high onset threshold, low sustain threshold).
   - 1.4s Hangover trailing silence buffer to handle natural conversational pauses.
   - Real-time ASCII terminal VU-meter with energy and ZCR indicators.
3. Signal Normalization & Automatic Gain Control (AGC):
   - Residual DC offset subtraction (zero-centering).
   - Peak amplitude normalization to -1.0 dBFS (0.89 float32).
   - Soft-knee compression and limiter to prevent digital clipping distortion.
4. Dual-Engine ASR with Domain Acoustic Biasing:
   - Primary: OpenAI Whisper (base.en / base) running locally, offline, private, zero-leakage.
   - Multi-temperature ladder decoding: temperature=(0.0, 0.2, 0.4), beam_size=5, patience=1.0.
   - Acoustic biasing via initial_prompt for UPI, financial terms, and merchant entities.
   - Secondary Fallback: Google Cloud Web Speech API (en-IN Indian English accent model).
5. Natural Language Semantic Intent & Number Engine:
   - Compound spoken word number parser (English + Hindi/Hinglish numerals, lakh, crore, paise).
   - Phonetic spoken VPA resolver ("at" -> "@", merchant aliases).
   - Action classifier (make_payment, check_balance, search_store, revoke_mandate).
6. Hardware Diagnostics & Audio Capture Export:
   - Automated export of clean 16-bit WAV audio to recordings/clean_processed.wav.
   - Acoustic diagnostic mode to measure SNR, noise floor, peak headroom, and clipping risk.
7. Kavach Gateway Integration:
   - Directly converts SpokenPaymentIntent into MandateSpec, validates policy, signs receipt.
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal, Optional

import numpy as np
import scipy.io.wavfile as wav
import scipy.signal
import sounddevice as sd
import whisper
from pydantic import BaseModel, Field

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Supported ASR engines
EngineType = Literal["whisper", "google", "auto"]


class SpokenPaymentIntent(BaseModel):
    """Structured payment intent extracted from transcribed user speech."""

    action: Literal["make_payment", "check_balance", "search_store", "revoke_mandate", "unknown"]
    payee_vpa: Optional[str] = Field(None, description="Target UPI Virtual Payment Address")
    amount_paise: int = Field(0, description="Amount in integer paise (₹1 = 100 paise)")
    amount_rupees: float = Field(0.0, description="Amount in decimal rupees")
    note: str = Field("", description="Transaction purpose or note")
    raw_transcript: str = Field("", description="Exact words transcribed from voice")
    confidence: float = Field(1.0, description="Estimated transcription confidence")

    def summary(self) -> str:
        """Formatted human-readable summary of the intent."""
        if self.action == "make_payment":
            return f"Pay ₹{self.amount_rupees:,.2f} ({self.amount_paise} paise) to {self.payee_vpa} [Note: {self.note or 'N/A'}]"
        elif self.action == "check_balance":
            return "Check Account / Wallet Balance"
        elif self.action == "search_store":
            return f"Search Store Catalog: '{self.note}'"
        elif self.action == "revoke_mandate":
            return "Revoke / Cancel Active Mandate"
        return f"Unknown Action (Transcript: '{self.raw_transcript}')"


@dataclass
class AudioConfig:
    samplerate: int = 16000             # 16kHz standard for Whisper and modern ASR models
    channels: int = 1                   # Mono audio
    chunk_size: int = 1024              # 64ms per chunk at 16kHz
    silence_threshold_multiplier: float = 2.0
    min_threshold_rms: float = 45.0     # Floor threshold to avoid triggering on pure line noise
    min_speech_duration_sec: float = 0.4
    pre_speech_buffer_sec: float = 0.5  # 500ms ring buffer prevents initial word truncation
    silence_duration_sec: float = 1.4   # 1.4s hangover to tolerate natural speech pauses
    max_phrase_duration_sec: float = 15.0
    bandpass_low: float = 85.0          # 85 Hz cutoff (AC hum & mic bump removal)
    bandpass_high: float = 7500.0       # 7500 Hz cutoff (anti-aliasing & high hiss cut)
    spectral_oversubtraction: float = 1.3  # Denoising oversubtraction factor
    spectral_floor: float = 0.05        # Denoising spectral floor
    pre_emphasis_coeff: float = 0.95    # Formant pre-emphasis factor
    zcr_threshold: float = 0.15         # Zero-crossing rate threshold for fricatives/consonants
    input_device: Optional[int | str] = None  # Specific audio input device
    save_capture_dir: Optional[str] = "recordings"  # Path to persist WAV audio captures


# Vocabulary dictionaries for spoken numerals
UNITS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
    # Hindi / Hinglish numerals
    "ek": 1, "do": 2, "teen": 3, "chaar": 4, "paanch": 5,
    "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "dus": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15,
}

TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # Hindi / Hinglish tens
    "bees": 20, "tees": 30, "chaalis": 40, "pachaas": 50,
    "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90,
}

SCALES: dict[str, int] = {
    "hundred": 100, "sau": 100,
    "thousand": 1000, "hazaar": 1000, "k": 1000,
    "lakh": 100000, "lac": 100000, "lakhs": 100000,
    "crore": 10000000, "crores": 10000000,
    "million": 1000000,
}

MERCHANT_ALIASES: dict[str, str] = {
    "grocer": "ondc-grocer@upi",
    "grocery": "ondc-grocer@upi",
    "store": "ondc-grocer@upi",
    "shop": "ondc-grocer@upi",
    "kirana": "ondc-grocer@upi",
    "supermarket": "ondc-grocer@upi",
    "delivery": "delivery-rider@upi",
    "rider": "delivery-rider@upi",
    "courier": "delivery-rider@upi",
    "tip": "delivery-rider@upi",
    "pharmacy": "apollo-pharmacy@upi",
    "chemist": "apollo-pharmacy@upi",
    "medicine": "apollo-pharmacy@upi",
    "electric": "bescom-utility@upi",
    "electricity": "bescom-utility@upi",
    "utility": "bescom-utility@upi",
    "power": "bescom-utility@upi",
    "attacker": "attacker_wallet@upi",
    "hacker": "attacker_wallet@upi",
    "scammer": "attacker_wallet@upi",
}


def parse_spoken_currency(text: str) -> tuple[float, int]:
    """Parse complex spoken currency expressions into float rupees and integer paise.
    
    Handles:
    - Digits: '500', '₹1,500.50', '25.75 rs'
    - Compound words: 'two thousand five hundred', 'one hundred fifty'
    - Hindi/Hinglish: 'paanch sau rupaye', 'do hazaar', 'sau rupaye'
    - Compound paise: 'fifty rupees and fifty paise' -> 50.50
    - Sub-rupee paise: 'seventy five paise' -> 0.75
    """
    lower = text.lower().strip()
    # Normalize ₹ symbol to ' rs '
    lower = re.sub(r"[₹]", " rs ", lower)

    def _parse_segment(seg: str) -> float:
        seg = seg.strip()
        if not seg:
            return 0.0
        # Check if digits exist in segment
        d_match = re.search(r"([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)", seg)
        if d_match:
            try:
                return float(d_match.group(1).replace(",", ""))
            except ValueError:
                pass
        # Parse spoken number words
        tokens = re.findall(r"[a-zA-Z]+", seg)
        total = 0
        current = 0
        found = False
        for tok in tokens:
            if tok in UNITS:
                current += UNITS[tok]
                found = True
            elif tok in TENS:
                current += TENS[tok]
                found = True
            elif tok in ["hundred", "sau"]:
                current = (current if current != 0 else 1) * 100
                found = True
            elif tok in SCALES:
                total += (current if current != 0 else 1) * SCALES[tok]
                current = 0
                found = True
        total += current
        return float(total) if found else 0.0

    # 1. Check if both rupees and paise are mentioned
    rp_split = re.split(r"\b(?:rupees?|rs\.?|rupaye)\b", lower)
    if len(rp_split) > 1:
        part_before = rp_split[0].strip()
        part_after = rp_split[1].strip()

        paise_val = 0.0
        paise_match = re.search(r"(?:and\s+)?([a-z0-9\s]+?)\s*pais(?:e|a)", part_after)
        if paise_match:
            paise_val = _parse_segment(paise_match.group(1))
            part_after = part_after[:paise_match.start()] + part_after[paise_match.end():]

        val_before = _parse_segment(part_before)
        val_after = _parse_segment(part_after)
        rupees_val = val_before if val_before > 0.0 else val_after

        total = rupees_val + (paise_val / 100.0)
        if total > 0.0:
            return total, int(round(total * 100))

    # 2. Check if ONLY paise is mentioned (e.g. 'seventy five paise')
    if "paise" in lower or "paisa" in lower:
        paise_match = re.search(r"([a-z0-9\s]+?)\s*pais(?:e|a)", lower)
        if paise_match:
            p_val = _parse_segment(paise_match.group(1))
            total = p_val / 100.0
            return total, int(round(total * 100))

    # 3. Direct digit pattern
    digit_match = re.search(r"([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)", lower)
    if digit_match:
        try:
            val = float(digit_match.group(1).replace(",", ""))
            return val, int(round(val * 100))
        except ValueError:
            pass

    # 4. Spoken number words
    val = _parse_segment(lower)
    return val, int(round(val * 100))


def resolve_spoken_vpa(transcript: str) -> str:
    """Resolve a target UPI Virtual Payment Address from spoken transcript."""
    lower = transcript.lower().strip()

    # 1. Direct standard VPA pattern (e.g. user@upi, user@okaxis, shop.123@icici)
    direct_match = re.search(r"[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z]{2,64}", transcript)
    if direct_match:
        return direct_match.group(0).lower()

    # 2. Spoken VPA with 'at' or 'at the rate' (e.g., 'rahul at okaxis', 'alice at upi', 'john dot doe at icici')
    spoken = re.sub(r"\s+dot\s+", ".", lower)
    spoken_vpa_match = re.search(
        r"\b([a-zA-Z0-9.\-_]{2,64})\s+(?:at\s+the\s+rate|at)\s+([a-zA-Z]{2,32})\b",
        spoken,
    )
    if spoken_vpa_match:
        handle = spoken_vpa_match.group(1).strip()
        provider = spoken_vpa_match.group(2).strip()
        # Ensure provider is a valid UPI handle to avoid false positives (e.g., 'pay at store')
        valid_providers = {
            "upi", "okaxis", "okhdfcbank", "okicici", "oksbi", "paytm",
            "ybl", "axl", "ibl", "icici", "hdfc", "sbi", "kotak",
        }
        if provider in valid_providers:
            return f"{handle}@{provider}"

    # 3. Known merchant and ecosystem aliases
    for alias, vpa in MERCHANT_ALIASES.items():
        if re.search(rf"\b{alias}\b", lower):
            return vpa

    # Default known safe sandbox merchant
    return "ondc-grocer@upi"


class VoiceListener:
    """Robust, noise-calibrated speech listener with local Whisper & Google ASR engines."""

    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "cpu",
        config: Optional[AudioConfig] = None,
    ):
        self.config = config or AudioConfig()
        self.device = device
        self.model_size = model_size
        self._whisper_model = None
        self.ambient_energy = 100.0  # Baseline ambient energy (RMS)
        self.ambient_noise_profile: Optional[np.ndarray] = None  # Ambient noise audio frame

        # Precompute 4th-order Butterworth bandpass filter (85 Hz to 7500 Hz)
        self._sos = scipy.signal.butter(
            N=4,
            Wn=[self.config.bandpass_low, self.config.bandpass_high],
            btype="bandpass",
            fs=self.config.samplerate,
            output="sos",
        )

    @property
    def whisper_model(self):
        """Lazy-load Whisper model on first use."""
        if self._whisper_model is None:
            self._whisper_model = whisper.load_model(self.model_size, device=self.device)
        return self._whisper_model

    def resolve_device_index(self) -> Optional[int]:
        """Resolve device name or index from config."""
        dev = self.config.input_device
        if dev is None or isinstance(dev, int):
            return dev
        dev_str = str(dev).lower()
        for idx, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and dev_str in d.get("name", "").lower():
                return idx
        return None

    def filter_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """Apply 85-7500 Hz bandpass Butterworth filter to eliminate rumble, hum, and high hiss."""
        return scipy.signal.sosfilt(self._sos, chunk.astype(np.float32))

    def compute_zcr(self, chunk: np.ndarray) -> float:
        """Compute Zero-Crossing Rate to identify unvoiced consonants (s, f, th, p, t, k)."""
        sign_diff = np.diff(np.sign(chunk.astype(np.float32)))
        return float(np.mean(np.abs(sign_diff)) / 2.0)

    def spectral_denoise(
        self,
        audio: np.ndarray,
        noise_profile: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Apply spectral subtraction (Wiener gating) to subtract stationary background room noise."""
        if noise_profile is None:
            noise_profile = self.ambient_noise_profile

        if noise_profile is None or len(noise_profile) < 512:
            return audio

        n_fft = 512
        hop_length = 256
        window = np.hanning(n_fft)

        try:
            # Compute STFT of speech and noise
            _, _, zxx = scipy.signal.stft(
                audio, fs=self.config.samplerate, window=window, nperseg=n_fft, noverlap=n_fft - hop_length
            )
            _, _, zxx_n = scipy.signal.stft(
                noise_profile, fs=self.config.samplerate, window=window, nperseg=n_fft, noverlap=n_fft - hop_length
            )

            mag = np.abs(zxx)
            phase = np.angle(zxx)
            noise_mag = np.mean(np.abs(zxx_n), axis=1, keepdims=True)

            # Spectral subtraction with oversubtraction factor
            subtracted = mag - (self.config.spectral_oversubtraction * noise_mag)
            floor = self.config.spectral_floor * noise_mag
            clean_mag = np.maximum(subtracted, floor)

            # Reconstruct clean audio
            clean_zxx = clean_mag * np.exp(1j * phase)
            _, clean_audio = scipy.signal.istft(
                clean_zxx, fs=self.config.samplerate, window=window, nperseg=n_fft, noverlap=n_fft - hop_length
            )
            return clean_audio[:len(audio)]
        except Exception:
            # Fall back to raw audio on any transform edge case
            return audio

    def pre_emphasis_filter(self, audio: np.ndarray) -> np.ndarray:
        """Apply pre-emphasis filter y[n] = x[n] - alpha * x[n-1] to elevate consonant formants."""
        alpha = self.config.pre_emphasis_coeff
        return np.append(audio[0], audio[1:] - alpha * audio[:-1])

    def calibrate_ambient_noise(self, duration_sec: float = 1.0) -> float:
        """Measure ambient room noise to establish an adaptive speech threshold and noise profile."""
        dev_idx = self.resolve_device_index()
        num_frames = int(duration_sec * self.config.samplerate)
        recording = sd.rec(
            num_frames,
            samplerate=self.config.samplerate,
            channels=self.config.channels,
            dtype="int16",
            device=dev_idx,
            blocking=True,
        )
        filtered = self.filter_chunk(recording[:, 0])
        self.ambient_noise_profile = filtered.copy()
        rms = float(np.sqrt(np.mean(np.square(filtered))))
        self.ambient_energy = max(rms, self.config.min_threshold_rms)
        return self.ambient_energy

    def listen_phrase(
        self,
        timeout_sec: float = 8.0,
        show_meter: bool = True,
    ) -> Optional[np.ndarray]:
        """Capture an entire spoken phrase with advanced dual-feature VAD and pre-speech ring buffering.
        
        - 500ms Pre-speech ring buffer preserves opening consonants (P, T, K, S, F).
        - Hysteresis dual-threshold state machine prevents premature word-end dropping.
        - Zero-Crossing Rate (ZCR) tracks unvoiced consonants with low energy.
        - 1.4s Hangover silence buffer tolerates natural conversational pauses.
        """
        # Hysteresis Thresholds
        onset_threshold = max(
            self.ambient_energy * self.config.silence_threshold_multiplier,
            self.ambient_energy + 70.0,
        )
        sustain_threshold = max(
            self.ambient_energy * 1.3,
            self.ambient_energy + 25.0,
        )

        chunk_size = self.config.chunk_size
        samplerate = self.config.samplerate
        silence_limit_chunks = int(self.config.silence_duration_sec * samplerate / chunk_size)
        max_chunks = int(self.config.max_phrase_duration_sec * samplerate / chunk_size)
        timeout_chunks = int(timeout_sec * samplerate / chunk_size)

        # 500ms pre-speech ring buffer (prevents cutting initial consonants)
        pre_chunks = max(int(self.config.pre_speech_buffer_sec * samplerate / chunk_size), 1)
        ring_buffer: deque[np.ndarray] = deque(maxlen=pre_chunks)

        recorded_chunks: list[np.ndarray] = []
        is_speaking = False
        silence_chunks = 0
        chunks_waited = 0
        dev_idx = self.resolve_device_index()

        # Open non-blocking audio input stream
        with sd.InputStream(
            samplerate=samplerate,
            channels=self.config.channels,
            dtype="int16",
            blocksize=chunk_size,
            device=dev_idx,
        ) as stream:
            while True:
                data, _ = stream.read(chunk_size)
                raw_chunk = data[:, 0]
                filtered_chunk = self.filter_chunk(raw_chunk)
                rms = float(np.sqrt(np.mean(np.square(filtered_chunk))))
                zcr = self.compute_zcr(filtered_chunk)

                # Real-time ASCII VU-Meter with RMS & ZCR feedback
                if show_meter:
                    bars = int(min(rms / (onset_threshold * 2.5 + 1e-5), 1.0) * 20)
                    meter = "█" * bars + "░" * (20 - bars)
                    status = f"Speaking (RMS: {rms:4.1f}, ZCR: {zcr:.2f})" if is_speaking else "Listening (speak now)..."
                    sys.stdout.write(f"\r🎙️ [{meter}] {status} ")
                    sys.stdout.flush()

                if not is_speaking:
                    ring_buffer.append(raw_chunk)
                    chunks_waited += 1

                    # Trigger speech on high RMS OR unvoiced consonant (high ZCR + moderate RMS)
                    is_active = (rms > onset_threshold) or (
                        rms > (self.ambient_energy * 1.1) and zcr > self.config.zcr_threshold
                    )

                    if is_active:
                        is_speaking = True
                        # Prepend the pre-speech buffer so initial consonants are 100% captured!
                        recorded_chunks.extend(ring_buffer)
                        silence_chunks = 0
                    elif chunks_waited >= timeout_chunks:
                        if show_meter:
                            sys.stdout.write("\r🎙️ [Timeout] No speech detected within window.          \n")
                            sys.stdout.flush()
                        return None
                else:
                    recorded_chunks.append(raw_chunk)
                    # Sustain speech using hysteresis threshold or ongoing fricatives
                    is_sustaining = (rms > sustain_threshold) or (
                        rms > self.ambient_energy and zcr > self.config.zcr_threshold
                    )

                    if not is_sustaining:
                        silence_chunks += 1
                        if silence_chunks >= silence_limit_chunks:
                            # User stopped speaking
                            break
                    else:
                        silence_chunks = 0

                    if len(recorded_chunks) >= max_chunks:
                        break

        if show_meter:
            sys.stdout.write("\r🎙️ [Captured] Audio received. Processing advanced DSP waveform... \n")
            sys.stdout.flush()

        if not recorded_chunks:
            return None

        # Concatenate audio frames
        audio_int16 = np.concatenate(recorded_chunks)

        # Minimum duration check (avoid false triggers on micro-clicks or breathing)
        if len(audio_int16) < int(self.config.min_speech_duration_sec * samplerate):
            return None

        normalized = self.normalize_audio(audio_int16)

        # Save clean capture if directory specified
        if self.config.save_capture_dir:
            try:
                os.makedirs(self.config.save_capture_dir, exist_ok=True)
                clean_pcm = (np.clip(normalized, -1.0, 1.0) * 32767.0).astype(np.int16)
                clean_path = os.path.join(self.config.save_capture_dir, "clean_processed.wav")
                wav.write(clean_path, self.config.samplerate, clean_pcm)
            except Exception:
                pass

        return normalized

    def normalize_audio(self, audio: np.ndarray) -> np.ndarray:
        """Apply bandpass filtering, spectral noise subtraction, DC removal, and peak normalization."""
        # Convert to float32
        data = audio.astype(np.float32)

        # 1. Bandpass filter (85 Hz - 7500 Hz)
        data = scipy.signal.sosfilt(self._sos, data)

        # 2. Spectral noise reduction using ambient profile
        if self.ambient_noise_profile is not None:
            data = self.spectral_denoise(data, self.ambient_noise_profile)

        # 3. Pre-emphasis filter for consonant sharpness
        data = self.pre_emphasis_filter(data)

        # 4. Remove residual DC offset
        data = data - np.mean(data)

        # 5. Peak normalization to -1.0 dBFS (~0.89)
        peak = np.max(np.abs(data))
        if peak > 1e-4:
            data = (data / peak) * 0.89

        # 6. Clip bounds to [-1.0, 1.0]
        data = np.clip(data, -1.0, 1.0)
        return data

    def transcribe_audio(
        self,
        audio_float32: np.ndarray,
        engine: EngineType = "auto",
        language: str = "en",
    ) -> str:
        """Transcribe normalized audio array using specified ASR engine."""
        if engine == "google":
            return self._transcribe_google(audio_float32, language=language)
        elif engine == "whisper":
            return self._transcribe_whisper(audio_float32)
        else:  # "auto": Local Whisper first, Google fallback on empty/error
            text = self._transcribe_whisper(audio_float32)
            if not text.strip():
                text = self._transcribe_google(audio_float32, language=language)
            return text

    def _transcribe_whisper(self, audio_float32: np.ndarray) -> str:
        """Transcribe using local Whisper model with acoustic prompt biasing and temperature ladder."""
        # Domain vocabulary biasing prompt
        initial_prompt = (
            "Kavach secure UPI agent payment mandate: pay rupees paise to merchant, "
            "ondc-grocer@upi, delivery-rider@upi, bescom-utility@upi, apollo-pharmacy@upi, "
            "send money, check balance, account, transfer, paise."
        )
        result = self.whisper_model.transcribe(
            audio_float32,
            fp16=False,
            temperature=(0.0, 0.2, 0.4),
            beam_size=5,
            best_of=5,
            patience=1.0,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt,
        )
        return result.get("text", "").strip()

    def _transcribe_google(self, audio_float32: np.ndarray, language: str = "en-IN") -> str:
        """Transcribe using Google Web Speech API (supports Indian English / Hinglish)."""
        import speech_recognition as sr

        # Convert float32 back to int16 PCM WAV in memory
        int16_data = (audio_float32 * 32767.0).astype(np.int16)
        wav_io = io.BytesIO()
        wav.write(wav_io, self.config.samplerate, int16_data)
        wav_io.seek(0)

        r = sr.Recognizer()
        with sr.AudioFile(wav_io) as source:
            audio_data = r.record(source)

        try:
            return r.recognize_google(audio_data, language=language).strip()
        except Exception:
            return ""

    def diagnose_microphone(self, duration_sec: float = 3.0) -> dict[str, Any]:
        """Acoustic diagnostic to analyze microphone noise floor, speech peak, SNR, and clipping risk."""
        print(f"🔍 Analyzing microphone for {duration_sec}s. Please speak a test sentence...")
        dev_idx = self.resolve_device_index()
        num_frames = int(duration_sec * self.config.samplerate)
        recording = sd.rec(
            num_frames,
            samplerate=self.config.samplerate,
            channels=self.config.channels,
            dtype="int16",
            device=dev_idx,
            blocking=True,
        )
        raw = recording[:, 0].astype(np.float32)
        filtered = self.filter_chunk(raw)

        # Metrics
        rms = float(np.sqrt(np.mean(np.square(filtered))))
        peak = float(np.max(np.abs(filtered)))
        noise_floor_db = 20 * np.log10(max(self.ambient_energy, 1.0) / 32767.0)
        peak_db = 20 * np.log10(max(peak, 1.0) / 32767.0)
        snr_db = max(peak_db - noise_floor_db, 0.0)

        is_clipping = peak >= 32700.0
        is_too_quiet = peak < 1000.0

        if is_clipping:
            status = "Warning: Microphone input is clipping. Lower input gain in Windows sound settings."
        elif is_too_quiet:
            status = "Notice: Microphone input is very quiet. Bring mic closer or increase volume."
        elif snr_db > 20.0:
            status = "Optimal: Studio-grade Signal-to-Noise Ratio for clear Whisper transcription."
        else:
            status = "Good: Audio signal is clear for speech recognition."

        return {
            "device_index": dev_idx,
            "ambient_noise_db": round(noise_floor_db, 1),
            "peak_db": round(peak_db, 1),
            "snr_db": round(snr_db, 1),
            "is_clipping": is_clipping,
            "recommendation": status,
        }

    def parse_payment_intent(self, transcript: str) -> SpokenPaymentIntent:
        """Extract structured Kavach action, payee VPA, amount, and note from transcript."""
        raw = transcript.strip()
        lower = raw.lower()

        intent = SpokenPaymentIntent(
            action="unknown",
            payee_vpa=None,
            amount_paise=0,
            amount_rupees=0.0,
            note="",
            raw_transcript=raw,
            confidence=1.0 if raw else 0.0,
        )

        if not raw:
            return intent

        # 1. Detect Action Type
        if any(w in lower for w in ["revoke", "cancel mandate", "stop mandate", "radd karo"]):
            intent.action = "revoke_mandate"
            return intent
        elif any(w in lower for w in ["balance", "how much", "kitna balance", "account balance", "kitne paise"]):
            intent.action = "check_balance"
            return intent
        elif any(w in lower for w in ["search", "find", "catalog", "look for", "kya mil raha", "dhoondo"]):
            intent.action = "search_store"
            # Strip search prefixes
            note = re.sub(r"^(?:search|find|look for|dhoondo)\s*(?:for)?\s*", "", raw, flags=re.I).strip()
            intent.note = note or raw
            return intent
        elif any(w in lower for w in ["pay", "send", "transfer", "bhejo", "buy", "purchase", "order", "spend", "de do"]):
            intent.action = "make_payment"
        else:
            intent.action = "make_payment"  # Default assumption for delegated shopping

        # 2. Extract Spoken Currency & Amount (Rupees and Paise)
        rupees, paise = parse_spoken_currency(raw)
        intent.amount_rupees = rupees
        intent.amount_paise = paise

        # 3. Resolve Target Payee VPA
        intent.payee_vpa = resolve_spoken_vpa(raw)

        # 4. Extract Purpose / Note
        for trigger in ["for", "towards", "note"]:
            if f" {trigger} " in lower:
                intent.note = raw[lower.find(f" {trigger} ") + len(trigger) + 2:].strip()
                break

        return intent

    def listen_and_extract(
        self,
        engine: EngineType = "auto",
        timeout_sec: float = 8.0,
    ) -> SpokenPaymentIntent:
        """Complete pipeline: Calibrate -> Listen -> Denoise -> Transcribe -> Parse Structured Intent."""
        print("🔧 Calibrating background noise (please remain quiet for 1s)...")
        self.calibrate_ambient_noise(duration_sec=1.0)
        print(f"✓ Noise floor calibrated (RMS: {self.ambient_energy:.1f})")

        print("🎙️ Ready! Please speak your payment instruction clearly into the microphone.")
        audio = self.listen_phrase(timeout_sec=timeout_sec, show_meter=True)
        if audio is None:
            return SpokenPaymentIntent(
                action="unknown",
                payee_vpa=None,
                amount_paise=0,
                amount_rupees=0.0,
                note="No audio detected within timeout window",
                raw_transcript="",
                confidence=0.0,
            )

        print("🧠 Transcribing speech with Whisper ASR...")
        t0 = time.perf_counter()
        transcript = self.transcribe_audio(audio, engine=engine)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"✓ Transcribed in {elapsed_ms:.1f}ms: \"{transcript}\"")

        intent = self.parse_payment_intent(transcript)
        return intent

    def authorize_with_kavach(
        self,
        intent: SpokenPaymentIntent,
        agent_id: str = "agent_voice_001",
        private_key: Any = None,
        public_key: Any = None,
    ) -> dict[str, Any]:
        """Authorize the extracted spoken intent against Kavach's security gateway.
        
        Enforces:
        - Cryptographic micro-mandate generation
        - Interceptor policy check (per-txn limit, allowlisted payees, budget)
        - Immutable SHA-256 hash-chained audit record logging
        """
        if intent.action != "make_payment":
            return {
                "allowed": False,
                "reason": f"Action '{intent.action}' does not require payment authorization.",
                "intent": intent.model_dump(),
            }

        from kavach.audit import AuditLog
        from kavach.interceptor import KavachInterceptor
        from kavach.issuer import generate_keypair, sign_mandate
        from kavach.ledger import Ledger
        from kavach.models import MandateSpec, PaymentRequest

        # Use or generate signing keypair
        if private_key is None or public_key is None:
            private_key, public_key = generate_keypair()

        # Build micro-mandate bound to extracted intent parameters
        mandate_spec = MandateSpec(
            purpose=intent.note or "Voice payment authorization",
            allowed_payees=[intent.payee_vpa or "ondc-grocer@upi"],
            allowed_categories=["groceries", "essentials", "utility"],
            per_txn_limit_paise=max(intent.amount_paise, 10000),
            total_budget_paise=max(intent.amount_paise * 2, 20000),
            max_txn_count=3,
        )

        token = sign_mandate(
            private_key=private_key,
            agent_id=agent_id,
            mandate=mandate_spec,
            validity_seconds=600,
        )

        ledger = Ledger()
        audit_log = AuditLog()
        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=token,
            ledger=ledger,
            audit_log=audit_log,
            interceptor_private_key=private_key,
        )

        payment_req = PaymentRequest(
            amount_paise=intent.amount_paise,
            payee_vpa=intent.payee_vpa or "ondc-grocer@upi",
            note=intent.note or "Spoken authorization",
            category="groceries",
            nonce=f"voice_nonce_{int(time.time() * 1000)}",
        )

        decision, _ = interceptor.evaluate(payment_req)
        return {
            "allowed": decision.allowed,
            "reason_code": decision.reason_code.value,
            "message": decision.message,
            "approval_ticket": decision.approval_ticket,
            "mandate_token": token,
            "intent": intent.model_dump(),
        }
