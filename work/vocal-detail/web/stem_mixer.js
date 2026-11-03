/* Stereo audition bus. Media retain both channels; the main audio is the shared clock. */
class StemMixer {
 constructor(master,absoluteTime,onError){
  this.master=master;this.absoluteTime=absoluteTime;this.onError=onError;this.slots=new Map();this.specs=[];this.multi=true;this.lastSync=0;this.context=null;this.requestedPlaying=false;this.starting=false;this.revision=0;
  master.addEventListener('play',()=>{if(!this.requestedPlaying){this.pause();return}this.starting=false;this.applyGains();this.sync(true);this.onStateChange?.()});
  master.addEventListener('pause',()=>{this.pauseFollowers();this.applyGains();this.onStateChange?.()});
  master.addEventListener('seeking',()=>this.sync(true));
  master.addEventListener('seeked',()=>this.sync(true));
  master.addEventListener('ratechange',()=>this.sync(true));
  master.addEventListener('ended',()=>{this.requestedPlaying=false;this.starting=false;this.revision++;this.pauseFollowers();this.applyGains();this.onStateChange?.()});
 }
 async activate(){
  if(!this.context){
   const Context=window.AudioContext||window.webkitAudioContext;if(!Context)throw Error('此浏览器不支持多轨音频，请使用支持 Web Audio 的浏览器。');
   this.context=new Context();this.masterSource=this.context.createMediaElementSource(this.master);this.masterGain=this.context.createGain();this.masterSource.connect(this.masterGain);this.masterGain.connect(this.context.destination);
   for(const slot of this.slots.values())this.connect(slot);
  }
  await this.context.resume();this.applyGains();
 }
 connect(slot){if(slot.source)return;slot.source=this.context.createMediaElementSource(slot.audio);slot.gain=this.context.createGain();slot.source.connect(slot.gain);slot.gain.connect(this.context.destination)}
 configure(specs,multi){
  this.specs=specs;this.multi=multi;
  for(const spec of specs){
   let slot=this.slots.get(spec.id);
   if(!slot){
    const audio=document.createElement('audio');audio.preload='metadata';audio.dataset.stemId=spec.id;audio.setAttribute('aria-hidden','true');audio.hidden=true;this.master.parentElement.append(audio);
    slot={audio,path:null,spec,failed:false,pending:false};this.slots.set(spec.id,slot);if(this.context)this.connect(slot);
    audio.addEventListener('loadedmetadata',()=>{slot.failed=false;this.syncSlot(slot,true)});
    audio.addEventListener('error',()=>{slot.failed=true;this.applyGains();this.onError?.(`「${slot.spec.label}」音频未能加载，请检查本地音频文件。`)});
   }
   slot.spec=spec;
   if(slot.path!==spec.path){slot.audio.pause();slot.audio.src=spec.path;slot.path=spec.path;slot.failed=false}
  }
  this.applyGains();for(const slot of this.slots.values())this.syncSlot(slot,false);this.publish();
 }
 included(slot){return this.multi&&this.specs.some(s=>s.id===slot.spec.id)&&slot.spec.enabled&&!slot.failed}
 setGain(gain,value,immediate=false){if(!gain)return;gain.gain.cancelScheduledValues(this.context.currentTime);if(immediate)gain.gain.setValueAtTime(value,this.context.currentTime);else gain.gain.setTargetAtTime(value,this.context.currentTime,.012)}
 applyGains(){
  // Fixed headroom by displayed-lane count. Muting does not boost the remaining stems.
  const laneGain=.85/Math.max(1,this.specs.length);
  const playing=this.requestedPlaying&&!this.master.paused;
  this.setGain(this.masterGain,playing&&!this.multi?.85:0,!playing);
  for(const slot of this.slots.values())this.setGain(slot.gain,playing&&this.included(slot)?laneGain:0,!playing);
  this.publish();
 }
 publish(){
  this.master.dataset.playbackMode=this.multi?'selected_stereo_stems':'single_reference_or_comparison';
  const playing=this.requestedPlaying&&!this.master.paused;
  this.master.dataset.transportState=this.requestedPlaying?(this.starting?'starting':'playing'):'paused';
  this.master.dataset.selectedStems=JSON.stringify(this.specs.filter(s=>s.enabled).map(s=>s.id));
  this.master.dataset.audibleStems=JSON.stringify(this.multi&&playing?this.specs.filter(s=>s.enabled&&!this.slots.get(s.id)?.failed).map(s=>s.id):[]);
  this.master.dataset.stemGain=(.85/Math.max(1,this.specs.length)).toFixed(4);this.master.dataset.audioChannels='stereo_preserved';
  for(const slot of this.slots.values())slot.audio.dataset.mixGainTarget=String(playing&&this.included(slot)?.85/Math.max(1,this.specs.length):0);
 }
 syncSlot(slot,force){
  const audio=slot.audio,target=this.absoluteTime()-slot.spec.start,covered=target>=0&&target<slot.spec.duration;
  const enabled=this.included(slot)&&covered;
  for(const key of ['preservesPitch','webkitPreservesPitch','mozPreservesPitch'])if(key in audio)audio[key]=true;
  audio.playbackRate=this.master.playbackRate;
  if(enabled&&audio.readyState>=1&&(force||Math.abs(audio.currentTime-target)>.075))audio.currentTime=Math.max(0,Math.min(slot.spec.duration-.01,target));
  if(!enabled||!this.requestedPlaying||this.master.paused||!this.context){audio.pause();return}
  if(audio.paused&&!slot.pending){slot.pending=true;audio.play().catch(error=>{if(error.name!=='AbortError'&&this.requestedPlaying){slot.failed=true;this.applyGains();this.onError?.(`「${slot.spec.label}」播放失败，请重试播放。`)}}).finally(()=>{slot.pending=false;if(!this.included(slot)||!this.requestedPlaying||this.master.paused)audio.pause()})}
 }
 sync(force=false){
  const now=performance.now();if(!force&&now-this.lastSync<400)return;this.lastSync=now;
  for(const slot of this.slots.values())this.syncSlot(slot,force);
 }
 pauseFollowers(){for(const slot of this.slots.values())slot.audio.pause()}
 pause(){this.revision++;this.requestedPlaying=false;this.starting=false;this.master.pause();this.pauseFollowers();this.applyGains();this.onStateChange?.()}
 async play(){
  const revision=++this.revision;this.requestedPlaying=true;this.starting=true;this.publish();this.onStateChange?.();
  try{
   await this.activate();if(revision!==this.revision||!this.requestedPlaying)return false;
   await this.master.play();
   if(revision!==this.revision||!this.requestedPlaying){if(!this.requestedPlaying){this.master.pause();this.pauseFollowers();this.applyGains()}return false}
   this.starting=false;this.applyGains();this.sync(true);this.onStateChange?.();return true;
  }catch(error){if(revision!==this.revision||!this.requestedPlaying)return false;this.pause();throw error}
 }
}
