
'use client';
type Json=Record<string,any>;
export default function ExecutionPanel({selected,busy,act}:{selected:Json;busy:boolean;act:(action:string,extra?:Json)=>Promise<void>}){
 const r=selected.result||{}, report=r.automatic_tests;
 const pending=['executing','execution_unknown'].includes(selected.status);
 const legacy=['awaiting_execution','result_checked'].includes(selected.status)&&!r.operation_id;
 const applied=['applied','verification_failed','validated','rollback_unknown'].includes(selected.status);
 if(!pending&&!legacy&&!applied&&!['execution_failed','execution_blocked','reverted'].includes(selected.status))return null;
 const success=selected.status==='applied'&&report?.passed;
 const company=selected.workflow?.confirmed_company?.name||selected.snapshot?.company?.name||'empresa';
 const fields=selected.workflow?.catalog?.fields||selected.events?.findLast((e:Json)=>e.sources)?.sources?.find((s:Json)=>s.id.startsWith('catalog:'))?.content?.fields||[];
 return <section className="card completion-panel">
 <h2>{selected.status==='validated'?'Experiência guardada':success?'Configuração aplicada no HCM':selected.status==='reverted'?'Configuração anterior restaurada':'Resultado da aplicação'}</h2>
 {legacy&&<><p>A proposta ainda precisa ser aplicada no HCM.</p><button className="primary" disabled={busy} onClick={()=>act('execute',{approval_hash:selected.approval_hash})}>Aprovar e aplicar em homologação</button>{r.differences?.length>0&&<p className="notice">O HCM apresentou diferenças na última consulta. Atualize a proposta antes de aplicar.</p>}</>}
 {pending&&<><p>A aplicação foi solicitada. Precisamos confirmar a resposta do HCM para concluir.</p><button className="primary" disabled={busy} onClick={()=>act('execute',{approval_hash:selected.approval_hash})}>Consultar resultado da aplicação</button></>}
 {success&&<><p>A configuração de {company} foi gravada em homologação e passou nas verificações automáticas.</p><p>Se o resultado atende ao seu pedido, aprove para guardar esta experiência e ajudar nos próximos atendimentos.</p><button className="primary" disabled={busy} onClick={()=>act('confirm-result')}>Aprovar e guardar experiência</button></>}
 {selected.status==='validated'&&<p>Você aprovou o resultado. Este atendimento poderá ajudar a preparar configurações em pedidos futuros.</p>}
 {selected.status==='verification_failed'&&<div role="alert" className="error"><p>Encontramos diferenças após a aplicação. O resultado ainda não pode ser aprovado.</p>{report?.checks?.filter((c:Json)=>!c.passed).map((c:Json)=><p key={c.name}>{c.name}</p>)}<button disabled={busy} onClick={()=>act('verify')}>Verificar novamente</button><button disabled={busy} onClick={()=>act('rollback')}>Desfazer esta aplicação</button></div>}
 {selected.status==='execution_blocked'&&<p role="alert" className="notice">O HCM bloqueou a aplicação. Confira o aviso e atualize o entendimento antes de gerar outra proposta.</p>}
 {selected.status==='execution_failed'&&<p role="alert" className="error">A configuração não passou nos testes. O HCM desfez a tentativa; os novos valores não foram gravados.</p>}
 {selected.status==='rollback_unknown'&&<><p>A reversão foi solicitada e precisa ser confirmada.</p><button disabled={busy} onClick={()=>act('rollback')}>Consultar reversão</button></>}
 {selected.status==='reverted'&&<p>As configurações anteriores foram restauradas. Este atendimento não será usado como solução aprovada.</p>}
 <p className="caption">{r.production_published?'Ambiente de homologação. A produção já recebeu estas regras pelo processo de publicação do HCM.':'Ambiente de homologação. Produção preservada.'}</p>
 {applied&&selected.proposal?.summary&&<details className="technical-details"><summary>Ver configuração aplicada</summary><p>{selected.proposal.summary}</p><ul>{selected.proposal.changes?.map((c:Json,i:number)=><li key={i}>{c.config_code?`Configuração ${c.config_code} · `:c.group_id!=null?`Grupo ${c.group_id} · `:''}{fields.find((f:Json)=>f.id===c.parameter)?.label||c.parameter}: {typeof c.value==='boolean'?(c.value?'Sim':'Não'):c.value!==null&&typeof c.value==='object'?JSON.stringify(c.value):String(c.value)}</li>)}</ul></details>}
 {report?.checks&&selected.status!=='reverted'&&<details className="technical-details"><summary>Ver detalhes das verificações automáticas</summary><p>Estes testes conferem se os valores foram aplicados e se as regras implementadas no HCM se comportam como esperado.</p><ul>{report.checks.map((check:Json,i:number)=><li key={i}><strong>{check.passed?'Passou':'Falhou'}: {check.name}</strong>{check.probes&&<details><summary>Ver cenários testados ({check.probes.length})</summary><pre>{JSON.stringify(check.probes,null,2)}</pre></details>}</li>)}</ul>{report.limits&&<p className="caption">{report.limits}</p>}{r.operation_id&&<p className="caption">Registro da operação: {r.operation_id}</p>}</details>}
 {['applied','validated'].includes(selected.status)&&<details className="technical-details"><summary>Outras opções</summary><div className="actions">{selected.status==='applied'&&<button disabled={busy} onClick={()=>act('verify')}>Verificar novamente</button>}<button disabled={busy} onClick={()=>act('rollback')}>Desfazer esta aplicação em homologação</button></div><p className="caption">A reversão só é permitida se não houver alterações posteriores no HCM.</p></details>}
 </section>
}
