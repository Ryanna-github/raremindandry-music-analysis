/* Native WebGL. Geometry is driven ONLY by measured stereo audio envelopes. */
class WaveLab {
 constructor(canvas,labels,overview){
  this.canvas=canvas;this.labels=labels;this.overview=overview;this.entries=[];this.position=0;this.start=0;this.duration=1;this.zoom=1;
  this.yaw=-.48;this.pitch=.66;this.camera=1;this.spacing=1;this.heightGain=1.5;this.defaultHeightGain=1.5;this.peakHeight=6.2;this.visualPower=.78;this.focusLane=0;this.unified=true;this.selectedId=null;this.loop=null;this.objects=[];this.geometryKey='';this.hovered=null;this.lastOverviewKey='';this.viewStart=null;
  this.gl=canvas.getContext('webgl',{alpha:true,antialias:true,preserveDrawingBuffer:true});
  if(this.gl){try{this.initGL()}catch(e){console.error(e);this.gl=null}}
  canvas.dataset.renderer=this.gl?'webgl':'2d-fallback';
  const stage=canvas.parentElement;
  this.labelControls=stage.querySelector?.('#waveLabelControls');this.labelButtons=new Map();
  stage.addEventListener('pointerdown',e=>{if(e.button!==0)return;stage.setPointerCapture(e.pointerId);this.drag=[e.clientX,e.clientY,this.yaw,this.pitch]});
  stage.addEventListener('pointermove',e=>{if(!this.drag){this.hitTest(e);return}this.yaw=this.wrapAngle(this.drag[2]+(e.clientX-this.drag[0])*.006);this.pitch=this.wrapAngle(this.drag[3]+(e.clientY-this.drag[1])*.006);this.onRotationChange?.();this.draw()});
  stage.addEventListener('pointerup',e=>{if(this.drag&&Math.hypot(e.clientX-this.drag[0],e.clientY-this.drag[1])<5){this.hitTest(e);if(this.hovered)this.onSelect?.(this.hovered)}this.drag=null});stage.addEventListener('pointercancel',()=>{this.drag=null});stage.addEventListener('pointerleave',()=>{if(!this.drag){this.hovered=null;this.onHover?.(null)}});
  stage.addEventListener('wheel',e=>{if(!e.shiftKey&&!e.ctrlKey&&!e.metaKey)return;e.preventDefault();if(e.shiftKey){this.spacing=Math.max(.35,Math.min(4,this.spacing*Math.exp(-e.deltaY*.0015)))}else this.camera=Math.max(1/3,Math.min(2.5,this.camera*Math.exp(e.deltaY*.0015)));this.onSpatialChange?.();this.draw()},{passive:false});
  // Dedicated horizontal axis: click to play a point; drag pans the view without seeking audio.
  overview.addEventListener('pointerdown',e=>{if(e.button!==0)return;overview.setPointerCapture(e.pointerId);const r=overview.getBoundingClientRect();this.timeDrag={x:e.clientX,width:r.width,left:r.left,start:this.view()[0],zoom:this.zoom,moved:false}});
  overview.addEventListener('pointermove',e=>{const d=this.timeDrag;if(!d)return;const dx=e.clientX-d.x;if(Math.abs(dx)<5&&!d.moved)return;if(!d.moved&&this.zoom===1){this.zoom=4;d.start=Math.max(0,this.position-this.duration/8);d.zoom=4}d.moved=true;this.viewStart=Math.max(0,Math.min(this.duration-this.duration/this.zoom,d.start-dx/d.width*this.duration/d.zoom));this.onTimeViewChange?.();this.draw()});
  overview.addEventListener('pointerup',e=>{const d=this.timeDrag;if(!d)return;this.timeDrag=null;if(!d.moved)this.onSeek?.(Math.max(0,Math.min(this.duration-.01,(e.clientX-d.left)/d.width*this.duration)),true)});
  overview.addEventListener('pointercancel',()=>{this.timeDrag=null});
  new ResizeObserver(()=>this.draw()).observe(stage);new ResizeObserver(()=>this.drawOverview()).observe(overview);
 }
 initGL(){
  const gl=this.gl;
  const compile=(type,src)=>{const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s};
  const vertex=compile(gl.VERTEX_SHADER,'attribute vec3 pos;uniform mat4 mvp;uniform vec4 view;uniform mediump vec4 material;varying float h;varying float vx;void main(){vec3 p=pos;p.y*=material.x*(1.0+material.y);p.z=(p.z-material.z)*(1.0+material.y*5.0)+material.z;p.x=(p.x-view.x)*view.y;p.z=(p.z-view.w)*view.z;h=abs(p.y);vx=p.x;gl_Position=mvp*vec4(p,1.0);}');
  const fragment=compile(gl.FRAGMENT_SHADER,'precision mediump float;uniform vec4 tint;uniform vec4 material;uniform float halfX;varying float h;varying float vx;void main(){if(abs(vx)>halfX)discard;float bands=mix(1.0,0.82+0.18*smoothstep(0.06,0.22,fract(h*1.6)),material.w);float light=0.76+0.24*min(h/5.0,1.0);gl_FragColor=vec4(tint.rgb*light*bands,tint.a);}');
  this.program=gl.createProgram();gl.attachShader(this.program,vertex);gl.attachShader(this.program,fragment);gl.linkProgram(this.program);
  if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
  gl.deleteShader(vertex);gl.deleteShader(fragment);this.pos=gl.getAttribLocation(this.program,'pos');this.mvp=gl.getUniformLocation(this.program,'mvp');this.tint=gl.getUniformLocation(this.program,'tint');this.viewUniform=gl.getUniformLocation(this.program,'view');this.halfUniform=gl.getUniformLocation(this.program,'halfX');this.material=gl.getUniformLocation(this.program,'material');
  gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.disable(gl.CULL_FACE);
 }
 multiply(a,b){const out=new Float32Array(16);for(let c=0;c<4;c++)for(let r=0;r<4;r++)for(let k=0;k<4;k++)out[c*4+r]+=a[k*4+r]*b[c*4+k];return out}
 wrapAngle(value){return ((value+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI}
 matrix(w,h){
  const sy=Math.sin(this.yaw),cy=Math.cos(this.yaw),sp=Math.sin(this.pitch),cp=Math.cos(this.pitch);
  const ry=new Float32Array([cy,0,-sy,0,0,1,0,0,sy,0,cy,0,0,0,0,1]);
  const rx=new Float32Array([1,0,0,0,0,cp,sp,0,0,-sp,cp,0,0,0,0,1]);
  const f=1/Math.tan(.53/2),near=.1,far=1000;
  const halfDepth=Math.max(2,(this.entries.length-1)*this.baseSpacing/2);
  const extentX=Math.abs(cy)*this.halfX+Math.abs(sy)*halfDepth;
  const extentZ=Math.abs(sy)*this.halfX+Math.abs(cy)*halfDepth;
  const extentY=Math.abs(sp)*extentZ+Math.abs(cp)*(this.peakHeight*this.heightGain+1);
  const fit=Math.max(28,extentX*f/(w/h)*1.13,extentY*f*1.2)+extentZ*Math.abs(cp)*.45;
  const distance=fit*this.camera;
  const tr=new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,-distance,1]);
  const p=new Float32Array([f/(w/h),0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);
  return this.multiply(p,this.multiply(tr,this.multiply(rx,ry)));
 }
 project(x,y,z){const m=this.mat;const q=[x,y,(z-this.focusOffset)*this.spacing,1],o=[0,0,0,0];for(let r=0;r<4;r++)for(let k=0;k<4;k++)o[r]+=m[k*4+r]*q[k];return [(o[0]/o[3]*.5+.5)*this.width,(-o[1]/o[3]*.5+.5)*this.height]}
 add(vertices,color,kind,id=null,layer='solid',lane=0){if(!vertices.length)return;const gl=this.gl,buf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buf);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);this.objects.push({buf,count:vertices.length/3,color,kind,id,layer,lane})}
 setData(entries,start,duration,unified=true){const changed=this.unified!==unified||this.entries.length!==entries.length;this.entries=entries;this.start=start;this.duration=duration;this.unified=unified;this.halfX=unified?32:22;this.baseSpacing=unified?(entries.length<=12?5.8:1.4):5.8;if(changed)this.focusLane=(entries.length-1)/2;this.scale=Math.max(.00001,...entries.map(e=>Math.max(...e.data.max.flat(),...e.data.min.flat().map(Math.abs))));this.geometryKey='';this.lastOverviewKey='';this.canvas.dataset.coverage=JSON.stringify(entries.map(e=>({id:e.id,start:e.start,end:e.start+e.duration})));this.draw()}
 visualAmplitude(value){return Math.sign(value)*Math.pow(Math.abs(value)/this.scale,this.visualPower)*this.peakHeight}
 view(){const length=this.duration/this.zoom;const lo=Math.max(0,Math.min(this.duration-length,this.viewStart??this.position-length/2));return [lo,lo+length]}
 panTo(start){this.viewStart=Math.max(0,Math.min(this.duration-this.duration/this.zoom,start));this.draw()}
 stereoEnvelope(d,i){return {max:Math.max(...d.max.map(c=>c[i])),min:Math.min(...d.min.map(c=>c[i])),rms:Math.sqrt(d.rms.reduce((sum,c)=>sum+c[i]*c[i],0)/d.rms.length)}}
 labelLayout(w,h){
  const gap=Math.min(21,(h-110)/Math.max(1,this.entries.length-1));
  const [lo,hi]=this.view();
  const items=this.entries.map((e,i)=>{const sec=Math.max(lo,Math.min(hi,e.start-this.start)),x=(sec-lo)/(hi-lo)*this.halfX*2-this.halfX,p=this.project(x,0,(i-(this.entries.length-1)/2)*this.baseSpacing-.72);return {e,i,p,y:Math.max(58,Math.min(h-48,Number.isFinite(p[1])?p[1]:58+i*gap))}}).sort((a,b)=>a.y-b.y);
  for(let i=1;i<items.length;i++)items[i].y=Math.max(items[i].y,items[i-1].y+gap);
  for(let i=items.length-1;i>=0;i--)items[i].y=Math.min(items[i].y,i===items.length-1?h-48:items[i+1].y-gap);
  return items;
 }
 build(){
  const gl=this.gl;for(const o of this.objects)gl.deleteBuffer(o.buf);this.objects=[];
  const depth=Math.max(3,(this.entries.length-1)*this.baseSpacing/2+2),grid=[];this.depth=depth;this.floor=-this.peakHeight-1;
  for(let x=-this.halfX;x<=this.halfX+.01;x+=this.halfX/8)grid.push(x,this.floor,-depth,x,this.floor,depth);for(let z=-depth;z<=depth;z+=this.baseSpacing)grid.push(-this.halfX,this.floor,z,this.halfX,this.floor,z);
  this.add(grid,[.17,.32,.46,.42],gl.LINES,null,'grid');
  this.entries.forEach((entry,index)=>{
   const d=entry.data,z=(index-(this.entries.length-1)/2)*this.baseSpacing;
   const l=0,r=d.bins-1,c=entry.color,bright=c;
   {
    const front=[],back=[],top=[],bottom=[],upper=[],lower=[],rmsLine=[],zc=z,zf=zc-.72,zb=zc+.72;
    for(let i=l;i<=r;i++){
     const local=i*d.hop_samples/d.sample_rate;if(local>entry.duration)break;const sec=entry.start-this.start+local,x=sec/this.duration*this.halfX*2-this.halfX;
     const envelope=this.stereoEnvelope(d,i),max=this.visualAmplitude(envelope.max),min=this.visualAmplitude(envelope.min);
     front.push(x,max,zf,x,min,zf);back.push(x,min,zb,x,max,zb);top.push(x,max,zf,x,max,zb);bottom.push(x,min,zb,x,min,zf);upper.push(x,max,zf);lower.push(x,min,zf);rmsLine.push(x,this.visualAmplitude(envelope.rms),zf-.015);
    }
    // One display body per stem: union stereo peaks, energy-averaged RMS. Audio is untouched.
    this.add(front,[...c,1],gl.TRIANGLE_STRIP,entry.id,'solid',zc);this.add(back,[...c.map(v=>v*.68),1],gl.TRIANGLE_STRIP,entry.id,'solid',zc);
    this.add(top,[...bright,1],gl.TRIANGLE_STRIP,entry.id,'roof',zc);this.add(bottom,[...c.map(v=>v*.55),1],gl.TRIANGLE_STRIP,entry.id,'roof',zc);
    this.add(upper,[...bright,.85],gl.LINE_STRIP,entry.id,'edge',zc);this.add(lower,[...bright,.5],gl.LINE_STRIP,entry.id,'edge',zc);this.add(rmsLine,[...bright,.65],gl.LINE_STRIP,entry.id,'edge',zc);
    this.add(front,[...c,.12],gl.TRIANGLE_STRIP,entry.id,'glow',zc);this.add(back,[...c,.1],gl.TRIANGLE_STRIP,entry.id,'glow',zc);
   }
   const x1=(entry.start-this.start)/this.duration*this.halfX*2-this.halfX,x2=(entry.start+entry.duration-this.start)/this.duration*this.halfX*2-this.halfX;
   this.add([x1,0,z-1.05,x2,0,z-1.05],[...c,.6],gl.LINES,entry.id,'edge',z);
   this.add([x1,this.floor,z-.8,x1,this.floor,z+.8,x2,this.floor,z-.8,x2,this.floor,z+.8],[...c,.065],gl.TRIANGLE_STRIP,entry.id,'grid',z);
  });
 }
 fit(canvas){const rect=canvas.getBoundingClientRect(),ratio=Math.min(devicePixelRatio||1,2);const w=Math.round(rect.width*ratio),h=Math.round(rect.height*ratio);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}return [rect.width,rect.height,ratio]}
 renderTint(object,enabled,active){
  // Always copy: muting must not accumulate alpha loss in the cached material.
  // Selection changes emphasis, never the source hue.
  const tint=object.color.slice();
  if(active&&object.layer==='edge')tint[3]=Math.min(1,tint[3]*1.2);
  // A body has front, back, roof and rim passes. Keep each muted pass faint so their
  // combined silhouette does not paint a different hue over the audible lane.
  if(!enabled)tint[3]=object.layer==='edge'?Math.max(.025,object.color[3]*.045):object.color[3]*.08;
  return tint;
 }
 draw(){
  if(!this.entries.length)return;
  const [w,h,dpr]=this.fit(this.canvas);if(w<1||h<1)return;this.width=w;this.height=h;this.mat=this.matrix(w,h);
  const [lo,hi]=this.view();this.focusOffset=(this.focusLane-(this.entries.length-1)/2)*this.baseSpacing;this.labelRects=[];
  this.canvas.dataset.yaw=this.yaw.toFixed(3);this.canvas.dataset.pitch=this.pitch.toFixed(3);this.canvas.dataset.rotationMode='360_free_orbit';this.canvas.dataset.windowStart=(lo+this.start).toFixed(3);this.canvas.dataset.windowEnd=(hi+this.start).toFixed(3);this.canvas.dataset.lanes=String(this.entries.length);this.canvas.dataset.spacing=this.spacing.toFixed(3);this.canvas.dataset.spatialZoom=(1/this.camera).toFixed(3);this.canvas.dataset.selectedId=this.selectedId||'';this.canvas.dataset.focusLane=this.focusLane.toFixed(2);this.canvas.dataset.heightGain=this.heightGain.toFixed(2);this.canvas.dataset.visualTransform='shared_scale_signed_power_0.78';this.canvas.dataset.geometry='single_body_stereo_envelope';this.canvas.dataset.displayBodies=String(this.entries.length);this.canvas.dataset.enabledIds=JSON.stringify(this.entries.filter(e=>e.enabled!==false).map(e=>e.id));this.canvas.dataset.colors=JSON.stringify(this.entries.map(e=>({id:e.id,color:e.css})));this.canvas.dataset.labelsPersistent='true';this.canvas.dataset.mutedOpacity='0.32';
  this.canvas.dataset.colorPolicy='source_hue_locked_with_label_leaders';this.canvas.dataset.mutedOpacity='0.08';
  if(this.gl){
   const gl=this.gl;gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);gl.uniformMatrix4fv(this.mvp,false,this.mat);
   const center=((lo+hi)/2)/this.duration*this.halfX*2-this.halfX;
   gl.uniform4f(this.viewUniform,center,this.zoom,this.spacing,this.focusOffset);gl.uniform1f(this.halfUniform,this.halfX);
   if(!this.geometryKey){this.build();this.geometryKey='built'}
   gl.enableVertexAttribArray(this.pos);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);
   const included=o=>!o.id||this.entries.find(e=>e.id===o.id)?.enabled!==false;
   const render=(o,expansion=0)=>{const active=o.id&&o.id===this.selectedId;gl.bindBuffer(gl.ARRAY_BUFFER,o.buf);gl.vertexAttribPointer(this.pos,3,gl.FLOAT,false,0,0);
    const tint=this.renderTint(o,included(o),active);
    gl.uniform4fv(this.tint,tint);gl.uniform4f(this.material,o.layer==='grid'?1:this.heightGain,expansion,o.lane,o.layer==='solid'?1:0);gl.drawArrays(o.kind,0,o.count)};
   gl.depthMask(false);for(const o of this.objects.filter(o=>o.layer==='grid'))render(o);
   gl.depthMask(true);for(const o of this.objects.filter(o=>(o.layer==='solid'||o.layer==='roof')&&included(o)))render(o);
   gl.depthMask(false);for(const o of this.objects.filter(o=>(o.layer==='solid'||o.layer==='roof')&&!included(o)))render(o);
   gl.depthMask(false);for(const o of this.objects.filter(o=>o.layer==='edge'&&included(o)))render(o);
   // Muted silhouettes remain discoverable even behind an enabled waveform wall.
   gl.disable(gl.DEPTH_TEST);for(const o of this.objects.filter(o=>o.layer==='edge'&&!included(o)))render(o);gl.enable(gl.DEPTH_TEST);
   // Subtle source-colored rim, not additive colored walls that tint other stems.
   for(const o of this.objects.filter(o=>o.layer==='glow'&&included(o)))render({...o,color:[...o.color.slice(0,3),o.color[3]*.4]},.025);gl.depthMask(true);
  }
  this.fit(this.labels);const ctx=this.labels.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.font='11px ui-monospace,monospace';ctx.fillStyle='#849db3';ctx.textAlign='center';
  const depth=this.depth||3,floor=this.floor||-1.7,ticks=w<480?3:7;
  for(let i=0;i<ticks;i++){const x=-this.halfX+i/(ticks-1)*this.halfX*2,p=this.project(x,floor-.2,depth);const sec=this.start+lo+i/(ticks-1)*(hi-lo);if(p[1]>32&&p[1]<h-16)ctx.fillText(this.time(sec),Math.max(27,Math.min(w-27,p[0])),p[1]+13)}
  ctx.textAlign='left';const labelItems=this.labelLayout(w,h),entryIds=new Set(this.entries.map(e=>e.id));
  for(const [id,button] of this.labelButtons)if(!entryIds.has(id)&&!button.hidden)button.hidden=true;
  for(const {e,i,p,y} of labelItems){const active=e.id===this.selectedId;
   const label=`${this.unified?(e.enabled===false?'□':'■'):(active?'▶':'◇')} ${String(i+1).padStart(2,'0')} ${e.shortLabel.slice(0,w<600?9:15)}${e.shortLabel.length>(w<600?9:15)?'…':''}`;
   ctx.fillStyle=e.css;const textWidth=ctx.measureText(label).width,x=Math.max(8,Math.min(w-textWidth-8,(Number.isFinite(p[0])?p[0]:8)-textWidth-9));
   // Labels may move to avoid overlap: connect each one to its actual lane, not its neighbour.
   const anchor=[Math.max(8,Math.min(w-8,p[0])),Math.max(42,Math.min(h-36,p[1]))];
   if(anchor.every(Number.isFinite)){const side=anchor[0]<x+textWidth/2?x-5:x+textWidth+5;ctx.strokeStyle=e.css;ctx.lineWidth=active?1.5:1;ctx.globalAlpha=e.enabled===false?.5:.85;ctx.setLineDash?.(e.enabled===false?[3,3]:[]);ctx.beginPath();ctx.moveTo(side,y-2);ctx.lineTo((side+anchor[0])/2,y-2);ctx.lineTo(...anchor);ctx.stroke();ctx.setLineDash?.([]);ctx.globalAlpha=1;ctx.fillStyle=e.css;ctx.fillRect(anchor[0]-2,anchor[1]-2,4,4)}
   if(this.labelControls){let button=this.labelButtons.get(e.id);if(!button){button=document.createElement('button');button.dataset.waveId=e.id;for(const name of ['pointerdown','pointerup'])button.addEventListener(name,event=>event.stopPropagation());button.onclick=event=>{event.stopPropagation();const entry=this.entries.find(entry=>entry.id===button.dataset.waveId);if(entry)this.onSelect?.(entry)};this.labelButtons.set(e.id,button);this.labelControls.append(button)}if(button.hidden)button.hidden=false;if(button.textContent!==label)button.textContent=label;const aria=`图中 ${e.label} ${this.unified?'播放勾选':'处理对照'}`,pressed=String(this.unified?e.enabled!==false:active);if(button.getAttribute('aria-label')!==aria)button.setAttribute('aria-label',aria);if(button.getAttribute('aria-pressed')!==pressed)button.setAttribute('aria-pressed',pressed);button.style.left=`${(x-4).toFixed(1)}px`;button.style.top=`${(y-10).toFixed(1)}px`;button.style.color=e.css;button.style.setProperty('--track-color',e.css);button.dataset.color=e.css;button.dataset.active=String(active);button.dataset.anchor=JSON.stringify(anchor)}
   else {ctx.fillStyle='#060b17de';ctx.fillRect(x-4,y-10,textWidth+8,16);ctx.fillStyle=e.css;if(active)ctx.fillRect(x-6,y-10,2,16);ctx.fillText(label,x,y+3)}this.labelRects.push({x,y:y-10,w:textWidth+8,h:16,entry:e});
  }
  const selected=this.entries.find(e=>e.id===this.selectedId);if(selected){ctx.fillStyle='#bddbe5';ctx.font='11px system-ui,sans-serif';ctx.fillText(this.unified?`当前编辑：${selected.label} · ${this.entries.filter(e=>e.enabled!==false).length} 轨勾选`:`当前处理：${selected.label} · 只播放此版本`,16,h-12)}
  const xp=(this.position-lo)/(hi-lo)*this.halfX*2-this.halfX;
  const p1=this.project(xp,floor,-depth),p2=this.project(xp,floor,depth),p3=this.project(xp,this.peakHeight*this.heightGain,depth);
  if(xp>=-this.halfX&&xp<=this.halfX){ctx.strokeStyle='#c2f8ffb0';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(...p1);ctx.lineTo(...p2);ctx.lineTo(...p3);ctx.stroke()}
  if(!this.gl){ctx.fillStyle='#abc4d7';ctx.fillText('WebGL 不可用 · 下方真实波形仍可定位与试听',20,58)}
  this.drawOverview();
 }
 hitTest(event){if(!this.entries.length||!this.mat)return;const rect=this.canvas.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
  const label=this.labelRects.find(r=>x>=r.x-4&&x<=r.x+r.w&&y>=r.y-3&&y<=r.y+r.h+3);let entry=label?.entry||null,best=10;
  if(!entry){const [lo,hi]=this.view();let ghostDistance=5;
   // Ghost outlines are drawn above opaque walls: click their visible edges to restore them.
   for(let lane=0;lane<this.entries.length;lane++){const e=this.entries[lane];if(e.enabled!==false)continue;const d=e.data,z=(lane-(this.entries.length-1)/2)*this.baseSpacing-.72;
    for(const boundary of ['max','min']){let previous=null;for(let i=0;i<d.bins;i+=Math.max(1,Math.floor(d.bins/240))){const sec=e.start-this.start+i*d.hop_samples/d.sample_rate;if(sec<lo||sec>hi||sec>e.start-this.start+e.duration){previous=null;continue}const envelope=this.stereoEnvelope(d,i),p=this.project((sec-lo)/(hi-lo)*this.halfX*2-this.halfX,this.visualAmplitude(envelope[boundary])*this.heightGain,z);
      if(previous){const dx=p[0]-previous[0],dy=p[1]-previous[1],q=Math.max(0,Math.min(1,((x-previous[0])*dx+(y-previous[1])*dy)/(dx*dx+dy*dy+1e-8))),distance=Math.hypot(x-previous[0]-q*dx,y-previous[1]-q*dy);if(distance<ghostDistance){ghostDistance=distance;entry=e}}previous=p;
    }}
   }
  }
  if(!entry){const [lo,hi]=this.view();for(let i=0;i<this.entries.length;i++){const e=this.entries[i],s=Math.max(lo,e.start-this.start),t=Math.min(hi,e.start+e.duration-this.start);if(t<=s)continue;
   const z=(i-(this.entries.length-1)/2)*this.baseSpacing,a=this.project((s-lo)/(hi-lo)*this.halfX*2-this.halfX,0,z),b=this.project((t-lo)/(hi-lo)*this.halfX*2-this.halfX,0,z),dx=b[0]-a[0],dy=b[1]-a[1],q=Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy+1e-8))),distance=Math.hypot(x-a[0]-q*dx,y-a[1]-q*dy);if(distance<best){best=distance;entry=e}}
   // Hit the actual volume too, not only its zero baseline. Pick the nearest visible depth.
   let nearest=Infinity;
   for(let lane=0;lane<this.entries.length;lane++){const candidate=this.entries[lane],d=candidate.data,z=(lane-(this.entries.length-1)/2)*this.baseSpacing;
    for(let i=0;i<d.bins;i+=Math.max(1,Math.floor(d.bins/240))){const sec=candidate.start-this.start+i*d.hop_samples/d.sample_rate;if(sec<lo||sec>hi||sec>candidate.start-this.start+candidate.duration)continue;
     const xx=(sec-lo)/(hi-lo)*this.halfX*2-this.halfX,envelope=this.stereoEnvelope(d,i),a=this.project(xx,this.visualAmplitude(envelope.max)*this.heightGain,z-.72),b=this.project(xx,this.visualAmplitude(envelope.min)*this.heightGain,z-.72),dx=b[0]-a[0],dy=b[1]-a[1],q=Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy+1e-8))),distance=Math.hypot(x-a[0]-q*dx,y-a[1]-q*dy),cameraDepth=this.mat[2]*xx+this.mat[6]*(this.visualAmplitude(envelope.max)*(1-q)+this.visualAmplitude(envelope.min)*q)*this.heightGain+this.mat[10]*(z-this.focusOffset)*this.spacing+this.mat[14];
     if(distance<7&&-cameraDepth<nearest){nearest=-cameraDepth;entry=candidate}
    }
   }
  }this.hovered=entry;this.onHover?.(entry,{x,y});
 }
 time(t){return `${String(Math.floor(t/60)).padStart(2,'0')}:${(t%60).toFixed(1).padStart(4,'0')}`}
 drawOverview(){
  const [w,h,dpr]=this.fit(this.overview);if(!w)return;const c=this.overview.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);if(!this.entries.length)return;
  const shown=this.unified?this.entries.filter(e=>e.enabled!==false):this.entries.filter(e=>e.id===this.selectedId);
  this.overview.dataset.shownIds=JSON.stringify(shown.map(e=>e.trackId||e.id));this.overview.dataset.colors=JSON.stringify(shown.map(e=>({id:e.trackId||e.id,color:e.css})));this.overview.dataset.waveformPolicy='selected_stereo_envelopes_overlaid_not_phase_sum';
  this.overview.dataset.windowHighlight='visible_3d_time_window';this.overview.dataset.position=String(this.start+this.position);
  const center=(h-22)/2,amplitude=Math.max(6,center-4);
  c.strokeStyle='#52647b';c.lineWidth=1;c.globalAlpha=.5;c.beginPath();c.moveTo(0,center);c.lineTo(w,center);c.stroke();c.globalAlpha=1;
  if(this.loop){c.fillStyle='#d8b78918';c.fillRect(this.loop[0]/this.duration*w,0,(this.loop[1]-this.loop[0])/this.duration*w,h-18)}
  // Every audible source uses the same hue as its 3D mesh and labels. No original-mix fallback.
  for(const e of shown){const d=e.data;for(const boundary of ['max','min']){c.strokeStyle=e.css;c.lineWidth=e.id===this.selectedId?1.4:1;c.beginPath();let connected=false;for(let x=0;x<w;x++){const sec=this.start+x/w*this.duration-e.start;if(sec<0||sec>=e.duration){connected=false;continue}const i=Math.min(d.bins-1,Math.floor(sec*d.sample_rate/d.hop_samples)),channels=d[boundary];let value=channels[0][i];for(let ch=1;ch<channels.length;ch++)value=boundary==='max'?Math.max(value,channels[ch][i]):Math.min(value,channels[ch][i]);const y=center-value/this.scale*amplitude;if(connected)c.lineTo(x,y);else c.moveTo(x,y);connected=true}c.stroke()}}
  const [lo,hi]=this.view();if(this.zoom>1){c.fillStyle='#05071099';c.fillRect(0,0,lo/this.duration*w,h-18);c.fillRect(hi/this.duration*w,0,w-hi/this.duration*w,h-18);c.strokeStyle='#d0dbea';c.lineWidth=1;c.setLineDash?.([5,3]);c.strokeRect(lo/this.duration*w,1,(hi-lo)/this.duration*w,h-20);c.setLineDash?.([])}
  if(!shown.length){c.fillStyle='#8799ad';c.font='11px system-ui,sans-serif';c.textAlign='center';c.fillText('未勾选音轨 · 当前静音',w/2,center-6)}
  const x=this.position/this.duration*w;c.fillStyle='#e5f9ff';c.fillRect(x,0,1.5,h-14);c.font='10px ui-monospace,monospace';c.fillStyle='#8aa2b8';c.textAlign='left';c.fillText(this.time(this.start),0,h-2);c.textAlign='right';c.fillText(this.time(this.start+this.duration),w,h-2);
  const status=this.overview.parentElement?.querySelector?.('#overviewStatus');if(status)status.textContent=this.unified?`已勾选 ${shown.length} 轨 · 同色包络叠加预览`:'当前处理版 · 同色包络预览';
 }
 update(position,loop){this.position=position;this.loop=loop;this.draw()}
 reset(){this.yaw=-.48;this.pitch=.66;this.camera=1;this.spacing=1;this.heightGain=this.defaultHeightGain;this.viewStart=null;this.onTimeViewChange?.();this.focusLane=(this.entries.length-1)/2;this.onSpatialChange?.();this.onRotationChange?.();this.draw()}
}
