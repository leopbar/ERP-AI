export type TraceEvent = {step:string;state:string;at:string;activity?:string;span_id?:string;meta?:Record<string,any>};
export type TraceRun = {run_id:string;active:boolean;base_revision:number;step:string;state:string;updated_at:string;started_at?:string;operation?:string;events:TraceEvent[];events_truncated?:boolean};
export type TraceProgress = TraceRun & {history?:TraceRun[];history_truncated?:boolean};
export type Span = {id:string;step:string;state:string;activity?:string;started_at:string;finished_at?:string;meta:Record<string,any>};
export type CaseEvent = {type:string;at:string;[key:string]:any};

// Published by GET /api/workflow/map (backend/app/workflow_map.py).
export type MapStop = {type:'stop';id:string;label:string;icon:string;tech:string[];node?:string|null;activities:string[];steps:string[];case_events:string[];optional:boolean;caption?:string|null};
export type MapDecision = {type:'decision';node:string;question:string;answer:string;to:string;label:string;note:string;internal:boolean};
export type MapStage = {id:string;nodes:string[];exit?:string;items:(MapStop|MapDecision)[]};
export type StageMap = {stages:MapStage[];main:Record<string,string>;detours:Record<string,{label:string;routes:Record<string,string>}>};

export type LineState = 'done'|'running'|'waiting'|'error'|'next'|'unknown';
export type StopView = MapStop & {number:number;state:LineState;spans:Span[];facts:CaseEvent[];at?:string;reached:boolean};
export type DecisionView = MapDecision & {outcome:'branch'|'main'|'unknown'|'unreached';reached:boolean};
export type ExitView = {type:'exit';state:LineState;reached:boolean};
export type LineItem = StopView|DecisionView|ExitView;

export const activityNames:Record<string,string> = {
 context_prepare:'Preparar contexto e esquema de resposta',model_prepare:'Preparar solicitação ao modelo',
 model_request:'Enviar solicitação e aguardar o modelo',model_response:'Receber resposta e consumo reportado',
 model_decode:'Conferir e ler a resposta estruturada',output_validate:'Validar a estrutura do resultado',
 output_retry:'Ampliar limite de saída e tentar novamente',alternatives_organize:'Organizar e conferir as três interpretações',
 routing:'Definir o próximo caminho do fluxo',sources_validate:'Conferir as fontes citadas no plano',
 coverage_validate:'Conferir se o plano faz cada item do pedido, e só ele',
 official_validation:'Validar o plano com as regras oficiais do HCM',company_lookup:'Buscar e identificar a empresa',
 catalog_read:'Consultar catálogo oficial',snapshot_read:'Consultar configuração atual',knowledge_retrieve:'Recuperar fontes e memórias compatíveis',
 scope_check:'Verificar se o pedido pertence ao escopo',modules_read:'Identificar os módulos disponíveis',intent_confirm:'Registrar interpretação confirmada',
 context_check:'Conferir se o contexto continua válido',hcm_apply:'Solicitar aplicação aprovada e aguardar o HCM',
 hcm_verify:'Consultar as verificações do HCM',hcm_rollback:'Solicitar reversão e aguardar o HCM',result_compare:'Comparar resultado e relatório com a proposta',
 reference_read:'Consultar referências complementares da empresa',
};
export const operationNames:Record<string,string>={analysis:'Análise dos agentes',execute:'Aplicação aprovada',verify:'Verificação do resultado',confirm:'Confirmação do resultado',rollback:'Reversão autorizada'};
export function stageKey(step:string){return ['company','catalog','snapshot','knowledge'].includes(step)?'context':step==='intent'||step==='consultant'?'choice':step==='rollback'?'apply':step;}

/** Pairs the start and finish records of each operation, in recorded order. Routes are not operations. */
export function spansOf(events:TraceEvent[]):Span[]{
 const spans:Span[]=[],open=new Map<string,Span>();
 for(const [index,event] of events.entries()){
  if(!event.activity||event.activity==='route')continue;
  const key=event.span_id||`${index}-${event.at}`,old=open.get(key);
  if(old){Object.assign(old,{state:event.state,meta:{...old.meta,...event.meta},finished_at:event.state==='running'?undefined:event.at});continue;}
  const span:Span={id:key,step:event.step,state:event.state,activity:event.activity,started_at:event.at,finished_at:['done','error'].includes(event.state)?event.at:undefined,meta:{...event.meta}};
  spans.push(span);open.set(key,span);
 }
 return spans;
}

const time=(value:string)=>Date.parse(value)||0;

/**
 * The stage line for one run: stops with their state, decisions with the route this run took.
 * fallback is the state of a stop without records: 'next' before it runs, 'unknown' when the
 * case passed the stage before detailed records existed. Nothing is inferred beyond the records.
 */
export function buildStageLine(map:StageMap,stage:MapStage,run:TraceRun|undefined,caseEvents:CaseEvent[]=[],fallback:LineState='next'):LineItem[]{
 const events=(run?.events||[]).filter(e=>stageKey(e.step)===stage.id);
 const spans=spansOf(events),nodeEvents=events.filter(e=>!e.activity);
 const routes=new Map(events.filter(e=>e.activity==='route').map(e=>[e.meta?.source,e.meta?.target]));
 const branched=new Set<string>(),items:LineItem[]=[];
 let number=0;
 for(const item of stage.items){
  if(item.type==='stop'){
   const own=spans.filter(s=>item.activities.includes(s.activity!)),marks=nodeEvents.filter(e=>item.steps.includes(e.step));
   const facts=caseEvents.filter(e=>item.case_events.includes(e.type));
   if(item.optional&&!own.length&&!marks.length&&!facts.length)continue;
   // The most recent record decides: a human decision recorded after its pause closes it.
   const latest=[own.at(-1)&&{at:own.at(-1)!.finished_at||own.at(-1)!.started_at,state:own.at(-1)!.state},marks.at(-1),facts.at(-1)&&{at:facts.at(-1)!.at,state:'done'}]
    .filter((c):c is {at:string;state:string}=>!!c).sort((a,b)=>time(a.at)-time(b.at)).at(-1);
   const state:LineState=own.some(s=>s.state==='running')?'running':(latest?.state as LineState)||fallback;
   items.push({...item,number:++number,state,spans:own,facts,at:latest?.at,reached:!['next','unknown'].includes(state)});
  }else{
   const target=routes.get(item.node);
   const outcome=branched.has(item.node)?'unreached':item.internal?(events.some(e=>e.meta?.repair===true)?'branch':routes.has(item.node)?'main':'unknown')
    :target===undefined?'unknown':target===item.to?'branch':'main';
   if(outcome==='branch')branched.add(item.node);
   items.push({...item,outcome,reached:outcome==='branch'||outcome==='main'});
  }
 }
 // A failure recorded only at stage level belongs to the last stop that has records.
 if(nodeEvents.at(-1)?.state==='error'&&!items.some(i=>'state' in i&&i.state==='error')){
  const stop=[...items].reverse().find((i):i is StopView=>i.type==='stop'&&i.reached)||items.find((i):i is StopView=>i.type==='stop');
  if(stop){stop.state='error';stop.reached=true;}
 }
 const stops=items.filter((i):i is StopView=>i.type==='stop');
 const allDone=stops.length>0&&stops.every(s=>s.state==='done'),unknown=stops.some(s=>s.state==='unknown');
 // Runs recorded before routes were tracked cannot tell which way the stage left.
 const legacy=!(run?.events||[]).some(e=>e.activity==='route');
 const exit:LineState=stage.exit?(routes.has(stage.exit)?(routes.get(stage.exit)===map.main[stage.exit]?'done':'next'):unknown||(allDone&&legacy)?'unknown':'next')
  :allDone?'done':unknown?'unknown':'next';
 items.push({type:'exit',state:exit,reached:exit==='done'});
 return items;
}
