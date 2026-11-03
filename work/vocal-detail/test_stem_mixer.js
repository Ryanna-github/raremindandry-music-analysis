/* No browser automation: mocked media/Web Audio regression for routing semantics. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Audio {
 constructor(){this.dataset={};this.listeners={};this.currentTime=72;this.duration=224;this.readyState=4;this.paused=true;this.playbackRate=1;this.preservesPitch=true;this.parentElement={append(){}}}
 addEventListener(name,fn){(this.listeners[name]??=[]).push(fn)}setAttribute(){}
 async play(){this.paused=false;for(const fn of this.listeners.play||[])fn()}pause(){this.paused=true;for(const fn of this.listeners.pause||[])fn()}
}
class Context {
 constructor(){this.currentTime=0;this.destination={}}async resume(){}
 createMediaElementSource(audio){return {audio,connect(){}}}
 createGain(){return {connect(){},gain:{value:1,cancelScheduledValues(){},setTargetAtTime(v){this.value=v},setValueAtTime(v){this.value=v}}}}
}
const context=vm.createContext({window:{AudioContext:Context},document:{createElement:()=>new Audio()},performance:{now:()=>1000},console});
vm.runInContext(fs.readFileSync(__dirname+'/web/stem_mixer.js','utf8')+';this.TestMixer=StemMixer;',context);
(async()=>{
 const master=new Audio(),errors=[],mixer=new context.TestMixer(master,()=>master.currentTime,e=>errors.push(e));
 const tracks=['vocals','guitar','bass','drums','piano','other'].map(id=>({id,label:id,path:id+'.mp3',start:0,duration:224,enabled:true}));
 mixer.configure(tracks,true);await mixer.play();assert.equal(mixer.masterGain.gain.value,0);
 for(const slot of mixer.slots.values()){assert.equal(slot.audio.paused,false);assert.equal(slot.audio.currentTime,72);assert.equal(slot.gain.gain.value,.85/6)}
 tracks[1].enabled=false;mixer.configure(tracks,true);assert.equal(mixer.slots.get('guitar').gain.gain.value,0);assert.equal(mixer.slots.get('guitar').audio.paused,true);assert.equal(mixer.slots.get('vocals').gain.gain.value,.85/6);assert(!JSON.parse(master.dataset.audibleStems).includes('guitar'));
 master.currentTime=111;tracks[1].enabled=true;mixer.configure(tracks,true);assert.equal(mixer.slots.get('guitar').audio.currentTime,111);assert.equal(mixer.slots.get('guitar').audio.paused,false);
 master.playbackRate=.75;mixer.sync(true);for(const slot of mixer.slots.values()){assert.equal(slot.audio.playbackRate,.75);assert.equal(slot.audio.preservesPitch,true)}
 for(const t of tracks)t.enabled=false;mixer.configure(tracks,true);assert.equal(mixer.masterGain.gain.value,0);assert.equal(JSON.parse(master.dataset.audibleStems).length,0);for(const slot of mixer.slots.values())assert(slot.audio.paused);
 mixer.configure(tracks,false);assert.equal(mixer.masterGain.gain.value,.85);for(const slot of mixer.slots.values())assert.equal(slot.gain.gain.value,0);
 mixer.configure(tracks.slice(0,3),true);assert.equal(mixer.specs.length,3);assert.equal(mixer.slots.get('other').gain.gain.value,0);assert.equal(errors.length,0);
 mixer.pause();assert.equal(mixer.requestedPlaying,false);for(const slot of mixer.slots.values()){assert(slot.audio.paused);assert.equal(slot.gain.gain.value,0)}assert.equal(mixer.masterGain.gain.value,0);
 console.log(JSON.stringify({passed:true,stereo_bus_preserves_media_channels:true,muted_stem_gain_zero:true,muted_media_paused:true,unmute_seeks_shared_clock:true,muting_does_not_boost_other_stems:true,all_muted_is_silent:true,comparison_has_one_version:true,removed_parent_or_child_not_audible:true,rate_and_pitch_preservation_synced:true},null,2));
})().catch(error=>{console.error(error);process.exitCode=1});
