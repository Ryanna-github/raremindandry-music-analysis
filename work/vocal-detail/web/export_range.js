'use strict';
/* Pure time parsing / range validation, shared by the UI and Node regressions. */
const ExportRange=(()=>{
 function parse(value){
  if(typeof value!=='string')return NaN;
  const text=value.trim().replace(/：/g,':'),parts=text.split(':');
  if(!text||parts.length>3||!/^\d+(?:\.\d+)?$/.test(parts.at(-1))||parts.slice(0,-1).some(p=>!/^\d+$/.test(p)))return NaN;
  const numbers=parts.map(Number);
  if(numbers.some(n=>!Number.isFinite(n))||numbers.slice(1).some(n=>n>=60))return NaN;
  return numbers.reduce((total,n)=>total*60+n,0);
 }
 function format(seconds){
  const milliseconds=Math.round(Math.max(0,seconds)*1000),minutes=Math.floor(milliseconds/60000),rest=milliseconds%60000;
  return `${String(minutes).padStart(2,'0')}:${String(Math.floor(rest/1000)).padStart(2,'0')}.${String(rest%1000).padStart(3,'0')}`;
 }
 function validate(start,end,bounds){
  if(!Number.isFinite(start)||!Number.isFinite(end))throw Error('请填写有效时间，例如 01:08.250 或 68.25 秒。');
  // Accept a rounded-to-milliseconds endpoint and clamp it to the exact media bound.
  if(start<bounds[0]-.0005-1e-9||end>bounds[1]+.0005+1e-9)throw Error(`时间须在 ${format(bounds[0])}–${format(bounds[1])} 内。`);
  start=Math.max(bounds[0],start);end=Math.min(bounds[1],end);
  if(end<=start)throw Error('结束时间必须晚于开始时间。');
  if(end-start<.05-1e-9)throw Error('时间段至少需要 0.05 秒。');
  return [start,end];
 }
 function resolve(choice,{bounds,ab,custom}){
  if(choice==='full')return validate(...bounds,bounds);
  if(choice==='ab')return validate(...ab,bounds);
  if(choice==='custom')return validate(parse(custom[0]),parse(custom[1]),bounds);
  throw Error('请选择有效的导出范围。');
 }
 return {parse,format,validate,resolve};
})();
if(typeof module!=='undefined'&&module.exports)module.exports=ExportRange;
