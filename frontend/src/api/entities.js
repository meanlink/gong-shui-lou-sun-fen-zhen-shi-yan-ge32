import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
const enc=encodeURIComponent;
export const merges={
 list:()=>api('/merges'),
 get:id=>api('/merges/'+id),
 create:data=>api('/merges',{method:'POST',body:JSON.stringify(data)}),
 updateSegment:(id,side,segment,version,values)=>api('/merges/'+id+'/copies/'+side+'/segments/'+enc(segment),{method:'PUT',body:JSON.stringify({version,values})}),
 deleteSegment:(id,side,segment,version)=>api('/merges/'+id+'/copies/'+side+'/segments/'+enc(segment)+'?version='+version,{method:'DELETE'}),
 addSegment:(id,side,version,record)=>api('/merges/'+id+'/copies/'+side+'/segments',{method:'POST',body:JSON.stringify({version,record})}),
 restoreSegment:(id,side,segment,version)=>api('/merges/'+id+'/copies/'+side+'/restore/'+enc(segment),{method:'POST',body:JSON.stringify({version})}),
 confirm:(id,side,version)=>api('/merges/'+id+'/copies/'+side+'/confirm',{method:'POST',body:JSON.stringify({version})}),
 decide:(id,conflictId,payload)=>api('/merges/'+id+'/conflicts/'+conflictId+'/decide',{method:'POST',body:JSON.stringify(payload)}),
 close:id=>api('/merges/'+id+'/close',{method:'POST'}),
 edits:(id,side,segment)=>api('/merges/'+id+'/edits?side='+side+(segment?'&segment_id='+enc(segment):''))
};
