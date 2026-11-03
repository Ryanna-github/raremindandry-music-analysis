"""Local, allowlisted stereo combination exports; never accepts arbitrary input paths."""
import copy
import hashlib
import json
import math
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class ExportError(ValueError):
    pass


def find_ffmpeg():
    executable = shutil.which('ffmpeg')
    if not executable:
        try:
            import imageio_ffmpeg
            executable = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError as error:
            raise ExportError('请用 work/separator-env/bin/python 启动本地服务器。') from error
    return executable


def safe_source(root,relative):
    if not isinstance(relative,str):
        raise ExportError('音频路径无效。')
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ExportError('所选音频文件不存在或不在本地 demo 内。')
    return path


def number(value,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ExportError(f'{label}必须是有效数字。')
    return float(value)


def plan_export(report,root,payload):
    if not isinstance(payload,dict) or set(payload)-{'tracks','vocal_expanded','format','range'}:
        raise ExportError('导出请求格式不正确。')
    hierarchy=report['hierarchy']; nodes={t['id']:t for t in report['tracks']}
    expanded=payload.get('vocal_expanded',False)
    if not isinstance(expanded,bool):
        raise ExportError('人声展开状态无效。')
    visible=[]
    for track_id in hierarchy['primary_ids']:
        visible.extend(hierarchy['vocal_children'] if expanded and track_id==hierarchy['vocal_root_id'] else [track_id])
    chosen=payload.get('tracks'); extension=payload.get('format','wav')
    if not isinstance(chosen,list) or not 1<=len(chosen)<=len(visible):
        raise ExportError('请至少勾选一个有效音轨。')
    if extension not in ['wav','mp3']:
        raise ExportError('只支持 WAV 或 MP3 导出。')
    first=min(float(nodes[i].get('video_start_seconds',0)) for i in hierarchy['primary_ids'])
    last=max(float(nodes[i].get('video_start_seconds',0))+float(nodes[i]['duration']) for i in hierarchy['primary_ids'])
    interval=payload.get('range',[first,last])
    if not isinstance(interval,list) or len(interval)!=2:
        raise ExportError('时间范围必须有开始和结束秒数。')
    start,end=[number(v,'时间') for v in interval]
    if start<first-1e-6 or end>last+1e-6 or end-start<.05-1e-9:
        raise ExportError('导出时间范围超出全片或不足 0.05 秒。')
    entries=[]; seen=set()
    for selected in chosen:
        if not isinstance(selected,dict) or set(selected)-{'id','mode'}:
            raise ExportError('请只提交音轨 ID 和处理方式，不支持自定义文件路径。')
        track_id=selected.get('id')
        if not isinstance(track_id,str) or track_id not in visible or track_id in seen:
            raise ExportError('音轨重复或不属于当前层级；总人声不能与子轨重复导出。')
        seen.add(track_id); t=nodes[track_id]; mode=selected.get('mode',t.get('default_mode','raw'))
        if not isinstance(mode,str) or mode not in t['audio']:
            raise ExportError('所选处理方式不存在。')
        level=t.get('levels',{}).get(mode,{})
        lossless=t.get('lossless',{}).get(mode)
        use_lossless=bool(lossless and 'listening_gain_db' in level and (root/lossless).is_file())
        relative=lossless if use_lossless else t['audio'][mode]
        path=safe_source(root,relative)
        correction=1.0
        if use_lossless:
            gain_db=number(level['listening_gain_db'],'试听增益')
            archive_scale=number(level.get('archive_scale',1),'无损归档缩放')
            if not -120<=gain_db<=60 or not 0<archive_scale<=1:
                raise ExportError('无损文件的电平元数据无效。')
            correction=10**(gain_db/20)/archive_scale
        t_start=float(t.get('video_start_seconds',0));t_end=t_start+float(t['duration'])
        overlap_start=max(start,t_start);overlap_end=min(end,t_end)
        if overlap_end<=overlap_start:
            continue
        entries.append({'id':track_id,'label':t['label'],'mode':mode,
                        'mode_label':t.get('mode_labels',{}).get(mode,mode),
                        'path':str(path),'source_relative':relative,'lossless_input':use_lossless,
                        'listening_level_correction':correction,'trim_start':overlap_start-t_start,
                        'trim_end':overlap_end-t_start,'delay_seconds':overlap_start-start})
    if not entries:
        raise ExportError('所选轨道不覆盖这个时间范围。')
    return {'entries':entries,'format':extension,'range':[start,end],
            'duration':end-start,'visible_lane_count':len(visible),'mix_gain':.85/len(visible),
            'vocal_expanded':expanded,'sample_rate':44100,'channels':2,'speed':1.0,
            'source_video':report.get('source_video'),'selection':copy.deepcopy(chosen)}


def render_mix(plan,directory,progress=None,ffmpeg=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    extension=plan['format']; temporary=directory/f'mix.partial.{extension}'; output=directory/f'mix.{extension}'
    command=[ffmpeg or find_ffmpeg(),'-hide_banner','-nostdin','-loglevel','error','-y']
    filters=[]
    for index,entry in enumerate(plan['entries']):
        command+=['-i',entry['path']]
        gain=entry['listening_level_correction']*plan['mix_gain']
        filters.append(f"[{index}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"atrim=start={entry['trim_start']:.9f}:end={entry['trim_end']:.9f},asetpts=PTS-STARTPTS,"
                       f"volume={gain:.12f},adelay={entry['delay_seconds']*1000:.6f}:all=1,"
                       f"apad,atrim=duration={plan['duration']:.9f}[s{index}]")
    labels=''.join(f'[s{i}]' for i in range(len(plan['entries'])))
    filters.append(f"{labels}amix=inputs={len(plan['entries'])}:normalize=0:duration=longest:dropout_transition=0[out]")
    command+=['-filter_complex',';'.join(filters),'-map','[out]','-ar','44100','-ac','2']
    command+=['-c:a','pcm_s24le'] if extension=='wav' else ['-c:a','libmp3lame','-b:a','320k']
    command+=['-progress','pipe:1','-nostats',str(temporary)]
    timed_out=threading.Event()
    with (directory/'render.log').open('w') as log:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=log,text=True)
        def stop_timeout():
            timed_out.set();process.kill()
        timer=threading.Timer(180,stop_timeout);timer.start()
        try:
            for line in process.stdout:
                if line.startswith('out_time_us=') and progress:
                    try:
                        progress(min(99,max(1,int(float(line.split('=',1)[1])/1e6/plan['duration']*100))))
                    except ValueError:
                        pass
            code=process.wait()
        finally:
            timer.cancel();process.stdout.close()
    if timed_out.is_set():
        raise ExportError('导出超过 180 秒，已停止；请重试较短的片段。')
    if code!=0:
        raise ExportError('音频合成失败，请检查所选文件；诊断保存在导出目录 render.log。')
    import soundfile as sf
    info=sf.info(temporary)
    if info.channels!=2 or info.samplerate!=44100 or abs(info.duration-plan['duration'])>.04:
        raise ExportError('导出后的声道、采样率或时长校验未通过。')
    temporary.rename(output)
    metadata=copy.deepcopy(plan)
    metadata['created_at']=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    metadata['audio_file']=output.name;metadata['output_frames']=info.frames
    metadata['output_bytes']=output.stat().st_size;metadata['output_subtype']=info.subtype
    metadata['mix_policy']='fixed_0.85_divided_by_visible_lanes_no_auto_boost'
    metadata['warning']='分轨可能串音与重叠；观众声为候选。组合不是原曲混音的精确复原。'
    for entry in metadata['entries']:
        source=Path(entry.pop('path'));entry['source_bytes']=source.stat().st_size
        digest=hashlib.sha256()
        with source.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        entry['source_sha256']=digest.hexdigest()
    manifest=directory/'combination.json';manifest.write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    return output,manifest,metadata


class MixExports:
    def __init__(self,root):
        self.root=Path(root).resolve();self.jobs={};self.lock=threading.Lock()
        self.capacity=threading.BoundedSemaphore(2);self.executor=ThreadPoolExecutor(max_workers=1)

    def submit(self,payload):
        report=json.loads((self.root/'manifest.json').read_text());plan=plan_export(report,self.root,payload)
        executable=find_ffmpeg()
        if shutil.disk_usage(self.root).free<250*1024**2:
            raise ExportError('可用空间不足 250 MB，请先腾出空间。')
        with self.lock:
            if len(self.jobs)>=100:
                raise ExportError('已有 100 个导出任务，请重启服务器或整理历史导出后重试。')
        if not self.capacity.acquire(blocking=False):
            raise ExportError('已有导出正在处理或排队，请等完成后重试。')
        job_id=uuid.uuid4().hex
        state={'id':job_id,'status':'queued','progress':0,'format':plan['format'],
               'labels':[e['label'] for e in plan['entries']],'range':plan['range']}
        with self.lock:
            self.jobs[job_id]=state
        self.executor.submit(self._run,job_id,plan,executable)
        return self.get(job_id)

    def get(self,job_id):
        with self.lock:
            result=self.jobs.get(job_id)
            return copy.deepcopy(result) if result else None

    def update(self,job_id,**fields):
        with self.lock:
            self.jobs[job_id].update(fields)

    def _run(self,job_id,plan,executable):
        try:
            self.update(job_id,status='rendering',progress=1)
            output,manifest,metadata=render_mix(plan,self.root/'exports'/job_id,
                progress=lambda value:self.update(job_id,progress=value),ffmpeg=executable)
            self.update(job_id,status='complete',progress=100,
                        audio_url=f'exports/{job_id}/{output.name}',manifest_url=f'exports/{job_id}/{manifest.name}',
                        bytes=output.stat().st_size,duration=metadata['duration'],channels=2,sample_rate=44100,
                        mix_gain=plan['mix_gain'])
        except Exception as error:
            self.update(job_id,status='failed',error=str(error) if isinstance(error,ExportError) else '导出失败，请查看本地日志。')
        finally:
            self.capacity.release()
