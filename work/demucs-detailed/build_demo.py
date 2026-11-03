"""Create portable local listening demo and documented lossless stems."""
import argparse
import json
import math
import subprocess
import zipfile
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent.parent
OUT = ROOT / "outputs" / "instrument_demo"
SR = 44100
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

PROFILES = {
    "original": "anull",
    "bass": "highpass=f=25,lowpass=f=4500,equalizer=f=750:t=q:w=0.8:g=2",
    "drums": "highpass=f=30,lowpass=f=17000,equalizer=f=2500:t=q:w=0.8:g=1.5",
    "guitar": "highpass=f=65,lowpass=f=12000,equalizer=f=1800:t=q:w=0.7:g=2",
    "piano": "anull",
    "vocals": "highpass=f=65,lowpass=f=15000,equalizer=f=2200:t=q:w=0.8:g=1.5",
    "other": "highpass=f=35,lowpass=f=16000",
    "kick": "highpass=f=25,lowpass=f=6500,equalizer=f=2800:t=q:w=0.8:g=1.5",
    "snare": "highpass=f=75,lowpass=f=14000,equalizer=f=2200:t=q:w=0.8:g=1.5",
    "toms": "highpass=f=35,lowpass=f=12000",
    "hh": "highpass=f=180,lowpass=f=17000",
    "ride": "highpass=f=120,lowpass=f=17000",
    "crash": "highpass=f=100,lowpass=f=17000",
}


def rms_db(x):
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12))


def normalize(x, target=-20, max_gain=14):
    db = rms_db(x)
    gain = min(target - db, max_gain)
    # Very weak estimates are intentionally left quiet to avoid implying a real instrument.
    if db < -48:
        gain = 0
    peak = float(np.max(np.abs(x)))
    if peak > 0:
        gain = min(gain, 20 * math.log10(0.84 / peak))
    return (x * 10 ** (gain / 20)).astype(np.float32), gain


def ffmpeg(*args):
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *map(str, args)], check=True)


def collect():
    six = BASE / "separated" / "htdemucs_6s" / "original_demo"
    tracks = [{"id": "original", "label": "原曲", "path": ROOT / "work/demucs-demo/original_demo.wav",
               "profile": "original", "source": "原视频音轨", "group": "参考", "note": "原视频 00:45–01:30"}]
    labels = {"guitar": "吉他", "bass": "贝斯", "drums": "鼓组", "other": "其他乐器",
              "vocals": "全部人声 · Demucs", "piano": "钢琴 · 弱信号参考"}
    for key in ["guitar", "bass", "drums", "other", "vocals", "piano"]:
        tracks.append({"id": key, "label": labels[key], "path": six / f"{key}.wav", "profile": key,
                       "source": "htdemucs_6s · shifts=4 · overlap=0.5", "group": "基础六轨", "note": ""})
    second = BASE / "separated" / "instrumental_pass" / "htdemucs_6s" / "instrumental"
    for key in ["guitar", "other"]:
        if (second / f"{key}.wav").exists():
            tracks.append({"id": f"{key}_second_pass", "label": {"guitar": "吉他", "other": "其他乐器"}[key] + " · 先去人声再分离",
                           "path": second / f"{key}.wav", "profile": key,
                           "source": "RoFormer 去人声 → htdemucs_6s · shifts=4 · overlap=0.5", "group": "乐器二次分离",
                           "note": "用于与直接分离对比；二次处理可能减少人声串音，也可能损失乐器细节。"})
    vocals = BASE / "specialists/vocals/vocals.wav"
    if vocals.exists():
        tracks.append({"id": "vocals_roformer", "label": "全部人声 · RoFormer", "path": vocals,
                       "profile": "vocals", "source": "Mel-Band RoFormer ep3005", "group": "人声细分",
                       "note": "可与 Demucs 人声同位置对比；模型结果仍可能含串音。"})
    karaoke = BASE / "specialists/karaoke"
    if (karaoke / "vocals.wav").exists():
        tracks.append({"id": "lead", "label": "主唱 · 估计", "path": karaoke / "vocals.wav",
                       "profile": "vocals", "source": "Mel-RoFormer Karaoke aufr33-viperx", "group": "人声细分",
                       "note": "从全部人声再次分离；不能保证主唱完整或完全排除和声。"})
        residual = karaoke / "instrumental.wav"
        if not residual.exists():
            residual = karaoke / "residual.wav"
        if residual.exists():
            tracks.append({"id": "backing", "label": "背景人声 / 和声 · 估计", "path": residual,
                           "profile": "vocals", "source": "Karaoke 分离的人声残差", "group": "人声细分",
                           "note": "包含和声估计，也可能有主唱尾音、混响和乐器残留；不等于独立和声原轨。"})
    drum_names = {"kick": "底鼓", "snare": "军鼓", "toms": "通鼓", "tom": "通鼓", "hh": "踩镲",
                  "hihat": "踩镲", "hi_hat": "踩镲", "ride": "叮叮镲", "crash": "吊镲"}
    for path in sorted((BASE / "specialists/drums").glob("*.wav")):
        key = path.stem.lower()
        tracks.append({"id": f"drum_{key}", "label": drum_names.get(key, key), "path": path,
                       "profile": key if key in PROFILES else "drums", "source": "MDX23C DrumSep aufr33-jarredou",
                       "group": "鼓组细分", "note": "从六轨鼓组再次分离；弱信号可能为其他鼓件的残留。"})
    ft = BASE / "separated/htdemucs_ft/original_demo"
    for key in ["bass", "drums", "vocals"]:
        if (ft / f"{key}.wav").exists():
            tracks.append({"id": f"{key}_ft", "label": {"bass": "贝斯", "drums": "鼓组", "vocals": "全部人声"}[key] + " · FT 对照",
                           "path": ft / f"{key}.wav", "profile": key, "source": "htdemucs_ft · shifts=2 · overlap=0.5",
                           "group": "模型对照", "note": "另一模型估计，可在相同位置切换对比。"})
    for key, label in [("other_harmonic", "其他乐器 · 持续音 / 和声成分"), ("other_percussive", "其他乐器 · 瞬态 / 打击成分")]:
        path = BASE / f"derived/{key}.wav"
        if path.exists():
            tracks.append({"id": key, "label": label, "path": path, "profile": "other", "source": "其他轨的 HPSS 信号分解",
                           "group": "其他轨细分", "note": "按持续音和瞬态分解，不代表两种独立乐器。"})
    return tracks


def package_demo():
    for name, include_wav in [("instrument_demo_listening.zip", False), ("instrument_demo_lossless.zip", True)]:
        archive = ROOT / "outputs" / name
        temporary = archive.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            for p in sorted(OUT.rglob("*")):
                if p.is_file() and not p.name.endswith("_slow.mp3") and (include_wav or "lossless" not in p.parts):
                    z.write(p, Path("instrument_demo") / p.relative_to(OUT))
        with zipfile.ZipFile(temporary) as z:
            if z.testzip() is not None:
                raise ValueError(f"Archive integrity failure: {temporary}")
        temporary.replace(archive)
        print(f"ARCHIVE {archive.name}: {archive.stat().st_size / 1024 ** 2:.1f}MB", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-archives", action="store_true")
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    if args.package_only:
        package_demo()
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for dirname in ["audio", "lossless/raw", "lossless/enhanced"]:
        (OUT / dirname).mkdir(parents=True, exist_ok=True)
    (BASE / "processed").mkdir(exist_ok=True)
    manifest = []
    for track in collect():
        key = track["id"]
        x, sr = sf.read(track["path"], dtype="float32", always_2d=True)
        if sr != SR or x.shape != (1984500, 2) or not np.isfinite(x).all():
            raise ValueError(f"Invalid input {key}: {sr} {x.shape}")
        db = rms_db(x)
        raw_path = OUT / "lossless/raw" / f"{key}.wav"
        # All analytical source estimates remain available without EQ or gain changes.
        raw_peak = float(np.max(np.abs(x)))
        raw_scale = min(1.0, 0.98 / max(raw_peak, 1e-12))
        sf.write(raw_path, x * raw_scale, SR, subtype="PCM_24")
        filtered_path = BASE / "processed" / f"{key}_filtered.wav"
        profile = PROFILES[track["profile"]] if db >= -48 else "anull"
        ffmpeg("-i", track["path"], "-af", profile, "-c:a", "pcm_f32le", filtered_path)
        filtered, fsr = sf.read(filtered_path, dtype="float32", always_2d=True)
        enhanced, gain = normalize(filtered)
        matched_raw, raw_gain = normalize(x)
        enhanced_wav = OUT / "lossless/enhanced" / f"{key}.wav"
        matched_path = BASE / "processed" / f"{key}_matched.wav"
        sf.write(enhanced_wav, enhanced, SR, subtype="PCM_24")
        sf.write(matched_path, matched_raw, SR, subtype="PCM_24")
        audio_files = {mode: f"audio/{key}_{mode}.mp3" for mode in ["raw", "enhanced"]}
        ffmpeg("-i", matched_path, "-c:a", "libmp3lame", "-b:a", "256k", OUT / audio_files["raw"])
        ffmpeg("-i", enhanced_wav, "-c:a", "libmp3lame", "-b:a", "256k", OUT / audio_files["enhanced"])
        track.pop("path")
        track.update({"audio": audio_files, "duration": 45, "sample_rate": SR, "source_rms_db": round(db, 2),
                      "enhanced_rms_db": round(rms_db(enhanced), 2), "gain_db": round(gain, 2), "raw_listen_gain_db": round(raw_gain, 2),
                      "raw_archive_scale": raw_scale, "eq": profile, "weak": db < -48,
                      "raw_wav": f"lossless/raw/{key}.wav", "enhanced_wav": f"lossless/enhanced/{key}.wav"})
        if track["weak"]:
            track["note"] += " 原始信号极弱，未强行提升电平；不能作为该乐器存在的证据。"
        manifest.append(track)
        print(f"BUILT {key}: input={db:.1f}dBFS gain={gain:.1f}dB", flush=True)
    report = {"source_video": "local_input.mov", "excerpt_start_seconds": 45,
              "excerpt_end_seconds": 90, "tracks": manifest,
              "enhancement": "适度均衡 + 有上限的电平提升 + 峰值留余量；未修音、未生成或补造音符。",
              "playback_speed_default": 1,
              "slow": "只在播放器中手动调速，不预先导出降速音频。",
              "listening_raw": "播放器中的原始分轨只做电平匹配；未经处理的模型输出见 lossless/raw。"}
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    template = (BASE / "player_template.html").read_text()
    (OUT / "index.html").write_text(template.replace("__MANIFEST__", json.dumps(report, ensure_ascii=False)))
    rows = ["|音轨|原速增强 MP3|原始 WAV|", "|---|---|---|"]
    for t in manifest:
        rows.append(f"|{t['label']}|[试听]({t['audio']['enhanced']})|[下载]({t['raw_wav']})|")
    readme = "\n".join([
        "# 乐器分离与扒谱试听 demo", "", "片段：原视频 00:45–01:30，共45秒。全部音频在本地处理。", "",
        "解压后用浏览器打开 index.html。选择音轨，可以在同一位置切换原始/增强、保持音高降速，或设置循环。", "",
        "增强采用温和的乐器专用 EQ、有限电平提升和峰值余量。没有自动修音，也没有生成式补全，以免改变需要扒谱的音符。",
        "播放器的原始/增强版本尽量匹配平均电平；需要未经处理的模型估计请使用 lossless/raw 中的24位 WAV。",
        "仅 MP3 试听包不含 WAV；下表的 WAV 链接需要下载并解压无损包后使用。",
        "默认原速播放；需要降速时手动调整播放器，不预先生成降速文件。", "",
        "轨名是模型目标类别，不代表歌曲中必然存在该乐器。钢琴轨极弱；主唱/背景人声为估计，可能有尾音、混响和串音。",
        "基础六轨、鼓组细分、人声细分及模型对照相互重叠，不应把所有文件一起叠加当作原曲。", "",
        *rows, "", "详细模型、均衡参数与电平信息见 manifest.json。",
    ])
    (OUT / "README.md").write_text(readme)
    if not args.skip_archives:
        package_demo()


if __name__ == "__main__":
    main()
