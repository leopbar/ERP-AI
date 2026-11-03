'use client';

import {useEffect,useState} from 'react';
import {BadgeCheck,BookmarkCheck,BookOpen,BookOpenCheck,Building2,Check,CircleAlert,Database,FileCheck2,FileSearch,FileText,GitBranch,GitCompare,History,Layers3,ListChecks,LoaderCircle,Pause,Scale,ScanSearch,Server,ShieldCheck,Sparkles,Hourglass,Undo2,Upload,UserRound,X,type LucideIcon} from 'lucide-react';
import AgentPortrait from './AgentPortrait';
import {activityNames,buildStageLine,operationNames,stageKey,type DecisionView,type LineState,type Span,type StageMap,type StopView,type TraceProgress,type TraceRun} from './stageTrace';

type Json=Record<string,any>;
type Step={id:string;title:string;description:string};
const icons:Record<string,LucideIcon>={request:FileText,modules:Layers3,scope:ShieldCheck,company:Building2,catalog:BookOpenCheck,snapshot:Database,knowledge:BookOpen,options:ListChecks,person:UserRound,intent:BookmarkCheck,sources:FileSearch,validator:BadgeCheck,check:ScanSearch,apply:Upload,rollback:Undo2,report:FileCheck2,compare:GitCompare,waiting:Hourglass};
const techs:Record<string,[string,LucideIcon]>={model:['Modelo de IA',Sparkles],hcm:['API do HCM',Server],rules:['Regras do ERP',Scale],database:['Banco do ERP',Database],person:['Você',UserRound]};
const stateNames:Record<LineState,string>={done:'Concluída',running:'Em andamento',waiting:'Aguardando você',error:'Interrompida',next:'Ainda não aconteceu',unknown:'Sem registro detalhado'};
const outcomeNames:Record<DecisionView['outcome'],string>={branch:'Este atendimento seguiu este desvio.',main:'Este atendimento seguiu a linha principal.',unknown:'Caminho não registrado nesta execução.',unreached:'Este atendimento não chegou a esta pergunta.'};
const factNames:Record<string,string>={opened:'Pedido registrado',request_rephrased:'Pedido reformulado',interpretation_confirmed:'Interpretação confirmada',interpretation_rejected:'Interpretações recusadas',execution_approved:'Plano exato aprovado para aplicação',validated:'Resultado aprovado e experiência guardada'};
const efforts:Record<string,string>={none:'Sem esforço adicional',minimal:'Mínimo',low:'Baixo',medium:'Médio',high:'Alto',xhigh:'Extra alto',not_supported:'Não enviado: modelo sem suporte'};
const roles:Record<string,string>={classification:'module_classifier',interpretations:'interpretation_agent',configuration:'configuration_agent'};
const time=(value?:string)=>value?new Date(value).toLocaleTimeString('pt-BR'):'—';
const number=(value:number)=>value.toLocaleString('pt-BR');
const duration=(value:number)=>value<1?`${Math.round(value*1000)} ms`:value<60?`${value.toFixed(1)} s`:`${Math.floor(value/60)} min ${Math.floor(value%60)} s`;
const opposite=(answer:string)=>answer.startsWith('sim')?'não':'sim';

function Metrics({meta}:{meta:Json}){
 const entries:[string,unknown][]=[['Entrada',meta.prompt_tokens],['Saída',meta.completion_tokens],['Total',meta.total_tokens],['Raciocínio',meta.reasoning_tokens],['Cache',meta.cached_tokens]];
 return <div className="stage-usage">{entries.filter(([,value])=>typeof value==='number').map(([label,value])=><span key={label}>{label}<b>{number(value as number)}</b><small>tokens</small></span>)}{typeof meta.estimated_usd==='number'&&<span>Custo estimado<b>US$ {meta.estimated_usd.toFixed(6)}</b><small>estimativa, não fatura</small></span>}</div>;
}

function StopMark({stop,live}:{stop:StopView;live:boolean}){
 const Icon=icons[stop.icon]||Check;
 const Badge=stop.state==='done'?Check:stop.state==='running'?LoaderCircle:stop.state==='waiting'?Pause:stop.state==='error'?CircleAlert:null;
 return <span className="stage-stop-mark">{stop.icon.startsWith('agent:')?<AgentPortrait kind={stop.icon.slice(6)} className="stage-robot"/>:<Icon size={20} strokeWidth={1.6}/>}<span className="stage-stop-number">{stop.number}</span>{Badge&&<span className="stage-stop-state"><Badge size={10} className={stop.state==='running'&&live?'spin':''}/></span>}</span>;
}

function StopDetail({stop,stage,selected,live,clock}:{stop:StopView;stage:string;selected?:Json|null;live:boolean;clock:number}){
 const meta=[...stop.spans].reverse().find(s=>s.meta?.model)?.meta||{},usage=[...stop.spans].reverse().find(s=>s.activity==='model_response')?.meta||{};
 const legacy=stop.activities.includes('model_request')&&!meta.model?(selected?.events||[]).filter((e:Json)=>e.type==='agent_completed'&&e.role===roles[stage]):[];
 const elapsed=(s:Span)=>typeof s.meta?.duration_seconds==='number'?duration(s.meta.duration_seconds):s.state==='running'&&live?`${duration(Math.max(0,(clock-new Date(s.started_at).getTime())/1000))} decorridos`:s.state==='running'?'Último registro: em andamento':stateNames[s.state as LineState]||'';
 const empty=!selected?'Durante um atendimento, aqui aparecem horários, duração e consumo desta parada.':stop.state==='unknown'?'A etapa foi concluída antes do registro detalhado; não há horários desta parada.':stop.state==='waiting'?`Aguardando sua ação no atendimento desde ${time(stop.at)}.`:stop.state==='next'?'Esta parada ainda não aconteceu nesta execução.':`Registrado às ${time(stop.at)}.`;
 return <div id="stage-stop-detail" className={`stage-detail is-${stop.state}`}>
  <div className="stage-detail-head"><strong><span>{stop.number}</span>{stop.label}</strong><em>{stateNames[stop.state]}</em></div>
  <div className="stage-tech">{stop.node&&<span className="graph"><GitBranch size={12}/>LangGraph · nó {stop.node}</span>}{stop.tech.map(key=>{const [label,Icon]=techs[key]||[key,Check];return <span key={key}><Icon size={12}/>{label}</span>})}</div>
  {meta.model&&<div className="stage-model"><div><small>Modelo solicitado</small><strong>{meta.model}</strong>{usage.returned_model&&usage.returned_model!==meta.model&&<small>Retornado: {usage.returned_model}</small>}</div><div><small>Esforço enviado</small><strong>{efforts[meta.reasoning_effort]||'Não registrado'}</strong></div><div><small>Limite de saída</small><strong>{typeof meta.max_completion_tokens==='number'?`${number(meta.max_completion_tokens)} tokens`:'Não registrado'}</strong></div><div className="stage-attempts">{meta.attempt&&<span>Rodada {meta.attempt}</span>}{meta.escalated&&<span>Modelo de reanálise</span>}{meta.repair&&<span>Correção automática do plano</span>}{meta.request_attempt>1&&<span>Reenvio {meta.request_attempt}</span>}</div><Metrics meta={usage}/></div>}
  {stop.spans.length>0&&<ol className="stage-ops" aria-label="Operações desta parada">{stop.spans.map(s=><li key={s.id} className={`is-${s.state}`}><span>{activityNames[s.activity!]||'Operação interna'}{s.meta?.module&&<small>{s.meta.module}</small>}</span><time>{time(s.started_at)}</time><b>{elapsed(s)}</b></li>)}</ol>}
  {stop.facts.length>0&&<ul className="stage-facts">{stop.facts.map((e,i)=><li key={`${i}-${e.at}`}><Check size={13}/>{factNames[e.type]||e.type}<time>{new Date(e.at).toLocaleString('pt-BR')}</time></li>)}</ul>}
  {legacy.map((event:Json,i:number)=>{const m=event.metrics||{},u=m.usage||{};return <div key={`${i}-${event.at}`} className="stage-legacy"><strong>{m.model||'Modelo não registrado'}</strong><small>{new Date(event.at).toLocaleString('pt-BR')}{typeof m.duration_seconds==='number'?` · ${duration(m.duration_seconds)}`:''} · esforço e passos internos não registrados</small><Metrics meta={{...u,reasoning_tokens:u.completion_tokens_details?.reasoning_tokens,estimated_usd:m.cost?.estimated_usd}}/></div>})}
  {!stop.spans.length&&!stop.facts.length&&!legacy.length&&<p className="stage-detail-empty">{empty}</p>}
 </div>;
}

export default function StageExecution({step,number:stepNumber,next,stageMap,selected,progress,offline,active,journeyState,onClose}:{step:Step;number:string;next?:{number:string;title:string};stageMap?:StageMap|null;selected?:Json|null;progress?:TraceProgress|null;offline:boolean;active:boolean;journeyState:string;onClose:()=>void}){
 const [chosen,setChosen]=useState(''),[open,setOpen]=useState<string|null>(null),[clock,setClock]=useState(Date.now());
 useEffect(()=>{setChosen('');setOpen(null)},[step.id,selected?.id]);
 const runs:TraceRun[]=progress?[...(progress.history||[]),progress]:[];
 const eligible=runs.filter(run=>run.events.some(e=>stageKey(e.step)===step.id));
 const run=eligible.find(r=>r.run_id===chosen)||eligible.at(-1);
 const live=!!run&&run.run_id===progress?.run_id&&active&&!offline;
 useEffect(()=>{if(!live)return;const timer=setInterval(()=>setClock(Date.now()),1000);return()=>clearInterval(timer)},[live]);
 const stage=stageMap?.stages.find(s=>s.id===step.id);
 const fallback:LineState=selected&&!run&&journeyState==='done'?'unknown':'next';
 const line=stage&&stageMap?buildStageLine(stageMap,stage,run,selected?.events||[],fallback):[];
 const stop=line.find((i):i is StopView=>i.type==='stop'&&i.id===open);
 const detours=[...new Set(line.flatMap(i=>i.type==='decision'&&stageMap?.detours[i.to]?[i.to]:[]))];
 const graph=!!stage?.nodes.length;
 const status=live?'Acompanhando agora':run?'Execução registrada':!selected?'Roteiro previsto':fallback==='unknown'?'Sem registro detalhado':'Ainda não iniciada';
 const exit=step.id==='validation'?<>Fim do LangGraph<small>segue para {next?.number} {next?.title}</small></>:next?<>Segue para<small>{next.number} {next.title}</small></>:<>Fim do atendimento<small>experiência guardada</small></>;
 return <div className="stage-panel" role="region" aria-label={`Por dentro de ${step.title}`}>
  <div className="stage-head">
   <div><p className="eyebrow">POR DENTRO DA ETAPA</p><h3><span>{stepNumber}</span>{step.title}</h3><p>{step.description}</p></div>
   <div className="stage-head-tools"><span className={'stage-graph-chip'+(graph?'':' outside')}><GitBranch size={13}/>{graph?`LangGraph · ${stage!.nodes.join(', ')}`:'Fora do LangGraph'}</span><span className={'stage-status'+(live?' live':'')}><span/>{status}</span><button type="button" className="stage-close" onClick={onClose} aria-label="Fechar esta etapa"><X size={16}/></button></div>
  </div>
  {eligible.length>1&&<label className="stage-run"><History size={14}/><span>Execução</span><select aria-label="Execução exibida" value={run?.run_id||''} onChange={e=>{setChosen(e.target.value);setOpen(null)}}>{[...eligible].reverse().map(r=><option key={r.run_id} value={r.run_id}>{operationNames[r.operation||'analysis']||'Execução'} · {new Date(r.started_at||r.updated_at).toLocaleString('pt-BR')}{r.run_id===progress?.run_id?' · mais recente':''}</option>)}</select></label>}
  {!stage?<p className="stage-detail-empty">{stageMap===undefined?'Carregando o roteiro da etapa…':'Não foi possível carregar o roteiro desta etapa. Tente abrir de novo.'}</p>:<>
   <ol className="stage-line" aria-label={`Paradas de ${step.title}`}>{line.map((item,i)=>{
    const flow=`${item.reached?' in-on':''}${line[i+1]?.reached?' out-on':''}`;
    if(item.type==='stop')return <li key={item.id} className={`stage-item stage-stop is-${item.state}${flow}${open===item.id?' chosen':''}`}><button type="button" aria-expanded={open===item.id} aria-controls="stage-stop-detail" onClick={()=>setOpen(open===item.id?null:item.id)}><StopMark stop={item} live={live}/><span className="stage-stop-label">{item.label}{item.caption&&item.spans.some(s=>s.meta?.[item.caption!])&&<small>{item.spans.find(s=>s.meta?.[item.caption!])!.meta[item.caption]}</small>}</span><span className="sr-only">Parada {item.number}: {stateNames[item.state]}</span></button></li>;
    if(item.type==='decision')return <li key={`${item.node}-${item.to}`} className={`stage-item stage-decision is-${item.outcome}${flow}`}><span className="stage-question">{item.question}</span><span className="stage-diamond" aria-hidden="true"/><span className="stage-main-answer" aria-hidden="true">{opposite(item.answer)}</span><span className="stage-branch"><span className="stage-answer">{item.answer}</span><span className="stage-branch-chip">{item.label}<small>{item.note}</small></span></span><span className="sr-only">{selected?outcomeNames[item.outcome]:'Pergunta possível neste ponto do fluxo.'}</span></li>;
    return <li key="exit" className={`stage-item stage-exit is-${item.state}${flow}`}><span className={'stage-exit-chip'+(step.id==='validation'?' graph-end':'')}>{exit}</span></li>;
   })}</ol>
   {detours.length>0&&<p className="stage-detours">{detours.map(id=><span key={id}><b>{stageMap!.detours[id].label}:</b> {Object.values(stageMap!.detours[id].routes).join(', ')}.</span>)}</p>}
   {stop?<StopDetail stop={stop} stage={step.id} selected={selected} live={live} clock={clock}/>:<p className="stage-hint">Clique numa parada para ver a tecnologia, os horários e o consumo.</p>}
  </>}
  <p className="stage-note"><Check size={13}/>Somente acompanhamento: abrir uma etapa não chama o modelo nem executa operações.{(progress?.history_truncated||run?.events_truncated)&&' O histórico guarda as 20 execuções anteriores e os últimos 160 registros de cada uma.'}</p>
 </div>;
}
