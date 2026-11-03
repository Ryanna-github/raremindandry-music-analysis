"""Conservative, phase-preserving cleanup guided by local source estimates.

No pitch correction, voice replacement, hard gating or generative gap filling.
"""
import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal, ndimage

BASE = Path(__file__).resolve().parent
SR = 44100
FFT = 2048
HOP = 512


def read(path):
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    assert sr == SR and x.shape[1] == 2 and np.isfinite(x).all(), path
    return x


def model_source(task, keyword):
    data = json.loads((BASE / f"{task}_full/result.json").read_text())
    canonical = lambda name: re.sub(r"[^a-z]", "", name.lower())
    accepted = {canonical(keyword)}
    if task == "denoise" and canonical(keyword) == "nonoise":
        # This checkpoint's YAML calls the clean target 'dry'.
        accepted.add("dry")
    matches = [p for name, p in data["outputs"].items() if canonical(name) in accepted]
    assert len(matches) == 1, (task, keyword, data["outputs"])
    return Path(matches[0])


def stft(x):
    return signal.stft(x, fs=SR, nperseg=FFT, noverlap=FFT-HOP,
                       boundary="zeros", padded=True, axis=0)[2]


def istft(z, length):
    return signal.istft(z, fs=SR, nperseg=FFT, noverlap=FFT-HOP,
                        input_onesided=True, boundary=True,
                        time_axis=-1, freq_axis=0)[1].T[:length].astype(np.float32)


def block_levels(x, step=.01):
    hop = round(SR * step)
    pad = (-len(x)) % hop
    y = np.pad(x, ((0, pad), (0, 0))).reshape(-1, hop, 2)
    power = np.mean(y.astype(np.float64)**2, axis=(1, 2))
    return power, hop


def stabilize(x, strength):
    # One shared stereo gain envelope; don't increase noise during pauses.
    x = signal.sosfilt(signal.butter(2, 55, "highpass", fs=SR, output="sos"), x, axis=0).astype(np.float32)
    power, hop = block_levels(x)
    level = 10*np.log10(ndimage.gaussian_filter1d(power, 12)+1e-12)
    active = level > max(-55, np.max(level)-25)
    target = float(np.median(level[active])) if np.any(active) else -40
    activity = 1/(1+np.exp(np.clip(-(level-target+18)/3, -30, 30)))
    gain_db = np.clip((target-level)*strength, -3, 1.5) * activity
    gain_db = ndimage.gaussian_filter1d(gain_db, 60)
    sample_gain = np.interp(np.arange(len(x)), np.arange(len(gain_db))*hop+hop/2, gain_db)
    return (x*10**(sample_gain[:, None]/20)).astype(np.float32), {
        "gain_db_min": float(gain_db.min()), "gain_db_max": float(gain_db.max()),
        "active_median_dbfs": target, "highpass_hz": 55,
        "dynamic_strength": strength, "envelope_smoothing_seconds": .6,
    }


def cleanup():
    v = read(BASE / "vocals_full/vocals.wav")
    lead = read(BASE / "karaoke_full/vocals.wav")
    backing = read(BASE / "karaoke_full/instrumental.wav")
    accompaniment = read(BASE / "vocals_full/instrumental.wav")
    bs_dry_path = model_source("dereverb", "No Reverb")
    dry_path = model_source("vr_deverb", "Dry")
    noise_free_path = model_source("denoise", "No Noise")
    dry, noise_free = read(dry_path), read(noise_free_path)
    assert all(x.shape == v.shape for x in (lead, backing, dry, noise_free, accompaniment))
    energy = lambda x: np.sqrt(np.mean(x.astype(np.float64)**2))
    retention = {"dereverb_rms_change_db": float(20*np.log10((energy(dry)+1e-12)/(energy(v)+1e-12))),
                 "denoise_rms_change_db": float(20*np.log10((energy(noise_free)+1e-12)/(energy(v)+1e-12))),
                 "bs_roformer_dereverb_rms_change_db": float(20*np.log10((energy(read(bs_dry_path))+1e-12)/(energy(v)+1e-12))),
                 "selected_dereverb_model": "UVR-De-Reverb-aufr33-jarredou.pth"}
    # Large global loss is a warning, not proof that the removed signal is noise.
    deverb_mix = (.10, .20) if retention["dereverb_rms_change_db"] < -10 else (.30, .55)
    noise_mix = (.40, .65) if retention["denoise_rms_change_db"] < -10 else (.70, .90)
    print(f"RETENTION GUARD {retention}, dereverb_mix={deverb_mix}, noise_mix={noise_mix}", flush=True)
    sources = {"all": v, "lead": lead, "backing": backing}
    outputs = {(k, mode): np.zeros_like(v) for k in sources
               for mode in (["balanced", "clarity"] if k != "backing" else ["balanced"])}
    # Context on both sides avoids chunk-edge changes; exact sample timeline kept.
    core, context = 20*SR, SR
    suppress_stats = []
    for start in range(0, len(v), core):
        end = min(len(v), start+core)
        left = (max(0, start-context)//HOP)*HOP
        right = min(len(v), end+context)
        zv, zd, zn, zi = (stft(x[left:right]) for x in (v, dry, noise_free, accompaniment))
        mag = np.sqrt(np.mean(np.abs(zv)**2, axis=1))
        def ratio(z):
            estimate = np.sqrt(np.mean(np.abs(z)**2, axis=1))
            r = np.clip(estimate/(mag+1e-8), 0, 1)
            # Never change the ratio abruptly at frame or frequency boundaries.
            return ndimage.gaussian_filter(r, sigma=(1.5, 2.5), mode="nearest")
        rd, rn = ratio(zd), ratio(zn)
        music_mag = np.sqrt(np.mean(np.abs(zi)**2, axis=1))
        # Conservative interference-dominance cue, not another independent stem.
        music_ratio = mag/np.sqrt(mag**2 + .025*music_mag**2 + 1e-16)
        music_ratio = ndimage.gaussian_filter(music_ratio, sigma=(1.5,2.5), mode="nearest")
        masks = {
            "balanced": (1-noise_mix[0]*(1-np.clip(rn, .35, 1))) * (1-deverb_mix[0]*(1-np.clip(rd, .5, 1))) * (1-.75*(1-np.clip(music_ratio,.5,1))),
            "clarity": (1-noise_mix[1]*(1-np.clip(rn, .18, 1))) * (1-deverb_mix[1]*(1-np.clip(rd, .5, 1))) * (1-.90*(1-np.clip(music_ratio,.3,1))),
        }
        # Preserve the source's own phase and stereo image, not the denoiser's.
        for key, source in sources.items():
            z = zv if key == "all" else stft(source[left:right])
            for mode in (["balanced", "clarity"] if key != "backing" else ["balanced"]):
                mask = masks[mode]
                if key == "backing":
                    mask = .4 + .6*mask  # harmonies and long tails need extra protection
                y = istft(z*mask[:, None, :], right-left)
                outputs[key, mode][start:end] = y[start-left:end-left]
        suppress_stats.append({"start": start/SR,
            "balanced_median_mask": float(np.median(masks["balanced"])),
            "clarity_median_mask": float(np.median(masks["clarity"]))})
        print(f"CLEAN {start/SR:.1f}–{end/SR:.1f}s", flush=True)
    out = BASE / "cleaned"
    out.mkdir(exist_ok=True)
    report = {"sample_rate": SR, "frames": len(v), "outputs": {},
        "guides": {"dereverb": str(dry_path), "denoise": str(noise_free_path)},
        "method": "Shared stereo soft spectral attenuation guided by learned denoise/dereverb estimates; original phase retained",
        "stft": {"fft": FFT, "hop": HOP, "chunk_seconds": 20, "context_seconds": 1},
        "balanced": {"noise_ratio_floor": .35, "noise_mix": noise_mix[0], "dereverb_ratio_floor": .5, "dereverb_mix": deverb_mix[0]},
        "clarity": {"noise_ratio_floor": .18, "noise_mix": noise_mix[1], "dereverb_ratio_floor": .5, "dereverb_mix": deverb_mix[1]},
        "retention_guard": retention,
        "accompaniment_guidance": {"path": str(BASE / "vocals_full/instrumental.wav"),
            "energy_weight": .025, "balanced_ratio_floor": .5, "balanced_mix": .75,
            "clarity_ratio_floor": .3, "clarity_mix": .9,
            "warning": "May also reduce quiet vocal details overlapping loud instruments; masks have nonzero floors"},
        "hard_gate": False, "pitch_correction": False, "generative_filling": False,
        "suppression_chunks": suppress_stats}
    for (key, mode), y in outputs.items():
        y, dynamics = stabilize(y, .2 if mode == "balanced" else .28)
        assert y.shape == v.shape and np.isfinite(y).all()
        path = out / f"{key}_{mode}.wav"
        sf.write(path, y, SR, subtype="FLOAT")
        report["outputs"][f"{key}_{mode}"] = {"path": str(path), "dynamics": dynamics}
    (out / "processing.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("CLEANUP COMPLETE", flush=True)


if __name__ == "__main__":
    cleanup()
