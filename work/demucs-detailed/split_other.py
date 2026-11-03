"""Complementary HPSS components; these are not independent instruments."""
from pathlib import Path
import librosa
import numpy as np
import soundfile as sf

BASE = Path(__file__).resolve().parent
x, sr = sf.read(BASE / "separated/htdemucs_6s/original_demo/other.wav", dtype="float32", always_2d=True)
spectrum = librosa.stft(x.T, n_fft=2048, hop_length=512)
shared_magnitude = np.mean(np.abs(spectrum), axis=0)
h_mask, p_mask = librosa.decompose.hpss(shared_magnitude, kernel_size=31, power=2, mask=True, margin=1)
out = BASE / "derived"
out.mkdir(exist_ok=True)
for name, mask in [("other_harmonic", h_mask), ("other_percussive", p_mask)]:
    wave = librosa.istft(spectrum * mask[None], hop_length=512, length=len(x)).T
    sf.write(out / f"{name}.wav", wave.astype(np.float32), sr, subtype="FLOAT")
    print(f"SAVED {name}: {wave.shape}", flush=True)
