"""Check delivered audio timelines, clipping and archive integrity."""
import json
import argparse
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "outputs/instrument_demo"
parser = argparse.ArgumentParser()
parser.add_argument("--skip-archives", action="store_true")
args = parser.parse_args()
report = json.loads((OUT / "manifest.json").read_text())
assert report.get("playback_speed_default") == 1
assert len({t["id"] for t in report["tracks"]}) == len(report["tracks"])
checks = []
for track in report["tracks"]:
    entry = {"id": track["id"], "files": []}
    if "levels" in track and len(track["levels"]) > 1 and track.get("comparison_level_policy") != "shared_gain_original_reference":
        groups = [["raw", "enhanced"], ["lead", "lead_clean"]] if track.get("scope") == "candidates" else [list(track["levels"])]
        for group in groups:
            matched = [track["levels"][key]["listening_active_rms_dbfs"] for key in group if key in track["levels"] and "listening_active_rms_dbfs" in track["levels"][key]]
            if matched:assert max(matched) - min(matched) < .02, (track["id"], matched)
    lossless = list(track.get("lossless", {}).items()) or [("raw_wav", track["raw_wav"]), ("enhanced_wav", track["enhanced_wav"])]
    for mode, relative in [*lossless, *track["audio"].items()]:
        path = OUT / relative
        x, sr = sf.read(path, dtype="float32", always_2d=True)
        expected = track["duration"]
        duration = len(x) / sr
        peak = float(np.max(np.abs(x)))
        assert sr == 44100 and x.shape[1] == 2, (path, sr, x.shape)
        assert abs(duration - expected) < 0.25, (path, duration)
        assert np.isfinite(x).all(), path
        assert peak < 1.001, (path, peak)
        assert "_slow" not in path.name, path
        entry["files"].append({"file": relative, "duration_seconds": round(duration, 4), "peak": round(peak, 6)})
    checks.append(entry)
archives=["instrument_demo_listening.zip", "instrument_demo_lossless.zip"]
if (ROOT / "outputs/vocal_continuity_repair.zip").exists():archives.append("vocal_continuity_repair.zip")
for name in ([] if args.skip_archives else archives):
    with zipfile.ZipFile(ROOT / "outputs" / name) as archive:
        assert archive.testzip() is None, name
        names = set(archive.namelist())
        assert "instrument_demo/index.html" in names
        assert not any(name.endswith("_slow.mp3") for name in names)
        archived_report = json.loads(archive.read("instrument_demo/manifest.json"))
        for track in archived_report["tracks"]:
            for path in track["audio"].values():
                assert "instrument_demo/" + path in names
            if "lossless" in name or "continuity_repair" in name:
                for path in track.get("lossless", {}).values():
                    assert "instrument_demo/" + path in names
    print(f"Verified {name}", flush=True)
result = {"passed": True, "listening_items": len(checks), "verified_audio_files": sum(len(c["files"]) for c in checks),
          "checks": checks}
(OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(f"Verified {len(checks)} items / {result['verified_audio_files']} audio files; timings, stereo and clipping checks passed.")
