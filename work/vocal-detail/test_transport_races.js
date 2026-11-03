/* Async transport regression. No real media playback or browser automation. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const resumes=[];
class Audio {
 constructor(){this.dataset={};this.listeners={};this.currentTime=72;this.readyState=4;this.paused=true;this.playbackRate=1;this.playCalls=0;this.parentElement={append(){}}}
 addEventListener(n,fn){(this.listeners[n]??=[]).push(fn)}setAttribute(){}
 async play(){this.playCalls++;this.paused=false;for(const fn of this.listeners.play||[])fn()}
 pause(){this.paused=true;for(const fn of this.listeners.pause||[])fn()}
}
class Context {
 constructor(){this.currentTime=0;this.destination={}}resume(){return new Promise(resolve=>resumes.push(resolve))}
 createMediaElementSource(){return {connect(){}}}createGain(){return {connect(){},gain:{value:1,cancelScheduledValues(){},setTargetAtTime(v){this.value=v},setValueAtTime(v){this.value=v}}}}
}
const context=vm.createContext({window:{AudioContext:Context},document:{createElement:()=>new Audio()},performance:{now:()=>1000},console});
vm.runInContext(fs.readFileSync(__dirname+'/web/stem_mixer.js','utf8')+';this.TestMixer=StemMixer;',context);
(async()=>{
 const master=new Audio(),mixer=new context.TestMixer(master,()=>master.currentTime,()=>{}),tracks=['lead','guitar'].map(id=>({id,label:id,path:id+'.mp3',start:0,duration:224,enabled:true}));mixer.configure(tracks,true);
 const first=mixer.play();assert.equal(mixer.requestedPlaying,true);mixer.pause();resumes.shift()();assert.equal(await first,false);assert(master.paused);assert.equal(master.playCalls,0,'cancel before context resume prevents media play');
 const obsolete=mixer.play(),resumeOld=resumes.shift();mixer.pause();const latest=mixer.play(),resumeNew=resumes.shift();resumeNew();assert.equal(await latest,true);resumeOld();assert.equal(await obsolete,false);assert.equal(master.paused,false,'late obsolete request cannot stop or replace a newer play');
 mixer.pause();assert.equal(mixer.masterGain.gain.value,0);for(const slot of mixer.slots.values()){assert(slot.audio.paused);assert.equal(slot.gain.gain.value,0)}
 let finishMedia,signalMedia;const mediaStarted=new Promise(resolve=>{signalMedia=resolve});master.play=()=>new Promise(resolve=>{finishMedia=()=>{master.paused=false;for(const fn of master.listeners.play||[])fn();resolve()};signalMedia()});
 const lateMedia=mixer.play();resumes.shift()();await mediaStarted;mixer.pause();finishMedia();assert.equal(await lateMedia,false);assert(master.paused,'late native play event must be immediately stopped');
 tracks[0].enabled=false;mixer.configure(tracks,true);mixer.sync(true);assert(master.paused);for(const slot of mixer.slots.values()){assert(slot.audio.paused);assert.equal(slot.gain.gain.value,0)}
 console.log(JSON.stringify({passed:true,pause_cancels_pending_context_resume:true,pause_stops_late_native_play:true,newer_user_play_wins:true,pause_zeros_all_gains:true,configure_while_paused_does_not_restart:true},null,2));
})().catch(error=>{console.error(error);process.exitCode=1});
