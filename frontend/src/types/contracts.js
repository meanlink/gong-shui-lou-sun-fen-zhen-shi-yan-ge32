/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,name:string,status:'open'|'closed',base_dataset_id:number,base_checksum:string,base_rows:number,output_dataset_id:number|null,open_conflicts:number|null,left:Object|null,right:Object|null}} MergeSession */
/** @typedef {{side:'left'|'right',label:string,version:number,confirmed_version:number|null,records:Array,row_revs:Object,edits:Array}} ProofCopy */
/** @typedef {{id:number,segment_id:string,field:string,field_label:string,kind:'same_field_edit'|'delete_edit'|'same_id_add',status:'open'|'decided'|'resolved',base:any,left:any,right:any,left_rev:number,right_rev:number,decision:Object|null,history:Array}} MergeConflict */
export {};
