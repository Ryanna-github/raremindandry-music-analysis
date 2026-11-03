"""Find listening candidates, not effect classifications or speaker identities."""
import json
from pathlib import Path

import numpy as np
from scipy import signal, ndimage

from clean_vocals import read, model_source, SR

BASE = Path(__file__).resolve().parent


def rms_blocks(x):
    pad = (-len(x)) % SR
    y = np.pad(x, ((0,pad),(0,0))).reshape(-1, SR, 2)
    return np.sqrt(np.mean(y.astype(np.float64)**2, axis=(1,2))+1e-15)


def bve_paths():
    report = json.loads((BASE / "bve_full/result.json").read_text())
    data, sources = report["model_data"], report["outputs"]
    primary = data["primary_stem"]
    secondary = next(name for name in sources if name != primary)
    # UVR's BVE semantic routing: Vocals is BACKING when is_bv_model=true.
    primary_is_backing = (primary == "Vocals") == bool(data.get("is_bv_model", False))
    return (sources[secondary], sources[primary]) if primary_is_backing else (sources[primary], sources[secondary])


def analyze():
    v = read(BASE / "vocals_full/vocals.wav")
    lead = read(BASE / "karaoke_full/vocals.wav")
    backing = read(BASE / "karaoke_full/instrumental.wav")
    # Exact matching prevents selecting No Crowd alongside Crowd.
    crowd_report = json.loads((BASE / "crowd_full/result.json").read_text())
    crowd_path = next(path for name,path in crowd_report["outputs"].items() if name.lower().replace(" ", "") == "crowd")
    crowd = read(crowd_path)
    dry = read(model_source("vr_deverb", "Dry"))
    bs_dry = read(model_source("dereverb", "No Reverb"))
    noise_free = read(model_source("denoise", "No Noise"))
    alt_lead_path, alt_backing_path = bve_paths()
    alt_lead, alt_backing = read(alt_lead_path), read(alt_backing_path)
    original = read(BASE / "original_full.wav")
    waves = {"all_vocals":v,"lead":lead,"backing":backing,"crowd_estimate":crowd,
             "dry_estimate":dry,"bs_dry_estimate":bs_dry,"denoise_residual":v-noise_free,"reverb_residual":v-dry,
             "bve_lead":alt_lead,"bve_backing":alt_backing,"original_mix":original}
    assert all(x.shape == v.shape for x in waves.values())
    energies = {key:rms_blocks(x) for key,x in waves.items()}
    n = len(energies["all_vocals"])
    vdb = 20*np.log10(energies["all_vocals"]+1e-12)
    active = vdb > max(-48, np.percentile(vdb,90)-22)
    ratio = lambda a,b: 20*np.log10((energies[a]+1e-8)/(energies[b]+1e-8))
    backing_ratio, alt_backing_ratio = ratio("backing","all_vocals"),ratio("bve_backing","all_vocals")
    crowd_ratio = ratio("crowd_estimate","original_mix")
    wet_ratio = ratio("reverb_residual","all_vocals")
    noise_ratio = ratio("denoise_residual","all_vocals")
    side = rms_blocks(np.repeat(((v[:,0]-v[:,1])/2)[:,None],2,axis=1))
    width_db = 20*np.log10((side+1e-8)/(energies["all_vocals"]+1e-8))
    # Log-band spectral changes are listening cues, not Auto-Tune/vocoder detectors.
    small = signal.resample_poly(v,1,4,axis=0)
    small_sr=SR//4
    freqs=np.fft.rfftfreq(small_sr,1/small_sr)
    edges=np.geomspace(65,small_sr/2,19)
    shapes=[]; centroids=[]; flatness=[]
    for i in range(n):
        chunk=small[i*small_sr:(i+1)*small_sr]
        chunk=np.pad(chunk,((0,max(0,small_sr-len(chunk))),(0,0)))
        power=np.mean(np.abs(np.fft.rfft(chunk*np.hanning(small_sr)[:,None],axis=0))**2,axis=1)
        bands=np.array([power[(freqs>=a)&(freqs<b)].sum() for a,b in zip(edges[:-1],edges[1:])])+1e-8
        shapes.append(np.log(bands/bands.sum()+1e-6))
        centroids.append(float(np.sum(freqs*power)/(power.sum()+1e-12)))
        p=power[(freqs>150)&(freqs<5000)]+1e-10
        flatness.append(float(np.exp(np.mean(np.log(p)))/np.mean(p)))
    shapes=ndimage.gaussian_filter1d(np.array(shapes),1,axis=0)
    change=np.r_[0,np.linalg.norm(np.diff(shapes,axis=0),axis=1)]
    metrics={"vocal_dbfs":vdb,"backing_to_vocal_db":backing_ratio,
             "bve_backing_to_vocal_db":alt_backing_ratio,
             "backing_classification_disagreement_db":np.abs(backing_ratio-alt_backing_ratio),
             "crowd_to_mix_db":crowd_ratio,"reverb_residual_to_vocal_db":wet_ratio,
             "denoise_residual_to_vocal_db":noise_ratio,"side_to_vocal_db":width_db,
             "spectral_centroid_hz_below_5512":np.array(centroids),
             "spectral_flatness_150_5000":np.array(flatness),"spectral_change_score":change}
    def zscore(a):
        middle=np.median(a[active]);spread=np.percentile(a[active],75)-np.percentile(a[active],25)
        return np.clip((a-middle)/max(spread,.1),-4,4)
    scores=[
        ("较单一主唱 · 参考候选",-zscore(backing_ratio)-.5*zscore(alt_backing_ratio),"主唱占比较高的自动候选；不证明只有一人。"),
        ("和声 / 叠唱 · 候选",zscore(backing_ratio)+.5*zscore(alt_backing_ratio),"两种背景人声拆分的相对能量较高；也可能包含混响和主唱尾音。"),
        ("人群 / 宽带声音 · 候选",zscore(crowd_ratio),"Crowd 模型残差相对较多；可能误收鼓、伴奏或和声，不是观众声的确认。"),
        ("主唱 / 和声归类分歧 · 候选",zscore(np.abs(backing_ratio-alt_backing_ratio)),"两种模型对主唱 / 背景的归类差异较大；先回听原曲，不能直接认定是和声或人声特效。"),
        ("音色 / 处理变化 · 候选 A",zscore(change),"谱形变化较明显；可能来自换唱法、叠唱、乐器串入或特效，未确认特效类型。"),
        ("音色 / 处理变化 · 候选 B",zscore(change)+.35*zscore(np.array(flatness)),"另一处谱形变化候选；不是 Auto-Tune、声码器或失真的自动判定。"),
    ]
    selected=[];duration=len(v)/SR
    for label,score,note in scores:
        averaged=ndimage.gaussian_filter1d(score,1.5)
        eligible=active.copy()
        eligible[:6]=False;eligible[-6:]=False
        for c in selected:
            eligible &= np.abs(np.arange(n)-c["center_seconds"])>=14
        if not np.any(eligible):
            continue
        index=int(np.argmax(np.where(eligible,averaged,-1e6)))
        start=max(0,min(duration-12,float(index)-6))
        selected.append({"label":label,"note":note,"center_seconds":index,
            "start_seconds":start,"end_seconds":min(duration,start+12),
            "score":round(float(averaged[index]),3),
            "metrics_at_center":{k:round(float(a[index]),4) for k,a in metrics.items()},
            "auditory_verified":False,"confirmed_effect":None})
    selected.sort(key=lambda c:c["start_seconds"])
    for index,c in enumerate(selected,1):c["id"]=f"vocal_candidate_{index}"
    result={"duration_seconds":duration,"frames":len(v),"step_seconds":1,
        "algorithmic_only":True,"no_ground_truth_stems":True,
        "speaker_count_estimated":False,"effect_types_confirmed":False,
        "bve_semantic_mapping":{"lead":alt_lead_path,"backing":alt_backing_path},
        "crowd_semantic_mapping":{"crowd":crowd_path,"primary_stem":crowd_report["model_data"]["primary_stem"]},
        "summary_rms_dbfs":{k:float(20*np.log10(np.sqrt(np.mean(x.astype(np.float64)**2))+1e-12)) for k,x in waves.items()},
        "metrics":{k:[round(float(x),4) for x in a] for k,a in metrics.items()},"candidates":selected}
    (BASE / "analysis.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({"rms":result["summary_rms_dbfs"],"candidates":selected},ensure_ascii=False,indent=2),flush=True)


if __name__=="__main__":analyze()
