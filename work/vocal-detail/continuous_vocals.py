"""Reusable, local-only continuity-first vocal workflow for NEW media.

python continuous_vocals.py --input song.mov --out outputs/new_song
or --all-vocals aligned.wav --lead aligned.wav --guide aligned.wav --out ...
Never overwrites the existing demo; output directory must be new.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from scipy import ndimage, signal

from clean_vocals import read, stabilize, SR
from repair_continuity import transform, inverse, track_tonal_ridge, FFT, HOP

BASE = Path(__file__).resolve().parent
DEFAULTS = BASE / "processing_defaults.json"


def levels(x, hop):
    padded = np.pad(x, ((0,(-len(x)) % hop),(0,0)))
    return np.sqrt(np.mean(padded.reshape(-1,hop,2).astype(float)**2,axis=(1,2)))


def find_candidates(all_v, lead, guide, cfg):
    hop = round(SR*cfg['candidate_step_seconds'])
    av, lv, gv = (levels(x,hop) for x in (all_v,lead,guide))
    active = av > max(10**(-55/20), float(np.percentile(av,95)) * .06)
    present = (lv/(av+1e-10) > .45) & active
    radius = round(cfg['neighbor_seconds']/cfg['candidate_step_seconds'])
    # Require nearby preceding lead evidence. The phrase may end in an effect
    # and never return to the primary model; a following lead is not mandatory.
    before = ndimage.maximum_filter1d(present.astype(int),radius+1,origin=radius//2)>0
    candidate = active & before & (lv/(av+1e-10)<cfg['lead_to_all_max_ratio']) & (gv/(av+1e-10)>cfg['guide_to_all_min_ratio'])
    candidate = ndimage.binary_closing(candidate,structure=np.ones(3))
    labels, n = ndimage.label(candidate)
    windows=[]
    for label in range(1,n+1):
        idx=np.flatnonzero(labels==label)
        if len(idx)*hop/SR < cfg['min_candidate_seconds']: continue
        start=max(0,idx[0]*hop/SR-.25); end=min(len(all_v)/SR,(idx[-1]+1)*hop/SR+.25)
        if windows and start<=windows[-1][1]+.25: windows[-1][1]=end
        else: windows.append([start,end])
    return windows


def repair_aligned(all_v, lead, guide, cfg):
    if not(all_v.shape == lead.shape == guide.shape):
        raise ValueError('All input stems must share stereo shape, sample rate and time alignment')
    clean, dynamic = stabilize(lead, .2)
    repaired=clean.copy(); windows=find_candidates(all_v,lead,guide,cfg) if cfg.get('enabled',True) else []; accepted=[]
    for start,end in windows:
        lo=max(0,round((start-cfg['context_seconds'])*SR)); hi=min(len(all_v),round((end+cfg['context_seconds'])*SR))
        zv,zl,zg=(transform(x[lo:hi]) for x in (all_v,lead,guide))
        mag=lambda z:np.sqrt(np.mean(np.abs(z)**2,axis=1))
        av,lv,gv=map(mag,(zv,zl,zg)); ratio=np.clip(gv/(av+1e-8),0,1)
        freq=np.fft.rfftfreq(FFT,1/SR)
        f0,ridge,confidence=track_tonal_ridge(av,ratio,freq,
            (cfg['pitch_midi_min'],cfg['pitch_midi_max']),cfg['pitch_midi_step'])
        # Harmonic evidence is mandatory; guide disagreement alone can be backing vocals.
        missing=np.clip((1-lv/(av+1e-8)-.1)/.6,0,1)
        mask=np.minimum(cfg['max_refill_mask'],ridge*np.clip((ratio-.06)/.6,0,1)*missing)
        mask*=np.clip((freq-55)/45,0,1)[:,None]
        mask=ndimage.gaussian_filter(mask,(.8,3))
        delta=inverse((zv-zl)*mask[:,None,:],hi-lo)
        delta=signal.sosfilt(signal.butter(2,55,'highpass',fs=SR,output='sos'),delta,axis=0).astype('float32')
        times=(lo+np.arange(len(delta)))/SR; fade=cfg['crossfade_seconds']
        window=np.minimum(np.clip((times-start)/fade,0,1),np.clip((end-times)/fade,0,1))
        window=.5-.5*np.cos(np.pi*window)
        delta*=window[:,None]
        if np.sqrt(np.mean(delta.astype(float)**2))<1e-7: continue
        repaired[lo:hi]+=delta
        accepted.append({'start_seconds':start,'end_seconds':end,
                         'median_ridge_hz':float(np.median(f0)),
                         'guide_confidence_median':float(np.median(confidence)),
                         'classification':'model_disagreement_candidate_not_verified_singer_or_effect'})
    changed=np.any(repaired!=clean,axis=1)
    allowed=np.zeros(len(clean),bool)
    for start,end in windows: allowed[round(start*SR):round(end*SR)+1]=True
    assert not np.any(changed & ~allowed)
    assert np.isfinite(repaired).all()
    return repaired,clean,{'candidate_windows':windows,'accepted_windows':accepted,'dynamic_cleanup':dynamic,
        'outside_candidate_sample_identical':True,'guide_waveform_spliced':False,'pitch_correction':False,
        'generative_filling':False,'subjective_quality_verified':False,
        'limitations':'Conservative candidate recovery may still leak harmonies, miss unvoiced sounds or leave long gaps; it never invents notes.'}


def file_hash(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def separate(task, source, out):
    runner=BASE.parent/'demucs-detailed/run_specialists.py'
    subprocess.run([sys.executable,str(runner),task,'--input',str(source),'--out',str(out)],check=True)
    return json.loads((out/'result.json').read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--all-vocals',type=Path);parser.add_argument('--lead',type=Path);parser.add_argument('--guide',type=Path)
    parser.add_argument('--out',required=True,type=Path);parser.add_argument('--config',type=Path,default=DEFAULTS)
    args=parser.parse_args();config=json.loads(args.config.read_text())
    if args.out.exists(): parser.error('Use a NEW output directory; existing files are not overwritten.')
    if bool(args.input)==bool(args.all_vocals): parser.error('Choose --input OR three aligned stems.')
    if not args.input and not(args.lead and args.guide): parser.error('Both --lead and --guide are required with --all-vocals.')
    args.out.mkdir(parents=True);out=args.out.resolve()
    if args.input:
        media=args.input.resolve();source=out/'original.wav'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-i',str(media),
                        '-vn','-ar','44100','-ac','2','-c:a','pcm_f32le',str(source)],check=True)
        v=separate('vocals',source,out/'models/vocals');all_path=Path(v['outputs']['Vocals'])
        k=separate('karaoke',all_path,out/'models/karaoke');lead_path=Path(k['outputs']['Vocals'])
        b=separate('bve',all_path,out/'models/bve')
        if not b['model_data'].get('is_bv_model'): raise ValueError('BVE semantic mapping must be verified.')
        guide_path=Path(b['outputs']['Instrumental'])
    else: all_path,lead_path,guide_path=(p.resolve() for p in (args.all_vocals,args.lead,args.guide))
    paths=[all_path,lead_path,guide_path];before={str(p):file_hash(p) for p in paths}
    repaired,raw,analysis=repair_aligned(*(read(p) for p in paths),config['continuity'])
    # Save FLOAT WAV for exact provenance, use shared gain in listening exports.
    sf.write(out/'lead_original_clean.wav',raw,SR,subtype='FLOAT')
    sf.write(out/'lead_continuous.wav',repaired,SR,subtype='FLOAT')
    scale=min(1,.85/max(float(np.max(np.abs(raw))),float(np.max(np.abs(repaired))),1e-12))
    for name,x in [('lead_original_clean',raw),('lead_continuous',repaired)]:
        sf.write(out/f'{name}.flac',x*scale,SR,subtype='PCM_24')
    analysis.update({'config':config,'source_sha256':before,'input_files_unchanged':all(file_hash(p)==h for p,h in zip(paths,before.values())),
                     'shared_listening_gain_db':float(20*np.log10(scale)), 'frames':len(raw),'sample_rate':SR})
    (out/'processing.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2))
    build_new_demo(out,all_path,lead_path,guide_path,raw,repaired,scale,analysis,config)
    print(json.dumps({'output':str(out),'candidates':analysis['candidate_windows'],'accepted':len(analysis['accepted_windows'])},indent=2))


def build_new_demo(out,all_path,lead_path,guide_path,clean,repaired,scale,analysis,config):
    from build_audio_insights import enrich_report,expected_lead
    all_v,lead=read(all_path),read(lead_path)
    waves={'vocal_all':{'raw':all_v},'vocal_lead':{'raw':lead,'enhanced':clean},
           'vocal_lead_continuous':{'raw':clean,'enhanced':repaired}}
    labels={'vocal_all':'全部人声 · 原始参考','vocal_lead':'主唱 · 原始 / 保守清理','vocal_lead_continuous':'主唱 · 连续保留'}
    # One gain for all versions. Write new files only, never replace input stems.
    shared=min(scale,.85/max(float(np.max(np.abs(all_v))),float(np.max(np.abs(lead))),1e-12))
    audio_dir=out/'audio';audio_dir.mkdir();lossless_dir=out/'lossless';lossless_dir.mkdir();tracks=[]
    for key,versions in waves.items():
        t={'id':key,'label':labels[key],'scope':'vocal','group':'连续性优先 · 新音频','source':'本地模型分离 + 原残差谐波回填',
           'video_start_seconds':0,'duration':len(all_v)/SR,'audio':{},'lossless':{},'listening_gain_db':float(20*np.log10(shared)),
           'comparison_level_policy':'shared_gain_original_reference','mode_labels':{'raw':'原始来源 / 清理前对照','enhanced':'连续保留' if key.endswith('continuous') else '保守清理'},
           'note':'自动检测只识别分歧候选，不确认歌手身份或特效。回填来自原信号，可能保留部分和声 / 背景；原版保留。',
           'processing_note':'默认原速。不生成音符、不修音、不拉伸；所有版本共享试听增益。'}
        for mode,x in versions.items():
            filename=f'{key}_{mode}';wav=lossless_dir/f'{filename}.wav';sf.write(wav,x*shared,SR,subtype='PCM_24')
            mp3=audio_dir/f'{filename}.mp3'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-i',str(wav),'-c:a','libmp3lame','-b:a','256k',str(mp3)],check=True)
            t['audio'][mode]=str(mp3.relative_to(out));t['lossless'][mode]=str(wav.relative_to(out))
        t['weak']=float(np.sqrt(np.mean(next(iter(versions.values())).astype(float)**2)))<10**(-48/20)
        tracks.append(t)
    report={'tracks':tracks,'default_track':'vocal_lead_continuous','playback_speed_default':1,
            'intro':f'新音频 · {len(all_v)/SR:.2f} 秒 · 默认连续性保留、原速试听。',
            'vocal_evaluation':{'report':'README.md'}}
    report=enrich_report(report,out,expected_lead([all_path,lead_path,guide_path]))
    report['processing_defaults']=config
    (out/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (out/'index.html').write_text((BASE/'player_template.html').read_text().replace('__MANIFEST__',json.dumps(report,ensure_ascii=False)))
    (out/'README.md').write_text('# 新音频 · 连续性优先\n\n打开 index.html，默认原速。所有处理本地完成，保留原始 / 保守对照。真实波形和逐版代理评分见页面，完整规则见 AUDIO_EVALUATION.md。\n\n' +
        '自动回填只有谐波与另一模型支持时进行，可能漏掉辅音、气声或长缺口，不能保证纯净主唱。当前的分歧候选与已执行窗口见 processing.json；候选不等于确定的电音 / 歌手切换。\n')


if __name__=='__main__': main()
