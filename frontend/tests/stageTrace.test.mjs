import test from 'node:test';
import assert from 'node:assert/strict';
import {buildStageLine,spansOf,stageKey} from '../app/stageTrace.ts';

const at=second=>`2026-11-02T16:00:${String(second).padStart(2,'0')}Z`;
const stop=(id,activities,extra={})=>({type:'stop',id,label:id,icon:id,tech:[],node:null,activities,steps:[],case_events:[],optional:false,...extra});
const decision=(node,to,answer='não')=>({type:'decision',node,question:`${node}?`,answer,to,label:to,note:'',internal:false});
const model=['context_prepare','model_prepare','model_request','model_response','model_decode','output_validate','output_retry'];
const map={main:{classify:'preview',preview:'context',context:'alternatives',choice:'remember',remember:'plan'},detours:{},stages:[
 {id:'classification',nodes:['classify'],exit:'classify',items:[stop('modules',['modules_read'],{optional:true}),stop('agent',model),decision('classify','__end__','sim'),decision('classify','context_expert','sim')]},
 {id:'context',nodes:['preview','context'],exit:'context',items:[stop('company',['company_lookup']),decision('preview','clarify'),stop('catalog',['catalog_read']),decision('context','consultant')]},
 {id:'choice',nodes:['choice','remember'],exit:'remember',items:[stop('choice',[],{steps:['choice'],case_events:['interpretation_confirmed','interpretation_rejected']}),decision('choice','guard_input','sim, 1ª vez'),stop('intent',['intent_confirm'],{steps:['intent']})]},
]};
const stage=id=>map.stages.find(s=>s.id===id);
const run=events=>({run_id:'r1',active:false,base_revision:1,step:'',state:'done',updated_at:at(59),events});
const span=(step,activity,id,start,end,meta={})=>[{step,state:'running',at:at(start),activity,span_id:id,meta},{step,state:'done',at:at(end),activity,span_id:id,meta}];
const route=(step,source,target,second)=>({step,state:'done',at:at(second),activity:'route',meta:{source,target}});
const call=[...span('classification','context_prepare','a',1,1),...span('classification','model_prepare','b',2,2,{model:'gpt-6-luna',reasoning_effort:'medium'}),
 {step:'classification',state:'running',at:at(3),activity:'model_request',span_id:'c',meta:{model:'gpt-6-luna'}},
 {step:'classification',state:'done',at:at(9),activity:'model_response',meta:{prompt_tokens:1200,completion_tokens:300}},
 {step:'classification',state:'done',at:at(9),activity:'model_request',span_id:'c',meta:{duration_seconds:6}},
 ...span('classification','model_decode','d',10,10),...span('classification','output_validate','e',11,11)];

test('one model call is one agent stop, and the recorded route keeps the main line',()=>{
 const line=buildStageLine(map,stage('classification'),run([...call,route('classification','classify','preview',12)]));
 const [agent,outOfScope,resume,exit]=line;
 assert.equal(line.length,4,'the optional modules stop stays hidden without records');
 assert.equal(agent.id,'agent');assert.equal(agent.number,1);assert.equal(agent.state,'done');
 assert.deepEqual(agent.spans.map(s=>s.activity),['context_prepare','model_prepare','model_request','model_response','model_decode','output_validate']);
 assert.equal(agent.spans[2].meta.duration_seconds,6);
 assert.deepEqual([outOfScope.outcome,resume.outcome],['main','main']);
 assert.equal(exit.state,'done');
});

test('a branch taken leaves the rest of the line unreached',()=>{
 const line=buildStageLine(map,stage('context'),run([...span('company','company_lookup','f',1,2),route('company','preview','clarify',3)]));
 const [company,identified,catalog,connector,exit]=line;
 assert.equal(company.state,'done');
 assert.equal(identified.outcome,'branch');
 assert.equal(catalog.state,'next');assert.equal(catalog.reached,false);
 assert.equal(connector.outcome,'unknown');
 assert.equal(exit.state,'next');
});

test('runs recorded before routes existed do not invent a path',()=>{
 const line=buildStageLine(map,stage('classification'),run(call));
 assert.deepEqual(line.filter(i=>i.type==='decision').map(i=>i.outcome),['unknown','unknown']);
 assert.equal(line.at(-1).state,'unknown');
 const old=buildStageLine(map,stage('context'),undefined,[],'unknown');
 assert.deepEqual(old.map(i=>i.type==='decision'?i.outcome:i.state),['unknown','unknown','unknown','unknown','unknown']);
});

test('a human decision recorded after the pause closes it; a new pause reopens it',()=>{
 const pause=[{step:'choice',state:'waiting',at:at(1)}];
 assert.equal(buildStageLine(map,stage('choice'),run(pause))[0].state,'waiting');
 const confirmed=[{type:'interpretation_confirmed',at:at(5)}];
 const line=buildStageLine(map,stage('choice'),run(pause),confirmed);
 assert.equal(line[0].state,'done');assert.equal(line[0].facts.length,1);
 assert.equal(buildStageLine(map,stage('choice'),run([...pause,{step:'choice',state:'waiting',at:at(8)}]),confirmed)[0].state,'waiting');
});

test('a failure recorded only for the stage lands on the last stop with records',()=>{
 const line=buildStageLine(map,stage('classification'),run([...span('classification','context_prepare','a',1,1),{step:'classification',state:'error',at:at(2)}]));
 assert.equal(line[0].state,'error');
});

test('routes are not operations; stage keys group the context and choice records',()=>{
 assert.deepEqual(spansOf([route('company','preview','context',1),...span('catalog','catalog_read','g',2,3,{module:'vacations'})]).map(s=>[s.activity,s.state,s.meta.module]),[['catalog_read','done','vacations']]);
 assert.equal(stageKey('catalog'),'context');
 assert.equal(stageKey('intent'),'choice');
 assert.equal(stageKey('rollback'),'apply');
});
