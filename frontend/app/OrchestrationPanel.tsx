'use client';
import {useEffect, useState} from 'react';
type Json = Record<string, any>;
const statusLabels:Record<string,string>={change:'Será configurado:',already_in_place:'Já está assim hoje:',gap:'Não será configurado:'};
const handoffLabels:[string,string][]=[['understood','O que entendi'],['possible','O que é possível'],['blocked_by','O que impediu'],['next_step','O que fazer agora']];

export function ModelRouting({primary,escalation,setPrimary,setEscalation}:{primary:string;escalation:string;setPrimary:(s:string)=>void;setEscalation:(s:string)=>void}){
 const [models,setModels]=useState<Json[]>([]);
 useEffect(()=>{fetch('/api/model/catalog').then(r=>r.ok?r.json():null).then(d=>setModels(d?.models||[])).catch(()=>{})},[]);
 const list=models.length?models:[{id:'gpt-6-luna',name:'GPT-6 Luna',input_usd:0.1,output_usd:0.5},{id:'gpt-6.1-sol',name:'GPT-6.1 Sol',input_usd:2,output_usd:10}];
 return <details className="routing-settings"><summary>Opções avançadas de IA</summary><div className="routing-grid">{[{label:'Primeira análise e plano',value:primary,set:setPrimary},{label:'Se escolher “Nenhuma”: reanálise e plano',value:escalation,set:setEscalation}].map(item=><label key={item.label}>{item.label}<select value={item.value} onChange={e=>item.set(e.target.value)}>{list.map(m=><option value={m.id} key={m.id}>{m.name} · US$ {m.input_usd} entrada / {m.output_usd} saída por milhão de tokens</option>)}</select></label>)}</div><p className="caption">A chave salva em Modelo de IA é reutilizada. Estas escolhas valem para este atendimento. A segunda rodada usa maior esforço nos modelos compatíveis. Após duas rodadas recusadas, o caso vai ao consultor. O histórico mostra consumo e custo estimado de cada chamada.</p></details>
}

export default function OrchestrationPanel({selected,busy,act}:{selected:Json;busy:boolean;act:(action:string,extra?:Json)=>Promise<void>}){
 const w=selected.workflow||{};
 const [feedback,setFeedback]=useState(''),[picks,setPicks]=useState<Record<string,number>>({}),[rejecting,setRejecting]=useState(false);
 const human=(value:string)=>(w.catalog?.fields||[]).reduce((text:string,f:Json)=>text.split(f.id).join(f.label||f.id),value||'');
 useEffect(()=>{setPicks({});setRejecting(false);setFeedback('')},[selected.id,w.phase,w.attempt,w.thread_id,w.clarification_count]);
 if(!w.thread_id||w.phase==='review'||['applied','validated','verification_failed','executing','execution_unknown','rollback_unknown','reverted','rejected','obsolete'].includes(selected.status))return null;
 if(w.phase==='out_of_scope')return <section className="card"><h2>Pedido fora do escopo de parametrização</h2><p role="alert">{w.explanation}</p><p className="caption" role="status">Entrada recusada: {w.scope_rejection_count||1}. Nenhuma configuração foi aplicada.</p><label>O que você deseja configurar no HCM?<textarea value={feedback} onChange={e=>setFeedback(e.target.value)} placeholder="Ex.: Na empresa ABC, desabilitar horas extras no Controle de Ponto." minLength={10} maxLength={8000}/></label><button className="primary" disabled={busy||feedback.trim().length<10} onClick={()=>act('orchestrate',{request:feedback,comment:'',primary_model:w.primary_model,escalation_model:w.escalation_model})}>Analisar novo pedido</button></section>;
 if(['workflow_error','analyzing'].includes(selected.status))return <section className="card"><h2>{selected.status==='analyzing'?'Análise em andamento':'Não foi possível concluir a análise'}</h2><p>{w.error||(selected.status==='analyzing'?'A análise foi iniciada. Se a conexão foi interrompida, use Tentar novamente para retomar.':'O processamento foi interrompido.')}</p><button className="primary" disabled={busy} onClick={()=>act('workflow',{action:'retry'})}>Tentar novamente</button></section>;
 if(w.phase==='closed')return <section className="card"><h2>Atendimento concluído pelo consultor</h2><p>{w.resolution}</p></section>;
 if(w.phase==='guide')return <section className="card"><p>Vamos identificar a empresa e o assunto automaticamente a partir do seu pedido.</p><button className="primary" disabled={busy} onClick={()=>act('orchestrate',{primary_model:w.primary_model,escalation_model:w.escalation_model})}>Analisar meu pedido</button></section>;
 if(w.phase==='clarify')return <section className="card"><h2>Preciso de um detalhe</h2>{w.clarification_count>0&&<p role="status" className="notice">Esclarecimento recebido. Ainda preciso da informação abaixo para continuar.</p>}<p>{w.explanation}</p><label>Seu esclarecimento<textarea value={feedback} onChange={e=>setFeedback(e.target.value)} maxLength={8000}/></label><button className="primary" disabled={busy||feedback.trim().length<3} onClick={()=>act('workflow',{action:'clarify',comment:feedback})}>Continuar</button><button disabled={busy} onClick={()=>act('workflow',{action:'handoff',comment:feedback})}>Enviar para Não tratados</button><details><summary>Reanalisar o pedido original</summary><p>Use esta opção se a integração foi atualizada depois deste atendimento.</p><button disabled={busy} onClick={()=>act('orchestrate',{primary_model:w.primary_model,escalation_model:w.escalation_model})}>Reanalisar pedido</button></details></section>;
 if(w.phase==='consultant')return <section className="card"><h2>Este pedido precisa de um consultor</h2>{w.handoff_summary?.understood||w.handoff_summary?.blocked_by?<dl className="handoff">{handoffLabels.filter(([k])=>w.handoff_summary[k]).map(([k,label])=><div key={k}><dt>{label}</dt><dd>{w.handoff_summary[k]}</dd></div>)}</dl>:<p style={{whiteSpace:'pre-line'}}>{w.reason}</p>}<p>O atendimento foi registrado em Não tratados para revisão de um consultor. Nenhuma configuração foi aplicada.</p><details><summary>Corrigir o entendimento do pedido</summary><label>O que precisa ser corrigido?<textarea value={feedback} onChange={e=>setFeedback(e.target.value)} maxLength={8000}/></label><button disabled={busy||feedback.trim().length<3} onClick={()=>act('orchestrate',{comment:feedback,primary_model:w.primary_model,escalation_model:w.escalation_model})}>Refazer entendimento</button></details><details><summary>Área do consultor</summary><label>Interpretação revisada ou conclusão<textarea value={feedback} onChange={e=>setFeedback(e.target.value)} maxLength={8000}/></label><div className="actions">{!!w.catalog?.fields?.length&&<button className="primary" disabled={busy||feedback.trim().length<10} onClick={()=>act('workflow',{action:'expert',comment:feedback})}>Preparar proposta revisada</button>}<button disabled={busy||feedback.trim().length<10} onClick={()=>act('workflow',{action:'close',comment:feedback})}>Registrar conclusão e encerrar</button></div></details></section>;
 if(w.phase!=='choice')return null;
 // One suggestion for the whole request, item by item; a doubtful item comes with the suggested reading picked.
 const items:Json[]=w.suggestion?.items||[];
 if(!items.length)return <section className="card"><h2>Este atendimento precisa de uma nova análise</h2><p>Ele foi analisado no formato anterior, com três opções. Reanalise para receber a sugestão item por item.</p><button className="primary" disabled={busy} onClick={()=>act('orchestrate',{primary_model:w.primary_model,escalation_model:w.escalation_model})}>Reanalisar pedido</button></section>;
 const pick=(item:Json)=>picks[item.id]??0;
 const chosen=(item:Json)=>item.readings[pick(item)]||item.readings[0];
 const leftOut=items.filter((item:Json)=>chosen(item).status==='gap');
 const nothing=leftOut.length===items.length;
 return <section className="card suggestion"><h2>Sugestão para o seu pedido</h2><p className="muted">{w.confirmed_company?.name} · {w.module_label}</p>{w.suggestion.explanation&&<p>{human(w.suggestion.explanation)}</p>}{w.information_gaps?.length>0&&<p className="notice" role="note">{w.information_gaps.map((g:Json)=>g.description).join(' ')} Confira antes de aceitar.</p>}
 <ol className="suggestion-items">{items.map((item:Json)=>{const reading=chosen(item);return <li key={item.id} className={reading.status}><strong>{item.text}</strong>{item.readings.length>1&&<fieldset><legend>{item.question||'Qual destas leituras?'}</legend>{item.readings.map((option:Json,k:number)=><label key={k} className={pick(item)===k?'chosen':''}><input type="radio" name={'item-'+item.id} checked={pick(item)===k} onChange={()=>setPicks({...picks,[item.id]:k})}/>{human(option.label)}{k===0&&<small>sugerida</small>}</label>)}</fieldset>}<p><span className="status">{statusLabels[reading.status]}</span> {human(reading.how)}</p>{reading.warning&&<p className="caption">Atenção: {human(reading.warning)}</p>}</li>})}</ol>
 {leftOut.length>0&&!nothing&&<p className="notice" role="note">Ao aceitar, {leftOut.length===1?'este item não será configurado':'estes itens não serão configurados'}: {leftOut.map((item:Json)=>item.text).join('; ')}. {leftOut.length===1?'Ele fica registrado':'Eles ficam registrados'} em Não tratados.</p>}
 {nothing&&<p className="notice" role="note">Com estas escolhas, nada do pedido pode ser configurado no HCM. Ao aceitar, o pedido vai para um consultor e nada é aplicado.</p>}
 {rejecting&&<label>O que está errado na sugestão?<textarea value={feedback} onChange={e=>setFeedback(e.target.value)} placeholder="Pode corrigir a empresa, o assunto ou a regra desejada." maxLength={8000}/></label>}
 <div className="actions">{rejecting?<><button className="primary" disabled={busy} onClick={()=>act('workflow',{action:'choose',choice:'none',comment:feedback})}>{w.attempt===1?'Refazer entendimento':'Pedir ajuda ao consultor'}</button><button disabled={busy} onClick={()=>setRejecting(false)}>Voltar à sugestão</button></>
 :<><button className="primary" disabled={busy} onClick={()=>act('workflow',{action:'choose',choice:'accept',readings:picks})}>Aceitar sugestão</button><button disabled={busy} onClick={()=>setRejecting(true)}>Não é isso</button></>}</div>
 </section>
}

export function ConsultantQueue({open}:{open:(id:number)=>void}){
 const [items,setItems]=useState<Json[]>([]),[error,setError]=useState('');
 useEffect(()=>{fetch('/api/unhandled').then(async r=>{if(!r.ok)throw new Error('Não foi possível carregar a fila.');return r.json()}).then(setItems).catch(e=>setError(e.message))},[]);
 return <><p className="eyebrow">PENDÊNCIAS DE PARAMETRIZAÇÃO</p><h1>Não tratados.</h1><p>Pedidos que a IA não concluiu. Abra um atendimento para consultar o motivo, revisar a regra e registrar a solução.</p>{error&&<p role="alert" className="error">{error}</p>}{items.length?items.map(i=><button className="case-row" key={i.id} onClick={()=>open(i.id)}><span className="case-number">#{i.id}</span><div><strong>{i.request}</strong><small>{i.company||'Empresa a identificar'} · {i.module||'Assunto em análise'} · {i.reason}</small></div><span>Abrir atendimento →</span></button>):<div className="empty">Nenhum atendimento não tratado.</div>}</>
}

export function KnowledgeSettings(){
 const [values,setValues]=useState<Json|null>(null),[error,setError]=useState('');
 useEffect(()=>{fetch('/api/knowledge/settings').then(r=>r.json()).then(setValues).catch(()=>setError('Não foi possível carregar as preferências.'))},[]);
 async function toggle(key:string){if(!values)return;const next={...values,[key]:!values[key]};try{const r=await fetch('/api/knowledge/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)});if(!r.ok)throw new Error();setValues(await r.json())}catch{setError('Não foi possível salvar a preferência.')}}
 if(!values)return null;
 return <section className="card"><h2>Fontes de conhecimento</h2><p>Além do catálogo e dos casos validados desta empresa, os agentes podem consultar duas fontes extras. Nenhuma delas treina o modelo nem autoriza alterações.</p>{error&&<p className="error">{error}</p>}
 <label><input type="checkbox" checked={!!values.cross_company} onChange={()=>toggle('cross_company')}/> Experiência anonimizada de outras empresas, no mesmo módulo e contrato, com nomes de empresa e grupos redigidos</label>
 <label><input type="checkbox" checked={!!values.lessons} onChange={()=>toggle('lessons')}/> Lições de atendimentos anteriores: interpretações recusadas, planos rejeitados pelo validador e conclusões do consultor</label></section>
}

export function MetricsPanel(){
 const [data,setData]=useState<Json|null>(null),[error,setError]=useState('');
 useEffect(()=>{fetch('/api/metrics').then(async r=>{if(!r.ok)throw new Error();setData(await r.json())}).catch(()=>setError('Não foi possível carregar as métricas.'))},[]);
 if(error)return <p className="error">{error}</p>;
 if(!data)return null;
 const usd=(v:number)=>'US$ '+(v||0).toFixed(4);
 return <section className="card"><h2>Uso e resultados</h2><p className="caption">Agregado de todos os atendimentos registrados. Custo é estimativa pelo consumo reportado, não fatura.</p>
 <div className="table-scroll"><table><thead><tr><th>Modelo</th><th>Papel</th><th>Chamadas</th><th>Reparos</th><th>Tokens</th><th>Duração média</th><th>Custo estimado</th></tr></thead><tbody>{(data.by_model||[]).map((m:Json)=><tr key={m.model+m.role}><td>{m.model}</td><td>{m.role}</td><td>{m.calls}</td><td>{m.repairs}</td><td>{m.prompt_tokens+m.completion_tokens}</td><td>{m.average_seconds}s</td><td>{usd(m.cost_usd)}</td></tr>)}</tbody></table></div>
 <div className="table-scroll"><table><thead><tr><th>Módulo</th><th>Atendimentos</th><th>Aceitos na 1ª rodada</th><th>Reanálises</th><th>Ao consultor</th><th>Aplicados</th><th>Revertidos</th><th>Validados</th></tr></thead><tbody>{(data.by_module||[]).map((m:Json)=><tr key={m.module}><td>{m.module}</td><td>{m.cases}</td><td>{m.first_round_accepted}</td><td>{m.escalations}</td><td>{m.consultant}</td><td>{m.applied}</td><td>{m.reverted}</td><td>{m.validated}</td></tr>)}</tbody></table></div>
 <p className="caption">Custo total estimado {usd(data.totals?.cost_usd)} · casos validados {data.totals?.validated} · custo por caso validado {data.totals?.cost_per_validated_case!=null?usd(data.totals.cost_per_validated_case):'—'}</p></section>
}

export function CapabilityGaps({open}:{open:(id:number)=>void}){
 const [items,setItems]=useState<Json[]>([]);
 useEffect(()=>{fetch('/api/unhandled/capabilities').then(r=>r.ok?r.json():[]).then(setItems).catch(()=>{})},[]);
 if(!items.length)return null;
 return <section className="card"><div className="section-heading"><h2>Capacidades ausentes no HCM</h2><a className="text-button" href="/api/unhandled/capabilities?format=csv">Exportar CSV</a></div><p className="caption">Lacunas descritas pelos agentes e confirmadas pelo encaminhamento, agrupadas por módulo e descrição. Insumo para o roadmap do HCM.</p><ul>{items.map((g:Json)=><li key={g.module+g.description}><strong>{g.module}</strong> · {g.description} · {g.count} atendimento(s){g.open>0&&` · ${g.open} em aberto`} {g.case_ids.map((id:number)=><button key={id} className="text-button" onClick={()=>open(id)}>#{id}</button>)}</li>)}</ul></section>
}

export function InterpretationMemories(){
 const [items,setItems]=useState<Json[]>([]),[error,setError]=useState('');
 const refresh=()=>fetch('/api/interpretation-memories').then(async r=>{if(!r.ok)throw new Error('Não foi possível carregar a memória.');setItems(await r.json())}).catch(e=>setError(e.message));
 useEffect(()=>{refresh()},[]);
 async function forget(id:number){try{const r=await fetch(`/api/interpretation-memories/${id}/forget`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!r.ok)throw new Error('Não foi possível desativar a memória.');await refresh()}catch(e){setError(e instanceof Error?e.message:'Falha ao desativar.')}}
 return <section className="card"><h2>Interpretações confirmadas</h2><p>Estas lembranças ajudam a entender seus próximos pedidos. São separadas dos casos aplicados, testados e aprovados; não treinam automaticamente o modelo.</p>{error&&<p className="error">{error}</p>}{!items.length&&<p className="muted">Escolha uma interpretação em um atendimento para começar esta memória.</p>}{items.map(i=><article className="confirmed-intent" key={i.id}><small>{i.module} · Atendimento #{i.case_id} · {i.active?'Ativa':'Desativada'}</small><p>{i.request}</p><strong>{i.meaning}</strong>{i.active&&<div><button onClick={()=>forget(i.id)}>Não usar mais esta lembrança</button></div>}</article>)}</section>
}
