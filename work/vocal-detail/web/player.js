'use strict';
const $=id=>document.getElementById(id),tracks=report.tracks,player=$('audio');
const startOf=t=>t.video_start_seconds??45,scopeOf=t=>t.scope||'instrument',defaultMode=t=>t.default_mode&&t.audio[t.default_mode]?t.default_mode:(t.audio.enhanced?'enhanced':Object.keys(t.audio)[0]);
const hierarchy=report.hierarchy||{primary_ids:tracks.filter(t=>t.ui_role!=='detail'&&t.ui_role!=='reference').map(t=>t.id),vocal_root_id:'vocal_all',vocal_children:[],reference_id:null};
const node=id=>tracks.find(t=>t.id===id),primaryTracks=hierarchy.primary_ids.map(node).filter(Boolean),vocalChildren=hierarchy.vocal_children.map(node).filter(Boolean),referenceTrack=node(hierarchy.reference_id);
const splitPreferenceKey='signal-lab:vocal-split:'+String(report.source_video||location.pathname);
let vocalExpanded=hierarchy.vocal_expanded_default===true;
try{vocalExpanded=localStorage.getItem(splitPreferenceKey)==='true'||vocalExpanded}catch{}
function displayTracks(){return primaryTracks.flatMap(t=>t.id===hierarchy.vocal_root_id&&vocalExpanded?vocalChildren:[t])}
let current=tracks.find(t=>t.id===report.default_track)||tracks[0],mode=defaultMode(current),scope='all',a=0,b=current.duration,looping=false,loadToken=0,waveToken=0,anim=null,lastAnimation=0,pendingResume=false;
if(vocalExpanded&&current.id===hierarchy.vocal_root_id&&vocalChildren.length){current=vocalChildren[0];mode=defaultMode(current);b=current.duration}
let exportBusy=false;
const globalStart=Math.min(...tracks.map(startOf)),globalEnd=Math.max(...tracks.map(t=>startOf(t)+t.duration)),selectedModes=new Map();
const enabledTracks=new Map(tracks.map(t=>[t.id,t.ui_role!=='reference']));
const mixer=new StemMixer(player,()=>startOf(current)+(player.currentTime||0),message=>{$('playerError').hidden=false;$('playerError').textContent=message});
const wave=new WaveLab($('wave3d'),$('waveLabels'),$('overview'));
const visualDefaults=report.processing_defaults?.visualization||{};
wave.peakHeight=visualDefaults.peak_height_world_units||6.2;wave.visualPower=visualDefaults.shared_amplitude_power||.78;wave.heightGain=visualDefaults.height_gain_default||1.5;wave.defaultHeightGain=wave.heightGain;
const swatch=(label,css)=>({label,css,color:[1,3,5].map(i=>parseInt(css.slice(i,i+2),16)/255)});
const palette=[swatch('原始','#2bf5ff'),swatch('保守清理','#ff41c7'),swatch('强化清理','#ff992f'),swatch('连续保留','#b6fa36')];
const modeSwatch=m=>palette[['raw','enhanced','clarity','continuous'].indexOf(m)]||palette[0];
const categories={vocals:swatch('人声','#2bf5ff'),lead:swatch('主唱','#2bf5ff'),backing:swatch('合声 / 和声','#ff41c7'),guitar:swatch('吉他','#ff7b24'),bass:swatch('贝斯','#aa60ff'),drums:swatch('鼓组','#b7f52a'),piano:swatch('钢琴 · 弱候选','#ffe353'),other:swatch('其他配乐','#ff4075'),crowd_candidate:swatch('观众声候选','#27e9a5'),vocal:swatch('人声','#2bf5ff'),instrument:swatch('配乐','#ff7b24'),diagnostic:swatch('诊断 / 残差','#91a9c2'),reference:swatch('原曲参考','#7897b6')};
function categoryFor(t){if(t.ui_role==='reference'||['original','vocal_original_mix','vocal_no_crowd'].includes(t.id))return'reference';if(categories[t.source_type])return t.source_type;if(t.group?.includes('诊断')||t.id.includes('residual'))return'diagnostic';return categories[scopeOf(t)]?scopeOf(t):'instrument'}
window.AUDIO_WAVES=window.AUDIO_WAVES||{};const wavePromises=new Map();
function time(t){return `${Math.floor(t/60).toString().padStart(2,'0')}:${(t%60).toFixed(1).padStart(4,'0')}`}
function preservePitch(){for(const name of ['preservesPitch','webkitPreservesPitch','mozPreservesPitch'])if(name in player)player[name]=true}
function labelFor(m,t=current){return t.mode_labels?.[m]||({raw:'原始分轨',enhanced:scopeOf(t)==='instrument'?'EQ 试听':'连续清理 · 保守',clarity:'更清晰 · 较强'}[m]||m)}
function syncTransport(){const wanted=pendingResume||mixer.requestedPlaying;for(const id of ['playPause','mixPlayPause']){$(id).textContent=wanted?'Ⅱ 暂停':'▶ 播放';$(id).setAttribute('aria-pressed',String(wanted))}$('mixTransportState').textContent=wanted?(pendingResume||mixer.starting?'正在载入 · 可随时暂停':'播放中'):'已暂停 · 所有音轨停止';}
function pauseAll(){pendingResume=false;mixer.pause();if(anim){cancelAnimationFrame(anim);anim=null}syncTransport();syncReadout()}
function togglePlayback(){if(pendingResume||mixer.requestedPlaying||!player.paused)pauseAll();else play()}
function play(){pendingResume=false;mixer.play().catch(e=>{if(e.name==='AbortError')return;$('playerError').hidden=false;$('playerError').textContent='播放未能开始，请重新点击播放或检查音频文件。'})}
mixer.onStateChange=syncTransport;
function updateWave(){wave.selectedId=wave.unified?current.id:`${current.id}:${mode}`;const offset=startOf(current)-wave.start;wave.update(offset+(player.currentTime||0),looping?[offset+a,offset+b]:null)}
function syncReadout(){const pos=player.currentTime||0;$('readout').textContent=`${time(startOf(current)+pos)} / ${time(startOf(current)+current.duration)}`;$('mixReadout').textContent=time(startOf(current)+pos);$('timeline').value=pos;updateWave();syncTimeView()}
function syncLoop(){$('loopText').textContent=`A ${time(startOf(current)+a)} → B ${time(startOf(current)+b)}`;$('loop').classList.toggle('active',looping);$('loop').setAttribute('aria-pressed',String(looping));$('loop').textContent=looping?'关闭循环':'开启循环';updateWave();refreshExportSelection()}
function animate(now){if(!now||now-lastAnimation>=33){syncReadout();mixer.sync();lastAnimation=now||0}if(!player.paused)anim=requestAnimationFrame(animate);else anim=null}
function included(t){return t.id===hierarchy.vocal_root_id&&vocalExpanded?vocalChildren.some(child=>enabledTracks.get(child.id)!==false):enabledTracks.get(t.id)!==false}
function configureMixer(){mixer.configure(displayTracks().map(t=>({id:t.id,label:t.label,path:t.audio[selectedModes.get(t.id)||defaultMode(t)],start:startOf(t),duration:t.duration,enabled:included(t)})),$('waveMode').value==='unified'&&current.ui_role!=='reference');syncMixerSelection()}
function syncMixerSelection(){
 const visible=displayTracks(),selected=visible.filter(included);$('mixStatus').textContent=mixer.multi?`${selected.length}/${visible.length} 轨参与播放${selected.length?'':' · 当前静音'}`:'单轨试听 · 原曲参考 / 处理对照';$('mixStatus').dataset.included=JSON.stringify(selected.map(t=>t.id));
 for(const entry of wave.entries)entry.enabled=wave.unified?included(node(entry.trackId)):true;
 for(const el of document.querySelectorAll('[data-mix-id]')){const t=node(el.dataset.mixId),active=t&&included(t);el.disabled=!mixer.multi;el.setAttribute('aria-pressed',String(active));el.classList.toggle('excluded',!active);if(el.classList.contains('mix-toggle')){el.textContent=active?'✓':'−';el.setAttribute('aria-label',`${active?'取消播放':'加入播放'} ${t.label}`)}}
 for(const id of ['mixAll','mixNone','mixSolo'])$(id).disabled=!mixer.multi;
 wave.draw();refreshExportSelection();
}
function exportInterval(){return ExportRange.resolve($('exportRange').value,{bounds:[globalStart,globalEnd],ab:[startOf(current)+a,startOf(current)+b],custom:[$('exportStart').value,$('exportEnd').value]})}
function refreshExportRange(){
 const custom=$('exportRange').value==='custom';$('exportCustomRange').hidden=!custom;let error=null,interval;
 try{interval=exportInterval()}catch(e){error=e}
 for(const id of ['exportStart','exportEnd'])$(id).setAttribute('aria-invalid',String(custom&&!!error));
 $('exportRangePreview').classList.toggle('invalid',!!error);$('exportRangePreview').textContent=error?error.message:`导出时间：${ExportRange.format(interval[0])} → ${ExportRange.format(interval[1])} · 时长 ${(interval[1]-interval[0]).toFixed(3)} 秒 · 原速 / 立体声`;
 return !error;
}
function refreshExportSelection(){const selected=displayTracks().filter(included),rangeValid=refreshExportRange();$('exportMix').disabled=exportBusy||!mixer.multi||!selected.length||!rangeValid;$('exportSelection').textContent=mixer.multi?(selected.length?selected.map(t=>`${t.label} · ${labelFor(selectedModes.get(t.id)||defaultMode(t),t)}`).join(' ＋ '):'尚未勾选音轨；请先选择组合。'):'返回「分轨总览」后导出勾选组合；此处只试听当前参考 / 处理版。'}
async function exportResponse(response){const type=response.headers.get('content-type')||'';if(!type.includes('application/json'))throw Error('本地导出服务不可用，请用更新后的 serve_demo.py 启动网页。');const result=await response.json();if(!response.ok)throw Error(result.error||'导出请求失败');return result}
async function exportCombination(){
 if(exportBusy||!mixer.multi)return;const selected=displayTracks().filter(included);if(!selected.length)return;
 let interval;try{interval=exportInterval()}catch(error){$('exportStatus').textContent=error.message;refreshExportSelection();return}
 const format=$('exportFormat').value,payload={tracks:selected.map(t=>({id:t.id,mode:selectedModes.get(t.id)||defaultMode(t)})),vocal_expanded:vocalExpanded,format,range:interval};
 const names=selected.map(t=>t.label).join(' ＋ ');exportBusy=true;refreshExportSelection();$('exportResult').hidden=true;$('exportProgress').hidden=false;$('exportProgress').value=0;$('exportStatus').textContent='正在提交本地导出任务…';$('exportSnapshot').textContent=`本次快照：${names} · ${ExportRange.format(interval[0])}–${ExportRange.format(interval[1])} · 原速 1.0× / 立体声。之后修改勾选或时间不会改变此任务。`;
 try{
  let job=await exportResponse(await fetch('/api/exports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));
  while(job.status==='queued'||job.status==='rendering'){$('exportProgress').value=job.progress||0;$('exportStatus').textContent=job.status==='queued'?'正在排队…':`本地合成 ${job.progress||0}% · 优先无损源，匹配试听电平`;await new Promise(resolve=>setTimeout(resolve,800));job=await exportResponse(await fetch(`/api/exports/${job.id}`,{cache:'no-store'}))}
  if(job.status!=='complete')throw Error(job.error||'导出未完成');$('exportProgress').value=100;$('exportStatus').textContent=`导出完成 · ${job.duration.toFixed(2)} 秒 · 双声道 / 44.1 kHz · ${(job.bytes/1024**2).toFixed(1)} MB`;
  const segment=interval.map(t=>ExportRange.format(t).replace(/:/g,'-')).join('_');$('exportMixDownload').href=job.audio_url;$('exportMixDownload').download=`组合_${selected.map(t=>t.label).join('_')}_${segment}.${format}`;$('exportMixDownload').textContent=`↓ 下载组合 ${format.toUpperCase()}`;$('exportManifestDownload').href=job.manifest_url;$('exportManifestDownload').download=`组合清单_${segment}.json`;$('exportResult').hidden=false;
 }catch(error){$('exportStatus').textContent=`导出失败：${error.message}`;$('exportProgress').hidden=true}finally{exportBusy=false;refreshExportSelection()}
}
function toggleIncluded(t){if(!t)return;const next=!included(t);enabledTracks.set(t.id,next);if(t.id===hierarchy.vocal_root_id)for(const child of vocalChildren)enabledTracks.set(child.id,next);if(t.parent_id===hierarchy.vocal_root_id)enabledTracks.set(hierarchy.vocal_root_id,vocalChildren.some(c=>enabledTracks.get(c.id)!==false));configureMixer()}
function selectMix(all,solo=false){for(const t of displayTracks())enabledTracks.set(t.id,solo?t.id===current.id:all);if(vocalExpanded)enabledTracks.set(hierarchy.vocal_root_id,vocalChildren.some(c=>enabledTracks.get(c.id)!==false));else for(const child of vocalChildren)enabledTracks.set(child.id,enabledTracks.get(hierarchy.vocal_root_id));configureMixer()}
function getWave(path){if(window.AUDIO_WAVES[path])return Promise.resolve(window.AUDIO_WAVES[path]);if(!wavePromises.has(path))wavePromises.set(path,new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=path;script.onload=()=>window.AUDIO_WAVES[path]?resolve(window.AUDIO_WAVES[path]):reject(Error('Invalid waveform asset'));script.onerror=()=>{wavePromises.delete(path);reject(Error('Waveform asset unavailable'))};document.head.append(script)}));return wavePromises.get(path)}
async function loadWaves(){
 const token=++waveToken,t=current,active=mode,unified=$('waveMode').value==='unified';$('waveStatus').textContent=unified?'载入全部细分轨道…':'载入当前轨对照…';selectedModes.set(t.id,active);
 const visible=displayTracks();
 const specs=unified?visible.map(track=>({track,m:selectedModes.get(track.id)||defaultMode(track)})):[active,...Object.keys(t.audio).filter(m=>m!==active)].map(m=>({track:t,m}));
 const results=await Promise.allSettled(specs.map(s=>getWave(s.track.waveforms[s.m])));if(token!==waveToken)return;
 const entries=[];results.forEach((result,i)=>{if(result.status!=='fulfilled'){console.warn('Missing waveform',specs[i].track.id);return}const {track,m}=specs[i],category=categoryFor(track),color=unified?categories[category]:modeSwatch(m);entries.push({data:result.value,id:unified?track.id:`${track.id}:${m}`,trackId:track.id,start:startOf(track),duration:track.duration,label:unified?track.label:labelFor(m,track),shortLabel:unified?track.label:labelFor(m,track),mode:m,reference:track.id==='vocal_original_mix',category,enabled:unified?included(track):true,css:color.css,color:color.color})});
 if(!entries.length){$('waveStatus').textContent='波形资源缺失，音频仍可播放';return}
 wave.setData(entries,unified?globalStart:startOf(t),unified?globalEnd-globalStart:t.duration,unified);wave.selectedId=unified?t.id:`${t.id}:${active}`;
 if(referenceTrack){try{const data=await getWave(referenceTrack.waveforms[defaultMode(referenceTrack)]);if(token!==waveToken)return;wave.referenceEntry={data,id:referenceTrack.id,start:startOf(referenceTrack),duration:referenceTrack.duration,css:categories.reference.css}}catch{}}
 syncSpatial();updateWave();
 const legendCategories=[...new Set(entries.map(e=>e.category))].map(k=>categories[k]);
 $('legend').replaceChildren();entries.forEach((entry,i)=>{const el=document.createElement(unified?'button':'span'),mark=document.createElement('i');el.style.setProperty('--track-color',entry.css);el.dataset.color=entry.css;mark.style.background=entry.css;mark.style.color=entry.css;el.append(mark,document.createTextNode(`${String(i+1).padStart(2,'0')} ${entry.label}`));if(unified){el.dataset.mixId=entry.trackId;el.setAttribute('aria-label',`切换 ${entry.label} 是否参与播放`);el.onclick=()=>toggleIncluded(node(entry.trackId))}$('legend').append(el)});
 $('waveStatus').textContent=wave.gl?(unified?`${vocalExpanded?'人声已细分':'基础分离'} · ${entries.length}/${visible.length} 轨 · 全片 ${time(globalEnd-globalStart)}`:`当前轨 · ${entries.length} 种处理对照`):'WebGL 不可用 · 下方二维波形可定位';
 $('amplitudeCaption').textContent='每类一个波形体 · 左右峰值合并展示 / 音频保持立体声';
 $('visualDisclosure').textContent=`视觉显示：共享 ±${wave.scale.toFixed(3)} FS 标尺，幅度幂 ${wave.visualPower.toFixed(2)} 与立体增高；不是原曲各乐器的真实响度比例。零信号仍为零，不逐轨归一化。`;
 $('unifiedNote').textContent=unified?(vocalExpanded?'总人声已替换为主唱 / 和声 / 观众声候选，不与父轨重复叠加；子轨仍可能有串音与重叠。收起时恢复总人声，不等同于已勾选子声部的精确合成。':'每类型一个展示波形与一个立体声音频。可按需细分人声。混听使用独立电平匹配分轨并预留余量，不是原曲混音的精确复原。'):'不同处理只作图形对照，音频仅播放当前处理版；多轨勾选保留，返回总览后继续生效。';
 $('laneDirectory').replaceChildren();entries.forEach((entry,i)=>{const btn=document.createElement('button'),n=document.createElement('span');n.className='lane-number';n.textContent=String(i+1).padStart(2,'0');n.style.color=entry.css;btn.style.setProperty('--track-color',entry.css);btn.dataset.id=entry.trackId;btn.classList.toggle('active',entry.trackId===current.id&&(unified||entry.mode===mode));btn.append(n,document.createTextNode(entry.label));btn.onclick=()=>{if(!unified){mode=entry.mode;load();return}chooseFromGraph(entry)};$('laneDirectory').append(btn)});$('laneCount').textContent=`${entries.length} 条`;configureMixer();syncTimeView();
}
function syncSpatial(){$('trackSpacing').value=wave.spacing;$('spacingValue').textContent=`${wave.spacing.toFixed(2)}×`;$('spatialZoom').value=1/wave.camera;$('spatialValue').textContent=`${(1/wave.camera).toFixed(2)}×`;$('waveHeight').value=wave.heightGain;$('heightValue').textContent=`${wave.heightGain.toFixed(2)}×`;$('laneFocus').max=Math.max(0,wave.entries.length-1);$('laneFocus').value=wave.focusLane;$('focusValue').textContent=wave.entries.length?`#${Math.round(wave.focusLane)+1}`:'全部'}
function chooseFromGraph(entry){const t=node(entry.trackId);if(t)selectTrack(t)}
wave.onSelect=entry=>{if(wave.unified)toggleIncluded(node(entry.trackId));else{mode=entry.mode;load()}};
wave.onHover=(entry,point)=>{const tip=$('waveHover');tip.hidden=!entry;if(entry){tip.textContent=`${entry.label} · ${labelFor(entry.mode,tracks.find(t=>t.id===entry.trackId))} · ${time(entry.start)}–${time(entry.start+entry.duration)}`;tip.style.left=`${Math.max(8,Math.min(wave.width-270,point.x+12))}px`;tip.style.top=`${Math.max(30,Math.min(wave.height-65,point.y+12))}px`}};
wave.onSpatialChange=syncSpatial;
function showEvaluation(){
 const evaluation=current.evaluation?.[mode];$('scores').replaceChildren();$('scoreWarnings').replaceChildren();
 if(!evaluation){$('confidence').textContent='未计算';$('measurements').textContent='暂无评价数据';return}
 $('confidence').textContent=`算法估计 · 可信度 ${evaluation.confidence}`;
 for(const [key,label] of [['quality','音质 · 代理综合'],['cleanliness','洁净度 / 杂音'],['stability','稳定性'],['continuity','连续性'],['integrity','数值完整性']]){
  const value=evaluation.scores[key],el=document.createElement('div');el.className='score'+(evaluation.weak_signal?' weak':'');
  const labelEl=document.createElement('div');labelEl.className='score-label';labelEl.textContent=label;
  const valueEl=document.createElement('div');valueEl.className='score-value';valueEl.append(document.createTextNode(value.toFixed(1)));const unit=document.createElement('span');unit.textContent='/ 10';valueEl.append(unit);
  const meter=document.createElement('div'),bar=document.createElement('div');meter.className='meter';bar.style.width=`${value*10}%`;meter.append(bar);el.append(labelEl,valueEl,meter);$('scores').append(el);
 }
 const m=evaluation.measurements;$('measurements').textContent=`RMS ${m.rms_dbfs.toFixed(1)} dBFS · 峰值 ${m.peak.toFixed(3)} FS · 活跃占比 ${(m.active_fraction*100).toFixed(1)}% · 平坦度 ${m.spectral_flatness.toFixed(3)} · 包络抽动 ${(m.active_envelope_pumping_fraction*100).toFixed(1)}%`;
 for(const warning of evaluation.warnings){const li=document.createElement('li');li.textContent=warning;$('scoreWarnings').append(li)}
}
function load(localPosition=player.currentTime||0){
 const playing=pendingResume||mixer.requestedPlaying||!player.paused,token=++loadToken;pauseAll();pendingResume=playing;syncTransport();$('playerError').hidden=true;player.src=current.audio[mode];
 player.onloadedmetadata=()=>{if(token!==loadToken)return;player.currentTime=Math.max(0,Math.min(localPosition,current.duration-.01,player.duration-.01));player.playbackRate=Number($('speed').value);preservePitch();syncReadout();if(pendingResume)play()};
 $('title').textContent=current.label;$('source').textContent=current.source;$('group').textContent=current.group;
 $('span').textContent=`${time(startOf(current))}–${time(startOf(current)+current.duration)}`;
 $('note').textContent=current.note||'乐器增强只做温和 EQ 与电平匹配。与原始分轨对比确认弱音、尾音与串音。';
 $('fine').textContent=current.processing_note||(scopeOf(current)==='instrument'?'乐器片段为 00:45–01:30；EQ 试听不是人声连续性修复。':'清理保留原相位与音高。不能逆转所有特效或保证逐位歌手分离。');
 $('download').href=current.audio[mode];const lossless=current.lossless?.[mode]||(mode==='raw'?current.raw_wav:current.enhanced_wav);$('downloadLossless').hidden=!lossless;if(lossless)$('downloadLossless').href=lossless;
 $('seekTime').min=globalStart;$('seekTime').max=globalEnd;$('seekTime').value=(startOf(current)+localPosition).toFixed(1);$('timeline').max=current.duration;$('timeline').value=localPosition;
 $('processing').replaceChildren();for(const m of Object.keys(current.audio)){const option=document.createElement('option');option.value=m;option.textContent=labelFor(m);option.selected=m===mode;$('processing').append(option)}
 $('processingHelp').textContent=current.source_type==='crowd_candidate'?'当前是观众声候选，不确认实际有人群；暂不提供强化处理，避免放大误分类。':mode==='raw'?'当前只使用分离结果，未额外清理；需要改善清晰度时再选择处理方式。':mode==='continuous'?(current.continuity_note||'连续保留：优先接回原残差中的漏分谐波。本 demo 修复 01:08–01:15，其他位置沿用保守清理；不生成音符。'):mode==='clarity'?'强化清理可能减少气声、尾音、空间感或原有特效，请回切原始确认。':scopeOf(current)==='instrument'?'EQ 辅助听辨只调整频率与试听电平，不是更精确的重新分离。':'保守清理：原相位软抑制、有限去混响与温和动态稳定，避免硬切弱音。';
 for(const btn of document.querySelectorAll('.track')){btn.classList.toggle('active',btn.dataset.id===current.id);btn.setAttribute('aria-current',String(btn.dataset.id===current.id))}
 showEvaluation();loadWaves();syncLoop();configureMixer();
}
function selectTrack(t){if(!t)return;const absolute=startOf(current)+(player.currentTime||0);selectedModes.set(current.id,mode);if(t.parent_id===hierarchy.vocal_root_id)vocalExpanded=true;else if(t.id===hierarchy.vocal_root_id)vocalExpanded=false;current=t;mode=selectedModes.get(t.id)||defaultMode(t);a=0;b=t.duration;looping=false;const within=absolute>=startOf(t)&&absolute<startOf(t)+t.duration,pos=within?absolute-startOf(t):0;$('selectionHint').textContent=within?'':`当前轨不覆盖原视频 ${time(absolute)}，从该轨 ${time(startOf(t))} 开始。`;renderTracks();renderWorkflow();load(pos)}
function renderTracks(){
 $('tracks').replaceChildren();$('scopeCount').textContent=`${primaryTracks.length} 基础轨`;let group='';
 function appendTrack(t,parent){
  const btn=document.createElement('button');btn.className='track';btn.dataset.id=t.id;btn.style.setProperty('--track-color',categories[categoryFor(t)].css);const label=document.createElement('span');label.className='track-label';label.textContent=t.label;
  if(t.weak){const tag=document.createElement('span');tag.className='tag';tag.textContent='弱信号 · 评分低可信';label.append(tag)}const score=document.createElement('span');score.className='track-score';const ev=t.evaluation?.[selectedModes.get(t.id)||defaultMode(t)];score.textContent=ev?ev.scores.quality.toFixed(1):'—';btn.append(label,score);btn.classList.toggle('active',t.id===current.id);btn.setAttribute('aria-current',String(t.id===current.id));if(t.id===hierarchy.vocal_root_id)btn.classList.add('tree-parent',...(vocalExpanded?[]:['collapsed']));btn.onclick=()=>selectTrack(t);const row=document.createElement('div'),toggle=document.createElement('button');row.className='track-row';toggle.className='mix-toggle';toggle.dataset.mixId=t.id;toggle.onclick=()=>toggleIncluded(t);row.append(toggle,btn);if(t.id===hierarchy.vocal_root_id&&vocalChildren.length){const split=document.createElement('button');split.id='vocalSplitTree';split.className='tree-split';split.textContent=vocalExpanded?'收起':'拆分';split.setAttribute('aria-label',vocalExpanded?'收起主唱合声观众声细分':'将人声拆分为主唱合声观众声');split.setAttribute('aria-expanded',String(vocalExpanded));split.onclick=toggleVocalSplit;row.append(split)}parent.append(row)
 }
 for(const t of primaryTracks){if(t.group!==group){const h=document.createElement('div');h.className='tree-section';h.textContent=t.group;$('tracks').append(h);group=t.group}appendTrack(t,$('tracks'));if(t.id===hierarchy.vocal_root_id&&vocalExpanded){const children=document.createElement('div');children.className='children';for(const child of vocalChildren)appendTrack(child,children);$('tracks').append(children)}}
 $('playReference').disabled=!referenceTrack;
}
function renderWorkflow(){
 for(const id of ['splitVocals','vocalDetailToggle']){$(id).disabled=!vocalChildren.length;$(id).textContent=vocalExpanded?'收起主唱 / 合声 / 观众声细分':'拆分人声 → 主唱 / 合声 / 观众声';$(id).setAttribute('aria-expanded',String(vocalExpanded))}
 $('vocalDetailStatus').textContent=vocalExpanded?'人声已拆分：3 个全片子声部，均可独立勾选、处理及组合导出。观众声为候选。':'当前是总人声；点击右侧拆分后，总人声将替换为 3 个可操作子声部。';
 try{localStorage.setItem(splitPreferenceKey,String(vocalExpanded))}catch{}
 $('workflowStatus').textContent=vocalExpanded?'人声 → 主唱 / 合声（和声） / 观众声候选 · 同类处理合并为选项':'全片人声 + 吉他 / 贝斯 / 鼓 / 钢琴 / 其他配乐 · 每类一轨';
 $('splitHint').textContent=vocalChildren.length?'本 demo 细分已本地预计算，点击加载缓存':'二级人声尚未生成；先完成基础分离，再按需运行 --split-vocals';
}
function toggleVocalSplit(){if(!vocalChildren.length)return;vocalExpanded=!vocalExpanded;
 if(vocalExpanded&&current.id===hierarchy.vocal_root_id){selectTrack(vocalChildren[0]);return}
 if(!vocalExpanded&&current.parent_id===hierarchy.vocal_root_id){selectTrack(node(hierarchy.vocal_root_id));return}
 renderTracks();renderWorkflow();loadWaves();
}
function renderTabsSelection(){for(const el of document.querySelectorAll('#tabs button')){el.classList.toggle('active',el.dataset.scope===scope);el.setAttribute('aria-pressed',String(el.dataset.scope===scope))}}
function changeScope(key){scope=key;renderTabsSelection();renderTracks()}
for(const t of tracks){const ev=t.evaluation?.[defaultMode(t)],tr=document.createElement('tr'),name=document.createElement('td'),btn=document.createElement('button');btn.textContent=t.label;btn.onclick=()=>{scope=scopeOf(t);for(const el of document.querySelectorAll('#tabs button'))el.classList.toggle('active',el.dataset.scope===scope);renderTracks();selectTrack(t);$('title').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'})};name.append(btn);tr.append(name);for(const key of ['quality','cleanliness','stability','continuity']){const td=document.createElement('td');td.textContent=ev?.scores[key].toFixed(1)||'—';tr.append(td)}const trust=document.createElement('td');trust.textContent=ev?.confidence||'—';tr.append(trust);$('evaluationTable').append(tr)}
$('trackCount').textContent=`${primaryTracks.length} 基础分轨 · 人声可细分`;$('intro').textContent=report.intro;
$('vocalReport').hidden=!report.vocal_evaluation;if(report.vocal_evaluation?.report)$('vocalReport').href=report.vocal_evaluation.report;$('repairReport').hidden=!report.continuity_repair;
 $('speed').onchange=e=>{player.playbackRate=Number(e.target.value);preservePitch()};$('back').onclick=()=>{player.currentTime=Math.max(0,player.currentTime-3);syncReadout()};$('playPause').onclick=togglePlayback;$('mixPlayPause').onclick=togglePlayback;$('mixStop').onclick=pauseAll;
player.addEventListener('play',()=>{syncTransport();if(mixer.requestedPlaying&&!anim)anim=requestAnimationFrame(animate)});player.addEventListener('pause',()=>{syncTransport();if(anim){cancelAnimationFrame(anim);anim=null}syncReadout()});
function seekAbsolute(value){if(!Number.isFinite(value))return;const absolute=Math.max(globalStart,Math.min(globalEnd-.01,value));if(absolute>=startOf(current)&&absolute<startOf(current)+current.duration){player.currentTime=absolute-startOf(current);$('selectionHint').textContent='';syncReadout()}else{const target=tracks.find(t=>t.id==='vocal_original_mix')||tracks.find(t=>absolute>=startOf(t)&&absolute<startOf(t)+t.duration);if(target){selectedModes.set(current.id,mode);current=target;mode=selectedModes.get(target.id)||defaultMode(target);a=0;b=target.duration;looping=false;scope='all';renderTabsSelection();renderTracks();load(absolute-startOf(target));$('selectionHint').textContent=`原选轨不覆盖 ${time(absolute)}，已切换至「${target.label}」作时间参考。`}}$('seekTime').value=absolute.toFixed(1)}
function seek(){seekAbsolute(Number($('seekTime').value))}
$('seek').onclick=seek;$('seekTime').onkeydown=e=>{if(e.key==='Enter')seek()};$('timeline').oninput=e=>{wave.viewStart=null;player.currentTime=Number(e.target.value);syncReadout()};wave.onSeek=(pos,playPoint)=>{wave.viewStart=null;seekAbsolute(wave.start+pos);if(playPoint)play()};
$('markA').onclick=()=>{a=player.currentTime;if(a>=b)b=Math.min(current.duration,a+3);syncLoop()};$('markB').onclick=()=>{b=player.currentTime;if(b<=a)a=Math.max(0,b-3);syncLoop()};
$('loop').onclick=()=>{if(b-a<.05)return;looping=!looping;if(looping&&(player.currentTime<a||player.currentTime>=b))player.currentTime=a;syncLoop()};$('clearLoop').onclick=()=>{a=0;b=current.duration;looping=false;syncLoop()};
player.ontimeupdate=()=>{if(looping&&player.currentTime>=b)player.currentTime=a;syncReadout()};player.onended=()=>{if(looping){player.currentTime=a;play()}};player.onerror=()=>{$('playerError').hidden=false;$('playerError').textContent='音频资源未能加载。离线打开时请保留 audio 文件夹；下载无损请使用包含无损文件的包。'};player.addEventListener('ratechange',preservePitch);player.addEventListener('seeked',syncReadout);
function syncTimeView(){const [lo,hi]=wave.view();$('zoom').value=String(wave.zoom);$('timeWindow').max=Math.max(0,wave.duration-wave.duration/wave.zoom);$('timeWindow').value=lo;$('timeWindow').disabled=wave.zoom===1;$('timeWindowValue').textContent=`${time(wave.start+lo)}–${time(wave.start+hi)}`}
wave.onTimeViewChange=syncTimeView;$('timeWindow').oninput=e=>{wave.panTo(Number(e.target.value));syncTimeView()};$('followPlayhead').onclick=()=>{wave.viewStart=null;wave.draw();syncTimeView()};
$('mixAll').onclick=()=>selectMix(true);$('mixNone').onclick=()=>selectMix(false);$('mixSolo').onclick=()=>selectMix(false,true);
$('zoom').onchange=e=>{wave.zoom=Number(e.target.value);wave.viewStart=null;wave.draw();syncTimeView()};$('resetView').onclick=()=>wave.reset();
function rotate(dx,dy){wave.yaw=wave.wrapAngle(wave.yaw+dx);wave.pitch=wave.wrapAngle(wave.pitch+dy);syncRotation();wave.draw()}
$('rotateLeft').onclick=()=>rotate(-Math.PI/12,0);$('rotateRight').onclick=()=>rotate(Math.PI/12,0);$('rotateUp').onclick=()=>rotate(0,-Math.PI/12);$('rotateDown').onclick=()=>rotate(0,Math.PI/12);
function syncRotation(){const degrees=angle=>((angle*180/Math.PI)%360+360)%360;$('yawAngle').value=degrees(wave.yaw);$('pitchAngle').value=degrees(wave.pitch);$('yawValue').textContent=`${Math.round(degrees(wave.yaw))}°`;$('pitchValue').textContent=`${Math.round(degrees(wave.pitch))}°`}
wave.onRotationChange=syncRotation;$('yawAngle').oninput=e=>{wave.yaw=wave.wrapAngle(Number(e.target.value)*Math.PI/180);syncRotation();wave.draw()};$('pitchAngle').oninput=e=>{wave.pitch=wave.wrapAngle(Number(e.target.value)*Math.PI/180);syncRotation();wave.draw()};
$('waveMode').onchange=()=>{wave.zoom=1;wave.viewStart=null;$('zoom').value='1';loadWaves();configureMixer()};
$('trackSpacing').oninput=e=>{wave.spacing=Number(e.target.value);syncSpatial();wave.draw()};
function changeSpacing(factor){wave.spacing=Math.max(.35,Math.min(4,wave.spacing*factor));syncSpatial();wave.draw()}
$('spacingMinus').onclick=()=>changeSpacing(1/1.25);$('spacingPlus').onclick=()=>changeSpacing(1.25);
$('spatialZoom').oninput=e=>{wave.camera=1/Number(e.target.value);syncSpatial();wave.draw()};$('laneFocus').oninput=e=>{wave.focusLane=Number(e.target.value);syncSpatial();wave.draw()};
$('waveHeight').oninput=e=>{wave.heightGain=Number(e.target.value);syncSpatial();wave.draw()};
$('focusTrack').onclick=()=>{const i=wave.entries.findIndex(e=>e.trackId===current.id);if(i<0)return;wave.focusLane=i;wave.spacing=Math.max(wave.spacing,1.6);wave.camera=.65;syncSpatial();wave.draw()};
$('processing').onchange=e=>{mode=e.target.value;selectedModes.set(current.id,mode);renderTracks();load()};$('splitVocals').onclick=toggleVocalSplit;$('vocalDetailToggle').onclick=toggleVocalSplit;$('playReference').onclick=()=>selectTrack(referenceTrack);$('exportMix').onclick=exportCombination;
$('exportStart').value=ExportRange.format(globalStart);$('exportEnd').value=ExportRange.format(globalEnd);
$('exportRange').onchange=refreshExportSelection;
for(const id of ['exportStart','exportEnd']){$(id).oninput=refreshExportSelection;$(id).onkeydown=e=>{if(e.key==='Enter'&&!$('exportMix').disabled)exportCombination()}}
for(const [id,target] of [['exportStartNow','exportStart'],['exportEndNow','exportEnd']])$(id).onclick=()=>{$(target).value=ExportRange.format(startOf(current)+(player.currentTime||0));refreshExportSelection()};
$('exportCopyAB').onclick=()=>{$('exportStart').value=ExportRange.format(startOf(current)+a);$('exportEnd').value=ExportRange.format(startOf(current)+b);refreshExportSelection()};
preservePitch();renderTracks();renderWorkflow();syncRotation();load(0);
