"""Two-stage LOCAL workflow. By default, only full-song vocals + instruments.

initialize_audio.py --input song.mov --out outputs/song
initialize_audio.py --out outputs/song --expand-vocals

Optional second stage is explicit, cached and leaves originals intact.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
import imageio_ffmpeg
import numpy as np
import soundfile as sf

from clean_vocals import read,stabilize,SR
from build_audio_insights import enrich_report,frames_rms
from build_hierarchical_demo import export_instrument,attach_hierarchy,gain_for
from continuous_vocals import repair_aligned,find_candidates

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent.parent
FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()


def specialist(task,source,out):
    sentinel=out/'result.json'
    if not sentinel.exists():subprocess.run([sys.executable,str(ROOT/'work/demucs-detailed/run_specialists.py'),task,'--input',str(source),'--out',str(out)],check=True)
    return json.loads(sentinel.read_text())


def export_vocal(out,key,label,waves,role='detail',source_type='lead',default='raw',note=''):
    gains={m:gain_for(x) for m,x in waves.items()}
    if role=='detail' and source_type=='lead':
        g=gains['raw']
        for m in ['enhanced','continuous']:
            if m in gains:gains[m]=g
    # Common peak protection after gain matching; no per-version clipping.
    peak=max(float(np.max(np.abs(x)))*10**(gains[m]/20) for m,x in waves.items())
    offset=min(0,float(20*np.log10(.84/max(peak,1e-12))))
    t={'id':key,'label':label,'scope':'vocal','ui_role':role,'source_type':source_type,'group':'基础分离' if role=='primary' else '人声细分',
       'video_start_seconds':0,'duration':len(next(iter(waves.values())))/SR,'sample_rate':SR,'default_mode':default,
       'audio':{},'lossless':{},'levels':{},'weak':False,'source':'本地模型估计','note':note,
       'mode_labels':{'raw':'仅分离 · 原始','enhanced':'轻度低频清理 / 动态稳定','continuous':'连续保留（推荐）'},
       'processing_note':'原速、原相位；不修音、不生成音符。每类一轨，处理作为选项。',
       'comparison_level_policy':'shared_gain_original_reference'}
    if role=='detail':t['parent_id']='vocal_all'
    for mode,x in waves.items():
        scale=10**((gains[mode]+offset)/20);wav=out/'work'/f'{key}_{mode}_listen.wav';sf.write(wav,x*scale,SR,subtype='PCM_24')
        mp3=f'audio/hierarchy/{key}_{mode}.mp3';flac=f'lossless/hierarchy/{key}_{mode}.flac'
        sf.write(out/flac,x*min(1,.98/max(float(np.max(np.abs(x))),1e-12)),SR,subtype='PCM_24')
        subprocess.run([FFMPEG,'-y','-hide_banner','-loglevel','error','-i',str(wav),'-c:a','libmp3lame','-b:a','256k',str(out/mp3)],check=True)
        t['audio'][mode]=mp3;t['lossless'][mode]=flac;t['levels'][mode]={'listening_gain_db':gains[mode]+offset}
    t['weak']=float(np.sqrt(np.mean(next(iter(waves.values())).astype(float)**2)))<10**(-48/20)
    return t


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--expand-vocals',action='store_true');args=parser.parse_args();out=args.out.resolve()
    if args.expand_vocals:
        if args.input:parser.error('Second stage uses the original stored in --out, not a different --input.')
        if not (out/'manifest.json').exists():parser.error('Run the basic stage first.')
        existing=json.loads((out/'manifest.json').read_text())
        if (out/'vocal_detail_processing.json').exists() and len(existing.get('hierarchy',{}).get('vocal_children',[]))==3:print('Vocal details already completed; originals remain unchanged.');return
    else:
        if not args.input:parser.error('--input is required for initialization.')
        if out.exists():parser.error('Use a NEW output directory; existing work is not overwritten.')
        out.mkdir(parents=True)
    for folder in ['work','audio/hierarchy','lossless/hierarchy']:(out/folder).mkdir(parents=True,exist_ok=True)
    cfg=json.loads((BASE/'processing_defaults.json').read_text());original=out/'original.wav'
    if not args.expand_vocals:
        subprocess.run([FFMPEG,'-hide_banner','-loglevel','error','-i',str(args.input.resolve()),'-vn','-ar','44100','-ac','2','-c:a','pcm_f32le',str(original)],check=True)
    vocals=specialist('vocals',original,out/'models/vocals');all_path=Path(vocals['outputs']['Vocals']);v=read(all_path)
    expected={'times':np.arange(len(frames_rms(v,SR)))*.1,'all_db':20*np.log10(frames_rms(v,SR)+1e-12),'windows':[]}
    if not args.expand_vocals:
        subprocess.run([str(ROOT/'work/demucs-env/bin/python'),str(ROOT/'work/demucs-detailed/separate_full_instruments.py'),
                        '--input',vocals['outputs']['Instrumental'],'--out',str(out/'models/instruments')],check=True)
        sources={k:read(out/f'models/instruments/{k}.wav') for k in ['guitar','bass','drums','piano']}
        sources['other']=read(original)-v-sum(sources.values())
        root=export_vocal(out,'vocal_all','人声',{'raw':v},role='primary',source_type='vocals',note='仅基础分离；二级声部尚未请求。')
        instruments=[export_instrument(k,sources[k],out,out/'work') for k in ['guitar','bass','drums','piano','other']]
        reference=export_vocal(out,'vocal_original_mix','原曲参考',{'raw':read(original)},role='reference',source_type='reference')
        report={'tracks':[root,*instruments,reference],'vocal_evaluation':{'report':'README.md'}}
        root_ids=[root['id'],*[t['id'] for t in instruments]];detail_ids=[]
    else:
        report=json.loads((out/'manifest.json').read_text());root_ids=report['hierarchy']['primary_ids']
        karaoke=specialist('karaoke',all_path,out/'models/karaoke');bve=specialist('bve',all_path,out/'models/bve')
        if not bve['model_data'].get('is_bv_model'):raise ValueError('BVE target mapping must be checked.')
        lead=read(karaoke['outputs']['Vocals']);backing=read(karaoke['outputs']['Instrumental']);guide=read(bve['outputs']['Instrumental'])
        continuous,clean,processing=repair_aligned(v,lead,guide,cfg['continuity']);back_clean,_=stabilize(backing,.15)
        crowd=specialist('crowd',all_path,out/'models/crowd');crowd_path=next(Path(p) for n,p in crowd['outputs'].items() if n.lower()=='crowd')
        lead_t=export_vocal(out,'vocal_lead','主唱',{'raw':lead,'enhanced':clean,'continuous':continuous},default='continuous')
        lead_t['continuity_note']='按本音频自动分歧候选进行原残差连续回填，不复用 demo 的时间或音域；不生成音符。'
        backing_t=export_vocal(out,'vocal_backing','和声',{'raw':backing,'enhanced':back_clean},source_type='backing',default='enhanced')
        crowd_t=export_vocal(out,'vocal_crowd','观众声 · 候选',{'raw':read(crowd_path)},source_type='crowd_candidate',note='Crowd 模型直接作用于全部人声；身份未确认，仍可能是和声或唱腔残留。')
        crowd_t['mode_labels']={'raw':'观众声候选 · 保守估计'}
        report['tracks']=[t for t in report['tracks'] if t.get('ui_role')!='detail']+[lead_t,backing_t,crowd_t]
        detail_ids=[lead_t['id'],backing_t['id'],crowd_t['id']];expected['windows']=find_candidates(v,lead,guide,cfg['continuity'])
        (out/'vocal_detail_processing.json').write_text(json.dumps(processing,ensure_ascii=False,indent=2))
    report=enrich_report(report,out,expected);report=attach_hierarchy(report,root_ids,detail_ids,'vocal_original_mix')
    (out/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(out/'index.html').write_text((BASE/'player_template.html').read_text().replace('__MANIFEST__',json.dumps(report,ensure_ascii=False)))
    (out/'README.md').write_text('# 两阶段本地分离\n\n默认基础分离为全片人声与5类配乐。\n\n可选：在项目目录运行 `work/separator-env/bin/python work/vocal-detail/initialize_audio.py --out '+str(args.out)+' --expand-vocals`，本地生成主唱、和声、观众声候选；刷新页面后可展开已完成的缓存。\n\n3D 两轴360度自由旋转，处理在同一轨内选择。全部代理评分不是听测准确率，子轨可能重叠。\n')
    print(f'DONE: {len(root_ids)} full-song roots; {len(detail_ids)} optional vocal children; {out}')


if __name__=='__main__':main()
