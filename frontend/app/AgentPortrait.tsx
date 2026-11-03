'use client';

import {useId} from 'react';

/** The agents' robots, shared by the journey map and the stage stops. */
export default function AgentPortrait({kind,className='journey-robot'}:{kind:string;className?:string}){
 const uid=useId().replace(/:/g,'');
 return <svg viewBox="0 0 96 96" className={className+' '+kind} aria-hidden="true"><defs><linearGradient id={uid} x2=".8" y2="1"><stop stopColor="#fff"/><stop offset="1" stopColor="#b6ccc5"/></linearGradient></defs>
  <ellipse cx="48" cy="84" rx="27" ry="5" fill="#183e3220"/><path d="M27 83v-9c0-15 42-15 42 0v9" fill={`url(#${uid})`} stroke="#71958b" strokeWidth="2"/>
  <path d="M48 17V9" stroke="#668e82" strokeWidth="3"/><circle cx="48" cy="8" r="4" fill="#a4ce74"/>
  <rect x="14" y="35" width="8" height="20" rx="4" fill="#94b4aa"/><rect x="74" y="35" width="8" height="20" rx="4" fill="#94b4aa"/>
  <rect x="21" y="20" width="54" height="47" rx="19" fill={`url(#${uid})`} stroke="#71958b" strokeWidth="2"/>
  <rect x="28" y="31" width="40" height="26" rx="11" fill="#234b46"/><ellipse cx="39" cy="43" rx="3.5" ry="5" fill="#d8f3ab"/><ellipse cx="57" cy="43" rx="3.5" ry="5" fill="#d8f3ab"/><path d="M44 51q4 3 8 0" fill="none" stroke="#95bbac" strokeWidth="1.5"/>
  {kind==='classifier'?<g stroke="#386a5b" strokeWidth="4"><circle cx="70" cy="65" r="12" fill="#edf9edc9"/><path d="m78 74 10 11" strokeLinecap="round"/></g>:kind==='interpreter'?<g><rect x="65" y="7" width="26" height="22" rx="6" fill="#98c56c"/><path d="m72 29-5 5v-7M71 15h14M71 20h10" fill="none" stroke="#fff" strokeWidth="2"/></g>:<g transform="rotate(12 75 72)"><rect x="63" y="54" width="26" height="34" rx="4" fill="#f7faf4" stroke="#668f80" strokeWidth="2"/><rect x="70" y="51" width="12" height="6" rx="2" fill="#94b66d"/><path d="m68 64 3 3 5-6m2 5h6m-16 9 3 3 5-6m2 5h6" fill="none" stroke="#648f73" strokeWidth="2"/></g>}
 </svg>;
}
