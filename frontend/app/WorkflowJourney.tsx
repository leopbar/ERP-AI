'use client';

import {Fragment, useEffect, useRef, useState, type CSSProperties} from 'react';
import {ArrowRight, BookOpen, Check, ChevronDown, CircleAlert, CirclePlay, CircleStop, Database, Eye, EyeOff, FileCheck2, FileText, GitBranch, LoaderCircle, Pause, ShieldCheck, UserRound} from 'lucide-react';
import AgentPortrait from './AgentPortrait';
import StageExecution from './StageExecution';
import type {StageMap, TraceProgress} from './stageTrace';

type Json = Record<string, any>;
type Observation = {case_id:number;revision:number;status:string;phase?:string;progress?:TraceProgress|null};
type State = 'next'|'done'|'running'|'waiting'|'error';
const steps = [
 {id:'request',title:'Pedido',role:'Você',icon:FileText,description:'A necessidade de negócio e a vigência dão início ao atendimento.'},
 {id:'scope',title:'Escopo',role:'Sistema',icon:ShieldCheck,description:'Confere se a solicitação pertence à parametrização assistida do HCM.'},
 {id:'classification',title:'Classificação',role:'Agente classificador',agent:'classifier',icon:GitBranch,description:'Identifica o assunto entre os módulos publicados pelo HCM e extrai o nome da empresa.'},
 {id:'context',title:'Contexto HCM',role:'Sistema',icon:Database,description:'Consulta a empresa, as regras oficiais e a situação atual. Recupera conhecimento compatível com o pedido.'},
 {id:'interpretations',title:'Interpretações',role:'Agente intérprete',agent:'interpreter',icon:GitBranch,description:'Prepara uma sugestão item por item: o que será configurado, o que já está em vigor, o que o HCM não faz e as dúvidas a confirmar. Pode identificar outros módulos necessários.'},
 {id:'choice',title:'Sua escolha',role:'Você',icon:UserRound,description:'O fluxo aguarda o seu aceite da sugestão, com as dúvidas confirmadas. Uma correção permite reanalisar o entendimento.'},
 {id:'configuration',title:'Configuração',role:'Agente configurador',agent:'configurator',icon:FileText,description:'Transforma a sugestão aceita em um plano baseado no catálogo oficial, só com o que é possível.'},
 {id:'validation',title:'Validação',role:'Sistema',icon:ShieldCheck,description:'Confere o plano e consulta o validador oficial. Uma rejeição permite uma rodada de correção automática.'},
 {id:'approval',title:'Aprovação',role:'Você',icon:UserRound,description:'A aplicação depende da sua aprovação do plano exato. As ações continuam no atendimento, abaixo deste mapa.'},
 {id:'apply',title:'Aplicação HCM',role:'Sistema',icon:Database,description:'Usa a integração existente para aplicar o plano aprovado em homologação.'},
 {id:'tests',title:'Testes',role:'Sistema / HCM',icon:FileCheck2,description:'O HCM executa os testes durante a aplicação. O ERP confere o relatório e compara os valores retornados.'},
 {id:'experience',title:'Experiência',role:'Você + sistema',icon:BookOpen,description:'Após os testes, sua confirmação torna o atendimento uma experiência aprovada para consultas futuras.'},
];
const details:Record<string,string> = {company:'Identificar a empresa no HCM',catalog:'Consultar o catálogo oficial',snapshot:'Ler a configuração atual',knowledge:'Recuperar fontes e memórias',intent:'Registrar a intenção confirmada',consultant:'Aguardar análise do consultor',rollback:'Reverter a aplicação autorizada'};
const names = Object.fromEntries(steps.map(s=>[s.id,s.title]));
const parentStep = (step:string)=>['company','catalog','snapshot','knowledge'].includes(step)?'context':step==='intent'?'choice':step;
const stateNames:Record<State,string> = {next:'Próxima etapa',done:'Concluído',running:'Em andamento',waiting:'Aguardando ação',error:'Interrompido'};
// LangGraph runs guard_input → plan (orchestration.py), pauses included; approval onwards runs outside the graph.
type GraphMarkData = {kind:'start'|'end';label:string;hint:string};
const graphMarks:Record<string,GraphMarkData> = {
 scope:{kind:'start',label:'Início do LangGraph',hint:'Do Escopo à Validação, o atendimento é conduzido pelo LangGraph, inclusive as pausas para sua escolha.'},
 validation:{kind:'end',label:'Fim do LangGraph',hint:'Aprovação, aplicação no HCM, testes e experiência acontecem fora do LangGraph.'},
};
const hiddenKey='erp-ai.journey.hidden';

// Deliberately separate from action requests: polling never resumes or applies a case.
function useObservation(selected:Json|null|undefined,busy:boolean,onRefresh:(id:number)=>void){
 const [observation,setObservation]=useState<Observation|null>(null),[offline,setOffline]=useState(false);
 const refreshRef=useRef(onRefresh);refreshRef.current=onRefresh;
 const id=selected?.id,revision=selected?.revision;
 useEffect(()=>{
  setObservation(null);setOffline(false);
  if(!id)return;
  let stopped=false,timer:ReturnType<typeof setTimeout>|undefined,controller:AbortController|undefined;
  async function poll(){
   if(stopped)return;
   let delay=busy?1800:8000;
   if(!document.hidden){
    controller=new AbortController();const timeout=setTimeout(()=>controller?.abort(),4000);
    try{
     const response=await fetch(`/api/cases/${id}/progress`,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
     if(!response.ok)throw new Error('progress');
     const value:Observation=await response.json();
     if(!stopped){setObservation(value);setOffline(false);if(value.progress?.active)delay=1800;if(value.revision!==revision&&!busy)refreshRef.current(id);}
    }catch{if(!stopped)setOffline(true);delay=8000;}
    finally{clearTimeout(timeout);}
   }
   if(!stopped)timer=setTimeout(poll,delay);
  }
  void poll();
  return()=>{stopped=true;clearTimeout(timer);controller?.abort();};
 },[id,revision,busy]);
 return {observation,offline};
}

// The stage map is static for a build: fetched when a stage is first opened, shared by every panel,
// and requested again after a failure when a stage is reopened.
let stageMapRequest:Promise<StageMap|null>|null=null;
function useStageMap(wanted:boolean){
 const [map,setMap]=useState<StageMap|null|undefined>(undefined);
 useEffect(()=>{
  if(!wanted||map)return;
  let alive=true;
  stageMapRequest??=fetch('/api/workflow/map',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).catch(()=>null);
  stageMapRequest.then(value=>{if(!value)stageMapRequest=null;if(alive)setMap(value)});
  return()=>{alive=false};
 },[wanted,map]);
 return map;
}

// Mirrors the map's breakpoints in globals.css: the stage panel opens under the selected stage's row.
function useColumns(){
 const [columns,setColumns]=useState(6);
 useEffect(()=>{
  const small=window.matchMedia('(max-width: 600px)'),medium=window.matchMedia('(max-width: 1000px)');
  const update=()=>setColumns(small.matches?3:medium.matches?4:6);
  update();small.addEventListener('change',update);medium.addEventListener('change',update);
  return()=>{small.removeEventListener('change',update);medium.removeEventListener('change',update)};
 },[]);
 return columns;
}

function GraphMark({kind,label,hint}:GraphMarkData){
 const Icon=kind==='start'?CirclePlay:CircleStop;
 return <span className={'journey-graph journey-graph-'+kind}><span title={hint}><Icon size={12}/>{label}</span></span>;
}

export default function WorkflowJourney({selected,busy=false,onRefresh=()=>{}}:{selected?:Json|null;busy?:boolean;onRefresh?:(id:number)=>void}){
 const {observation,offline}=useObservation(selected,busy,onRefresh);
 const [expanded,setExpanded]=useState<string|null>(null);
 // Per-viewer preference. The card only mounts on the client, after login.
 const [hidden,setHidden]=useState(()=>{try{return localStorage.getItem(hiddenKey)==='1'}catch{return false}});
 function toggleHidden(){const next=!hidden;setHidden(next);if(next)setExpanded(null);try{localStorage.setItem(hiddenKey,next?'1':'0')}catch{}}
 const panelRef=useRef<HTMLLIElement>(null),mapRef=useRef<HTMLOListElement>(null);
 useEffect(()=>{if(expanded)panelRef.current?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'nearest'})},[expanded]);
 function closeStage(){const id=expanded;setExpanded(null);mapRef.current?.querySelector<HTMLButtonElement>(`[data-step="${id}"]`)?.focus()}
 const stageMap=useStageMap(!!expanded),columns=useColumns();
 // Row and column of the selected stage: 6 columns snake back on the second row, 4 and 3 follow reading order.
 const open=steps.findIndex(s=>s.id===expanded);
 const row=open<0?0:columns===6?(open<6?1:2):Math.floor(open/columns)+1;
 const column=open<0?0:columns===6?(open<6?open+1:12-open):open%columns+1;
 const selectedId=selected?.id;
 useEffect(()=>setExpanded(null),[selectedId]);
 const status=observation?.status||selected?.status,phase=observation?.phase||selected?.workflow?.phase;
 const progress=observation?.progress;
 const events=progress?.events||[];
 // Persisted case state is authoritative after an operation finishes. An old active
 // observation must not animate a completed case (e.g. worker stopped after commit).
 const running=!!progress?.active&&(['analyzing','executing','rollback_unknown'].includes(status)||progress.base_revision===observation?.revision);
 const liveStep=running?parentStep(progress!.step):null;
 const states:Record<string,State>=Object.fromEntries(steps.map(s=>[s.id,'next']));
 if(selected){
  states.request='done';
  const w=selected.workflow||{};
  if(w.classification?.module||w.options?.length){states.scope='done';}
  if(w.classification?.module)states.classification='done';
  if(w.suggestion?.items?.length||w.options?.length){states.context='done';states.interpretations='done';}
  if(w.selected?.id||w.confirmed_meaning)states.choice='done';
  if(selected.proposal?.summary){states.configuration='done';}
  if(selected.approval_hash)states.validation='done';
  if(['awaiting_interpretation'].includes(status))states.choice='waiting';
  if(['review','awaiting_execution','result_checked'].includes(status))states.approval='waiting';
  if(['executing','execution_unknown','execution_blocked','execution_failed','applied','verification_failed','validated','reverted','rollback_unknown'].includes(status)){
   states.approval='done';
  }
  if(['applied','verification_failed','validated'].includes(status)){states.apply='done';states.tests=selected.result?.automatic_tests?.passed?'done':'error';}
  if(status==='applied')states.experience='waiting';
  if(status==='validated')states.experience='done';
  if(status==='out_of_scope')states.scope='error';
  if(status==='proposal_failed')states.validation='error';
  if(status==='execution_blocked'||status==='execution_failed')states.apply='error';
  if(status==='execution_unknown')states.apply='waiting';
  if(status==='awaiting_context')states.context='waiting';
  if(status==='analyzing'&&!running)states.classification='waiting';
  if(status==='awaiting_model')states.configuration='waiting';
  if(status==='workflow_error')states[parentStep(progress?.step||'classification')]='error';
  // Use this invocation's real events only while it runs. They can include a
  // repeated classification, context refresh or automatic plan repair.
  if(running){
   // A reanalysis invalidates the old downstream picture immediately, even
   // while the action response still contains the previous proposal.
   const first=events.map(e=>steps.findIndex(s=>s.id===parentStep(e.step))).filter(i=>i>=0);
   if(first.length){for(const step of steps.slice(Math.min(...first)))states[step.id]='next';}
   for(const e of events){const key=parentStep(e.step);if(key in states)states[key]=e.state as State;}
   if(liveStep&&liveStep in states)states[liveStep]=progress!.state as State;
  }
 }
 const pending=Object.keys(states).find(k=>states[k]==='waiting');
 let message=!selected?'Seu próximo pedido começa aqui. Conheça o caminho completo.':running?`${details[progress!.step]||names[progress!.step]||'Processamento'} · ${stateNames[progress!.state as State]||'Em andamento'}`:pending?`${names[pending]} · aguardando sua ação no atendimento abaixo.`:'Confira as etapas e os registros deste atendimento.';
 if(status==='validated')message='Resultado aprovado e experiência guardada para consultas futuras.';
 if(status==='out_of_scope')message='Pedido fora do escopo. Veja a orientação abaixo para reformular.';
 if(status==='workflow_error')message='O processamento foi interrompido. Consulte a opção de retomada abaixo.';
 if(status==='analyzing'&&!running)message='Análise iniciada. Aguardando confirmação da etapa pelo servidor.';
 if(status==='consultant_queue'||phase==='consultant')message='Aguardando consultor. O motivo e as próximas ações estão no atendimento.';
 if(status==='consultant_closed')message='Atendimento encerrado pelo consultor. Consulte a conclusão abaixo.';
 if(status==='execution_unknown')message='A resposta da aplicação precisa ser confirmada. Consulte o resultado abaixo.';
 if(status==='execution_failed')message='A aplicação encontrou uma falha. Confira abaixo o resultado e a situação da reversão.';
 if(status==='execution_blocked')message='A aplicação foi bloqueada pelo HCM. Confira o motivo no atendimento.';
 if(status==='verification_failed')message='Os testes encontraram diferenças. Confira o resultado antes de continuar.';
 if(status==='reverted')message='Aplicação revertida. Este atendimento não é uma solução aprovada.';
 if(status==='rollback_unknown')message=running?'Reversão em andamento. Aguardando a resposta do HCM.':'A reversão precisa ser confirmada. Consulte o atendimento abaixo.';
 const focus=steps.find(s=>s.id===(expanded||liveStep||pending))||steps[0];
 const wait=Object.values(states).includes('waiting')||status==='consultant_queue';
 const error=Object.values(states).includes('error');
 const StatusIcon=offline?CircleAlert:error?CircleAlert:running&&progress?.state==='running'?LoaderCircle:wait||progress?.state==='waiting'?Pause:selected?Check:ArrowRight;
 return <section className={`card journey ${offline?'journey-offline':''}`} aria-label="Workflow dos agentes">
  <div className="journey-heading"><div><p className="eyebrow">DO PEDIDO À EXPERIÊNCIA</p><h2>O caminho da sua solicitação</h2>{hidden?<p role="status" aria-live="polite">{offline?'Acompanhamento temporariamente indisponível.':message}</p>:<p>Agentes, sistema e você. Cada etapa conectada.</p>}</div><div className="journey-tools"><span className={'journey-mode '+(running&&!offline?'live':'')}><span/>{selected?(offline?'Último estado conhecido':running?'Acompanhando execução':'Estado do atendimento'):'Visão do fluxo'}</span><button type="button" className="text-button journey-toggle" onClick={toggleHidden} aria-expanded={!hidden} aria-controls="journey-body">{hidden?<Eye size={15}/>:<EyeOff size={15}/>}{hidden?'Mostrar fluxo':'Ocultar fluxo'}</button></div></div>
  <div id="journey-body" hidden={hidden}>
  <ol ref={mapRef} className={'journey-map'+(open<0?'':` open-row-${row}`)} aria-label="Etapas do atendimento">{steps.map((step,i)=>{const state=states[step.id];const Icon=step.icon;const Badge=state==='done'?Check:state==='running'?LoaderCircle:state==='waiting'?Pause:state==='error'?CircleAlert:null;const mark=graphMarks[step.id];return <Fragment key={step.id}><li className={`journey-cell journey-${state}`}>
   {mark?.kind==='start'&&<GraphMark {...mark}/>}
   <button type="button" data-step={step.id} className={'journey-step '+(expanded===step.id?'chosen':'')} aria-expanded={expanded===step.id} aria-controls="journey-details" onClick={()=>setExpanded(expanded===step.id?null:step.id)}>
    <span className="journey-number">{String(i+1).padStart(2,'0')}</span><span className="journey-portrait">{step.agent?<AgentPortrait kind={step.agent}/>:<Icon size={32} strokeWidth={1.5}/>} {Badge&&<span className="journey-state-icon"><Badge size={12} className={state==='running'?'spin':''}/></span>}</span>
    <strong>{step.title}</strong><span className="journey-role">{step.role}</span><span className="journey-state-label">{selected?stateNames[state]:'Ver etapa'}</span>
   </button>{mark?.kind==='end'&&<GraphMark {...mark}/>}<span className="journey-connector" aria-hidden="true"><ArrowRight size={18}/></span>
  </li>{expanded===step.id&&<li ref={panelRef} id="journey-details" className="journey-inside" style={{gridRow:row+1,'--col':column,'--cols':columns} as CSSProperties}>
   <StageExecution step={step} number={String(i+1).padStart(2,'0')} next={steps[i+1]&&{number:String(i+2).padStart(2,'0'),title:steps[i+1].title}} stageMap={stageMap} selected={selected} progress={progress} offline={offline} active={running} journeyState={state} onClose={closeStage}/>
  </li>}</Fragment>})}</ol>
  <div className={'journey-current '+(error?'has-error':wait?'has-wait':'')} role="status" aria-live="polite"><StatusIcon size={19} className={StatusIcon===LoaderCircle?'spin':''}/><div><strong>{offline?'Acompanhamento temporariamente indisponível.':message}</strong><span>{offline?'O fluxo do atendimento é independente deste painel. Vamos tentar reconectar.':selected?(progress?.updated_at?`Último registro: ${new Date(progress.updated_at).toLocaleTimeString('pt-BR')}.`:'Estado confirmado do atendimento. Ainda não há registros detalhados desta execução.'):'Selecione uma etapa para entender o que acontece.'}</span></div></div>
  <div className="journey-bottom"><div className="journey-legend"><span><Check/>Concluído</span><span><LoaderCircle/>Em andamento</span><span><Pause/>Aguardando ação</span><span><CircleAlert/>Interrompido</span><span className="journey-legend-graph"><i/>LangGraph: etapas 02 a 08</span></div><button type="button" className="text-button" onClick={()=>setExpanded(expanded?null:focus.id)} aria-expanded={!!expanded} aria-controls="journey-details">{expanded?'Ocultar detalhes':'Explorar etapas'}<ChevronDown size={15}/></button></div>
  </div>
 </section>;
}
